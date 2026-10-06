"""Derivatives Scope Resolution — Milestone G.11-OPT-3 (Phases 1 & 2).

Single authoritative source for the derivatives workbench *scope*: which
underlying, which expiry, which listed strikes, which ATM, which lot size, and
which shared filter state the user selected.

Root cause this module exists to eliminate
------------------------------------------
The pre-OPT-3 workbench derived ATM from ``round(spot / hardcoded_strike_step)``
using a spot price that the provider parser hardcoded to ``24500.00`` for every
underlying. NIFTY (23,140.50) and BANKNIFTY (55,580.40) therefore produced the
*same* ATM of 24,500, which is not even a listed BANKNIFTY strike.

This module resolves ATM from the **actual listed strike grid** published by the
authorized provider contract master, independently per underlying, with:

  * a documented, deterministic tie-break rule,
  * a documented material-change rule for recomputation,
  * explicit provenance so a calendar-derived fallback can never be presented as
    a provider-listed contract,
  * lot/tick metadata that is *refused* when absent rather than guessed.

Strict architecture rules preserved: read-only, zero trading logic, zero
fabrication, no secrets in output.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.core.config import settings

#: Market clock. A naive timestamp is always read as IST, never as UTC.
IST = timezone(timedelta(hours=5, minutes=30), name="IST")

logger = logging.getLogger(__name__)

# ===========================================================================
# 1. Interval, window, strike-count and unit vocabularies (Phase 2/5/6/7)
# ===========================================================================

#: Charting/aggregation intervals actually supported by the interval engine.
INTERVAL_CHOICES_MINUTES: tuple[int, ...] = (1, 2, 3, 5, 10, 15, 30, 45, 60)

#: Analysis windows offered by the OI Change workbench (subset + full session).
ANALYSIS_WINDOW_CHOICES_MINUTES: tuple[int, ...] = (3, 5, 10, 15, 30, 45, 60)

#: Strike-count selector values. ``None`` means "all listed strikes".
STRIKE_COUNT_CHOICES: tuple[int, ...] = (5, 10, 15, 20, 25)

#: Semantics of the strike-count control. The UI must label this explicitly.
STRIKE_COUNT_SEMANTICS = "STRIKES_PER_SIDE"

WINDOW_FULL_SESSION = "FULL_SESSION"
WINDOW_CUSTOM = "CUSTOM"

OI_UNIT_CONTRACTS = "CONTRACTS"
OI_UNIT_LOTS = "LOTS"
OI_UNIT_QUANTITY = "QUANTITY"
OI_UNIT_CHOICES: tuple[str, ...] = (OI_UNIT_CONTRACTS, OI_UNIT_LOTS, OI_UNIT_QUANTITY)

#: Baseline provenance labels for interval deltas (Phase 6).
BASELINE_INTRADAY_INTERVAL = "INTRADAY_INTERVAL"
BASELINE_PREVIOUS_CLOSE = "PREVIOUS_CLOSE"
BASELINE_CUSTOM_RANGE = "CUSTOM_RANGE"
BASELINE_NONE = "NONE"

#: Provenance labels. Never collapse these: the UI renders them verbatim.
PROV_PROVIDER_LISTED = "PROVIDER_LISTED_CONTRACT_MASTER"
PROV_CALENDAR_FALLBACK = "CALENDAR_DERIVED_FALLBACK"
PROV_PERSISTED_OBSERVATION = "PERSISTED_PROVIDER_OBSERVATION"
PROV_LIVE_OBSERVATION = "LIVE_PROVIDER_OBSERVATION"
PROV_MODEL_CALCULATED = "MODEL_CALCULATED"
PROV_UNAVAILABLE = "UNAVAILABLE"

#: Reason codes surfaced whenever a value cannot be produced from real data.
REASON_NO_LISTED_STRIKES = "NO_LISTED_STRIKES"
REASON_NO_SPOT = "NO_SPOT_PRICE"
REASON_UNSUPPORTED_UNDERLYING = "UNSUPPORTED_UNDERLYING"
REASON_EXPIRY_NOT_LISTED = "EXPIRY_NOT_LISTED"
REASON_NO_LOT_SIZE = "NO_VERIFIED_LOT_SIZE"
REASON_INSUFFICIENT_OBSERVATIONS = "INSUFFICIENT_OBSERVATIONS"
REASON_STALE_SPOT = "STALE_SPOT"

#: Tie-break rule used when a spot is exactly equidistant between two listed
#: strikes. ``LOWER`` is the documented default: it is deterministic and
#: conservative (it never rounds the money-ness upward).
TIE_BREAK_LOWER = "LOWER"
TIE_BREAK_HIGHER = "HIGHER"
DEFAULT_TIE_BREAK = TIE_BREAK_LOWER

#: A spot move is "material" for ATM purposes when it can change the selected
#: strike, i.e. it reaches half of one listed grid step.
MATERIAL_SPOT_MOVE_STEP_FRACTION = Decimal("0.5")


# ===========================================================================
# 2. Underlying catalog — identity only, never prices or grids
# ===========================================================================

@dataclass(frozen=True)
class UnderlyingDefinition:
    """Identity and provider addressing for one supported underlying.

    Deliberately excludes strike step and lot size: those are *provider facts*
    resolved from the contract master. ``fallback_strike_step`` exists only so a
    clearly-labelled calendar/offline path can be evaluated, and every consumer
    must propagate ``grid_source`` so fallback values are never shown as listed.
    """

    symbol: str
    name: str
    exchange: str
    segment: str
    contract_type: str
    index_instrument_key: str | None
    fallback_strike_step: Decimal | None
    instrument_master_exchange: str | None


UNDERLYING_CATALOG: dict[str, UnderlyingDefinition] = {
    "NIFTY": UnderlyingDefinition(
        symbol="NIFTY",
        name="NIFTY 50",
        exchange="NSE",
        segment="NFO",
        contract_type="INDEX",
        index_instrument_key="NSE_INDEX|Nifty 50",
        fallback_strike_step=Decimal("50"),
        instrument_master_exchange="NSE",
    ),
    "BANKNIFTY": UnderlyingDefinition(
        symbol="BANKNIFTY",
        name="NIFTY BANK",
        exchange="NSE",
        segment="NFO",
        contract_type="INDEX",
        index_instrument_key="NSE_INDEX|Nifty Bank",
        fallback_strike_step=Decimal("100"),
        instrument_master_exchange="NSE",
    ),
    "FINNIFTY": UnderlyingDefinition(
        symbol="FINNIFTY",
        name="NIFTY FINANCIAL SERVICES",
        exchange="NSE",
        segment="NFO",
        contract_type="INDEX",
        index_instrument_key="NSE_INDEX|Nifty Fin Service",
        fallback_strike_step=Decimal("50"),
        instrument_master_exchange="NSE",
    ),
    "MIDCPNIFTY": UnderlyingDefinition(
        symbol="MIDCPNIFTY",
        name="NIFTY MIDCAP SELECT",
        exchange="NSE",
        segment="NFO",
        contract_type="INDEX",
        index_instrument_key="NSE_INDEX|NIFTY MID SELECT",
        fallback_strike_step=Decimal("25"),
        instrument_master_exchange="NSE",
    ),
    "SENSEX": UnderlyingDefinition(
        symbol="SENSEX",
        name="S&P BSE SENSEX",
        exchange="BSE",
        segment="BFO",
        contract_type="INDEX",
        index_instrument_key="BSE_INDEX|SENSEX",
        fallback_strike_step=Decimal("100"),
        instrument_master_exchange="BSE",
    ),
}

#: All canonical supported index underlying symbols.
SUPPORTED_UNDERLYINGS: tuple[str, ...] = tuple(sorted(UNDERLYING_CATALOG.keys()))

_INDEX_ALIASES: dict[str, str] = {
    "NIFTY50": "NIFTY",
    "NIFTY_50": "NIFTY",
    "NIFTYBANK": "BANKNIFTY",
    "NIFTY_BANK": "BANKNIFTY",
    "BANK_NIFTY": "BANKNIFTY",
    "NIFTYFINSERVICE": "FINNIFTY",
    "NIFTY_FIN_SERVICE": "FINNIFTY",
    "NIFTYFINANCIALSERVICES": "FINNIFTY",
    "NIFTY_MID_SELECT": "MIDCPNIFTY",
    "NIFTYMIDSELECT": "MIDCPNIFTY",
    "MIDCAPNIFTY": "MIDCPNIFTY",
    "BSESENSEX": "SENSEX",
    "BSE_SENSEX": "SENSEX",
}


def normalize_underlying(symbol: str) -> str:
    """Normalize an underlying symbol to its canonical catalog key form.

    Stock underlyings are returned in upper-case as-is; only index aliases are
    rewritten. Never raises.
    """
    if not symbol or not symbol.strip():
        return ""
    cleaned = symbol.strip().upper().replace(" ", "").replace("-", "_")
    return _INDEX_ALIASES.get(cleaned, cleaned)


def get_underlying(symbol: str) -> UnderlyingDefinition | None:
    """Return the catalog definition for an underlying, or ``None``."""
    return UNDERLYING_CATALOG.get(normalize_underlying(symbol))


def supported_underlying_rows() -> list[dict[str, Any]]:
    """Catalog rows for the UI underlying selector."""
    return [
        {
            "symbol": spec.symbol,
            "name": spec.name,
            "exchange": spec.exchange,
            "segment": spec.segment,
            "contract_type": spec.contract_type,
            "is_index": True,
        }
        for spec in UNDERLYING_CATALOG.values()
    ]


# ===========================================================================
# 3. Listed strike grid inference
# ===========================================================================

@dataclass(frozen=True)
class ListedStrikeGrid:
    """The strikes that actually exist for one underlying + expiry.

    ``step`` is *observed*, not assumed. When the listed grid is not uniformly
    spaced (deep OTM/ITM wings genuinely widen on Indian indices) ``is_uniform``
    is False and ``step`` describes the tight grid around the window.
    """

    strikes: tuple[Decimal, ...]
    step: Decimal | None
    is_uniform: bool
    observed_steps: tuple[Decimal, ...] = ()
    source: str = PROV_PROVIDER_LISTED
    reason: str | None = None

    @property
    def is_available(self) -> bool:
        return bool(self.strikes)

    @property
    def min_strike(self) -> Decimal | None:
        return self.strikes[0] if self.strikes else None

    @property
    def max_strike(self) -> Decimal | None:
        return self.strikes[-1] if self.strikes else None

    def contains(self, strike: Decimal) -> bool:
        return strike in self.strikes

    def describe(self) -> str:
        if not self.strikes:
            return f"No listed strikes ({self.reason or REASON_NO_LISTED_STRIKES})"
        span = f"{self.min_strike:,.0f}–{self.max_strike:,.0f}"
        step_txt = f"step {self.step:,.0f}" if self.step is not None else "step unknown"
        uniformity = "uniform" if self.is_uniform else "non-uniform wings"
        return f"{len(self.strikes)} listed strikes, {span}, {step_txt} ({uniformity})"


def infer_strike_grid(
    strikes: Iterable[Decimal | float | int | str],
    *,
    source: str = PROV_PROVIDER_LISTED,
    reason: str | None = None,
) -> ListedStrikeGrid:
    """Infer the real listed strike grid from actual listed strikes.

    The dominant (most frequent) spacing is reported as the grid step, so
    widened deep wings do not corrupt the step used near the money.
    """
    parsed: list[Decimal] = []
    for raw in strikes:
        try:
            value = Decimal(str(raw))
        except Exception:  # noqa: BLE001 - defensive: bad provider value
            continue
        if value.is_finite() and value > 0:
            parsed.append(value)

    unique = tuple(sorted(set(parsed)))
    if len(unique) < 2:
        return ListedStrikeGrid(
            strikes=unique,
            step=None,
            is_uniform=False,
            observed_steps=(),
            source=source,
            reason=reason or REASON_NO_LISTED_STRIKES,
        )

    steps = [unique[i + 1] - unique[i] for i in range(len(unique) - 1)]
    counts: dict[Decimal, int] = {}
    for s in steps:
        counts[s] = counts.get(s, 0) + 1
    dominant = max(sorted(counts.items()), key=lambda kv: kv[1])[0]

    return ListedStrikeGrid(
        strikes=unique,
        step=dominant,
        is_uniform=len(counts) == 1,
        observed_steps=tuple(sorted(counts)),
        source=source,
        reason=reason,
    )


# ===========================================================================
# 4. ATM resolution — the Phase 1 fix
# ===========================================================================

@dataclass(frozen=True)
class AtmResolution:
    """Result of resolving ATM against the actual listed strike grid."""

    spot: Decimal | None
    atm_strike: Decimal | None
    method: str
    tie_break: str
    grid_step: Decimal | None
    grid_source: str
    is_listed: bool
    distance: Decimal | None
    lower: Decimal | None = None
    upper: Decimal | None = None
    reason: str | None = None

    @property
    def is_resolved(self) -> bool:
        return self.atm_strike is not None and self.is_listed

    def to_dict(self) -> dict[str, Any]:
        return {
            "spot": _dec_str(self.spot),
            "atm_strike": _dec_str(self.atm_strike),
            "method": self.method,
            "tie_break": self.tie_break,
            "grid_step": _dec_str(self.grid_step),
            "grid_source": self.grid_source,
            "is_listed": self.is_listed,
            "distance": _dec_str(self.distance),
            "lower_strike": _dec_str(self.lower),
            "upper_strike": _dec_str(self.upper),
            "reason": self.reason,
        }


def _dec_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    normalized = value.normalize()
    return format(normalized, "f")


def format_display_number(value: Any, decimals: int = 0) -> str:
    """Format a provider-derived number for display without guessing its type.

    Scope payloads carry exact decimals as strings (``_dec_str``) so a strike,
    lot size or tick size never loses precision on the way to the browser. Call
    sites therefore receive a mix of ``Decimal``, ``float`` and ``str`` and must
    render all of them identically: a provider value must never 500 a page just
    because it arrived as text.

    ``None`` renders as an em dash, matching every other derivatives table.
    Values that are not numeric at all are returned unchanged rather than
    coerced, so an unexpected payload is visible instead of silently zeroed.
    """
    if value is None:
        return "\u2014"
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return str(value)
    if not dec.is_finite():
        return "\u2014"
    try:
        quantized = dec.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return str(value)
    return f"{quantized:,.{decimals}f}"


def resolve_atm(
    spot: Decimal | float | int | str | None,
    grid: ListedStrikeGrid,
    *,
    tie_break: str = DEFAULT_TIE_BREAK,
) -> AtmResolution:
    """Resolve ATM as the **nearest actually listed strike** to the real spot.

    Rules (documented, deterministic, tested):

    1. No spot, or no listed strikes, yields an unresolved result carrying a
       reason code — never a fabricated strike.
    2. Otherwise the candidate is the listed strike minimising
       ``abs(spot - strike)``.
    3. Equal-distance ties are broken by ``tie_break``: ``LOWER`` (default)
       selects the lower strike, ``HIGHER`` selects the upper one. Selection is
       performed on the exact ``(distance, strike)`` pair so the result never
       depends on floating-point noise or on Python's banker's rounding.
    4. ``is_listed`` is always True on success: a strike outside the listed grid
       is never returned.
    """
    if spot is None:
        return AtmResolution(
            spot=None,
            atm_strike=None,
            method="NONE",
            tie_break=tie_break,
            grid_step=grid.step,
            grid_source=grid.source,
            is_listed=False,
            distance=None,
            reason=REASON_NO_SPOT,
        )
    try:
        spot_dec = Decimal(str(spot))
    except Exception:  # noqa: BLE001
        return AtmResolution(
            spot=None,
            atm_strike=None,
            method="NONE",
            tie_break=tie_break,
            grid_step=grid.step,
            grid_source=grid.source,
            is_listed=False,
            distance=None,
            reason=REASON_NO_SPOT,
        )

    if spot_dec <= 0:
        return AtmResolution(
            spot=spot_dec,
            atm_strike=None,
            method="NONE",
            tie_break=tie_break,
            grid_step=grid.step,
            grid_source=grid.source,
            is_listed=False,
            distance=None,
            reason=REASON_NO_SPOT,
        )

    if not grid.strikes:
        return AtmResolution(
            spot=spot_dec,
            atm_strike=None,
            method="NONE",
            tie_break=tie_break,
            grid_step=grid.step,
            grid_source=grid.source,
            is_listed=False,
            distance=None,
            reason=grid.reason or REASON_NO_LISTED_STRIKES,
        )

    below = [s for s in grid.strikes if s <= spot_dec]
    above = [s for s in grid.strikes if s >= spot_dec]
    lower = max(below) if below else None
    upper = min(above) if above else None

    if lower is None:
        chosen, distance, method = upper, (upper - spot_dec), "NEAREST_LISTED_BELOW_GRID_FLOOR"
    elif upper is None:
        chosen, distance, method = lower, (spot_dec - lower), "NEAREST_LISTED_ABOVE_GRID_CEILING"
    elif lower == upper:
        chosen, distance, method = lower, Decimal("0"), "EXACT_LISTED_STRIKE"
    else:
        d_low = spot_dec - lower
        d_high = upper - spot_dec
        if d_low == d_high:
            chosen = lower if tie_break == TIE_BREAK_LOWER else upper
            distance = d_low
            method = f"NEAREST_LISTED_TIE_BROKEN_{tie_break}"
        elif d_low < d_high:
            chosen, distance, method = lower, d_low, "NEAREST_LISTED_STRIKE"
        else:
            chosen, distance, method = upper, d_high, "NEAREST_LISTED_STRIKE"

    return AtmResolution(
        spot=spot_dec,
        atm_strike=chosen,
        method=method,
        tie_break=tie_break,
        grid_step=grid.step,
        grid_source=grid.source,
        is_listed=grid.contains(chosen),
        distance=distance,
        lower=lower,
        upper=upper,
    )


def select_atm_window(
    grid: ListedStrikeGrid,
    atm: Decimal,
    strike_count: int | None,
) -> tuple[Decimal, ...]:
    """Select ``strike_count`` strikes on **each side** of ATM, plus ATM itself.

    ``strike_count=None`` selects every listed strike. Expiry scope is never
    silently widened: selection is bounded strictly to the supplied grid.
    """
    if not grid.strikes:
        return ()
    if atm not in grid.strikes:
        return ()
    idx = grid.strikes.index(atm)
    if strike_count is None or strike_count < 0:
        return grid.strikes
    start = max(0, idx - strike_count)
    end = min(len(grid.strikes), idx + strike_count + 1)
    return grid.strikes[start:end]


def describe_strike_selection(strike_count: int | None, selected: int, available: int) -> str:
    """Unambiguous label for the strike-count control (Phase 3 requirement)."""
    if strike_count is None:
        return f"All listed strikes ({selected} of {available})"
    return (
        f"ATM ±{strike_count} strikes per side — "
        f"{selected} strikes total (of {available} listed)"
    )


def is_material_spot_change(
    previous: Decimal | None,
    current: Decimal | None,
    grid_step: Decimal | None,
) -> bool:
    """Whether a spot move can change the selected ATM (Phase 1 recompute rule).

    Material means the move reaches half of one listed grid step, i.e. the point
    at which the nearest listed strike is capable of changing. When the grid step
    is unknown, any change is treated as material so the resolver re-runs.
    """
    if previous is None or current is None:
        return True
    if grid_step is None or grid_step <= 0:
        return previous != current
    return abs(current - previous) >= (grid_step * MATERIAL_SPOT_MOVE_STEP_FRACTION)


# ===========================================================================
# 5. Lot size / tick size — provider facts, refused when absent
# ===========================================================================

@dataclass(frozen=True)
class LotSizeResolution:
    """Verified lot size for one underlying, with explicit refusal semantics."""

    lot_size: int | None
    tick_size: Decimal | None
    source: str
    reason: str | None = None

    @property
    def is_verified(self) -> bool:
        return self.lot_size is not None and self.lot_size > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "lot_size": self.lot_size,
            "tick_size": _dec_str(self.tick_size),
            "source": self.source,
            "verified": self.is_verified,
            "reason": self.reason,
        }


def resolve_lot_size(
    contracts: Sequence[dict[str, Any]],
    *,
    source: str = PROV_PROVIDER_LISTED,
) -> LotSizeResolution:
    """Resolve lot/tick from provider contract rows.

    Returns an unverified refusal when the provider did not publish a usable lot
    size — never a guess. Callers must refuse contracts↔lots↔quantity conversion
    while ``is_verified`` is False (Phase 8 requirement).
    """
    lots = {int(c["lot_size"]) for c in contracts if c.get("lot_size")}
    if not lots:
        return LotSizeResolution(
            lot_size=None, tick_size=None, source=source, reason=REASON_NO_LOT_SIZE
        )
    ticks = {
        Decimal(str(c["tick_size"]))
        for c in contracts
        if c.get("tick_size") not in (None, "", 0)
    }
    if len(lots) > 1:
        # Mixed lot sizes for one underlying+expiry means the master is
        # inconsistent; report the smallest and degrade to unverified.
        return LotSizeResolution(
            lot_size=min(lots),
            tick_size=min(ticks) if ticks else None,
            source=source,
            reason="AMBIGUOUS_LOT_SIZE",
        )
    return LotSizeResolution(
        lot_size=next(iter(lots)),
        tick_size=next(iter(ticks)) if ticks else None,
        source=source,
    )


def convert_oi_units(
    open_interest_contracts: int | None,
    lot_size: int | None,
    unit: str,
) -> dict[str, Any]:
    """Convert contracts → lots → quantity, refusing unsound conversions.

    Never invents a lot size. Returns ``available: False`` with a reason when the
    requested conversion cannot be performed from verified metadata.
    """
    unit_norm = (unit or OI_UNIT_CONTRACTS).strip().upper()
    if unit_norm not in OI_UNIT_CHOICES:
        return {
            "unit": unit_norm,
            "unit_requested": unit_norm,
            "value": open_interest_contracts,
            "available": False,
            "reason": "UNSUPPORTED_OI_UNIT",
        }
    if open_interest_contracts is None:
        return {
            "unit": unit_norm,
            "value": None,
            "available": False,
            "reason": REASON_INSUFFICIENT_OBSERVATIONS,
        }
    if unit_norm == OI_UNIT_CONTRACTS:
        return {
            "unit": OI_UNIT_CONTRACTS,
            "value": open_interest_contracts,
            "available": True,
            "lot_size": lot_size,
            "reason": None,
        }
    if not lot_size or lot_size <= 0:
        return {
            "unit": unit_norm,
            "value": None,
            "available": False,
            "lot_size": None,
            "reason": REASON_NO_LOT_SIZE,
        }
    lots = Decimal(open_interest_contracts) / Decimal(lot_size)
    if unit_norm == OI_UNIT_LOTS:
        return {
            "unit": OI_UNIT_LOTS,
            "value": float(lots),
            "display": f"{float(lots):,.2f}",
            "available": True,
            "lot_size": lot_size,
            "reason": None,
        }
    return {
        "unit": OI_UNIT_QUANTITY,
        "value": open_interest_contracts * lot_size,
        "available": True,
        "lot_size": lot_size,
        "reason": None,
    }


# ===========================================================================
# 6. Shared derivatives filter schema (Phase 2)
# ===========================================================================

@dataclass
class DerivativesFilterSpec:
    """Validated shared filter/selection state for every derivatives view.

    One schema validates every page's query string so no view can silently widen
    the selected strike range or expiry scope.
    """

    underlying: str = "NIFTY"
    contract_type: str = "INDEX"
    expiry: date | None = None
    expiries: tuple[date, ...] = ()
    strike_count: int | None = 5
    min_strike: Decimal | None = None
    max_strike: Decimal | None = None
    call_strikes: tuple[Decimal, ...] = ()
    put_strikes: tuple[Decimal, ...] = ()
    interval_minutes: int = 5
    window: str = "INTRADAY_INTERVAL"
    session_date: date | None = None
    start_time: str | None = None
    end_time: str | None = None
    oi_unit: str = OI_UNIT_CONTRACTS
    page_size: int = 50
    sort_by: str = "timestamp"
    sort_dir: str = "desc"
    visible_columns: tuple[str, ...] = ()
    #: Raw tokens as received from the query string. Kept so an unparseable value
    #: can be *rejected* instead of silently coerced to a default: ``interval=17m``
    #: previously became 5 minutes and returned HTTP 200 with the wrong window.
    raw_interval: str | None = None
    raw_strike_count: str | None = None

    # ------------------------------------------------------------------ utils

    def errors(self) -> list[str]:
        """Return every validation error for this filter state."""
        problems: list[str] = []
        if not normalize_underlying(self.underlying):
            problems.append("underlying is required")
        ct = (self.contract_type or "").strip().upper()
        if ct not in {"INDEX", "STOCK", "AUTO"}:
            problems.append("contract_type must be INDEX, STOCK or AUTO")
        if self.raw_strike_count is not None:
            # Validate the caller's literal token: an unparseable value used to be
            # coerced to the default and silently changed the selected scope.
            _count, strike_problem = parse_strike_count_token(self.raw_strike_count)
            if strike_problem:
                problems.append(strike_problem)
        elif self.strike_count is not None and self.strike_count not in STRIKE_COUNT_CHOICES:
            allowed = ", ".join(str(c) for c in STRIKE_COUNT_CHOICES)
            problems.append(f"strike_count must be one of {allowed} or 'all'")
        if self.interval_minutes not in INTERVAL_CHOICES_MINUTES:
            allowed = ", ".join(str(c) for c in INTERVAL_CHOICES_MINUTES)
            problems.append(f"interval must be one of {allowed} minutes")
        if self.raw_interval is not None and not _is_session_token(self.raw_interval):
            parsed_interval = parse_interval_token(self.raw_interval)
            if parsed_interval is None:
                allowed = ", ".join(str(c) for c in INTERVAL_CHOICES_MINUTES)
                problems.append(
                    f"interval '{self.raw_interval}' is not supported "
                    f"(use {allowed} minutes or 'session')"
                )
            elif parsed_interval != self.interval_minutes:
                problems.append(
                    f"interval '{self.raw_interval}' was coerced to "
                    f"{self.interval_minutes} minutes"
                )
        if self.window not in {
            "INTRADAY_INTERVAL",
            WINDOW_FULL_SESSION,
            WINDOW_CUSTOM,
        }:
            problems.append("window must be INTRADAY_INTERVAL, FULL_SESSION or CUSTOM")
        if (self.oi_unit or "").strip().upper() not in OI_UNIT_CHOICES:
            problems.append("oi_unit must be CONTRACTS, LOTS or QUANTITY")
        if self.min_strike is not None and self.max_strike is not None:
            if self.min_strike > self.max_strike:
                problems.append("min_strike must not exceed max_strike")
        if self.window == WINDOW_CUSTOM and not (self.start_time or self.end_time):
            problems.append("CUSTOM window requires start_time or end_time")
        if self.page_size <= 0 or self.page_size > 5000:
            problems.append("page_size must be between 1 and 5000")
        if (self.sort_dir or "").strip().lower() not in {"asc", "desc"}:
            problems.append("sort_dir must be asc or desc")
        return problems

    def is_valid(self) -> bool:
        return not self.errors()

    def normalized(self) -> DerivativesFilterSpec:
        """Return a cleaned copy: canonical symbols, sorted de-duplicated strikes."""
        return DerivativesFilterSpec(
            underlying=normalize_underlying(self.underlying),
            contract_type=(self.contract_type or "INDEX").strip().upper(),
            expiry=self.expiry,
            expiries=tuple(sorted({e for e in self.expiries if e is not None})),
            strike_count=self.strike_count,
            min_strike=self.min_strike,
            max_strike=self.max_strike,
            call_strikes=tuple(sorted(set(self.call_strikes))),
            put_strikes=tuple(sorted(set(self.put_strikes))),
            interval_minutes=self.interval_minutes,
            window=self.window,
            session_date=self.session_date,
            start_time=self.start_time,
            end_time=self.end_time,
            oi_unit=(self.oi_unit or OI_UNIT_CONTRACTS).strip().upper(),
            page_size=self.page_size,
            sort_by=(self.sort_by or "timestamp").strip().lower(),
            sort_dir=(self.sort_dir or "desc").strip().lower(),
            visible_columns=tuple(self.visible_columns),
            raw_interval=self.raw_interval,
            raw_strike_count=self.raw_strike_count,
        )

    # ------------------------------------------------------------- lifecycle

    def fingerprint(self) -> str:
        """Stable identifier for this exact scope.

        Clients echo it back so a *stale* response for a previously selected
        underlying can be discarded instead of overwriting the current view.
        """
        payload = {
            "underlying": normalize_underlying(self.underlying),
            "contract_type": (self.contract_type or "").upper(),
            "expiry": self.expiry.isoformat() if self.expiry else None,
            "expiries": [e.isoformat() for e in sorted(self.expiries)],
            "strike_count": self.strike_count,
            "min_strike": _dec_str(self.min_strike),
            "max_strike": _dec_str(self.max_strike),
            "call_strikes": [_dec_str(s) for s in sorted(self.call_strikes)],
            "put_strikes": [_dec_str(s) for s in sorted(self.put_strikes)],
            "interval_minutes": self.interval_minutes,
            "window": self.window,
            "session_date": self.session_date.isoformat() if self.session_date else None,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "oi_unit": (self.oi_unit or "").upper(),
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]

    def reset(self) -> DerivativesFilterSpec:
        """Reset Filters control: defaults only, no inherited selection."""
        return DerivativesFilterSpec(underlying=normalize_underlying(self.underlying))

    @classmethod
    def from_query(cls, params: dict[str, Any]) -> DerivativesFilterSpec:
        """Build a validated spec from raw query parameters (internal use only)."""
        expiry = _parse_date(params.get("expiry"))
        expiries = tuple(
            e for e in (_parse_date(x) for x in _as_list(params.get("expiries"))) if e
        )
        return cls(
            underlying=str(params.get("symbol") or params.get("underlying") or "NIFTY"),
            contract_type=str(params.get("contract_type") or "INDEX"),
            expiry=expiry,
            expiries=expiries,
            strike_count=_parse_strike_count(params.get("strikes") or params.get("strike_count")),
            min_strike=_parse_decimal(params.get("min_strike")),
            max_strike=_parse_decimal(params.get("max_strike")),
            call_strikes=tuple(
                s for s in (_parse_decimal(x) for x in _as_list(params.get("call_strikes"))) if s
            ),
            put_strikes=tuple(
                s for s in (_parse_decimal(x) for x in _as_list(params.get("put_strikes"))) if s
            ),
            interval_minutes=_parse_interval(params.get("interval")),
            window=str(params.get("window") or "INTRADAY_INTERVAL").upper(),
            session_date=_parse_date(params.get("session_date")),
            start_time=params.get("start_time"),
            end_time=params.get("end_time"),
            oi_unit=str(params.get("oi_unit") or OI_UNIT_CONTRACTS).upper(),
            page_size=int(params.get("page_size") or 50),
            sort_by=str(params.get("sort_by") or "timestamp"),
            sort_dir=str(params.get("sort_dir") or "desc"),
            visible_columns=tuple(_as_list(params.get("columns"))),
            raw_interval=_raw_token(params.get("interval")),
            raw_strike_count=_raw_token(params.get("strikes") or params.get("strike_count")),
        )


def _raw_token(value: Any) -> str | None:
    """Keep the caller's literal token so validation can reject, not coerce it."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _is_session_token(value: Any) -> bool:
    return str(value).strip().upper() in {WINDOW_FULL_SESSION, "SESSION", "DAY"}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [x for x in str(value).split(",") if str(x).strip()]


