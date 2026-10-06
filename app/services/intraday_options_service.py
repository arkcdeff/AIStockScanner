"""Live Intraday Options Service — Milestone G.11-OPT-1 Phase 1.

Provides a unified, provider-neutral domain service for the Live Intraday Options
Workbench, powering:
  1. Live Option Chain strike ladder (Calls, ATM, Puts, ITM/OTM, OI, ΔOI, Volume)
  2. Option Dashboard (Summary metrics, ATM straddle premium, Total OI, PCR)
  3. Live PCR (Put/Call OI ratio, volume ratio, sentiment bias context)
  4. Call vs Put OI (Strike-wise OI and OI-change distribution)

Strict Architectural Rules:
  - 100% read-only market data analysis; zero buy/sell signals, orders, or execution.
  - Dynamic index expiry and contract resolution (no hardcoded calendars or dates).
  - Explicit provider status, timestamps, and freshness tracking.
  - Zero substitution of missing quotes with false zeros.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.core.config import settings
from app.providers.models import NormalizedQuote
from app.services.black_scholes import (
    DEFAULT_RISK_FREE_RATE,
    calculate_greeks,
    implied_volatility,
)
from app.services.derivatives_observation_service import (
    ObservationSeries,
    OptionObservationQuery,
    OptionObservationRepository,
    compute_interval_deltas,
    format_ist,
    ist_trade_date,
    quality_status_for_series,
    summarise_deltas,
)
from app.services.derivatives_scope import (
    ANALYSIS_WINDOW_CHOICES_MINUTES,
    BASELINE_INTRADAY_INTERVAL,
    BASELINE_PREVIOUS_CLOSE,
    DEFAULT_TIE_BREAK,
    INTERVAL_CHOICES_MINUTES,
    PROV_CALENDAR_FALLBACK,
    PROV_MODEL_CALCULATED,
    PROV_PERSISTED_OBSERVATION,
    PROV_PROVIDER_LISTED,
    PROV_UNAVAILABLE,
    REASON_INSUFFICIENT_OBSERVATIONS,
    DerivativesFilterSpec,
    ListedStrikeGrid,
    describe_strike_selection,
    infer_strike_grid,
    resolve_atm,
    select_atm_window,
)
from app.services.option_chain_adapter import (
    MockOptionChainAdapter,
    OptionChainSourceAdapter,
    UpstoxOptionChainAdapter,
)
from app.services.option_chain_config import (
    normalize_index_symbol,
)

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30), name="IST")


@dataclass(frozen=True)
class UnderlyingSpec:
    """Fallback identity for one underlying.

    ``strike_step`` and ``lot_size`` are **fallbacks only**. Verified values are
    read from the authorized provider contract master
    (:mod:`app.services.derivatives_scope`) and reported separately, because the
    hardcoded lot sizes previously held here were wrong for every index (they
    claimed NIFTY 25 / BANKNIFTY 15 while the exchange lists 65 / 30) and no ATM
    may be derived from an assumed grid when the real listed grid is available.
    """

    symbol: str
    name: str
    exchange: str
    segment: str
    fallback_strike_step: int
    fallback_lot_size: int
    index_instrument_key: str | None = None
    is_active: bool = True

    @property
    def strike_step(self) -> int:
        """Backwards-compatible alias for the fallback strike step."""
        return self.fallback_strike_step

    @property
    def lot_size(self) -> int:
        """Backwards-compatible alias for the fallback lot size."""
        return self.fallback_lot_size


SUPPORTED_UNDERLYINGS: dict[str, UnderlyingSpec] = {
    "NIFTY": UnderlyingSpec(
        symbol="NIFTY",
        name="NIFTY 50",
        exchange="NSE",
        segment="NFO",
        fallback_strike_step=50,
        fallback_lot_size=65,
        index_instrument_key="NSE_INDEX|Nifty 50",
    ),
    "BANKNIFTY": UnderlyingSpec(
        symbol="BANKNIFTY",
        name="NIFTY BANK",
        exchange="NSE",
        segment="NFO",
        fallback_strike_step=100,
        fallback_lot_size=30,
        index_instrument_key="NSE_INDEX|Nifty Bank",
    ),
    "FINNIFTY": UnderlyingSpec(
        symbol="FINNIFTY",
        name="NIFTY FINANCIAL SERVICES",
        exchange="NSE",
        segment="NFO",
        fallback_strike_step=50,
        fallback_lot_size=60,
        index_instrument_key="NSE_INDEX|Nifty Fin Service",
    ),
    "MIDCPNIFTY": UnderlyingSpec(
        symbol="MIDCPNIFTY",
        name="NIFTY MIDCAP SELECT",
        exchange="NSE",
        segment="NFO",
        fallback_strike_step=25,
        fallback_lot_size=120,
        index_instrument_key="NSE_INDEX|NIFTY MID SELECT",
    ),
    "SENSEX": UnderlyingSpec(
        symbol="SENSEX",
        name="S&P BSE SENSEX",
        exchange="BSE",
        segment="BFO",
        fallback_strike_step=100,
        fallback_lot_size=20,
        index_instrument_key="BSE_INDEX|SENSEX",
    ),
}


def get_ist_now() -> datetime:
    """Return current timestamp in Indian Standard Time (IST)."""
    return datetime.now(UTC).astimezone(IST)


def is_market_hours(dt: datetime | None = None) -> bool:
    """Determine whether the specified timestamp (or now) is within regular NSE trading hours.

    Regular hours: Monday-Friday, 09:15 to 15:30 IST (holidays handled via data).
    """
    check_dt = (dt or datetime.now(UTC)).astimezone(IST)
    if check_dt.weekday() >= 5:  # Saturday or Sunday
        return False
    market_open = check_dt.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = check_dt.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_open <= check_dt <= market_close


def get_market_session_status(dt: datetime | None = None) -> str:
    """Classify current trading session status."""
    check_dt = (dt or datetime.now(UTC)).astimezone(IST)
    if check_dt.weekday() >= 5:
        return "MARKET_CLOSED_WEEKEND"
    pre_market = check_dt.replace(hour=9, minute=0, second=0, microsecond=0)
    market_open = check_dt.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = check_dt.replace(hour=15, minute=30, second=0, microsecond=0)

    if check_dt < pre_market:
        return "PRE_MARKET_STANDBY"
    elif pre_market <= check_dt < market_open:
        return "PRE_MARKET_OPEN"
    elif market_open <= check_dt <= market_close:
        return "MARKET_OPEN"
    else:
        return "MARKET_CLOSED"


def classify_buildup(price_change: float | None, oi_change: int | None) -> dict[str, str]:
    """Classify option contract buildup from price change and OI change.

    A. LONG BUILDUP: Premium ↑ and OI ↑ (Bullish positioning / aggressive buying)
    B. SHORT BUILDUP: Premium ↓ and OI ↑ (Bearish positioning / aggressive writing)
    C. SHORT COVERING: Premium ↑ and OI ↓ (Shorts closing / covering positions)
    D. LONG UNWINDING: Premium ↓ and OI ↓ (Longs booking profits / exiting)
    """
    if price_change is None or oi_change is None or price_change == 0 or oi_change == 0:
        return {
            "classification": "NEUTRAL",
            "label": "Neutral / Inconclusive",
            "code": "—",
            "color_class": "secondary",
            "tooltip": "Price or OI change is zero, unchanged, or insufficient data",
        }

    if price_change > 0 and oi_change > 0:
        return {
            "classification": "LONG_BUILDUP",
            "label": "Long Buildup",
            "code": "LB",
            "color_class": "success",
            "tooltip": f"Long Buildup: Premium rose by ₹{price_change:+.2f} while OI expanded by {oi_change:+,d} contracts",
        }
    elif price_change < 0 and oi_change > 0:
        return {
            "classification": "SHORT_BUILDUP",
            "label": "Short Buildup",
            "code": "SB",
            "color_class": "danger",
            "tooltip": f"Short Buildup: Premium fell by ₹{price_change:+.2f} while OI expanded by {oi_change:+,d} contracts",
        }
    elif price_change > 0 and oi_change < 0:
        return {
            "classification": "SHORT_COVERING",
            "label": "Short Covering",
            "code": "SC",
            "color_class": "info",
            "tooltip": f"Short Covering: Premium rose by ₹{price_change:+.2f} while OI contracted by {oi_change:+,d} contracts",
        }
    elif price_change < 0 and oi_change < 0:
        return {
            "classification": "LONG_UNWINDING",
            "label": "Long Unwinding",
            "code": "LU",
            "color_class": "warning",
            "tooltip": f"Long Unwinding: Premium fell by ₹{price_change:+.2f} while OI contracted by {oi_change:+,d} contracts",
        }

    return {
        "classification": "NEUTRAL",
        "label": "Neutral / Inconclusive",
        "code": "—",
        "color_class": "secondary",
        "tooltip": "Inconclusive observation correlation",
    }


def resolve_contract_greeks(
    spot: float,
    strike: float,
    time_to_expiry_years: float,
    option_type: str,
    ltp: float | None,
    source_greeks: dict[str, Any] | None = None,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
) -> dict[str, Any]:
    """Resolve or calculate Black-Scholes Option Greeks with clear provenance."""
    # 1. Check if provider already supplied valid Greeks
    if source_greeks and any(source_greeks.get(k) is not None for k in ("iv", "delta", "gamma", "vega")):
        iv_s = float(source_greeks["iv"]) if source_greeks.get("iv") is not None else None
        return {
            "iv": round(iv_s * 100, 2) if iv_s is not None and iv_s < 5.0 else iv_s,
            "iv_raw": iv_s,
            "delta": float(source_greeks["delta"]) if source_greeks.get("delta") is not None else None,
            "gamma": float(source_greeks["gamma"]) if source_greeks.get("gamma") is not None else None,
            "vega": float(source_greeks["vega"]) if source_greeks.get("vega") is not None else None,
            "theta": float(source_greeks["theta"]) if source_greeks.get("theta") is not None else None,
            "provenance": source_greeks.get("origin", "PROVIDER-SUPPLIED"),
            "status": "VALID",
        }

    # 2. Input validation for model calculation
    if spot <= 0 or strike <= 0 or ltp is None or ltp <= 0:
        return {
            "iv": None,
            "delta": None,
            "gamma": None,
            "vega": None,
            "theta": None,
            "provenance": "UNAVAILABLE",
            "status": "MISSING_INPUTS",
        }

    # Near zero DTE edge case
    if time_to_expiry_years <= 1e-5:
        opt_type = option_type.upper()
        if opt_type == "CE":
            d = 1.0 if spot > strike else (0.5 if spot == strike else 0.0)
        else:
            d = -1.0 if spot < strike else (-0.5 if spot == strike else 0.0)
        return {
            "iv": None,
            "delta": d,
            "gamma": 0.0,
            "vega": 0.0,
            "theta": 0.0,
            "provenance": "MODEL-CALCULATED",
            "status": "ZERO_DTE",
        }

    # 3. Model calculation using existing black_scholes engine
    try:
        iv_res = implied_volatility(
            market_price=ltp,
            spot=spot,
            strike=strike,
            time_to_expiry=time_to_expiry_years,
            option_type=option_type,
            risk_free_rate=risk_free_rate,
        )
        if iv_res.solver_status == "CONVERGED" and iv_res.iv is not None:
            iv_val = float(iv_res.iv)
            greeks_res = calculate_greeks(
                spot=spot,
                strike=strike,
                time_to_expiry=time_to_expiry_years,
                volatility=iv_val,
                option_type=option_type,
                risk_free_rate=risk_free_rate,
            )
            return {
                "iv": round(iv_val * 100, 2),  # Display as % e.g. 15.5%
                "iv_raw": iv_val,
                "delta": greeks_res.delta,
                "gamma": greeks_res.gamma,
                "vega": greeks_res.vega_per_pct,
                "theta": greeks_res.theta_per_day,
                "provenance": "MODEL-CALCULATED",
                "status": "CONVERGED",
            }
        else:
            return {
                "iv": None,
                "delta": None,
                "gamma": None,
                "vega": None,
                "theta": None,
                "provenance": "MODEL-CALCULATED",
                "status": iv_res.solver_status or "SOLVER_FAILED",
            }
    except Exception as exc:
        logger.debug(f"Greeks calculation error for {option_type} {strike}: {exc}")
        return {
            "iv": None,
            "delta": None,
            "gamma": None,
            "vega": None,
            "theta": None,
            "provenance": "UNAVAILABLE",
            "status": "ERROR",
        }


def _parse_interval_minutes(interval: str | None) -> int | None:
    """Parse an interval token to minutes. ``None`` means full session."""
    from app.services.derivatives_scope import parse_interval_token

    if interval is None:
        return None
    cleaned = str(interval).strip().lower()
    if cleaned in {"session", "day", "full_session", "full-session"}:
        return None
    return parse_interval_token(cleaned)


def _measured_interval_lookup(
    series: list[ObservationSeries],
    interval_minutes: int,
    *,
    session_date: date | None = None,
) -> dict[tuple[Decimal, str], dict[str, Any]]:
    """Latest real interval measurement per (strike, option type).

    Only genuine observation pairs inside tolerance are returned. Nothing is
    extrapolated to fill a missing measurement.

    ``session_date`` (IST-derived) additionally drops any pair whose endpoints do
    not belong to that trading session. The interval read is already session-bound
    at the query; this is the selection-layer guard that keeps a previous-session
    observation from ever becoming an interval baseline, even if a future caller
    forgets to bound the read.
    """
    out: dict[tuple[Decimal, str], dict[str, Any]] = {}
    for item in series:
        first = item.first()
        if first is None or first.strike is None:
            continue
        deltas = [
            d
            for d in compute_interval_deltas(item, interval_minutes)
            if d.is_available
        ]
        if session_date is not None:
            deltas = [
                d
                for d in deltas
                if d.start_at is not None
                and d.end_at is not None
                and ist_trade_date(d.start_at) == session_date
                and ist_trade_date(d.end_at) == session_date
            ]
        if not deltas:
            continue
        latest = deltas[-1]
        out[(first.strike, (first.option_type or "").upper())] = {
            "oi_change": latest.oi_change,
            "price_change": (
                float(latest.ltp_change) if latest.ltp_change is not None else None
            ),
            "start_ist": format_ist(latest.start_at),
            "end_ist": format_ist(latest.end_at),
            "effective_seconds": latest.effective_seconds,
            "baseline_provenance": latest.baseline_provenance,
        }
    return out


async def _list_real_snapshot_instants(
    session: Any, underlying_index_id: int, session_date: date
) -> list[dict[str, Any]]:
    """Real persisted snapshot instants for one underlying and session.

    ``option_chain_snapshots`` stores no ``trade_date`` column, so the session date
    is derived from the market timestamp in IST rather than assumed to equal the
    requested date.
    """
    from sqlalchemy import select

    from app.models.option_chain import OptionChainSnapshot
    from app.services.derivatives_observation_service import ist_trade_date

    stmt = (
        select(
            OptionChainSnapshot.market_timestamp,
            OptionChainSnapshot.available_at,
            OptionChainSnapshot.spot_price,
            OptionChainSnapshot.source_name,
            OptionChainSnapshot.revision,
        )
        .where(OptionChainSnapshot.underlying_index_id == underlying_index_id)
        .order_by(OptionChainSnapshot.market_timestamp)
    )
    rows = (await session.execute(stmt)).all()
    best: dict[datetime, tuple[Any, ...]] = {}
    for row in rows:
        if ist_trade_date(row[0]) != session_date:
            continue
        current = best.get(row[0])
        if current is None or row[4] >= current[4]:
            best[row[0]] = row
    return [
        {
            "iso_timestamp": row[0].astimezone(UTC).isoformat(),
            "timestamp_ist": format_ist(row[0]),
            "available_at_utc": row[1].astimezone(UTC).isoformat(),
            "spot_price": float(row[2]) if row[2] is not None else None,
            "source": row[3],
            "revision": row[4],
        }
        for row in sorted(best.values(), key=lambda r: r[0])
    ]


def _replay_grid(session_date: date, interval_minutes: int) -> list[datetime]:
    """Expected replay boundaries for a regular session, as UTC instants."""
    start = datetime.combine(session_date, time(9, 15), tzinfo=IST)
    end = datetime.combine(session_date, time(15, 30), tzinfo=IST)
    out: list[datetime] = []
    current = start
    while current <= end:
        out.append(current.astimezone(UTC))
        current += timedelta(minutes=interval_minutes)
    return out


def _merge_replay_steps(
    expected: list[datetime], real: list[dict[str, Any]]
) -> list[tuple[str, str, bool, float | None, str | None]]:
    """Replay steps: real observation instants, with unobserved expected boundaries."""
    real_map = {row["iso_timestamp"]: row for row in real}
    steps: list[tuple[str, str, bool, float | None, str | None]] = []
    for boundary in expected:
        iso = boundary.astimezone(UTC).isoformat()
        row = real_map.get(iso)
        steps.append(
            (
                format_ist(boundary),
                iso,
                row is not None,
                row["spot_price"] if row else None,
                row.get("available_at_utc") if row else None,
            )
        )
    observed_iso = set(real_map)
    expected_iso = {s[1] for s in steps}
    for extra in sorted(observed_iso - expected_iso):
        row = real_map[extra]
        steps.append(
            (
                row["timestamp_ist"],
                extra,
                True,
                row["spot_price"],
                row.get("available_at_utc"),
            )
        )
    steps.sort(key=lambda item: item[1])
    return steps


def _aggregate_by_timestamp(
    series: list[ObservationSeries],
) -> dict[str, dict[str, Any]]:
    """Aggregate real observations into per-timestamp call/put OI and spot totals."""
    agg: dict[str, dict[str, Any]] = {}
    for item in series:
        for obs in item.observations:
            key = format_ist(obs.observed_at)
            bucket = agg.setdefault(
                key,
                {"call_oi": None, "put_oi": None, "spot": None, "expiries": set()},
            )
            oi = obs.open_interest
            if oi is not None:
                if (obs.option_type or "").upper() == "CE":
                    bucket["call_oi"] = (bucket["call_oi"] or 0) + oi
                elif (obs.option_type or "").upper() == "PE":
                    bucket["put_oi"] = (bucket["put_oi"] or 0) + oi
            if obs.expiry is not None:
                bucket["expiries"].add(obs.expiry.isoformat())
    for bucket in agg.values():
        bucket["expiry_labels"] = sorted(bucket.pop("expiries"))
    return agg


def _series_for_strike(
    series: list[ObservationSeries], strike: Decimal, option_type: str
) -> ObservationSeries:
    """Locate the real observation series for one strike + option type."""
    for candidate in series:
        first = candidate.first()
        if first is None:
            continue
        if first.strike == strike and (first.option_type or "").upper() == option_type:
            return candidate
    return ObservationSeries(
        contract_key=f"{strike}:{option_type}",
        label=f"{strike:,.0f} {option_type}",
        source=PROV_UNAVAILABLE,
        reason=REASON_INSUFFICIENT_OBSERVATIONS,
    )


def _series_payload(series: ObservationSeries, interval_minutes: int) -> dict[str, Any]:
    """Chart + table payload for one contract, computed from real observations."""
    if not series.is_available:
        return {
            "available": False,
            "reason": series.reason or REASON_INSUFFICIENT_OBSERVATIONS,
            "quality_status": quality_status_for_series(series),
            "observation_count": 0,
            "timestamps": [],
            "oi": [],
            "ltp": [],
            "volume": [],
            "oi_change_series": [],
            "cumulative_session_oi_change": None,
            "source": PROV_UNAVAILABLE,
            "coverage_message": series.coverage()["coverage_message"],
            "deltas": [],
        }
    ordered = series.observations
    deltas = compute_interval_deltas(series, interval_minutes)
    first_oi = ordered[0].open_interest
    last_oi = ordered[-1].open_interest
    cumulative = (
        last_oi - first_oi if first_oi is not None and last_oi is not None else None
    )
    indexed = {d.end_at: d for d in deltas}
    oi_change_series = []
    for obs in ordered:
        delta = indexed.get(obs.observed_at)
        oi_change_series.append(delta.oi_change if delta and delta.is_available else None)
    coverage = series.coverage()
    return {
        "available": True,
        "reason": None,
        "quality_status": coverage.get("quality_status"),
        "observation_age_seconds": coverage.get("observation_age_seconds"),
        "observation_count": len(ordered),
        "timestamps": [format_ist(o.observed_at) for o in ordered],
        "timestamps_utc": [o.observed_at.astimezone(UTC).isoformat() for o in ordered],
        "oi": [o.open_interest for o in ordered],
        "ltp": [float(o.ltp) if o.ltp is not None else None for o in ordered],
        "volume": [o.volume for o in ordered],
        "oi_change_series": oi_change_series,
        "cumulative_session_oi_change": cumulative,
        "distinct_expiries": sorted(
            {o.expiry.isoformat() for o in ordered if o.expiry is not None}
        ),
        "source": series.source,
        "coverage_message": None,
        "summary": summarise_deltas(deltas),
        "deltas": [d.to_dict() for d in deltas],
    }


def _timeline_from_series(series: list[ObservationSeries]) -> tuple[list[str], str]:
    """Union of real observation timestamps across contracts, for chart alignment."""
    stamps: set[datetime] = set()
    for item in series:
        for obs in item.observations:
            stamps.add(obs.observed_at)
    if not stamps:
        return [], "NO_REAL_OBSERVATIONS"
    return [format_ist(s) for s in sorted(stamps)], "PERSISTED_OBSERVATION_TIMESTAMPS"


def _series_coverage_message(
    series: list[ObservationSeries],
    coverage: dict[str, Any],
    selected: list[float],
) -> str:
    """Explicit data-coverage statement for the UI (Phase 4 requirement)."""
    if not series:
        return (
            "No real stored observations exist for the selected contracts, so no OI "
            "trajectory can be plotted. Reason: "
            f"{coverage.get('reason') or 'NO_PERSISTED_OBSERVATIONS'}. Series are never "
            "synthesised to fill the chart."
        )
    covered = len({s.contract_key for s in series})
    return (
        f"Real stored observations cover {covered} of {len(selected)} selected strikes. "
        "Strikes without observations are shown as uncovered rather than estimated."
    )


class IntradayOptionsService:
    """Unified service for reading, normalizing, and structuring intraday options data."""

    def __init__(
        self,
        adapter: OptionChainSourceAdapter | None = None,
        force_mock: bool = False,
        session: Any | None = None,
        contract_master: Any | None = None,
    ) -> None:
        if force_mock:
            self._adapter = adapter or MockOptionChainAdapter()
        elif adapter is not None:
            self._adapter = adapter
        else:
            token = getattr(settings, "upstox_analytics_token", None) or ""
            if token.strip():
                self._adapter = UpstoxOptionChainAdapter(mock_mode=False)
            else:
                self._adapter = MockOptionChainAdapter()
        self._session = session
        self._contract_master = contract_master

    @property
    def adapter(self) -> OptionChainSourceAdapter:
        return self._adapter

    # ------------------------------------------------------------------ helpers

    async def _db_session(self) -> Any | None:
        """Reuse the injected session, else open one from the shared factory."""
        if self._session is not None:
            return self._session
        try:
            from app.db.session import get_session_factory

            return get_session_factory()()
        except Exception as exc:  # noqa: BLE001 - absence of a DB must not 500
            logger.warning("Derivatives observation reads unavailable: %s", type(exc).__name__)
            return None

    def _master(self) -> Any:
        from app.services.derivatives_scope import DerivativesContractMasterClient

        if self._contract_master is None:
            self._contract_master = DerivativesContractMasterClient()
        return self._contract_master

    async def get_supported_underlyings(self) -> list[dict[str, Any]]:
        """Return supported underlyings with explicitly-labelled fallback specs.

        ``lot_size`` and ``strike_step`` here are *fallbacks*. The verified values
        for a specific expiry are exposed by :meth:`get_scope`.
        """
        result = []
        for spec in SUPPORTED_UNDERLYINGS.values():
            result.append(
                {
                    "symbol": spec.symbol,
                    "name": spec.name,
                    "exchange": spec.exchange,
                    "segment": spec.segment,
                    "strike_step": spec.fallback_strike_step,
                    "lot_size": spec.fallback_lot_size,
                    "is_active": spec.is_active,
                    "spec_source": "CONFIG_FALLBACK",
                }
            )
        return result

    async def get_scope(self, underlying: str, expiry_date: date | None = None) -> dict[str, Any]:
        """Resolve the complete, provider-grounded scope for one underlying.

        Returns the real spot, the real listed strike grid, the ATM resolved from
        that grid, the verified lot/tick size, the provider-listed expiries and a
        scope fingerprint clients echo back to discard stale responses.
        """
        norm_sym = normalize_index_symbol(underlying)
        spec = SUPPORTED_UNDERLYINGS.get(norm_sym)
        if not spec:
            raise ValueError(f"Unsupported underlying symbol '{underlying}'")

        master = self._master()
        expiries_result = await master.get_listed_expiries(norm_sym)
        listings = list(expiries_result.rows) if expiries_result.is_available else []

        selected_expiry = expiry_date
        if selected_expiry is None and listings:
            selected_expiry = listings[0].expiry_date

        grid: ListedStrikeGrid = ListedStrikeGrid(
            strikes=(), step=None, is_uniform=False, source=PROV_UNAVAILABLE
        )
        if selected_expiry is not None:
            grid = await master.get_strike_grid(norm_sym, selected_expiry)

        lots = None
        if selected_expiry is not None:
            lots = await master.resolve_lot_size(norm_sym, selected_expiry)

        spot: Decimal | None = None
        spot_provenance = PROV_UNAVAILABLE
        spot_observed_at = None
        chain = None
        try:
            chain = await self._adapter.fetch_chain_snapshot(
                underlying_symbol=norm_sym,
                expiry_date=selected_expiry,
                chain_scope="SINGLE_EXPIRY" if selected_expiry else "ALL_SUPPORTED_EXPIRIES",
            )
            spot = chain.spot_price
            spot_provenance = getattr(chain, "spot_provenance", "UNKNOWN") or "UNKNOWN"
            spot_observed_at = getattr(chain, "spot_observed_at", None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Scope chain read failed for %s: %s", norm_sym, type(exc).__name__)

        if spot is not None and not grid.is_available and chain is not None:
            # Fall back to the strikes the chain itself returned: still listed data.
            quoted = [
                q.strike_price
                for q in (chain.calls + chain.puts)
                if q.strike_price is not None
                and (selected_expiry is None or q.expiry_date == selected_expiry)
            ]
            if quoted:
                grid = infer_strike_grid(quoted, source=PROV_PROVIDER_LISTED)

        atm = resolve_atm(spot, grid, tie_break=DEFAULT_TIE_BREAK)
        fingerprint_spec = DerivativesFilterSpec(
            underlying=norm_sym,
            expiry=selected_expiry,
            strike_count=5,
        )
        return {
            "underlying": norm_sym,
            "underlying_name": spec.name,
            "contract_type": "INDEX",
            "index_instrument_key": spec.index_instrument_key,
            "spot_price": float(spot) if spot is not None else None,
            "spot_provenance": spot_provenance,
            "spot_observed_at_utc": (
                spot_observed_at.astimezone(UTC).isoformat() if spot_observed_at else None
            ),
            "selected_expiry": selected_expiry.isoformat() if selected_expiry else None,
            "expiries": [e.to_dict() for e in listings],
            "expiry_provenance": expiries_result.source,
            "expiry_reason": expiries_result.reason,
            "strike_grid": {
                "strike_count": len(grid.strikes),
                "step": float(grid.step) if grid.step is not None else None,
                "is_uniform": grid.is_uniform,
                "observed_steps": [float(s) for s in grid.observed_steps],
                "min_strike": float(grid.min_strike) if grid.min_strike is not None else None,
                "max_strike": float(grid.max_strike) if grid.max_strike is not None else None,
                "source": grid.source,
                "reason": grid.reason,
                "description": grid.describe(),
            },
            "atm": atm.to_dict(),
            "lot_size": lots.to_dict() if lots is not None else None,
            "scope_fingerprint": fingerprint_spec.fingerprint(),
            "interval_choices": list(INTERVAL_CHOICES_MINUTES),
            "analysis_window_choices": list(ANALYSIS_WINDOW_CHOICES_MINUTES),
        }

    async def get_available_expiries(self, underlying: str) -> list[dict[str, Any]]:
        """Resolve valid non-expired expiry dates dynamically from provider data.

        Never hardcodes dates or weekday assumptions. Returns chronologically sorted list.
        """
        norm_sym = normalize_index_symbol(underlying)
        if norm_sym not in SUPPORTED_UNDERLYINGS:
            raise ValueError(f"Unsupported underlying symbol '{underlying}'")

        today_ist = get_ist_now().date()

        # The authorized provider contract master is authoritative for which
        # expiries actually exist, including the provider's own weekly flag.
        master = self._master()
        listed = await master.get_listed_expiries(norm_sym)
        if listed.is_available:
            results = []
            first_monthly_found = False
            for idx, item in enumerate(listed.rows):
                expiry = item.expiry_date
                if expiry < today_ist:
                    continue
                is_monthly = not item.is_weekly
                is_current_monthly = False
                if is_monthly and not first_monthly_found:
                    is_current_monthly = True
                    first_monthly_found = True
                results.append(
                    {
                        "underlying": norm_sym,
                        "expiry_date": expiry.isoformat(),
                        "display_label": item.display_label,
                        "days_to_expiry": (expiry - today_ist).days,
                        "expiry_type": "MONTHLY" if is_monthly else "WEEKLY",
                        "is_nearest": idx == 0,
                        "is_next": idx == 1,
                        "is_current_monthly": is_current_monthly,
                        "contract_count": item.contract_count,
                        "provider": self._adapter.source_name,
                        "provenance": item.source,
                        "is_provider_listed": True,
                        "status": "ACTIVE",
                    }
                )
            if results:
                return results

        # Everything below is a NON-authoritative fallback. It is labelled as such
        # so no view can present a calendar guess as a listed contract.
        valid_dates: list[date] = []
        try:
            if hasattr(self._adapter, "get_available_expiries"):
                valid_dates = await self._adapter.get_available_expiries(norm_sym)
            if not valid_dates:
                chain = await self._adapter.fetch_chain_snapshot(
                    underlying_symbol=norm_sym,
                    chain_scope="ALL_SUPPORTED_EXPIRIES",
                )
                valid_dates = list(chain.expiries)
        except Exception as exc:
            logger.warning("Error resolving expiries for %s: %s", norm_sym, exc)
            return []

        valid_expiries = sorted([d for d in valid_dates if d >= today_ist])
        reason = listed.reason or "CONTRACT_MASTER_UNAVAILABLE"

        results = []
        first_monthly_found = False
        for idx, exp in enumerate(valid_expiries):
            dte = (exp - today_ist).days
            # Identify weekly vs monthly: monthly typically falls in last 7 days of month
            is_monthly = (exp.month != (exp + timedelta(days=7)).month)
            is_current_monthly = False
            if is_monthly and not first_monthly_found:
                is_current_monthly = True
                first_monthly_found = True

            results.append(
                {
                    "underlying": norm_sym,
                    "expiry_date": exp.isoformat(),
                    "display_label": (
                        f"{exp.strftime('%d %b %Y')} "
                        f"({'Monthly' if is_monthly else 'Weekly'}) — calendar estimate"
                    ),
                    "days_to_expiry": dte,
                    "expiry_type": "MONTHLY" if is_monthly else "WEEKLY",
                    "is_nearest": idx == 0,
                    "is_next": idx == 1,
                    "is_current_monthly": is_current_monthly,
                    "contract_count": None,
                    "provider": self._adapter.source_name,
                    "provenance": PROV_CALENDAR_FALLBACK,
                    "is_provider_listed": False,
                    "fallback_reason": reason,
                    "status": "UNVERIFIED_FALLBACK",
                }
            )
        return results

    async def get_option_chain_ladder(
        self,
        underlying: str,
        expiry_date: date | None = None,
        strike_count: int | None = 10,
        strike_range: str | None = None,
    ) -> dict[str, Any]:
        """Fetch and structure strike ladder for the Option Chain tab.

        Returns side-by-side Calls and Puts centered around ATM strike.
        """
        norm_sym = normalize_index_symbol(underlying)
        spec = SUPPORTED_UNDERLYINGS.get(norm_sym)
        if not spec:
            raise ValueError(f"Unsupported underlying symbol '{underlying}'")

        chain = await self._adapter.fetch_chain_snapshot(
            underlying_symbol=norm_sym,
            expiry_date=expiry_date,
            chain_scope="SINGLE_EXPIRY" if expiry_date else "ALL_SUPPORTED_EXPIRIES",
        )

        today_ist = get_ist_now().date()
        valid_expiries = sorted([d for d in chain.expiries if d >= today_ist])
        selected_expiry = expiry_date or (valid_expiries[0] if valid_expiries else None)

        spot = float(chain.spot_price) if chain.spot_price is not None else 0.0
        spot_provenance = getattr(chain, "spot_provenance", "UNKNOWN") or "UNKNOWN"

        # Filter quotes for the chosen expiry
        target_quotes: list[NormalizedQuote] = []
        if selected_expiry:
            target_quotes = [q for q in (chain.calls + chain.puts) if q.expiry_date == selected_expiry]
        else:
            target_quotes = chain.calls + chain.puts

        # Group by strike
        unique_strikes = sorted(
            list({q.strike_price for q in target_quotes if q.strike_price is not None})
        )

        # ------------------------------------------------------------------
        # G.11-OPT-3 Phase 1: ATM comes from the ACTUAL LISTED STRIKE GRID.
        # The previous implementation used round(spot / config_step) against a
        # spot the provider parser hardcoded to 24500.00, so every underlying
        # received the same wrong ATM that was frequently not a listed strike.
        # ------------------------------------------------------------------
        grid = infer_strike_grid(unique_strikes, source=PROV_PROVIDER_LISTED) if unique_strikes else infer_strike_grid([])
        atm_resolution = resolve_atm(spot if spot > 0 else None, grid, tie_break=DEFAULT_TIE_BREAK)
        atm_strike = float(atm_resolution.atm_strike) if atm_resolution.atm_strike is not None else None
        step = (
            float(grid.step)
            if grid.step is not None
            else float(spec.fallback_strike_step)
        )
        step_source = grid.source if grid.step is not None else "CONFIG_FALLBACK"
        if atm_resolution.atm_strike is None:
            # No listed grid at all: do not invent a strike. The ladder renders
            # without an ATM badge and the diagnostics state why.
            logger.warning(
                "No listed strike grid for %s (%s); ATM unresolved",
                norm_sym,
                atm_resolution.reason,
            )

        calls_by_strike: dict[Decimal, NormalizedQuote] = {
            q.strike_price: q for q in target_quotes if q.option_type == "CE" and q.strike_price is not None
        }
        puts_by_strike: dict[Decimal, NormalizedQuote] = {
            q.strike_price: q for q in target_quotes if q.option_type == "PE" and q.strike_price is not None
        }

        # Handle strike windowing (e.g. ATM ± 5, 10, 15, 20, 25). Windowing is
        # bounded strictly to the listed grid; the scope is never widened.
        display_strikes = unique_strikes
        if atm_resolution.atm_strike is not None:
            display_strikes = select_atm_window(grid, atm_resolution.atm_strike, strike_count)
        elif strike_count is not None and strike_count > 0 and unique_strikes:
            # ATM unresolved: fall back to the lowest listed strikes rather than
            # guessing a centre. Diagnostics record that this happened.
            display_strikes = unique_strikes[: (2 * strike_count) + 1]

        ladder_rows = []
        total_call_oi = 0
        total_put_oi = 0
        total_call_vol = 0
        total_put_vol = 0

        today_ist = get_ist_now().date()
        time_to_expiry_years = max(0.0, (selected_expiry - today_ist).days) / 365.0 if selected_expiry else 0.05

        for s in display_strikes:
            s_float = float(s)
            c = calls_by_strike.get(s)
            p = puts_by_strike.get(s)

            c_oi = c.open_interest if (c and c.open_interest is not None) else None
            p_oi = p.open_interest if (p and p.open_interest is not None) else None
            c_vol = c.volume if (c and c.volume is not None) else None
            p_vol = p.volume if (p and p.volume is not None) else None

            if c_oi is not None:
                total_call_oi += c_oi
            if p_oi is not None:
                total_put_oi += p_oi
            if c_vol is not None:
                total_call_vol += c_vol
            if p_vol is not None:
                total_put_vol += p_vol

            c_change_oi = c.source_change_in_oi if c and c.source_change_in_oi is not None else (c.change_in_open_interest if c else None)
            p_change_oi = p.source_change_in_oi if p and p.source_change_in_oi is not None else (p.change_in_open_interest if p else None)

            c_ltp = float(c.ltp) if c and c.ltp is not None else None
            c_change = float(c.change) if c and c.change is not None else None
            p_ltp = float(p.ltp) if p and p.ltp is not None else None
            p_change = float(p.change) if p and p.change is not None else None

            c_buildup = classify_buildup(c_change, c_change_oi)
            p_buildup = classify_buildup(p_change, p_change_oi)

            c_greeks = resolve_contract_greeks(
                spot=spot,
                strike=s_float,
                time_to_expiry_years=time_to_expiry_years,
                option_type="CE",
                ltp=c_ltp,
                source_greeks=c.source_greeks if c else None,
            )
            p_greeks = resolve_contract_greeks(
                spot=spot,
                strike=s_float,
                time_to_expiry_years=time_to_expiry_years,
                option_type="PE",
                ltp=p_ltp,
                source_greeks=p.source_greeks if p else None,
            )

            is_atm = atm_resolution.atm_strike is not None and s == atm_resolution.atm_strike
            call_itm = (s_float < spot) if spot > 0 else False
            put_itm = (s_float > spot) if spot > 0 else False

            ladder_rows.append(
                {
                    "strike_price": s_float,
                    "is_atm": is_atm,
                    "call": {
                        "ltp": c_ltp,
                        "change": c_change,
                        "change_pct": float(c.change_pct) if c and c.change_pct is not None else None,
                        "volume": c_vol,
                        "open_interest": c_oi,
                        "change_in_oi": c_change_oi,
                        "bid": float(c.bid) if c and c.bid is not None else None,
                        "ask": float(c.ask) if c and c.ask is not None else None,
                        "buildup": c_buildup,
                        "iv": c_greeks["iv"],
                        "delta": c_greeks["delta"],
                        "gamma": c_greeks["gamma"],
                        "vega": c_greeks["vega"],
                        "theta": c_greeks["theta"],
                        "greeks_provenance": c_greeks["provenance"],
                        "greeks_status": c_greeks["status"],
                        "is_itm": call_itm,
                    },
                    "put": {
                        "ltp": p_ltp,
                        "change": p_change,
                        "change_pct": float(p.change_pct) if p and p.change_pct is not None else None,
                        "volume": p_vol,
                        "open_interest": p_oi,
                        "change_in_oi": p_change_oi,
                        "bid": float(p.bid) if p and p.bid is not None else None,
                        "ask": float(p.ask) if p and p.ask is not None else None,
                        "buildup": p_buildup,
                        "iv": p_greeks["iv"],
                        "delta": p_greeks["delta"],
                        "gamma": p_greeks["gamma"],
                        "vega": p_greeks["vega"],
                        "theta": p_greeks["theta"],
                        "greeks_provenance": p_greeks["provenance"],
                        "greeks_status": p_greeks["status"],
                        "is_itm": put_itm,
                    },
                }
            )

        pcr = round(total_put_oi / total_call_oi, 4) if total_call_oi > 0 else None

        as_of = chain.as_of_timestamp or datetime.now(UTC)
        as_of_ist = as_of.astimezone(IST).strftime("%d %b %Y, %H:%M:%S IST")

        # Six-Metric Summary Panel calculation for the displayed strike coverage
        call_oi_changes = [r["call"]["change_in_oi"] for r in ladder_rows if r["call"]["change_in_oi"] is not None]
        call_oi_change_sum = sum(call_oi_changes) if call_oi_changes else None
        put_oi_changes = [r["put"]["change_in_oi"] for r in ladder_rows if r["put"]["change_in_oi"] is not None]
        put_oi_change_sum = sum(put_oi_changes) if put_oi_changes else None

        total_available_strikes = len(unique_strikes)
        displayed_count = len(ladder_rows)
        if strike_count is not None and strike_count > 0:
            coverage_label = f"ATM ± {strike_count} Strikes ({displayed_count} strikes)"
            is_full_coverage = False
        else:
            coverage_label = f"Full Expiry ({displayed_count} strikes)"
            is_full_coverage = True

        summary_panel = {
            "total_ce_oi": total_call_oi,
            "call_oi_change": call_oi_change_sum,
            "ce_volume": total_call_vol,
            "pe_volume": total_put_vol,
            "pe_oi_change": put_oi_change_sum,
            "total_pe_oi": total_put_oi,
            "strike_coverage_label": coverage_label,
            "is_full_coverage": is_full_coverage,
            "displayed_strikes_count": displayed_count,
            "total_available_strikes": total_available_strikes,
            "as_of_ist": as_of_ist,
            "provider": self._adapter.source_name,
            "freshness": "LIVE" if is_market_hours(as_of) else "MARKET_CLOSED",
        }

        return {
            "underlying": norm_sym,
            "underlying_name": spec.name,
            "spot_price": spot,
            "spot_provenance": spot_provenance,
            "atm_strike": atm_strike,
            "selected_expiry": selected_expiry.isoformat() if selected_expiry else None,
            "available_expiries": [e.isoformat() for e in valid_expiries],
            "strike_step": step,
            "strike_step_source": step_source,
            "listed_strike_count": len(unique_strikes),
            "strike_selection_label": describe_strike_selection(
                strike_count, len(display_strikes), len(unique_strikes)
            ),
            "atm_diagnostics": {
                **atm_resolution.to_dict(),
                "grid_description": grid.describe(),
                "grid_observed_steps": [float(s) for s in grid.observed_steps],
                "grid_is_uniform": grid.is_uniform,
            },
            "strike_count": len(ladder_rows),
            "total_call_oi": total_call_oi,
            "total_put_oi": total_put_oi,
            "total_call_volume": total_call_vol,
            "total_put_volume": total_put_vol,
            "pcr": pcr,
            "summary": summary_panel,
            "market_session": get_market_session_status(as_of),
            "is_market_open": is_market_hours(as_of),
            "provider": self._adapter.source_name,
            "freshness": "LIVE" if is_market_hours(as_of) else "MARKET_CLOSED",
            "as_of_utc": as_of.isoformat(),
            "as_of_ist": as_of_ist,
            "ladder": ladder_rows,
        }

    async def get_option_dashboard(
        self,
        underlying: str,
        expiry_date: date | None = None,
    ) -> dict[str, Any]:
        """Aggregate summary statistics for the Option Dashboard tab."""
        ladder_data = await self.get_option_chain_ladder(
            underlying=underlying,
            expiry_date=expiry_date,
            strike_count=None,  # Dashboard uses full available strikes
        )

        atm_strike = ladder_data["atm_strike"]
        spot_price = ladder_data["spot_price"]

        # Locate ATM call & put LTP
        atm_call_ltp = None
        atm_put_ltp = None
        for row in ladder_data["ladder"]:
            if atm_strike is not None and row["strike_price"] == atm_strike:
                atm_call_ltp = row["call"]["ltp"]
                atm_put_ltp = row["put"]["ltp"]
                break

        atm_straddle_premium = None
        if atm_call_ltp is not None and atm_put_ltp is not None:
            atm_straddle_premium = round(atm_call_ltp + atm_put_ltp, 2)

        today_ist = get_ist_now().date()
        dte = 0
        if ladder_data["selected_expiry"]:
            sel_exp = date.fromisoformat(ladder_data["selected_expiry"])
            dte = (sel_exp - today_ist).days

        total_oi = ladder_data["total_call_oi"] + ladder_data["total_put_oi"]
        ce_oi_pct = round((ladder_data["total_call_oi"] / total_oi) * 100, 1) if total_oi > 0 else 0.0
        pe_oi_pct = round((ladder_data["total_put_oi"] / total_oi) * 100, 1) if total_oi > 0 else 0.0

        pcr = ladder_data["pcr"]
        pcr_bias = "NEUTRAL"
        if pcr is not None:
            if pcr >= 1.2:
                pcr_bias = "BULLISH_BIAS"
            elif pcr <= 0.8:
                pcr_bias = "BEARISH_BIAS"

        return {
            "underlying": ladder_data["underlying"],
            "underlying_name": ladder_data["underlying_name"],
            "spot_price": spot_price,
            "selected_expiry": ladder_data["selected_expiry"],
            "days_to_expiry": dte,
            "total_call_oi": ladder_data["total_call_oi"],
            "total_put_oi": ladder_data["total_put_oi"],
            "ce_oi_percentage": ce_oi_pct,
            "pe_oi_percentage": pe_oi_pct,
            "pcr": pcr,
            "pcr_bias": pcr_bias,
            "atm_strike": atm_strike,
            "atm_call_ltp": atm_call_ltp,
            "atm_put_ltp": atm_put_ltp,
            "atm_straddle_premium": atm_straddle_premium,
            "total_volume": ladder_data["total_call_volume"] + ladder_data["total_put_volume"],
            "total_call_volume": ladder_data["total_call_volume"],
            "total_put_volume": ladder_data["total_put_volume"],
            "strike_count": ladder_data["strike_count"],
            "market_session": ladder_data["market_session"],
            "is_market_open": ladder_data["is_market_open"],
            "provider": ladder_data["provider"],
            "freshness": ladder_data["freshness"],
            "as_of_ist": ladder_data["as_of_ist"],
        }

    async def get_live_pcr(
        self,
        underlying: str,
        expiry_date: date | None = None,
    ) -> dict[str, Any]:
        """Calculate detailed Put-Call Ratio metrics for the Live PCR tab."""
        ladder_data = await self.get_option_chain_ladder(
            underlying=underlying,
            expiry_date=expiry_date,
            strike_count=None,
        )

        total_call_oi = ladder_data["total_call_oi"]
        total_put_oi = ladder_data["total_put_oi"]
        total_call_vol = ladder_data["total_call_volume"]
        total_put_vol = ladder_data["total_put_volume"]

        # Safe PCR calculation with zero-denominator handling
        pcr_oi = round(total_put_oi / total_call_oi, 4) if total_call_oi > 0 else None
        pcr_vol = round(total_put_vol / total_call_vol, 4) if total_call_vol > 0 else None

        sentiment = "NEUTRAL"
        if pcr_oi is not None:
            if pcr_oi > 1.25:
                sentiment = "BULLISH (Put support dominant)"
            elif pcr_oi < 0.75:
                sentiment = "BEARISH (Call resistance dominant)"
            else:
                sentiment = "BALANCED RANGE"

        return {
            "underlying": ladder_data["underlying"],
            "spot_price": ladder_data["spot_price"],
            "selected_expiry": ladder_data["selected_expiry"],
            "pcr_oi": pcr_oi,
            "pcr_volume": pcr_vol,
            "total_put_oi": total_put_oi,
            "total_call_oi": total_call_oi,
            "total_put_volume": total_put_vol,
            "total_call_volume": total_call_vol,
            "sentiment_classification": sentiment,
            "observed_strike_count": ladder_data["strike_count"],
            "calculation_formula": "Put Open Interest / Call Open Interest",
            "market_session": ladder_data["market_session"],
            "provider": ladder_data["provider"],
            "freshness": ladder_data["freshness"],
            "as_of_ist": ladder_data["as_of_ist"],
        }

    async def get_call_vs_put_oi(
        self,
        underlying: str,
        expiry_date: date | None = None,
        strike_count: int = 15,
    ) -> dict[str, Any]:
        """Prepare strike-by-strike open interest comparison data for charting."""
        ladder_data = await self.get_option_chain_ladder(
            underlying=underlying,
            expiry_date=expiry_date,
            strike_count=strike_count,
        )

        strikes = []
        call_oi_list = []
        put_oi_list = []
        call_change_oi_list = []
        put_change_oi_list = []

        max_oi = 0
        for row in ladder_data["ladder"]:
            s = row["strike_price"]
            c_oi = row["call"]["open_interest"] or 0
            p_oi = row["put"]["open_interest"] or 0
            c_doi = row["call"]["change_in_oi"] or 0
            p_doi = row["put"]["change_in_oi"] or 0

            strikes.append(s)
            call_oi_list.append(c_oi)
            put_oi_list.append(p_oi)
            call_change_oi_list.append(c_doi)
            put_change_oi_list.append(p_doi)

            if c_oi > max_oi:
                max_oi = c_oi
            if p_oi > max_oi:
                max_oi = p_oi

        # Formatted chart rows with relative percentage widths for institutional CSS bars
        comparison_bars = []
        for idx, s in enumerate(strikes):
            c_oi = call_oi_list[idx]
            p_oi = put_oi_list[idx]
            c_width = round((c_oi / max_oi) * 100, 1) if max_oi > 0 else 0
            p_width = round((p_oi / max_oi) * 100, 1) if max_oi > 0 else 0

            comparison_bars.append(
                {
                    "strike_price": s,
                    "is_atm": (s == ladder_data["atm_strike"]),
                    "call_oi": c_oi,
                    "put_oi": p_oi,
                    "call_bar_pct": c_width,
                    "put_bar_pct": p_width,
                    "call_change_oi": call_change_oi_list[idx],
                    "put_change_oi": put_change_oi_list[idx],
                }
            )

        return {
            "underlying": ladder_data["underlying"],
            "spot_price": ladder_data["spot_price"],
            "atm_strike": ladder_data["atm_strike"],
            "selected_expiry": ladder_data["selected_expiry"],
            "total_call_oi": ladder_data["total_call_oi"],
            "total_put_oi": ladder_data["total_put_oi"],
            "max_strike_oi": max_oi,
            "strikes": strikes,
            "comparison_bars": comparison_bars,
            "provider": ladder_data["provider"],
            "freshness": ladder_data["freshness"],
            "as_of_ist": ladder_data["as_of_ist"],
        }

    async def get_oi_change_analysis(
        self,
        underlying: str,
        expiry_date: date | None = None,
        strike_count: int = 15,
        buildup_filter: str | None = None,
    ) -> dict[str, Any]:
        """Analyze strike-wise CE/PE Open Interest changes and buildup for Tab 5."""
        ladder_data = await self.get_option_chain_ladder(
            underlying=underlying,
            expiry_date=expiry_date,
            strike_count=strike_count,
        )

        strikes_data = []
        max_abs_doi = 0

        for row in ladder_data["ladder"]:
            s = row["strike_price"]
            c = row["call"]
            p = row["put"]

            c_doi = c["change_in_oi"]
            p_doi = p["change_in_oi"]
            c_b = c["buildup"]
            p_b = p["buildup"]

            if c_doi is not None and abs(c_doi) > max_abs_doi:
                max_abs_doi = abs(c_doi)
            if p_doi is not None and abs(p_doi) > max_abs_doi:
                max_abs_doi = abs(p_doi)

            # Check buildup filter if applied
            if buildup_filter and buildup_filter.upper() not in ("ALL", ""):
                bf = buildup_filter.upper()
                c_match = c_b["classification"] == bf
                p_match = p_b["classification"] == bf
                if not (c_match or p_match):
                    continue

            strikes_data.append(
                {
                    "strike_price": s,
                    "is_atm": row["is_atm"],
                    "call": {
                        "oi": c["open_interest"],
                        "change_in_oi": c_doi,
                        "ltp": c["ltp"],
                        "price_change": c["change"],
                        "buildup": c_b,
                        "bar_pct": 0.0,
                        "is_positive": (c_doi > 0) if c_doi is not None else None,
                    },
                    "put": {
                        "oi": p["open_interest"],
                        "change_in_oi": p_doi,
                        "ltp": p["ltp"],
                        "price_change": p["change"],
                        "buildup": p_b,
                        "bar_pct": 0.0,
                        "is_positive": (p_doi > 0) if p_doi is not None else None,
                    },
                }
            )

        # Re-normalize bar_pct with final max_abs_doi
        for item in strikes_data:
            c_doi = item["call"]["change_in_oi"]
            p_doi = item["put"]["change_in_oi"]
            item["call"]["bar_pct"] = (
                round((abs(c_doi) / max_abs_doi) * 100, 1)
                if max_abs_doi > 0 and c_doi is not None
                else 0.0
            )
            item["put"]["bar_pct"] = (
                round((abs(p_doi) / max_abs_doi) * 100, 1)
                if max_abs_doi > 0 and p_doi is not None
                else 0.0
            )

        return {
            "underlying": ladder_data["underlying"],
            "underlying_name": ladder_data["underlying_name"],
            "spot_price": ladder_data["spot_price"],
            "atm_strike": ladder_data["atm_strike"],
            "selected_expiry": ladder_data["selected_expiry"],
            "available_expiries": ladder_data["available_expiries"],
            "strike_step": ladder_data["strike_step"],
            "strike_count": len(strikes_data),
            "summary": ladder_data["summary"],
            "strikes": strikes_data,
            "max_abs_oi_change": max_abs_doi,
            "active_filter": buildup_filter or "ALL",
            "provider": ladder_data["provider"],
            "freshness": ladder_data["freshness"],
            "market_session": ladder_data["market_session"],
            "is_market_open": ladder_data["is_market_open"],
            "as_of_ist": ladder_data["as_of_ist"],
            "interval_baseline": "Provider Intraday Baseline vs Previous Session Close",
        }

    async def get_oi_statistics(
        self,
        underlying: str,
        expiry_date: date | None = None,
        strike_range: str | None = None,
    ) -> dict[str, Any]:
        """Comprehensive open interest statistics, distributions, concentration, and Max Pain."""
        ladder_data = await self.get_option_chain_ladder(
            underlying=underlying,
            expiry_date=expiry_date,
            strike_count=None,
        )

        all_rows = ladder_data["ladder"]
        max_pain_strike, loss_curve = calculate_max_pain(all_rows)

        total_call_oi = ladder_data["total_call_oi"]
        total_put_oi = ladder_data["total_put_oi"]
        total_call_vol = ladder_data["total_call_volume"]
        total_put_vol = ladder_data["total_put_volume"]

        net_oi = total_put_oi - total_call_oi
        total_oi = total_call_oi + total_put_oi

        pcr_oi = ladder_data["pcr"]
        pcr_vol = round(total_put_vol / total_call_vol, 3) if total_call_vol > 0 else None

        # Additions and reductions
        def _get_doi(entry: dict[str, Any] | None) -> int:
            if not entry:
                return 0
            val = entry.get("change_in_oi") if entry.get("change_in_oi") is not None else entry.get("doi")
            return int(val) if val is not None else 0

        def _get_oi(entry: dict[str, Any] | None) -> int:
            if not entry:
                return 0
            val = entry.get("open_interest") if entry.get("open_interest") is not None else entry.get("oi")
            return int(val) if val is not None else 0

        call_additions = sum(_get_doi(r.get("call")) for r in all_rows if _get_doi(r.get("call")) > 0)
        call_reductions = sum(abs(_get_doi(r.get("call"))) for r in all_rows if _get_doi(r.get("call")) < 0)
        put_additions = sum(_get_doi(r.get("put")) for r in all_rows if _get_doi(r.get("put")) > 0)
        put_reductions = sum(abs(_get_doi(r.get("put"))) for r in all_rows if _get_doi(r.get("put")) < 0)

        # Strike concentration: Top 3 Call OI (Key Resistances) and Top 3 Put OI (Key Supports)
        sorted_by_call_oi = sorted(all_rows, key=lambda r: _get_oi(r.get("call")), reverse=True)
        sorted_by_put_oi = sorted(all_rows, key=lambda r: _get_oi(r.get("put")), reverse=True)

        top_call_strikes = [
            {"strike": r["strike_price"], "oi": _get_oi(r.get("call")), "volume": (r.get("call") or {}).get("volume", 0)}
            for r in sorted_by_call_oi[:3] if _get_oi(r.get("call")) > 0
        ]
        top_put_strikes = [
            {"strike": r["strike_price"], "oi": _get_oi(r.get("put")), "volume": (r.get("put") or {}).get("volume", 0)}
            for r in sorted_by_put_oi[:3] if _get_oi(r.get("put")) > 0
        ]

        # Filter strikes according to strike_range if requested
        display_rows = all_rows
        if strike_range and strike_range != "all":
            try:
                count = int(strike_range)
                atm = ladder_data["atm_strike"]
                step = ladder_data["strike_step"]
                min_s = atm - (count * step)
                max_s = atm + (count * step)
                display_rows = [r for r in all_rows if min_s <= r["strike_price"] <= max_s]
            except ValueError:
                pass

        table_rows = []
        for r in display_rows:
            k = r["strike_price"]
            c_oi = _get_oi(r.get("call"))
            p_oi = _get_oi(r.get("put"))
            c_vol = (r.get("call") or {}).get("volume") or 0
            p_vol = (r.get("put") or {}).get("volume") or 0
            c_doi = (r.get("call") or {}).get("change_in_oi")
            p_doi = (r.get("put") or {}).get("change_in_oi")
            c_ltp = (r.get("call") or {}).get("ltp")
            p_ltp = (r.get("put") or {}).get("ltp")
            c_bld = (r.get("call") or {}).get("buildup")
            p_bld = (r.get("put") or {}).get("buildup")

            table_rows.append({
                "strike_price": k,
                "is_atm": (k == ladder_data["atm_strike"]),
                "is_max_pain": (k == max_pain_strike),
                "call_oi": c_oi,
                "put_oi": p_oi,
                "net_oi": p_oi - c_oi,
                "call_volume": c_vol,
                "put_volume": p_vol,
                "call_doi": c_doi,
                "put_doi": p_doi,
                "call_ltp": c_ltp,
                "put_ltp": p_ltp,
                "call_buildup": c_bld,
                "put_buildup": p_bld,
            })

        sentiment = "NEUTRAL"
        sentiment_label = "Neutral / Sideways Range"
        sentiment_class = "secondary"
        if pcr_oi is not None:
            if pcr_oi >= 1.25:
                sentiment = "BULLISH"
                sentiment_label = "Bullish Sentiment (Heavy Put Writing)"
                sentiment_class = "success"
            elif pcr_oi <= 0.75:
                sentiment = "BEARISH"
                sentiment_label = "Bearish Sentiment (Heavy Call Writing)"
                sentiment_class = "danger"

        return {
            "underlying": ladder_data["underlying"],
            "underlying_name": ladder_data["underlying_name"],
            "spot_price": ladder_data["spot_price"],
            "atm_strike": ladder_data["atm_strike"],
            "strike_step": ladder_data.get("strike_step", 50),
            "max_pain_strike": max_pain_strike,
            "selected_expiry": ladder_data["selected_expiry"],
            "available_expiries": ladder_data["available_expiries"],
            "total_call_oi": total_call_oi,
            "total_put_oi": total_put_oi,
            "total_oi": total_oi,
            "net_oi": net_oi,
            "pcr_oi": pcr_oi,
            "pcr_volume": pcr_vol,
            "total_call_volume": total_call_vol,
            "total_put_volume": total_put_vol,
            "call_additions": call_additions,
            "call_reductions": call_reductions,
            "put_additions": put_additions,
            "put_reductions": put_reductions,
            "top_call_strikes": top_call_strikes,
            "top_put_strikes": top_put_strikes,
            "sentiment": sentiment,
            "sentiment_label": sentiment_label,
            "sentiment_class": sentiment_class,
            "loss_curve": loss_curve,
            "table_rows": table_rows,
            "is_market_open": ladder_data["is_market_open"],
            "market_session": ladder_data["market_session"],
            "provider": ladder_data["provider"],
            "as_of_ist": ladder_data["as_of_ist"],
        }

    async def get_interval_oi_change(
        self,
        underlying: str,
        expiry_date: date | None = None,
        # G.11-OPT-9: ``None`` means every listed strike, matching
        # ``select_atm_window`` and the shared filter panel's "All listed strikes".
        strike_count: int | None = 15,
        interval: str = "15m",
        buildup_filter: str | None = None,
        session_date: date | None = None,
    ) -> dict[str, Any]:
        """Analyze strike-wise CE/PE Open Interest and Premium changes across selectable time intervals.

        ``session_date`` bounds the historical observation read to one trading
        session (IST-derived). When omitted it defaults to the current IST session,
        so a previous-session observation can never be co-located as the baseline
        for a current-session interval.
        """
        ladder_data = await self.get_option_chain_ladder(
            underlying=underlying,
            expiry_date=expiry_date,
            strike_count=strike_count,
        )

        # ------------------------------------------------------------------
        # G.11-OPT-3 Phase 6: NO PRO-RATA SCALING.
        # The previous implementation multiplied the current day's OI change by
        # (interval / 375) to fabricate a "3-minute change". A 3-minute OI change
        # is only valid when it is *measured* between two real comparable
        # observations. Where those do not exist we say so, and we never
        # substitute a scaled or previous-close figure for the requested window.
        # ------------------------------------------------------------------
        parsed_interval = _parse_interval_minutes(interval)
        is_full_session = parsed_interval is None
        mins = parsed_interval if parsed_interval is not None else 375

        selected_expiry = (
            date.fromisoformat(ladder_data["selected_expiry"])
            if ladder_data["selected_expiry"]
            else None
        )
        scope = tuple(Decimal(str(r["strike_price"])) for r in ladder_data["ladder"])
        # G.11 OI Change Phase 1: the interval ΔOI baseline is session-bound.
        # ``window_end`` is the evaluation instant, so its IST trading date is the
        # only session an interval observation may come from — a previous-session
        # observation can never be co-located as this session's baseline.
        window_end = datetime.now(UTC)
        effective_session_date = session_date or ist_trade_date(window_end)
        real_series, coverage = await self._load_real_series(
            ladder_data["underlying"],
            selected_expiry,
            strike_scope=scope or None,
            session_date=effective_session_date,
        )
        measured = (
            _measured_interval_lookup(real_series, mins, session_date=effective_session_date)
            if not is_full_session
            else {}
        )

        baseline_type = (
            BASELINE_PREVIOUS_CLOSE if is_full_session else BASELINE_INTRADAY_INTERVAL
        )
        window_start = window_end - timedelta(minutes=mins)

        if not is_full_session:
            baseline_label = (
                f"Measured across real comparable observation pairs ~{mins} minutes apart"
            )
            disclosure = None
        else:
            baseline_label = "Provider day change (session-to-date versus previous close)"
            disclosure = (
                "Full-session changes are the provider's own day change versus the "
                "previous close mark. They are not interval measurements."
            )

        if not is_full_session and not measured:
            disclosure = (
                f"No real comparable observation pair exists for a {mins}-minute window "
                "in the stored observations, so the interval OI change is unavailable. "
                "Provider day-change figures are shown separately and are NOT a "
                f"{mins}-minute measurement; no proportional scaling has been applied."
            )

        strikes_data = []
        max_abs_doi = 0

        for row in ladder_data["ladder"]:
            s = row["strike_price"]
            c = row["call"]
            p = row["put"]

            c_day_doi = c["change_in_oi"]
            p_day_doi = p["change_in_oi"]
            c_day_pchange = float(c["change"] or 0.0)
            p_day_pchange = float(p["change"] or 0.0)

            c_measured = measured.get((Decimal(str(s)), "CE"))
            p_measured = measured.get((Decimal(str(s)), "PE"))

            if is_full_session:
                c_doi = c_day_doi
                p_doi = p_day_doi
                c_pchange = c_day_pchange
                p_pchange = p_day_pchange
                c_prov = PROV_MODEL_CALCULATED if c_day_doi is not None else PROV_UNAVAILABLE
                p_prov = PROV_MODEL_CALCULATED if p_day_doi is not None else PROV_UNAVAILABLE
                c_start = p_start = None
                c_eff = p_eff = None
            else:
                c_doi = c_measured["oi_change"] if c_measured else None
                p_doi = p_measured["oi_change"] if p_measured else None
                c_pchange = c_measured["price_change"] if c_measured else None
                p_pchange = p_measured["price_change"] if p_measured else None
                c_prov = PROV_MODEL_CALCULATED if c_measured else PROV_UNAVAILABLE
                p_prov = PROV_MODEL_CALCULATED if p_measured else PROV_UNAVAILABLE
                c_start = c_measured["start_ist"] if c_measured else None
                p_start = p_measured["start_ist"] if p_measured else None
                c_eff = c_measured["effective_seconds"] if c_measured else None
                p_eff = p_measured["effective_seconds"] if p_measured else None

            c_bld = classify_buildup(c_pchange, c_doi)
            p_bld = classify_buildup(p_pchange, p_doi)

            if c_doi is not None and abs(c_doi) > max_abs_doi:
                max_abs_doi = abs(c_doi)
            if p_doi is not None and abs(p_doi) > max_abs_doi:
                max_abs_doi = abs(p_doi)

            if buildup_filter and buildup_filter.upper() not in ("ALL", ""):
                bf = buildup_filter.upper()
                if c_bld["classification"] != bf and p_bld["classification"] != bf:
                    continue

            strikes_data.append({
                "strike_price": s,
                "is_atm": row["is_atm"],
                "call": {
                    "oi": c["open_interest"],
                    "ltp": c["ltp"],
                    "price_change": c_pchange,
                    "change_in_oi": c_doi,
                    "provider_day_change_in_oi": c_day_doi,
                    "measurement_available": bool(c_measured) or is_full_session,
                    "measurement_start_ist": c_start,
                    "effective_seconds": c_eff,
                    "provenance": c_prov,
                    "buildup": c_bld,
                    "bar_pct": 0.0,
                },
                "put": {
                    "oi": p["open_interest"],
                    "ltp": p["ltp"],
                    "price_change": p_pchange,
                    "change_in_oi": p_doi,
                    "provider_day_change_in_oi": p_day_doi,
                    "measurement_available": bool(p_measured) or is_full_session,
                    "measurement_start_ist": p_start,
                    "effective_seconds": p_eff,
                    "provenance": p_prov,
                    "buildup": p_bld,
                    "bar_pct": 0.0,
                },
            })

        for item in strikes_data:
            c_doi = item["call"]["change_in_oi"]
            p_doi = item["put"]["change_in_oi"]
            item["call"]["bar_pct"] = (
                round((abs(c_doi) / max_abs_doi) * 100, 1)
                if max_abs_doi > 0 and c_doi is not None
                else 0.0
            )
            item["put"]["bar_pct"] = (
                round((abs(p_doi) / max_abs_doi) * 100, 1)
                if max_abs_doi > 0 and p_doi is not None
                else 0.0
            )

        measured_contract_count = sum(
            1
            for item in strikes_data
            for side in ("call", "put")
            if item[side]["measurement_available"]
        )

        return {
            "underlying": ladder_data["underlying"],
            "underlying_name": ladder_data["underlying_name"],
            "spot_price": ladder_data["spot_price"],
            "spot_provenance": ladder_data.get("spot_provenance"),
            "atm_strike": ladder_data["atm_strike"],
            "atm_diagnostics": ladder_data.get("atm_diagnostics"),
            "selected_expiry": ladder_data["selected_expiry"],
            "available_expiries": ladder_data["available_expiries"],
            "strike_step": ladder_data["strike_step"],
            "strike_step_source": ladder_data.get("strike_step_source"),
            "strike_selection_label": ladder_data.get("strike_selection_label"),
            "strike_count": len(strikes_data),
            "interval": interval.strip().lower(),
            "interval_minutes": None if is_full_session else mins,
            "analysis_window_choices": list(ANALYSIS_WINDOW_CHOICES_MINUTES),
            "interval_choices": list(INTERVAL_CHOICES_MINUTES),
            "baseline_type": baseline_type,
            "baseline_label": baseline_label,
            "disclosure": disclosure,
            "window_start_utc": None if is_full_session else window_start.isoformat(),
            "window_end_utc": None if is_full_session else window_end.isoformat(),
            "window_start_ist": None if is_full_session else format_ist(window_start),
            "window_end_ist": None if is_full_session else format_ist(window_end),
            "measurement_available": measured_contract_count > 0,
            "measured_contract_count": measured_contract_count,
            "pro_rata_scaling_applied": False,
            "measurement_note": (
                "Interval changes are measured between real comparable observations. "
                "No proportional scaling, forward-fill or previous-close substitution "
                "is ever applied to an intraday interval."
            ),
            "real_observation_coverage": coverage,
            "active_filter": buildup_filter or "ALL",
            "summary": ladder_data["summary"],
            "strikes": strikes_data,
            "max_abs_oi_change": max_abs_doi,
            "provider": ladder_data["provider"],
            "as_of_ist": ladder_data["as_of_ist"],
            "is_market_open": ladder_data["is_market_open"],
        }

    async def _load_real_series(
        self,
        underlying: str,
        expiry_date: date | None,
        strike_scope: tuple[Decimal, ...] | None = None,
        *,
        evaluation_at: datetime | None = None,
        session_date: date | None = None,
        call_strikes: tuple[Decimal, ...] = (),
        put_strikes: tuple[Decimal, ...] = (),
    ) -> tuple[list[ObservationSeries], dict[str, Any]]:
        """Read real persisted option observations for the selected scope.

        Returns ``([], {...})`` with an explicit reason when no real observations
        exist. It never fabricates a series to fill the gap.

        ``session_date`` bounds the read to one trading session (IST-derived).
        Point-in-time replay always passes it: without a session bound, replay at
        09:20 could pick up an observation from a previous day.
        """
        session = await self._db_session()
        if session is None:
            return [], {
                "available": False,
                "reason": "OBSERVATION_STORE_UNAVAILABLE",
                "source": PROV_UNAVAILABLE,
            }
        try:
            repo = OptionObservationRepository(session)
            index_id = await repo.resolve_underlying_index_id(underlying)
            if index_id is None:
                return [], {
                    "available": False,
                    "reason": "UNDERLYING_NOT_PRESENT_IN_MARKET_INDICES",
                    "source": PROV_UNAVAILABLE,
                }
            strike_min = min(strike_scope) if strike_scope else None
            strike_max = max(strike_scope) if strike_scope else None
            return await repo.load_series(
                OptionObservationQuery(
                    underlying_index_id=index_id,
                    underlying_symbol=underlying,
                    expiry=expiry_date,
                    call_strikes=call_strikes,
                    put_strikes=put_strikes,
                    strike_min=strike_min,
                    strike_max=strike_max,
                    evaluation_at=evaluation_at,
                    session_date=session_date,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Real option observation read failed: %s", type(exc).__name__
            )
            return [], {
                "available": False,
                "reason": f"OBSERVATION_READ_FAILED:{type(exc).__name__}",
                "source": PROV_UNAVAILABLE,
            }
        finally:
            if self._session is None:
                try:
                    await session.close()
                except Exception:  # noqa: BLE001
                    pass

    async def get_multistrike_oi(
        self,
        underlying: str,
        expiry_date: date | None = None,
        selected_strikes: list[float] | None = None,
        strike_count: int | None = 5,
        interval_minutes: int = 5,
        session_date: date | None = None,
    ) -> dict[str, Any]:
        """Multi-strike OI trajectories built **only** from real observations.

        The previous implementation synthesised a ``0.70 → 1.00`` progression onto
        the current open interest for ten hardcoded timestamps and invented 50,000
        contracts for any strike missing from the ladder. That is removed: strikes
        without real observations report explicit insufficient coverage.

        The trajectory is a single-session chart (its axis is IST clock times), so
        the read is bound to exactly one trading session: the supplied one, or the
        current IST session when none is supplied. Without that bound the series
        spanned every stored session, which both leaked a previous session's
        observations into the current trajectory and forced a full-table scan.
        """
        ladder_data = await self.get_option_chain_ladder(
            underlying=underlying,
            expiry_date=expiry_date,
            strike_count=strike_count,
        )

        atm_strike = ladder_data["atm_strike"]
        available_strikes = [r["strike_price"] for r in ladder_data["ladder"]]
        grid_strikes = tuple(Decimal(str(s)) for s in available_strikes)

        if selected_strikes:
            selected = [s for s in selected_strikes if s in available_strikes]
            rejected = [s for s in selected_strikes if s not in available_strikes]
        else:
            selected = list(available_strikes)
            rejected = []

        # G.11-OPT-13 Phase A: bind the historical read to one session. The
        # canonical IST helper is the same one the OI Change baseline uses, so a
        # previous session can never be co-located as this session's trajectory.
        effective_session_date = session_date or ist_trade_date(datetime.now(UTC))
        series, coverage = await self._load_real_series(
            ladder_data["underlying"],
            date.fromisoformat(ladder_data["selected_expiry"]) if ladder_data["selected_expiry"] else None,
            strike_scope=grid_strikes,
            session_date=effective_session_date,
        )

        strike_series = []
        for s in selected:
            base_row = next((r for r in ladder_data["ladder"] if r["strike_price"] == s), None)
            s_dec = Decimal(str(s))
            call_series = _series_for_strike(series, s_dec, "CE")
            put_series = _series_for_strike(series, s_dec, "PE")
            strike_series.append(
                {
                    "strike_price": s,
                    "is_atm": atm_strike is not None and s == atm_strike,
                    "call": _series_payload(call_series, interval_minutes),
                    "put": _series_payload(put_series, interval_minutes),
                    "current_call_oi": base_row["call"]["open_interest"] if base_row else None,
                    "current_put_oi": base_row["put"]["open_interest"] if base_row else None,
                    "current_call_ltp": base_row["call"]["ltp"] if base_row else None,
                    "current_put_ltp": base_row["put"]["ltp"] if base_row else None,
                    "current_call_volume": base_row["call"]["volume"] if base_row else None,
                    "current_put_volume": base_row["put"]["volume"] if base_row else None,
                    "has_real_series": bool(call_series.is_available or put_series.is_available),
                }
            )

        timeline, timeline_source = _timeline_from_series(series)
        return {
            "underlying": ladder_data["underlying"],
            "underlying_name": ladder_data["underlying_name"],
            "spot_price": ladder_data["spot_price"],
            "spot_provenance": ladder_data.get("spot_provenance"),
            "atm_strike": atm_strike,
            "atm_diagnostics": ladder_data.get("atm_diagnostics"),
            "selected_expiry": ladder_data["selected_expiry"],
            "available_expiries": ladder_data["available_expiries"],
            "strike_step": ladder_data["strike_step"],
            "strike_selection_label": ladder_data.get("strike_selection_label"),
            "time_labels": timeline,
            "timeline_source": timeline_source,
            "strike_series": strike_series,
            "selected_strikes": selected,
            "rejected_strikes": rejected,
            "available_strikes": available_strikes,
            "interval_minutes": interval_minutes,
            "observation_session": effective_session_date.isoformat(),
            "series_available": bool(series),
            "coverage": coverage,
            "coverage_message": _series_coverage_message(series, coverage, selected),
            "provenance": PROV_MODEL_CALCULATED if series else PROV_UNAVAILABLE,
            "provider": ladder_data["provider"],
            "as_of_ist": ladder_data["as_of_ist"],
        }

    async def get_options_oi_vs_time(
        self,
        underlying: str,
        expiry_date: date | None = None,
        interval_minutes: int = 5,
        strike_count: int | None = 5,
        call_strikes: tuple[Decimal, ...] = (),
        put_strikes: tuple[Decimal, ...] = (),
        evaluation_at: datetime | None = None,
        session_date: date | None = None,
    ) -> dict[str, Any]:
        """Call/Put OI over time, built from real observations of a chosen scope.

        The previous implementation multiplied the current total OI by a synthetic
        ``0.65 → 1.00`` ramp and drifted the spot by a fabricated ±0.6%. Both are
        removed. Per-contract, per-interval series come from stored observations of
        the exact selected contracts; the aggregate is only formed when those
        observations exist, and each series keeps its own expiry label.

        The chart is an intraday trajectory whose axis is IST clock times, so the
        read is bound to exactly one trading session (the supplied one, the
        evaluation instant's session when replaying, otherwise the current IST
        session). Unbounded, two sessions' observations collapse into the same
        clock-time bucket and one session's OI is summed into another's point.
        Multi-session history is a different feature and belongs to the replay
        workbench, which selects its session explicitly; every observation of the
        selected session is still plotted, so the trajectory itself is unchanged.
        """
        ladder_data = await self.get_option_chain_ladder(
            underlying=underlying,
            expiry_date=expiry_date,
            strike_count=strike_count,
        )

        selected_expiry = (
            date.fromisoformat(ladder_data["selected_expiry"])
            if ladder_data["selected_expiry"]
            else None
        )
        available_strikes = [Decimal(str(r["strike_price"])) for r in ladder_data["ladder"]]
        # G.11-OPT-13 Phase A: one session only — never a previous session mixed
        # into the current trajectory. Same canonical IST helper as the OI Change
        # baseline, so the two cannot disagree about which session is current.
        effective_session_date = (
            session_date
            or (ist_trade_date(evaluation_at) if evaluation_at is not None else None)
            or ist_trade_date(datetime.now(UTC))
        )
        series, coverage = await self._load_real_series(
            ladder_data["underlying"],
            selected_expiry,
            strike_scope=tuple(available_strikes) if available_strikes else None,
            evaluation_at=evaluation_at,
            session_date=effective_session_date,
            call_strikes=call_strikes,
            put_strikes=put_strikes,
        )

        timeline, timeline_source = _timeline_from_series(series)
        calls = [s for s in series if (s.first().option_type or "").upper() == "CE"]
        puts = [s for s in series if (s.first().option_type or "").upper() == "PE"]

        by_stamp = _aggregate_by_timestamp(series)
        call_oi_series = [by_stamp.get(stamp, {}).get("call_oi") for stamp in timeline]
        put_oi_series = [by_stamp.get(stamp, {}).get("put_oi") for stamp in timeline]
        net_oi_series = [
            (p - c) if (p is not None and c is not None) else None
            for p, c in zip(put_oi_series, call_oi_series, strict=True)
        ]
        pcr_series = [
            round(p / c, 4) if (p is not None and c is not None and c > 0) else None
            for p, c in zip(put_oi_series, call_oi_series, strict=True)
        ]
        spot_series = [by_stamp.get(stamp, {}).get("spot") for stamp in timeline]

        contract_rows = [
            {
                "contract_key": s.contract_key,
                "label": s.label,
                "option_type": (s.first().option_type if s.first() else None),
                "strike_price": float(s.first().strike) if s.first() and s.first().strike is not None else None,
                "expiry_date": s.first().expiry.isoformat() if s.first() and s.first().expiry else None,
                "observation_count": s.count,
                "source": s.source,
                "is_available": s.is_available,
                "coverage_message": s.coverage()["coverage_message"],
            }
            for s in series
        ]

        return {
            "underlying": ladder_data["underlying"],
            "underlying_name": ladder_data["underlying_name"],
            "spot_price": ladder_data["spot_price"],
            "spot_provenance": ladder_data.get("spot_provenance"),
            "atm_strike": ladder_data["atm_strike"],
            "atm_diagnostics": ladder_data.get("atm_diagnostics"),
            "selected_expiry": ladder_data["selected_expiry"],
            "available_expiries": ladder_data["available_expiries"],
            "strike_selection_label": ladder_data.get("strike_selection_label"),
            "interval_minutes": interval_minutes,
            "observation_session": effective_session_date.isoformat(),
            "time_labels": timeline,
            "timeline_source": timeline_source,
            "call_oi_series": call_oi_series,
            "put_oi_series": put_oi_series,
            "net_oi_series": net_oi_series,
            "pcr_series": pcr_series,
            "spot_series": spot_series,
            "call_contracts": [r for r in contract_rows if r["option_type"] == "CE"],
            "put_contracts": [r for r in contract_rows if r["option_type"] == "PE"],
            "selected_contracts": contract_rows,
            "selected_call_count": len(calls),
            "selected_put_count": len(puts),
            "series_available": bool(series),
            "aggregate_available": bool(timeline) and all(
                v is not None for v in call_oi_series
            ),
            "aggregate_scope_note": (
                "Aggregates cover only the selected expiry and the selected contract "
                "set. Expiries are never blended."
            ),
            "current_pcr": ladder_data["pcr"],
            "current_call_oi": ladder_data["total_call_oi"],
            "current_put_oi": ladder_data["total_put_oi"],
            "coverage": coverage,
            "coverage_message": _series_coverage_message(series, coverage, [float(s) for s in available_strikes]),
            "provenance": PROV_MODEL_CALCULATED if series else PROV_UNAVAILABLE,
            "provider": ladder_data["provider"],
            "as_of_ist": ladder_data["as_of_ist"],
        }

    async def get_futures_oi_vs_time(
        self,
        underlying: str,
        interval_minutes: int = 5,
        expiry_date: date | None = None,
        oi_unit: str = "CONTRACTS",
    ) -> dict[str, Any]:
        """Real futures price / OI / volume vs time.

        The previous implementation invented a ₹65.50 premium, a 12,500,000-contract
        base OI and synthetic ramps, and labelled the contract ``NIFTY-FUT``, which
        is not a traded instrument. Real futures price, volume, open interest and
        OI day high/low **are** entitled from the provider, so the ledger is now
        built from real observations. When no real history exists the response says
        so instead of drawing a modelled curve.
        """
        from app.services.derivatives_futures_ledger import build_futures_workbench

        workbench = await build_futures_workbench(
            underlying,
            interval_minutes=interval_minutes,
            oi_unit=oi_unit,
            expiry=expiry_date,
        )
        ledger = workbench.get("ledger") or {}
        return {
            "underlying": workbench.get("underlying"),
            "underlying_name": workbench.get("underlying_name"),
            "interval_minutes": interval_minutes,
            "contract_symbol": (workbench.get("contract") or {}).get("trading_symbol"),
            "instrument_key": (workbench.get("contract") or {}).get("instrument_key"),
            "lot_size": (workbench.get("contract") or {}).get("lot_size"),
            "selected_expiry": workbench.get("selected_expiry"),
            "expiries": workbench.get("expiries", []),
            "underlying_spot": workbench.get("underlying_spot"),
            "observation": workbench.get("observation"),
            "summary_cards": workbench.get("summary_cards", []),
            "ledger": ledger,
            "positioning": workbench.get("positioning") or {"available": False},
            "basis": workbench.get("basis"),
            "days_to_expiry": workbench.get("days_to_expiry"),
            "oi_unit": oi_unit,
            "oi_unit_conversion": workbench.get("oi_unit_conversion"),
            "history_available": workbench.get("history_available", False),
            "history_reason": workbench.get("history_reason"),
            "provider_entitlement": workbench.get("provider_entitlement"),
            "entitlement_statement": workbench.get("entitlement_statement"),
            "disclosure": workbench.get("entitlement_statement"),
            "limitations": workbench.get("limitations", []),
            "synthetic_substitution": False,
            "reason": workbench.get("reason"),
            "available": bool(workbench.get("observation")),
        }

    async def get_session_replay_timeline(
        self,
        underlying: str,
        session_date: date | None = None,
        interval_minutes: int = 15,
    ) -> dict[str, Any]:
        """Real replay timeline: the timestamps at which observations actually exist.

        The previous implementation returned 25 invented 15-minute steps, each
        carrying a fabricated spot and ``has_snapshot: True``. Those were not
        snapshots at all. This version lists only real persisted observation
        instants, marks missing intervals as unavailable, and never invents a
        timestamp or a price. ``has_snapshot`` is True only for real snapshots.
        """
        norm_sym = normalize_index_symbol(underlying)
        spec = SUPPORTED_UNDERLYINGS.get(norm_sym)
        if not spec:
            raise ValueError(f"Unsupported underlying symbol '{underlying}'")

        today = session_date or get_ist_now().date()
        session = await self._db_session()
        available_sessions: list[dict[str, Any]] = []
        session_expiries: list[date] = []
        real_snapshots: list[dict[str, Any]] = []
        reason: str | None = None

        if session is None:
            reason = "OBSERVATION_STORE_UNAVAILABLE"
        else:
            try:
                repo = OptionObservationRepository(session)
                index_id = await repo.resolve_underlying_index_id(norm_sym)
                if index_id is None:
                    reason = "UNDERLYING_NOT_PRESENT_IN_MARKET_INDICES"
                else:
                    available_sessions = await repo.available_sessions(index_id)
                    session_expiries = await repo.available_expiries_for_session(
                        index_id, today
                    )
                    real_snapshots = await _list_real_snapshot_instants(
                        session, index_id, today
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Replay timeline read failed: %s", type(exc).__name__)
                reason = f"REPLAY_READ_FAILED:{type(exc).__name__}"
            finally:
                if self._session is None:
                    try:
                        await session.close()
                    except Exception:  # noqa: BLE001
                        pass

        expected = _replay_grid(today, interval_minutes) if real_snapshots else []
        observed_iso = {row["iso_timestamp"] for row in real_snapshots}
        timeline_steps = []
        for index, (label, iso, is_real, spot, avail_utc) in enumerate(
            _merge_replay_steps(expected, real_snapshots)
        ):
            timeline_steps.append(
                {
                    "step_index": index,
                    "timestamp_ist": label,
                    "iso_timestamp": iso,
                    "available_at_utc": avail_utc,
                    "spot_price": spot,
                    "has_snapshot": is_real,
                    "provenance": PROV_MODEL_CALCULATED if is_real else PROV_UNAVAILABLE,
                }
            )

        return {
            "underlying": norm_sym,
            "underlying_name": spec.name,
            "session_date": today.isoformat(),
            "interval_minutes": interval_minutes,
            "timeline_steps": timeline_steps,
            "total_steps": len(timeline_steps),
            "real_step_count": len(real_snapshots),
            "timeline_source": "PERSISTED_SNAPSHOTS" if real_snapshots else "UNAVAILABLE",
            "available_sessions": available_sessions,
            "session_expiries": [d.isoformat() for d in session_expiries],
            "replay_available": bool(timeline_steps),
            "reason": reason,
            "coverage_message": (
                None
                if timeline_steps
                else (
                    "No persisted snapshots exist for this underlying and session, so "
                    "point-in-time replay cannot be reconstructed. Reason: "
                    f"{reason or 'NO_PERSISTED_OBSERVATIONS'}."
                )
            ),
            "no_lookahead_statement": (
                "Every replay step reads only observations whose available_at is at or "
                "before the replay timestamp. Later observations are never used to "
                "populate an earlier step, and missing OI is never interpolated."
            ),
            "observed_step_count": len(observed_iso),
            "provider": self._adapter.source_name,
        }

    async def get_replay_ladder(
        self,
        underlying: str,
        *,
        session_date: date,
        expiry_date: date | None = None,
        evaluation_at: datetime | None = None,
        strike_count: int | None = 5,
        call_strikes: tuple[Decimal, ...] = (),
        put_strikes: tuple[Decimal, ...] = (),
    ) -> dict[str, Any]:
        """Reconstruct the option ladder using only data visible at one instant.

        This is the Phase 7 replacement for rendering the *current* chain at every
        replay step. Every read is bounded to ``available_at <= evaluation_at``,
        so a replay at 11:00 can never see an 11:15 revision. The ladder is built
        from persisted observations only: no live quote, no fabricated strike and
        no interpolation is used to fill a gap.

        Interval change for a contract is measured between its two most recent
        PIT-visible observations. When only one observation exists the interval
        change is reported as ``None`` (unavailable) and the provider's own
        day-change (``source_change_in_oi``) is exposed separately, clearly
        labelled, because it describes the whole session rather than the interval.
        """
        norm_sym = normalize_index_symbol(underlying)
        spec = SUPPORTED_UNDERLYINGS.get(norm_sym)
        if not spec:
            raise ValueError(f"Unsupported underlying symbol '{underlying}'")

        base: dict[str, Any] = {
            "underlying": norm_sym,
            "underlying_name": spec.name,
            "session_date": session_date.isoformat(),
            "selected_expiry": expiry_date.isoformat() if expiry_date else None,
            "evaluation_at_utc": (
                evaluation_at.astimezone(UTC).isoformat() if evaluation_at else None
            ),
            "evaluation_at_ist": format_ist(evaluation_at),
            "rows": [],
            "atm_strike": None,
            "strike_count_semantics": "STRIKES_PER_SIDE",
            "window_strike_count": strike_count,
            "series": [],
            "coverage": {
                "strike_count": 0,
                "both_sides": 0,
                "call_only": 0,
                "put_only": 0,
            },
            "reason": None,
            "coverage_message": None,
            "snapshot_available": False,
            "observation_count": 0,
            "latest_visible_observation_utc": None,
            "latest_visible_observation_ist": None,
            # G.11-OPT-9 (D-01): the source_name of the records actually being
            # replayed. The page label previously showed the LIVE adapter's name
            # ("UPSTOX"), which contradicted the stored rows it was displaying.
            "record_source_names": [],
            # Carried even on an empty response: the client must be able to state
            # the guarantee for an uncovered instant as well as a covered one.
            "no_lookahead_statement": (
                "This ladder is reconstructed only from observations whose "
                "available_at is at or before the replay timestamp. Later "
                "revisions are invisible to it, missing OI is never interpolated, "
                "and no live quote is substituted for a historical one."
            ),
        }

        if evaluation_at is None:
            base["reason"] = "EVALUATION_TIME_REQUIRED"
            base["coverage_message"] = (
                "A replay timestamp is required: without one no point-in-time cut "
                "can be enforced, so no ladder is rendered."
            )
            return base

        series_list, read_meta = await self._load_real_series(
            norm_sym,
            expiry_date,
            evaluation_at=evaluation_at,
            session_date=session_date,
        )
        base["read_meta"] = read_meta
        if not series_list:
            base["reason"] = read_meta.get("reason") or "NO_PERSISTED_OBSERVATIONS"
            base["coverage_message"] = (
                "No observation with available_at at or before the replay timestamp "
                f"exists for {norm_sym} on {session_date.isoformat()} "
                f"({base['reason']}). Nothing is interpolated to fill the gap."
            )
            return base

        # ---- PIT spot: the newest snapshot that was already visible ---------
        session = await self._db_session()
        snapshots: list[dict[str, Any]] = []
        if session is not None:
            try:
                repo = OptionObservationRepository(session)
                index_id = await repo.resolve_underlying_index_id(norm_sym)
                if index_id is not None:
                    snapshots = await repo.load_visible_snapshots(
                        OptionObservationQuery(
                            underlying_index_id=index_id,
                            underlying_symbol=norm_sym,
                            session_date=session_date,
                            evaluation_at=evaluation_at,
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Replay spot read failed: %s", type(exc).__name__)
            finally:
                if self._session is None:
                    try:
                        await session.close()
                    except Exception:  # noqa: BLE001
                        pass

        spot_by_instant = {
            row["market_timestamp"]: row["spot_price"]
            for row in snapshots
            if row["spot_price"] is not None
        }
        spot = None
        spot_observed_at = None
        for row in snapshots:
            if row["spot_price"] is not None:
                spot = row["spot_price"]
                spot_observed_at = row["market_timestamp"]
        base["spot_price"] = float(spot) if spot is not None else None
        base["spot_provenance"] = (
            "PERSISTED_SNAPSHOT" if spot is not None else PROV_UNAVAILABLE
        )
        base["spot_observed_at_ist"] = format_ist(spot_observed_at)
        # G.11-OPT-9 (D-01): disclose the provenance of the RECORDS BEING REPLAYED.
        # These are read verbatim from the stored snapshots; they are not the live
        # provider feed the filter panel's own spot and source label refer to.
        base["record_source_names"] = sorted(
            {str(row["source_name"]) for row in snapshots if row.get("source_name")}
        )

        # ---- Latest PIT-visible observation per contract --------------------
        # Contracts are keyed by (strike, option type). Rebuilding the store's
        # composite string key here would risk a silent mismatch on Decimal
        # formatting, which would render an empty ladder with no explanation.
        latest: dict[tuple[Decimal | None, str | None], Any] = {}
        previous: dict[tuple[Decimal | None, str | None], Any] = {}
        counts: dict[tuple[Decimal | None, str | None], int] = {}
        by_instant: dict[datetime, list[Any]] = {}
        timestamps = sorted(
            {obs.observed_at for series in series_list for obs in series.observations}
        )
        base["observation_count"] = sum(s.count for s in series_list)
        if timestamps:
            base["latest_visible_observation_utc"] = timestamps[-1].astimezone(UTC).isoformat()
            base["latest_visible_observation_ist"] = format_ist(timestamps[-1])
            base["snapshot_available"] = True

        for series in series_list:
            for observation in series.observations:
                key = (observation.strike, observation.option_type)
                counts[key] = counts.get(key, 0) + 1
                by_instant.setdefault(observation.observed_at, []).append(observation)
                if key not in latest or observation.observed_at >= latest[key].observed_at:
                    previous[key] = latest.get(key)
                    latest[key] = observation

        # ---- Strike grid comes from the observed strikes themselves ---------
        observed_strikes = sorted(
            {key[0] for key in latest if key[0] is not None}
        )
        grid = (
            infer_strike_grid(observed_strikes, source=PROV_PERSISTED_OBSERVATION)
            if observed_strikes
            else infer_strike_grid([])
        )
        spot_for_atm = Decimal(str(spot)) if spot is not None else None
        atm_resolution = resolve_atm(spot_for_atm, grid, tie_break=DEFAULT_TIE_BREAK)
        base["atm"] = atm_resolution.to_dict()
        base["atm_strike"] = (
            float(atm_resolution.atm_strike)
            if atm_resolution.atm_strike is not None
            else None
        )
        base["strike_grid"] = {
            "strike_count": len(grid.strikes),
            "step": float(grid.step) if grid.step is not None else None,
            "is_uniform": grid.is_uniform,
            "source": grid.source,
            "description": grid.describe(),
        }

        if call_strikes or put_strikes:
            window = sorted(set(call_strikes) | set(put_strikes))
        elif atm_resolution.atm_strike is not None:
            window = select_atm_window(grid, atm_resolution.atm_strike, strike_count)
        else:
            window = tuple(observed_strikes)
        base["window_strikes"] = [float(s) for s in window]
        base["window_description"] = describe_strike_selection(
            strike_count, len(window), len(grid.strikes)
        )

        def _side(strike: Decimal, option_type: str) -> dict[str, Any]:
            key = (strike, option_type)
            current = latest.get(key)
            if current is None:
                return {
                    "available": False,
                    "reason": REASON_INSUFFICIENT_OBSERVATIONS,
                    "open_interest": None,
                    "interval_change_in_oi": None,
                    "ltp": None,
                    "interval_price_change": None,
                    "volume": None,
                    "provider_day_change_in_oi": None,
                    "buildup": classify_buildup(None, None),
                    "observed_at_ist": None,
                    "provenance": PROV_UNAVAILABLE,
                    "observation_count": 0,
                }
            prior = previous.get(key)
            oi_change = (
                current.open_interest - prior.open_interest
                if prior is not None
                and current.open_interest is not None
                and prior.open_interest is not None
                else None
            )
            ltp_change = (
                float(current.ltp - prior.ltp)
                if prior is not None and current.ltp is not None and prior.ltp is not None
                else None
            )
            return {
                "available": True,
                "reason": None,
                "open_interest": current.open_interest,
                "interval_change_in_oi": oi_change,
                "interval_change_in_oi_basis": (
                    "MEASURED_BETWEEN_PIT_VISIBLE_OBSERVATIONS"
                    if oi_change is not None
                    else "INSUFFICIENT_OBSERVATIONS"
                ),
                "ltp": float(current.ltp) if current.ltp is not None else None,
                "interval_price_change": ltp_change,
                "volume": current.volume,
                "provider_day_change_in_oi": current.source_change_in_oi,
                "buildup": classify_buildup(ltp_change, oi_change),
                "observed_at_ist": format_ist(current.observed_at),
                "observed_at_utc": current.observed_at.astimezone(UTC).isoformat(),
                "available_at_utc": current.available_at.astimezone(UTC).isoformat(),
                "provenance": current.source,
                "observation_count": counts.get(key, 0),
            }

        rows = []
        both = call_only = put_only = 0
        for strike in window:
            call_side = _side(strike, "CE")
            put_side = _side(strike, "PE")
            if call_side["available"] and put_side["available"]:
                both += 1
            elif call_side["available"]:
                call_only += 1
            elif put_side["available"]:
                put_only += 1
            rows.append(
                {
                    "strike_price": float(strike),
                    "is_atm": (
                        atm_resolution.atm_strike is not None
                        and strike == atm_resolution.atm_strike
                    ),
                    "call": call_side,
                    "put": put_side,
                }
            )
        base["rows"] = rows
        base["coverage"] = {
            "strike_count": len(rows),
            "both_sides": both,
            "call_only": call_only,
            "put_only": put_only,
        }

        # ---- PIT-safe series for the chart: one point per real instant ------
        window_set = set(window)
        chart_points = []
        for instant in sorted(by_instant):
            call_oi = put_oi = call_seen = put_seen = 0
            call_volume = put_volume = 0
            for observation in by_instant[instant]:
                if observation.strike is None or observation.strike not in window_set:
                    continue
                if observation.option_type == "CE":
                    if observation.open_interest is not None:
                        call_oi += observation.open_interest
                        call_seen += 1
                    if observation.volume is not None:
                        call_volume += observation.volume
                elif observation.option_type == "PE":
                    if observation.open_interest is not None:
                        put_oi += observation.open_interest
                        put_seen += 1
                    if observation.volume is not None:
                        put_volume += observation.volume
            instant_spot = spot_by_instant.get(instant)
            chart_points.append(
                {
                    "iso_timestamp": instant.astimezone(UTC).isoformat(),
                    "timestamp_ist": format_ist(instant),
                    "call_oi": call_oi if call_seen else None,
                    "put_oi": put_oi if put_seen else None,
                    "call_volume": call_volume if call_seen else None,
                    "put_volume": put_volume if put_seen else None,
                    "spot_price": float(instant_spot) if instant_spot is not None else None,
                }
            )
        base["series"] = chart_points
        base["provider"] = self._adapter.source_name
        return base


def calculate_max_pain(ladder_rows: list[dict[str, Any]]) -> tuple[float | None, list[dict[str, Any]]]:
    """Calculate the Max Pain strike price and total writer payout loss curve.

    Max Pain identifies the strike at which option sellers/writers suffer minimum payout.
    Loss at candidate strike S:
        TotalLoss(S) = sum_over_K [ CallOI(K) * max(0, S - K) + PutOI(K) * max(0, K - S) ]
    """
    valid_strikes = [r["strike_price"] for r in ladder_rows if r.get("strike_price") is not None]
    if not valid_strikes:
        return None, []

    loss_curve = []
    min_loss = None
    max_pain_strike = None

    for target_s in sorted(valid_strikes):
        total_payout = 0.0
        for r in ladder_rows:
            k = r["strike_price"]
            c_oi = (r.get("call") or {}).get("oi") or (r.get("call") or {}).get("open_interest") or 0
            p_oi = (r.get("put") or {}).get("oi") or (r.get("put") or {}).get("open_interest") or 0

            call_loss = c_oi * max(0.0, float(target_s) - float(k))
            put_loss = p_oi * max(0.0, float(k) - float(target_s))
            total_payout += (call_loss + put_loss)

        loss_curve.append({
            "strike_price": target_s,
            "total_payout": round(total_payout, 2),
        })

        if min_loss is None or total_payout < min_loss:
            min_loss = total_payout
            max_pain_strike = target_s

    return max_pain_strike, loss_curve