def _parse_decimal(value: Any) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return Decimal(str(value).strip())
    except Exception:  # noqa: BLE001
        return None


def _parse_date(value: Any) -> date | None:
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except Exception:  # noqa: BLE001
        return None


def parse_interval_token(value: Any) -> int | None:
    """Parse ``3``, ``3m``, ``3 min`` etc. into minutes. ``None`` when invalid."""
    if value is None:
        return None
    text = str(value).strip().lower().replace("minutes", "").replace("min", "").replace("m", "")
    text = text.strip()
    if not text:
        return None
    try:
        parsed = int(float(text))
    except Exception:  # noqa: BLE001
        return None
    return parsed if parsed in INTERVAL_CHOICES_MINUTES else None


def parse_evaluation_time(
    value: Any,
    *,
    session_date: date | None = None,
    default_date: date | None = None,
) -> datetime | None:
    """Parse a replay evaluation instant into an aware UTC datetime.

    Accepts a full ISO-8601 timestamp (with or without an offset) or a bare
    ``HH:MM`` / ``HH:MM:SS`` market time. A naive value is interpreted as IST
    because that is the market's clock, never as UTC: reading 11:00 IST as 11:00
    UTC would shift the point-in-time cut by 5.5 hours and leak later data into
    an earlier replay step.

    Returns ``None`` when the token cannot be understood, so the caller can
    reject the request instead of silently replaying at the wrong instant.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        candidate = value
    else:
        text = str(value).strip()
        if not text:
            return None
        candidate = None
        try:
            candidate = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            candidate = None
        if candidate is None:
            base_day = session_date or default_date
            for fmt in ("%H:%M:%S", "%H:%M", "%H%M"):
                try:
                    parsed_time = datetime.strptime(text, fmt).time()
                except ValueError:
                    continue
                if base_day is None:
                    return None
                return datetime.combine(base_day, parsed_time, tzinfo=IST).astimezone(UTC)
        if candidate is None:
            return None
    if candidate.tzinfo is None:
        return candidate.replace(tzinfo=IST).astimezone(UTC)
    return candidate.astimezone(UTC)


def _parse_interval(value: Any) -> int:
    if _is_session_token(value):
        return INTERVAL_CHOICES_MINUTES[-1]
    return parse_interval_token(value) or 5


def parse_strike_count_token(value: Any) -> tuple[int | None, str | None]:
    """Parse a Strike Count token into ``(count, problem)``.

    ``count`` is ``None`` for the ``all`` selection. ``problem`` is non-``None``
    when the token is not a supported value, so callers can reject it instead of
    substituting a default the user did not ask for.
    """
    if value is None or str(value).strip() == "":
        return 5, None
    text = str(value).strip().lower()
    if text in {"all", "none", "0"}:
        return None, None
    try:
        parsed = int(text)
    except Exception:  # noqa: BLE001
        allowed = ", ".join(str(c) for c in STRIKE_COUNT_CHOICES)
        return 5, f"strike_count '{value}' is not a number (use {allowed} or 'all')"
    if parsed <= 0:
        return None, None
    if parsed not in STRIKE_COUNT_CHOICES:
        allowed = ", ".join(str(c) for c in STRIKE_COUNT_CHOICES)
        return parsed, f"strike_count must be one of {allowed} or 'all'"
    return parsed, None


def _parse_strike_count(value: Any) -> int | None:
    return parse_strike_count_token(value)[0]


# ===========================================================================
# 7. Provider contract master client (Phase 10 grounding)
# ===========================================================================

@dataclass
class ListedExpiry:
    """One provider-listed expiry option."""

    expiry_date: date
    is_weekly: bool
    contract_count: int
    display_label: str
    source: str = PROV_PROVIDER_LISTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "expiry_date": self.expiry_date.isoformat(),
            "is_weekly": self.is_weekly,
            "expiry_type": "WEEKLY" if self.is_weekly else "MONTHLY",
            "contract_count": self.contract_count,
            "display_label": self.display_label,
            "source": self.source,
        }


@dataclass
class OptionContractRow:
    """One provider-listed option contract row (authoritative)."""

    instrument_key: str
    trading_symbol: str
    expiry_date: date
    strike_price: Decimal
    option_type: str
    lot_size: int | None
    tick_size: Decimal | None
    is_weekly: bool


@dataclass
class FutureContractRow:
    """One provider-listed futures contract row (authoritative)."""

    instrument_key: str
    trading_symbol: str
    expiry_date: date
    lot_size: int | None
    name: str


@dataclass
class ContractMasterResult:
    """Outcome of a contract-master read, provenance included."""

    rows: list[Any] = field(default_factory=list)
    source: str = PROV_UNAVAILABLE
    reason: str | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_provider_listed(self) -> bool:
        return self.source == PROV_PROVIDER_LISTED

    @property
    def is_available(self) -> bool:
        return bool(self.rows)


class DerivativesContractMasterClient:
    """Read-only client for the authorized provider contract master.

    Two authoritative sources are used, and nothing else:

    * ``/option/contract`` — option contracts, expiries, strikes, lot/tick size,
      and the provider's own ``weekly`` flag.
    * the provider's published instrument master (a public static asset) — futures
      instrument keys, expiries and lot sizes, which ``/option/contract`` does not
      carry (verified: zero futures rows in that endpoint's payload).

    A calendar-derived expiry schedule is available as an explicitly-labelled
    fallback and is **never** returned as ``PROVIDER_LISTED_CONTRACT_MASTER``.
    """

    def __init__(
        self,
        *,
        token: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        ttl_seconds: int | None = None,
        cache_dir: Path | None = None,
        disk_cache: bool = True,
    ) -> None:
        self._token = (token if token is not None else settings.upstox_analytics_token) or ""
        self._base_url = base_url or settings.upstox_base_url
        self._timeout = timeout or float(
            getattr(settings, "derivatives_instrument_master_timeout_seconds", 60)
        )
        self._ttl = ttl_seconds or int(
            getattr(settings, "derivatives_contract_master_ttl_seconds", 900)
        )
        self._cache_dir = cache_dir or Path(
            getattr(settings, "derivatives_contract_master_cache_dir", "data/reference/derivatives_contract_master")
        )
        self._disk_cache = disk_cache
        self._option_cache: dict[str, tuple[float, ContractMasterResult]] = {}
        self._future_cache: dict[str, tuple[float, ContractMasterResult]] = {}

    # ---------------------------------------------------------------- helpers

    def _headers(self) -> dict[str, str]:
        return {"Accept": "application/json", "Authorization": f"Bearer {self._token}"}

    def _cache_valid(self, entry: tuple[float, ContractMasterResult] | None) -> bool:
        return entry is not None and (time.monotonic() - entry[0]) < self._ttl

    def clear_cache(self) -> None:
        self._option_cache.clear()
        self._future_cache.clear()

    # ---------------------------------------------------------------- options

    async def get_option_contracts(self, underlying: str) -> ContractMasterResult:
        """Fetch every provider-listed option contract for one underlying."""
        symbol = normalize_underlying(underlying)
        spec = UNDERLYING_CATALOG.get(symbol)
        if spec is None or not spec.index_instrument_key:
            return ContractMasterResult(
                rows=[],
                source=PROV_UNAVAILABLE,
                reason=REASON_UNSUPPORTED_UNDERLYING,
            )

        cached = self._option_cache.get(symbol)
        if self._cache_valid(cached):
            assert cached is not None
            return cached[1]

        if not self._token.strip():
            return ContractMasterResult(
                rows=[], source=PROV_UNAVAILABLE, reason="PROVIDER_TOKEN_NOT_CONFIGURED"
            )

        try:
            import httpx

            url = f"{self._base_url}/option/contract"
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    url,
                    params={"instrument_key": spec.index_instrument_key},
                    headers=self._headers(),
                )
            if resp.status_code != 200:
                logger.warning(
                    "Contract master HTTP %s for %s", resp.status_code, symbol
                )
                return ContractMasterResult(
                    rows=[],
                    source=PROV_UNAVAILABLE,
                    reason=f"PROVIDER_HTTP_{resp.status_code}",
                )
            payload = resp.json().get("data", []) or []
        except Exception as exc:  # noqa: BLE001 - transport failure is not fatal
            logger.warning("Contract master read failed for %s: %s", symbol, type(exc).__name__)
            return ContractMasterResult(
                rows=[], source=PROV_UNAVAILABLE, reason=f"PROVIDER_READ_FAILED:{type(exc).__name__}"
            )

        rows: list[OptionContractRow] = []
        for raw in payload:
            parsed = _parse_option_contract_row(raw)
            if parsed is not None:
                rows.append(parsed)

        result = ContractMasterResult(rows=rows, source=PROV_PROVIDER_LISTED)
        self._option_cache[symbol] = (time.monotonic(), result)
        return result

    async def get_listed_expiries(self, underlying: str) -> ContractMasterResult:
        """Provider-listed, non-expired expiries with the provider's weekly flag."""
        base = await self.get_option_contracts(underlying)
        if not base.is_available:
            return base

        today = datetime.now(UTC).date()
        grouped: dict[date, list[OptionContractRow]] = {}
        for row in base.rows:
            if row.expiry_date >= today:
                grouped.setdefault(row.expiry_date, []).append(row)

        listings: list[ListedExpiry] = []
        for expiry_date in sorted(grouped):
            group = grouped[expiry_date]
            is_weekly = any(r.is_weekly for r in group)
            listings.append(
                ListedExpiry(
                    expiry_date=expiry_date,
                    is_weekly=is_weekly,
                    contract_count=len(group),
                    display_label=(
                        f"{expiry_date.strftime('%d %b %Y')} "
                        f"({'Weekly' if is_weekly else 'Monthly'})"
                    ),
                )
            )
        return ContractMasterResult(
            rows=listings, source=PROV_PROVIDER_LISTED, fetched_at=base.fetched_at
        )

    async def get_strike_grid(
        self, underlying: str, expiry_date: date, option_type: str | None = None
    ) -> ListedStrikeGrid:
        """Real listed strike grid for one underlying + expiry."""
        base = await self.get_option_contracts(underlying)
        if not base.is_available:
            return ListedStrikeGrid(
                strikes=(), step=None, is_uniform=False,
                source=base.source, reason=base.reason or REASON_NO_LISTED_STRIKES,
            )
        wanted = (option_type or "").strip().upper() or None
        strikes = [
            row.strike_price
            for row in base.rows
            if row.expiry_date == expiry_date and (wanted is None or row.option_type == wanted)
        ]
        if not strikes:
            return ListedStrikeGrid(
                strikes=(), step=None, is_uniform=False,
                source=PROV_PROVIDER_LISTED, reason=REASON_EXPIRY_NOT_LISTED,
            )
        return infer_strike_grid(strikes, source=PROV_PROVIDER_LISTED)

    async def resolve_lot_size(self, underlying: str, expiry_date: date) -> LotSizeResolution:
        """Verified lot/tick for one underlying + expiry, or an explicit refusal."""
        base = await self.get_option_contracts(underlying)
        if not base.is_available:
            return LotSizeResolution(
                lot_size=None, tick_size=None, source=base.source,
                reason=base.reason or REASON_NO_LOT_SIZE,
            )
        rows = [
            {"lot_size": r.lot_size, "tick_size": r.tick_size}
            for r in base.rows
            if r.expiry_date == expiry_date
        ]
        if not rows:
            return LotSizeResolution(
                lot_size=None, tick_size=None, source=PROV_PROVIDER_LISTED,
                reason=REASON_EXPIRY_NOT_LISTED,
            )
        return resolve_lot_size(rows, source=PROV_PROVIDER_LISTED)

    # ---------------------------------------------------------------- futures

    async def get_futures_contracts(self, underlying: str) -> ContractMasterResult:
        """Provider-listed futures contracts for one underlying.

        The provider's option-contract endpoint carries no futures rows (verified),
        so the published instrument master is the authoritative source. The parsed
        futures subset is cached on disk because the raw asset is ~1.9 MB.
        """
        symbol = normalize_underlying(underlying)
        spec = UNDERLYING_CATALOG.get(symbol)
        if spec is None or not spec.instrument_master_exchange:
            return ContractMasterResult(
                rows=[], source=PROV_UNAVAILABLE, reason=REASON_UNSUPPORTED_UNDERLYING
            )

        cached = self._future_cache.get(symbol)
        if self._cache_valid(cached):
            assert cached is not None
            return cached[1]

        result = await self._read_futures(spec)
        self._future_cache[symbol] = (time.monotonic(), result)
        return result

    async def _read_futures(self, spec: UnderlyingDefinition) -> ContractMasterResult:
        import gzip

        raw_rows = self._read_futures_disk_cache(spec.instrument_master_exchange)
        if raw_rows is None:
            if not self._token.strip():
                return ContractMasterResult(
                    rows=[], source=PROV_UNAVAILABLE, reason="PROVIDER_TOKEN_NOT_CONFIGURED"
                )
            template = getattr(
                settings,
                "derivatives_instrument_master_url",
                "https://assets.upstox.com/market-quote/instruments/exchange/{exchange}.json.gz",
            )
            url = template.format(exchange=spec.instrument_master_exchange)
            try:
                import httpx

                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.get(url)
                if resp.status_code != 200:
                    return ContractMasterResult(
                        rows=[],
                        source=PROV_UNAVAILABLE,
                        reason=f"INSTRUMENT_MASTER_HTTP_{resp.status_code}",
                    )
                content = resp.content
            except Exception as exc:  # noqa: BLE001
                return ContractMasterResult(
                    rows=[],
                    source=PROV_UNAVAILABLE,
                    reason=f"INSTRUMENT_MASTER_READ_FAILED:{type(exc).__name__}",
                )
            try:
                if content[:2] == b"\x1f\x8b":
                    content = gzip.decompress(content)
                raw_rows = json.loads(content.decode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                return ContractMasterResult(
                    rows=[],
                    source=PROV_UNAVAILABLE,
                    reason=f"INSTRUMENT_MASTER_PARSE_FAILED:{type(exc).__name__}",
                )
            self._write_futures_disk_cache(spec.instrument_master_exchange, raw_rows)

        today = datetime.now(UTC).date()
        rows: list[FutureContractRow] = []
        for raw in raw_rows:
            if str(raw.get("instrument_type", "")).upper() != "FUT":
                continue
            name = str(raw.get("name") or "").strip().upper()
            if name != spec.symbol:
                continue
            expiry = _parse_epoch_or_date(raw.get("expiry"))
            if expiry is None or expiry < today:
                continue
            rows.append(
                FutureContractRow(
                    instrument_key=str(raw.get("instrument_key") or ""),
                    trading_symbol=str(raw.get("trading_symbol") or ""),
                    expiry_date=expiry,
                    lot_size=int(raw["lot_size"]) if raw.get("lot_size") else None,
                    name=name,
                )
            )
        rows.sort(key=lambda r: r.expiry_date)
        return ContractMasterResult(rows=rows, source=PROV_PROVIDER_LISTED)

    def _disk_cache_path(self, exchange: str) -> Path:
        return Path(self._cache_dir) / f"{exchange.lower()}_futures.json"

    def _read_futures_disk_cache(self, exchange: str) -> list[dict[str, Any]] | None:
        if not self._disk_cache:
            return None
        path = self._disk_cache_path(exchange)
        try:
            if not path.is_file():
                return None
            if (time.time() - path.stat().st_mtime) > self._ttl * 4:
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else None
        except Exception:  # noqa: BLE001 - a bad cache must never break a read
            return None

    def _write_futures_disk_cache(self, exchange: str, rows: list[dict[str, Any]]) -> None:
        if not self._disk_cache:
            return
        futures = [r for r in rows if str(r.get("instrument_type", "")).upper() == "FUT"]
        try:
            path = self._disk_cache_path(exchange)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(futures), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - cache is best-effort
            logger.debug("Could not write derivatives futures cache: %s", type(exc).__name__)


def _parse_option_contract_row(raw: dict[str, Any]) -> OptionContractRow | None:
    """Parse one provider option-contract row; ``None`` when unusable."""
    expiry = _parse_epoch_or_date(raw.get("expiry"))
    if expiry is None:
        return None
    try:
        strike = Decimal(str(raw.get("strike_price")))
    except Exception:  # noqa: BLE001
        return None
    option_type = str(raw.get("instrument_type") or "").strip().upper()
    if option_type not in {"CE", "PE"}:
        return None
    return OptionContractRow(
        instrument_key=str(raw.get("instrument_key") or ""),
        trading_symbol=str(raw.get("trading_symbol") or ""),
        expiry_date=expiry,
        strike_price=strike,
        option_type=option_type,
        lot_size=int(raw["lot_size"]) if raw.get("lot_size") else None,
        tick_size=Decimal(str(raw["tick_size"])) if raw.get("tick_size") else None,
        is_weekly=bool(raw.get("weekly")),
    )


def _parse_epoch_or_date(value: Any) -> date | None:
    """Parse either an ISO date or an epoch-millisecond timestamp."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    if text.isdigit():
        try:
            millis = int(text)
        except Exception:  # noqa: BLE001
            return None
        # Heuristic: 13-digit values are milliseconds, 10-digit are seconds.
        seconds = millis / 1000.0 if millis > 10_000_000_000 else float(millis)
        try:
            return datetime.fromtimestamp(seconds, tz=UTC).date()
        except Exception:  # noqa: BLE001
            return None
    try:
        return date.fromisoformat(text[:10])
    except Exception:  # noqa: BLE001
        return None


def calendar_derived_expiries(ref_date: date | None = None) -> list[date]:
    """Calendar-derived scenario dates, clearly labelled as a fallback.

    Retained only so an offline evaluation path exists. Every consumer must
    propagate ``PROV_CALENDAR_FALLBACK``: these are **not** listed contracts.
    """
    return _calendar_schedule(ref_date)


def _calendar_schedule(ref_date: date | None) -> list[date]:
    import calendar as _calendar
    from datetime import timedelta as _timedelta

    today = ref_date or datetime.now(UTC).date()

    def last_weekday(year: int, month: int, weekday: int = 3) -> date:
        last_day = _calendar.monthrange(year, month)[1]
        candidate = date(year, month, last_day)
        return candidate - _timedelta(days=(candidate.weekday() - weekday) % 7)

    days_ahead = (3 - today.weekday()) % 7
    anchor = today + _timedelta(days=days_ahead)
    weeklies = [anchor + _timedelta(days=7 * w) for w in range(4)]

    monthlies: list[date] = []
    for offset in range(4):
        month_index = (today.month - 1 + offset) % 12 + 1
        year = today.year + ((today.month - 1 + offset) // 12)
        candidate = last_weekday(year, month_index)
        if candidate >= today:
            monthlies.append(candidate)

    return sorted({d for d in (weeklies + monthlies) if d >= today})
