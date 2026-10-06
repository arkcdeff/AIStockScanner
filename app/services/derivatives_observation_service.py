"""Derivatives Observation Service — Milestone G.11-OPT-3 (Phases 5, 6, 7).

Supplies the derivatives workbench with **real observations only**.

Design contract (this is the whole point of the module)
------------------------------------------------------
Every number this service returns is traceable to one of exactly four sources:

  1. ``PERSISTED_PROVIDER_OBSERVATION`` — rows in the G.11-OPT-1 tables written by
     ``OptionChainPersistenceService`` from a real provider snapshot.
  2. ``LIVE_PROVIDER_OBSERVATION`` — a read-only provider quote taken right now.
  3. ``MODEL_CALCULATED`` — arithmetic over (1) or (2), e.g. an interval delta or a
     percentage change.
  4. ``UNAVAILABLE`` — no real data exists for the request.

There is no fifth category. Nothing here synthesises a price, an open interest
value, a timestamp or a "realistic" progression. When history is missing the
caller receives an explicit insufficiency reason. In particular:

  * Interval OI change is measured by co-locating two *comparable* real
    observations, never by scaling a full-session delta by a duration ratio.
  * Point-in-time reads filter on ``available_at <= evaluation_timestamp`` so a
    replay at T can never see an observation recorded after T.
  * Missing OI is ``None`` (MISSING != ZERO) and is never forward-filled.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.derivatives_scope import (
    BASELINE_CUSTOM_RANGE,
    BASELINE_INTRADAY_INTERVAL,
    BASELINE_NONE,
    BASELINE_PREVIOUS_CLOSE,
    PROV_LIVE_OBSERVATION,
    PROV_MODEL_CALCULATED,
    PROV_PERSISTED_OBSERVATION,
    PROV_UNAVAILABLE,
    REASON_INSUFFICIENT_OBSERVATIONS,
)

logger = logging.getLogger(__name__)

IST_OFFSET = timedelta(hours=5, minutes=30)

#: An observation older than this (relative to the evaluation instant) is stale
#: and must never be classified as a genuine buildup.
DEFAULT_STALENESS_SECONDS = 300

#: Availability states used by every payload.
AVAILABILITY_OK = "OK"
AVAILABILITY_INSUFFICIENT = "INSUFFICIENT_OBSERVATIONS"
AVAILABILITY_UNAVAILABLE = "UNAVAILABLE"

#: Data-quality reasons surfaced verbatim to the UI.
ISSUE_DUPLICATE_TIMESTAMP = "DUPLICATE_TIMESTAMP"
ISSUE_OUT_OF_ORDER = "OUT_OF_ORDER_OBSERVATION"
ISSUE_STALE = "STALE_OBSERVATION"
ISSUE_MISSING_OI = "MISSING_OPEN_INTEREST"
ISSUE_MISSING_LTP = "MISSING_LTP"

# G.11-OPT-5 §5.A: shared observation-quality vocabulary. Existing issue
# constants map onto these; no parallel status model is introduced.
QUALITY_VALID = "VALID"
QUALITY_STALE = "STALE"
QUALITY_MISSING = "MISSING"
QUALITY_DUPLICATE = "DUPLICATE"
QUALITY_OUT_OF_ORDER = "OUT_OF_ORDER"
QUALITY_PROVIDER_ERROR = "PROVIDER_ERROR"
QUALITY_INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
QUALITY_CONTRACT_UNRESOLVED = "CONTRACT_UNRESOLVED"


def quality_status_for_series(series: ObservationSeries) -> str:
    """Map a series' existing audit issues onto the shared quality vocabulary."""
    issues = set(series.issues or ())
    if ISSUE_OUT_OF_ORDER in issues:
        return QUALITY_OUT_OF_ORDER
    if ISSUE_DUPLICATE_TIMESTAMP in issues:
        return QUALITY_DUPLICATE
    if ISSUE_STALE in issues:
        return QUALITY_STALE
    if not series.is_available:
        return QUALITY_INSUFFICIENT_HISTORY
    if any(
        obs.open_interest is None or obs.ltp is None for obs in series.observations
    ):
        return QUALITY_MISSING
    return QUALITY_VALID


def quality_status_for_delta(delta: IntervalDelta) -> str:
    """Map an interval delta's availability/issues onto the quality vocabulary."""
    if delta.availability != AVAILABILITY_OK:
        reason = str(delta.reason or "")
        if "UNRESOLVED" in reason:
            return QUALITY_CONTRACT_UNRESOLVED
        return QUALITY_INSUFFICIENT_HISTORY
    issues = set(delta.issues or ())
    if ISSUE_OUT_OF_ORDER in issues:
        return QUALITY_OUT_OF_ORDER
    if ISSUE_DUPLICATE_TIMESTAMP in issues:
        return QUALITY_DUPLICATE
    if ISSUE_STALE in issues:
        return QUALITY_STALE
    if ISSUE_MISSING_OI in issues or ISSUE_MISSING_LTP in issues:
        return QUALITY_MISSING
    return QUALITY_VALID


def quality_summary(series_list: Sequence[ObservationSeries]) -> dict[str, Any]:
    """Aggregate series-level quality statuses into one compact block.

    The overall status is the worst observed among series that actually carry
    observations (OUT_OF_ORDER and DUPLICATE beat STALE, which beats MISSING,
    which beats a clean VALID series). Series that merely lack history are
    counted but only drive the overall verdict when no observed series exists
    at all — missing history must not contaminate the verdict of data that is
    present and clean.
    """
    counts: dict[str, int] = {}
    for series in series_list:
        status = quality_status_for_series(series)
        counts[status] = counts.get(status, 0) + 1
    severity = [
        QUALITY_OUT_OF_ORDER,
        QUALITY_DUPLICATE,
        QUALITY_STALE,
        QUALITY_MISSING,
        QUALITY_CONTRACT_UNRESOLVED,
        QUALITY_PROVIDER_ERROR,
        QUALITY_VALID,
    ]
    observed = {
        status: count for status, count in counts.items() if count and status != QUALITY_INSUFFICIENT_HISTORY
    }
    if observed:
        overall: str = next(s for s in severity if observed.get(s))
    else:
        overall = QUALITY_INSUFFICIENT_HISTORY
    return {
        "overall": overall,
        "counts": counts,
        "series_count": len(series_list),
    }


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def to_ist(value: datetime | None) -> datetime | None:
    """Convert a UTC instant to IST for display."""
    return None if value is None else _as_utc(value) + IST_OFFSET


def format_ist(value: datetime | None, with_date: bool = False) -> str | None:
    """IST display string used consistently across workbench tables."""
    if value is None:
        return None
    ist = to_ist(value)
    assert ist is not None
    return ist.strftime("%d %b %Y %H:%M:%S") if with_date else ist.strftime("%H:%M:%S")


def format_ist_short(value: datetime | None) -> str | None:
    if value is None:
        return None
    ist = to_ist(value)
    assert ist is not None
    return ist.strftime("%H:%M")


def ist_trade_date(value: datetime) -> date:
    """Indian trading date for a UTC market timestamp.

    ``option_chain_snapshots`` deliberately carries no ``trade_date`` column: the
    session date is derived from the market timestamp in the exchange timezone,
    which is the same convention the OPT-1 persistence service uses.
    """
    ist = to_ist(value)
    assert ist is not None
    return ist.date()


# ===========================================================================
# 1. Observation value objects
# ===========================================================================

@dataclass(frozen=True)
class ContractObservation:
    """A single real observation of one derivative contract."""

    contract_key: str
    label: str
    observed_at: datetime
    available_at: datetime
    source: str
    strike: Decimal | None = None
    option_type: str | None = None
    expiry: date | None = None
    instrument_key: str | None = None
    contract_id: int | None = None
    ltp: Decimal | None = None
    open_interest: int | None = None
    volume: int | None = None
    source_change_in_oi: int | None = None
    lot_size: int | None = None
    quality_flags: tuple[str, ...] = ()
    snapshot_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_key": self.contract_key,
            "label": self.label,
            "observed_at_utc": _as_utc(self.observed_at).isoformat(),
            "observed_at_ist": format_ist(self.observed_at),
            "available_at_utc": _as_utc(self.available_at).isoformat(),
            "available_at_ist": format_ist(self.available_at),
            "source": self.source,
            "strike_price": float(self.strike) if self.strike is not None else None,
            "option_type": self.option_type,
            "expiry_date": self.expiry.isoformat() if self.expiry else None,
            "instrument_key": self.instrument_key,
            "ltp": float(self.ltp) if self.ltp is not None else None,
            "open_interest": self.open_interest,
            "volume": self.volume,
            "source_change_in_oi": self.source_change_in_oi,
            "quality_flags": list(self.quality_flags),
            "snapshot_id": self.snapshot_id,
        }


@dataclass(frozen=True)
class IntervalDelta:
    """A delta measured between two real, comparable observations."""

    contract_key: str
    label: str
    interval_minutes: int
    baseline_provenance: str
    availability: str
    start_at: datetime | None = None
    end_at: datetime | None = None
    effective_seconds: float | None = None
    tolerance_seconds: float | None = None
    within_tolerance: bool | None = None
    oi_start: int | None = None
    oi_end: int | None = None
    oi_change: int | None = None
    ltp_start: Decimal | None = None
    ltp_end: Decimal | None = None
    ltp_change: Decimal | None = None
    ltp_change_pct: Decimal | None = None
    volume_start: int | None = None
    volume_end: int | None = None
    volume_change: int | None = None
    reason: str | None = None
    issues: tuple[str, ...] = ()

    @property
    def is_available(self) -> bool:
        return self.availability == AVAILABILITY_OK

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_key": self.contract_key,
            "label": self.label,
            "interval_minutes": self.interval_minutes,
            "baseline_provenance": self.baseline_provenance,
            "availability": self.availability,
            "start_at_utc": _as_utc(self.start_at).isoformat() if self.start_at else None,
            "end_at_utc": _as_utc(self.end_at).isoformat() if self.end_at else None,
            "start_at_ist": format_ist(self.start_at),
            "end_at_ist": format_ist(self.end_at),
            "effective_seconds": self.effective_seconds,
            "effective_label": (
                f"{self.effective_seconds / 60.0:.1f}m" if self.effective_seconds else None
            ),
            "tolerance_seconds": self.tolerance_seconds,
            "within_tolerance": self.within_tolerance,
            "oi_start": self.oi_start,
            "oi_end": self.oi_end,
            "oi_change": self.oi_change,
            "ltp_start": float(self.ltp_start) if self.ltp_start is not None else None,
            "ltp_end": float(self.ltp_end) if self.ltp_end is not None else None,
            "ltp_change": float(self.ltp_change) if self.ltp_change is not None else None,
            "ltp_change_pct": (
                float(self.ltp_change_pct) if self.ltp_change_pct is not None else None
            ),
            "volume_start": self.volume_start,
            "volume_end": self.volume_end,
            "volume_change": self.volume_change,
            "reason": self.reason,
            "issues": list(self.issues),
            "quality_status": quality_status_for_delta(self),
        }


@dataclass
class ObservationSeries:
    """Ordered real observations for one contract, with coverage diagnostics."""

    contract_key: str
    label: str
    observations: list[ContractObservation] = field(default_factory=list)
    source: str = PROV_UNAVAILABLE
    reason: str | None = None
    issues: tuple[str, ...] = ()

    @property
    def is_available(self) -> bool:
        return bool(self.observations)

    @property
    def count(self) -> int:
        return len(self.observations)

    def first(self) -> ContractObservation | None:
        return self.observations[0] if self.observations else None

    def last(self) -> ContractObservation | None:
        return self.observations[-1] if self.observations else None

    def coverage(self) -> dict[str, Any]:
        first, last = self.first(), self.last()
        return {
            "contract_key": self.contract_key,
            "label": self.label,
            "observation_count": self.count,
            "source": self.source,
            "available": self.is_available,
            "reason": self.reason,
            "quality_status": quality_status_for_series(self),
            "issues": list(self.issues),
            "first_observation_ist": format_ist(first.observed_at) if first else None,
            "last_observation_ist": format_ist(last.observed_at) if last else None,
            "last_observation_utc": (
                _as_utc(last.observed_at).isoformat() if last else None
            ),
            "observation_age_seconds": (
                max(0.0, (datetime.now(UTC) - _as_utc(last.observed_at)).total_seconds())
                if last
                else None
            ),
            "coverage_message": (
                None
                if self.is_available
                else (
                    f"No valid observations for {self.label}. "
                    f"Reason: {self.reason or REASON_INSUFFICIENT_OBSERVATIONS}."
                )
            ),
        }


# ===========================================================================
# 2. Series hygiene
# ===========================================================================

def order_and_audit(observations: Sequence[ContractObservation]) -> tuple[list[ContractObservation], tuple[str, ...]]:
    """Sort observations and report duplicate/out-of-order/missing data.

    Duplicates are collapsed to the latest-available copy of the same instant.
    Ordering is by ``observed_at``; ``available_at`` is never rewritten.
    """
    issues: set[str] = set()
    ordered = sorted(observations, key=lambda o: (_as_utc(o.observed_at), _as_utc(o.available_at)))
    if list(observations) != ordered:
        issues.add(ISSUE_OUT_OF_ORDER)

    deduped: dict[datetime, ContractObservation] = {}
    for obs in ordered:
        key = _as_utc(obs.observed_at)
        if key in deduped:
            issues.add(ISSUE_DUPLICATE_TIMESTAMP)
            if _as_utc(obs.available_at) >= _as_utc(deduped[key].available_at):
                deduped[key] = obs
        else:
            deduped[key] = obs

    final = [deduped[k] for k in sorted(deduped)]
    for obs in final:
        if obs.open_interest is None:
            issues.add(ISSUE_MISSING_OI)
        if obs.ltp is None:
            issues.add(ISSUE_MISSING_LTP)
    return final, tuple(sorted(issues))


def mark_stale_issues(
    observations: Sequence[ContractObservation],
    evaluation_at: datetime,
    staleness_seconds: int = DEFAULT_STALENESS_SECONDS,
) -> dict[str, tuple[str, ...]]:
    """Per-contract staleness flags relative to an evaluation instant."""
    out: dict[str, tuple[str, ...]] = {}
    for obs in observations:
        age = (_as_utc(evaluation_at) - _as_utc(obs.observed_at)).total_seconds()
        if age > staleness_seconds:
            out[obs.contract_key] = (ISSUE_STALE,)
    return out


# ===========================================================================
# 3. Interval engine — real deltas, no pro-rata
# ===========================================================================

def tolerance_seconds_for(interval_minutes: int, ratio: float | None = None) -> float:
    """Maximum tolerated lag when co-locating the baseline observation.

    Defaults to one third of the requested interval, so a "3-minute" change can
    never silently be computed from a 30-minute gap.
    """
    effective_ratio = (
        ratio
        if ratio is not None
        else float(getattr(settings, "derivatives_interval_tolerance_ratio", 0.34))
    )
    return max(30.0, interval_minutes * 60.0 * effective_ratio)


def locate_baseline(
    ordered: Sequence[ContractObservation],
    target_at: datetime,
    tolerance_seconds: float,
) -> ContractObservation | None:
    """Find the real observation closest to ``target_at`` within tolerance.

    Never interpolates and never forward-fills: an observation outside tolerance
    is treated as absent so the caller reports insufficient data instead of
    inventing a baseline.
    """
    target = _as_utc(target_at)
    best: ContractObservation | None = None
    best_delta = None
    for obs in ordered:
        at = _as_utc(obs.observed_at)
        if at > target:
            break
        delta = (target - at).total_seconds()
        if delta <= tolerance_seconds and (best_delta is None or delta < best_delta):
            best, best_delta = obs, delta
    return best


def _delta_between(
    start: ContractObservation,
    end: ContractObservation,
    *,
    interval_minutes: int,
    baseline_provenance: str,
    tolerance_seconds: float | None,
) -> IntervalDelta:
    """Arithmetic over two real observations. Missing values stay missing."""
    oi_change: int | None = None
    if start.open_interest is not None and end.open_interest is not None:
        oi_change = end.open_interest - start.open_interest

    ltp_change: Decimal | None = None
    ltp_pct: Decimal | None = None
    if start.ltp is not None and end.ltp is not None:
        ltp_change = end.ltp - start.ltp
        if start.ltp > 0:
            ltp_pct = (ltp_change / start.ltp) * Decimal("100")

    volume_change: int | None = None
    if start.volume is not None and end.volume is not None:
        volume_change = end.volume - start.volume

    effective = (_as_utc(end.observed_at) - _as_utc(start.observed_at)).total_seconds()
    issues: list[str] = []
    if end.open_interest is None or start.open_interest is None:
        issues.append(ISSUE_MISSING_OI)

    return IntervalDelta(
        contract_key=end.contract_key,
        label=end.label,
        interval_minutes=interval_minutes,
        baseline_provenance=baseline_provenance,
        availability=AVAILABILITY_OK,
        start_at=start.observed_at,
        end_at=end.observed_at,
        effective_seconds=effective,
        tolerance_seconds=tolerance_seconds,
        within_tolerance=(
            None
            if tolerance_seconds is None
            else effective <= (interval_minutes * 60.0 + tolerance_seconds)
        ),
        oi_start=start.open_interest,
        oi_end=end.open_interest,
        oi_change=oi_change,
        ltp_start=start.ltp,
        ltp_end=end.ltp,
        ltp_change=ltp_change,
        ltp_change_pct=ltp_pct,
        volume_start=start.volume,
        volume_end=end.volume,
        volume_change=volume_change,
        issues=tuple(issues),
    )


def compute_interval_deltas(
    series: ObservationSeries,
    interval_minutes: int,
    *,
    tolerance_ratio: float | None = None,
    evaluation_at: datetime | None = None,
) -> list[IntervalDelta]:
    """Real interval deltas for one contract's observation series.

    For each observation after the first, the baseline is the real observation
    closest to ``observed_at - interval`` inside tolerance. Steps whose baseline
    does not exist are returned as ``INSUFFICIENT_OBSERVATIONS`` rather than
    being filled with a scaled or previous-close value.
    """
    tolerance = tolerance_seconds_for(interval_minutes, tolerance_ratio)
    ordered = series.observations
    if not ordered:
        return [
            IntervalDelta(
                contract_key=series.contract_key,
                label=series.label,
                interval_minutes=interval_minutes,
                baseline_provenance=BASELINE_INTRADAY_INTERVAL,
                availability=AVAILABILITY_INSUFFICIENT,
                tolerance_seconds=tolerance,
                reason=series.reason or REASON_INSUFFICIENT_OBSERVATIONS,
            )
        ]

    cutoff = _as_utc(evaluation_at) if evaluation_at else None
    deltas: list[IntervalDelta] = []
    for end in ordered:
        if cutoff is not None and _as_utc(end.observed_at) > cutoff:
            continue
        target = _as_utc(end.observed_at) - timedelta(minutes=interval_minutes)
        baseline = locate_baseline(ordered, target, tolerance)
        if baseline is None:
            continue
        if _as_utc(baseline.observed_at) >= _as_utc(end.observed_at):
            continue
        deltas.append(
            _delta_between(
                baseline,
                end,
                interval_minutes=interval_minutes,
                baseline_provenance=BASELINE_INTRADAY_INTERVAL,
                tolerance_seconds=tolerance,
            )
        )
    return deltas


def compute_range_delta(
    series: ObservationSeries,
    *,
    start_at: datetime,
    end_at: datetime,
    tolerance_ratio: float | None = None,
) -> IntervalDelta:
    """Real delta across a user-selected custom window (Phase 6 Custom Range)."""
    ordered = series.observations
    span_minutes = max(
        1, int(round((_as_utc(end_at) - _as_utc(start_at)).total_seconds() / 60.0))
    )
    tolerance = tolerance_seconds_for(span_minutes, tolerance_ratio)
    if not ordered:
        return IntervalDelta(
            contract_key=series.contract_key,
            label=series.label,
            interval_minutes=span_minutes,
            baseline_provenance=BASELINE_CUSTOM_RANGE,
            availability=AVAILABILITY_INSUFFICIENT,
            tolerance_seconds=tolerance,
            reason=series.reason or REASON_INSUFFICIENT_OBSERVATIONS,
        )

    start_obs = locate_baseline(
        ordered, _as_utc(start_at) + timedelta(seconds=tolerance), tolerance
    )
    end_obs = locate_baseline(
        ordered, _as_utc(end_at) + timedelta(seconds=tolerance), tolerance
    )
    if start_obs is None or end_obs is None or start_obs is end_obs:
        return IntervalDelta(
            contract_key=series.contract_key,
            label=series.label,
            interval_minutes=span_minutes,
            baseline_provenance=BASELINE_CUSTOM_RANGE,
            availability=AVAILABILITY_INSUFFICIENT,
            tolerance_seconds=tolerance,
            reason="NO_COMPARABLE_OBSERVATIONS_IN_RANGE",
            start_at=start_obs.observed_at if start_obs else None,
            end_at=end_obs.observed_at if end_obs else None,
        )
    return _delta_between(
        start_obs,
        end_obs,
        interval_minutes=span_minutes,
        baseline_provenance=BASELINE_CUSTOM_RANGE,
        tolerance_seconds=tolerance,
    )


def previous_close_delta(series: ObservationSeries) -> IntervalDelta:
    """Full-session change against the earliest real observation of the session.

    Provenance is reported as ``PREVIOUS_CLOSE`` only when a genuine prior-session
    mark exists; otherwise it is labelled by what it actually is — the earliest
    observation in the stored series — so a 3-minute request can never be
    silently served from a session-open baseline.
    """
    ordered = series.observations
    if len(ordered) < 2:
        return IntervalDelta(
            contract_key=series.contract_key,
            label=series.label,
            interval_minutes=0,
            baseline_provenance=BASELINE_PREVIOUS_CLOSE,
            availability=AVAILABILITY_INSUFFICIENT,
            reason=series.reason or REASON_INSUFFICIENT_OBSERVATIONS,
        )
    return _delta_between(
        ordered[0],
        ordered[-1],
        interval_minutes=0,
        baseline_provenance=BASELINE_PREVIOUS_CLOSE,
        tolerance_seconds=None,
    )


# ===========================================================================
# 4. Persisted option observations (PIT-safe)
# ===========================================================================

@dataclass
class OptionObservationQuery:
    """Filter for a persisted option observation read."""

    underlying_index_id: int | None = None
    underlying_symbol: str | None = None
    session_date: date | None = None
    expiry: date | None = None
    call_strikes: tuple[Decimal, ...] = ()
    put_strikes: tuple[Decimal, ...] = ()
    strike_min: Decimal | None = None
    strike_max: Decimal | None = None
    evaluation_at: datetime | None = None
    limit_observations: int = 5000


class OptionObservationRepository:
    """Read-only, point-in-time-safe access to persisted option observations.

    Only rows whose ``available_at`` is at or before the evaluation instant are
    ever returned, so replay cannot leak a later revision. ``OptionChainSnapshot``
    revisions are resolved by taking the newest PIT-visible revision per
    ``market_timestamp``.
    """

    def __init__(self, session: Any) -> None:
        self._session = session

    async def resolve_underlying_index_id(self, symbol: str) -> int | None:
        from sqlalchemy import select

        from app.models.market_index import MarketIndex
        from app.services.derivatives_scope import get_underlying

        spec = get_underlying(symbol)
        codes = {symbol.strip().upper()}
        if spec is not None:
            codes.add(spec.symbol)
            codes.add(spec.name.upper())
            if getattr(spec, "index_instrument_key", None):
                key_part = spec.index_instrument_key.split("|")[-1]
                codes.add(key_part)
                codes.add(key_part.upper())
        stmt = select(MarketIndex.id).where(
            (MarketIndex.code.in_(codes))
            | (MarketIndex.name.in_(codes))
            | (MarketIndex.provider_symbol.in_(codes))
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def available_sessions(self, underlying_index_id: int) -> list[dict[str, Any]]:
        """Real session dates that actually have persisted snapshots."""
        from sqlalchemy import select

        from app.models.option_chain import OptionChainSnapshot

        stmt = (
            select(
                OptionChainSnapshot.market_timestamp,
                OptionChainSnapshot.available_at,
            )
            .where(OptionChainSnapshot.underlying_index_id == underlying_index_id)
            .order_by(OptionChainSnapshot.market_timestamp)
        )
        rows = (await self._session.execute(stmt)).all()

        # Derive the IST session date from the market timestamp, exactly as the
        # OPT-1 persistence service does (there is no stored trade_date column).
        buckets: dict[date, dict[str, Any]] = {}
        seen: set[tuple[date, datetime]] = set()
        for market_ts, _available_at in rows:
            session = ist_trade_date(market_ts)
            key = (session, market_ts)
            bucket = buckets.setdefault(
                session,
                {
                    "trade_date": session.isoformat(),
                    "snapshot_count": 0,
                    "first_market_timestamp_utc": market_ts.isoformat(),
                    "last_market_timestamp_utc": market_ts.isoformat(),
                    "first_observation_ist": format_ist(market_ts),
                    "last_observation_ist": format_ist(market_ts),
                },
            )
            if key not in seen:
                seen.add(key)
                bucket["snapshot_count"] += 1
                bucket["last_market_timestamp_utc"] = market_ts.isoformat()
                bucket["last_observation_ist"] = format_ist(market_ts)

        return [buckets[d] for d in sorted(buckets, reverse=True)]

    async def available_expiries_for_session(
        self, underlying_index_id: int, session_date: date
    ) -> list[date]:
        """Real distinct contract expiries that exist for this session date."""
        from sqlalchemy import select

        from app.models.option_chain import OptionChainQuote, OptionChainSnapshot

        snap_stmt = (
            select(OptionChainSnapshot.expiry_scope)
            .where(
                OptionChainSnapshot.underlying_index_id == underlying_index_id,
                OptionChainSnapshot.expiry_scope.isnot(None),
            )
            .distinct()
        )
        snap_rows = (await self._session.execute(snap_stmt)).scalars().all()
        quote_stmt = (
            select(OptionChainQuote.expiry_date)
            .where(OptionChainQuote.trade_date == session_date)
            .distinct()
        )
        quote_rows = (await self._session.execute(quote_stmt)).scalars().all()
        combined = set(snap_rows) | set(quote_rows)
        return sorted([d for d in combined if d is not None])

    async def _visible_snapshot_rows(self, query: OptionObservationQuery) -> list[tuple[Any, ...]]:
        """Snapshot rows visible at ``evaluation_at``, one per market timestamp.

        Point-in-time safe by construction: a snapshot whose ``available_at`` is
        after the evaluation instant did not exist yet at replay time and is
        never returned. Where several revisions share a ``market_timestamp`` the
        newest revision that was already available wins.
        """
        from sqlalchemy import select

        from app.models.option_chain import OptionChainSnapshot

        if query.underlying_index_id is None:
            return []

        snapshot_stmt = select(
            OptionChainSnapshot.id,
            OptionChainSnapshot.market_timestamp,
            OptionChainSnapshot.available_at,
            OptionChainSnapshot.source_name,
            OptionChainSnapshot.quality_flags,
            OptionChainSnapshot.revision,
            OptionChainSnapshot.spot_price,
        ).where(OptionChainSnapshot.underlying_index_id == query.underlying_index_id)

        if query.evaluation_at is not None:
            snapshot_stmt = snapshot_stmt.where(
                OptionChainSnapshot.available_at <= _as_utc(query.evaluation_at)
            )
        snapshot_stmt = snapshot_stmt.order_by(OptionChainSnapshot.market_timestamp)
        rows = (await self._session.execute(snapshot_stmt)).all()

        if query.session_date is not None:
            # The session date is derived from the market timestamp in IST; it is
            # never assumed to equal the ingestion date or the query date.
            rows = [r for r in rows if ist_trade_date(r[1]) == query.session_date]

        best: dict[datetime, tuple[Any, ...]] = {}
        for row in rows:
            key = row[1]
            current = best.get(key)
            if current is None or row[5] >= current[5]:
                best[key] = row
        return [best[key] for key in sorted(best)]

    async def load_visible_snapshots(
        self, query: OptionObservationQuery
    ) -> list[dict[str, Any]]:
        """PIT-visible snapshots with their real underlying spot price.

        Used by replay to read the spot that was genuinely observable at the
        replay instant instead of today's live quote.
        """
        rows = await self._visible_snapshot_rows(query)
        return [
            {
                "snapshot_id": row[0],
                "market_timestamp": row[1],
                "market_timestamp_utc": _as_utc(row[1]).isoformat(),
                "market_timestamp_ist": format_ist(row[1]),
                "available_at": row[2],
                "available_at_utc": _as_utc(row[2]).isoformat(),
                "source_name": row[3],
                "quality_flags": tuple(sorted((row[4] or {}).keys())),
                "revision": row[5],
                "spot_price": _dec(row[6]),
            }
            for row in rows
        ]

    async def load_series(
        self, query: OptionObservationQuery
    ) -> tuple[list[ObservationSeries], dict[str, Any]]:
        """Load real observations grouped by option contract."""
        from sqlalchemy import select

        from app.models.option_chain import OptionChainQuote

        if query.underlying_index_id is None:
            return [], {
                "available": False,
                "reason": "UNDERLYING_NOT_RESOLVED",
                "source": PROV_UNAVAILABLE,
            }

        snapshots = await self._visible_snapshot_rows(query)
        if not snapshots:
            return [], {
                "available": False,
                "reason": "NO_PERSISTED_OBSERVATIONS",
                "source": PROV_PERSISTED_OBSERVATION,
                "evaluation_at_utc": (
                    _as_utc(query.evaluation_at).isoformat() if query.evaluation_at else None
                ),
                "session_date": (
                    query.session_date.isoformat() if query.session_date else None
                ),
            }

        snapshot_ids = [row[0] for row in snapshots]
        meta_by_id = {row[0]: row for row in snapshots}

        quote_stmt = select(OptionChainQuote).where(
            OptionChainQuote.snapshot_id.in_(snapshot_ids)
        )
        if query.session_date is not None:
            quote_stmt = quote_stmt.where(OptionChainQuote.trade_date == query.session_date)
        if query.expiry is not None:
            quote_stmt = quote_stmt.where(OptionChainQuote.expiry_date == query.expiry)
        if query.strike_min is not None:
            quote_stmt = quote_stmt.where(OptionChainQuote.strike_price >= query.strike_min)
        if query.strike_max is not None:
            quote_stmt = quote_stmt.where(OptionChainQuote.strike_price <= query.strike_max)
        if query.call_strikes:
            quote_stmt = quote_stmt.where(OptionChainQuote.strike_price.in_(query.call_strikes))
            if query.put_strikes:
                quote_stmt = quote_stmt.where(
                    OptionChainQuote.strike_price.in_(set(query.call_strikes) | set(query.put_strikes))
                )
        elif query.put_strikes:
            quote_stmt = quote_stmt.where(OptionChainQuote.strike_price.in_(query.put_strikes))

        quotes = (await self._session.execute(quote_stmt)).scalars().all()

        grouped: dict[str, ObservationSeries] = {}
        for quote in quotes:
            meta = meta_by_id.get(quote.snapshot_id)
            if meta is None:
                continue
            key = _contract_key(quote.strike_price, quote.option_type, quote.expiry_date)
            series = grouped.get(key)
            if series is None:
                series = ObservationSeries(
                    contract_key=key,
                    label=_contract_label(
                        query.underlying_symbol, quote.strike_price, quote.option_type, quote.expiry_date
                    ),
                    source=PROV_PERSISTED_OBSERVATION,
                )
                grouped[key] = series
            series.observations.append(
                ContractObservation(
                    contract_key=key,
                    label=series.label,
                    observed_at=meta[1],
                    available_at=meta[2],
                    source=PROV_PERSISTED_OBSERVATION,
                    strike=_dec(quote.strike_price),
                    option_type=quote.option_type,
                    expiry=quote.expiry_date,
                    contract_id=quote.contract_id,
                    ltp=_dec(quote.ltp),
                    open_interest=quote.open_interest,
                    volume=quote.volume,
                    source_change_in_oi=quote.source_change_in_oi,
                    quality_flags=tuple(sorted((meta[4] or {}).keys())),
                    snapshot_id=quote.snapshot_id,
                )
            )

        out: list[ObservationSeries] = []
        for series in grouped.values():
            ordered, issues = order_and_audit(series.observations)
            series.observations = ordered[: query.limit_observations]
            series.issues = issues
            if not series.is_available:
                series.reason = REASON_INSUFFICIENT_OBSERVATIONS
            out.append(series)
        out.sort(key=lambda s: (s.label, s.contract_key))

        return out, {
            "available": bool(out),
            "reason": None if out else "NO_QUOTES_FOR_SELECTED_SCOPE",
            "source": PROV_PERSISTED_OBSERVATION,
            "snapshot_count": len(snapshot_ids),
            "observation_count": sum(s.count for s in out),
            "quality_summary": quality_summary(out),
            "session_date": (
                query.session_date.isoformat() if query.session_date else None
            ),
            "evaluation_at_utc": (
                _as_utc(query.evaluation_at).isoformat() if query.evaluation_at else None
            ),
        }


def _contract_key(strike: Any, option_type: str | None, expiry: date | None) -> str:
    strike_txt = str(strike) if strike is not None else "?"
    expiry_txt = expiry.isoformat() if expiry else "?"
    return f"{expiry_txt}:{strike_txt}:{option_type or '?'}"


def _contract_label(
    underlying: str | None, strike: Any, option_type: str | None, expiry: date | None
) -> str:
    symbol = (underlying or "?").upper()
    expiry_txt = expiry.strftime("%d%b%y").upper() if expiry else "?"
    strike_txt = f"{_dec(strike):,.0f}" if strike is not None else "?"
    return f"{symbol} {expiry_txt} {strike_txt} {option_type or '?'}"


# ===========================================================================
# 5. Futures observations (real provider reads + accumulated real captures)
# ===========================================================================

@dataclass(frozen=True)
class FuturesObservation:
    """One real futures observation from the authorized provider."""

    instrument_key: str
    trading_symbol: str
    underlying: str
    expiry: date | None
    observed_at: datetime
    received_at: datetime
    source: str
    last_price: Decimal | None = None
    previous_close: Decimal | None = None
    day_open: Decimal | None = None
    day_high: Decimal | None = None
    day_low: Decimal | None = None
    volume: int | None = None
    open_interest: int | None = None
    oi_day_high: int | None = None
    oi_day_low: int | None = None
    net_change: Decimal | None = None
    total_buy_quantity: int | None = None
    total_sell_quantity: int | None = None
    lot_size: int | None = None
    underlying_spot: Decimal | None = None
    underlying_spot_observed_at: datetime | None = None
    quality_flags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument_key": self.instrument_key,
            "trading_symbol": self.trading_symbol,
            "underlying": self.underlying,
            "expiry_date": self.expiry.isoformat() if self.expiry else None,
            "observed_at_utc": _as_utc(self.observed_at).isoformat(),
            "observed_at_ist": format_ist(self.observed_at),
            "received_at_utc": _as_utc(self.received_at).isoformat(),
            "source": self.source,
            "last_price": float(self.last_price) if self.last_price is not None else None,
            "previous_close": (
                float(self.previous_close) if self.previous_close is not None else None
            ),
            "day_open": float(self.day_open) if self.day_open is not None else None,
            "day_high": float(self.day_high) if self.day_high is not None else None,
            "day_low": float(self.day_low) if self.day_low is not None else None,
            "volume": self.volume,
            "open_interest": self.open_interest,
            "oi_day_high": self.oi_day_high,
            "oi_day_low": self.oi_day_low,
            "net_change": float(self.net_change) if self.net_change is not None else None,
            "total_buy_quantity": self.total_buy_quantity,
            "total_sell_quantity": self.total_sell_quantity,
            "lot_size": self.lot_size,
            "underlying_spot": (
                float(self.underlying_spot) if self.underlying_spot is not None else None
            ),
            "underlying_spot_observed_at_ist": (
                format_ist(self.underlying_spot_observed_at)
                if self.underlying_spot_observed_at
                else None
            ),
            "quality_flags": list(self.quality_flags),
        }


class FuturesObservationStore:
    """Append-only store of **real** futures observations captured by an operator.

    The provider serves a current futures quote but no historical OI series, so
    interval futures OI deltas can only ever be computed from observations that
    were actually recorded. This store holds exactly those, newest last, one JSON
    document per underlying+session.

    Writes happen only when
    ``settings.derivatives_observation_capture_enabled`` is true, and this
    milestone never enables it: no ingestion is performed.
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base_dir = Path(
            base_dir
            or getattr(settings, "derivatives_observation_dir", "data/derivatives_observations")
        )

    @property
    def enabled(self) -> bool:
        return bool(getattr(settings, "derivatives_observation_capture_enabled", False))

    def path_for(self, underlying: str, session_date: date) -> Path:
        return self._base_dir / f"{underlying.upper()}_{session_date.isoformat()}.jsonl"

    def get_storage_usage(self) -> int:
        """Calculate total bytes occupied by stored observations."""
        if not self._base_dir.exists():
            return 0
        total = 0
        for item in self._base_dir.glob("*.jsonl"):
            try:
                total += item.stat().st_size
            except OSError:
                continue
        return total

    def summarize_history(self) -> dict[str, Any]:
        """Durable-history summary derived from the JSONL store.

        G.11-OPT-5 (D2/D7): in-memory capture telemetry resets on application
        restart while the append-only files persist. This reconstructs the
        observation count, most recent observation instant, per-underlying
        coverage, and the number of malformed (torn/corrupt) records that were
        skipped, so capture status can report disk truth after a restart.
        Read-only; never rewrites observation files.
        """
        if not self._base_dir.exists():
            return {
                "total_observations": 0,
                "files": 0,
                "malformed_records": 0,
                "last_observation_utc": None,
                "last_observation_ist": None,
                "by_underlying": {},
            }
        total = 0
        malformed = 0
        files = 0
        last_dt: datetime | None = None
        by_underlying: dict[str, dict[str, Any]] = {}
        for path in self._base_dir.glob("*.jsonl"):
            files += 1
            stem_parts = path.stem.split("_")
            underlying = stem_parts[0].upper() if stem_parts else "UNKNOWN"
            bucket = by_underlying.setdefault(
                underlying,
                {"observations": 0, "malformed_records": 0, "last_observation_utc": None},
            )
            try:
                handle = path.open("r", encoding="utf-8")
            except OSError:
                continue
            with handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:  # noqa: BLE001 - torn/corrupt record
                        malformed += 1
                        bucket["malformed_records"] += 1
                        continue
                    total += 1
                    bucket["observations"] += 1
                    ts_txt = str(row.get("observed_at_utc") or "")
                    if ts_txt > str(bucket["last_observation_utc"] or ""):
                        bucket["last_observation_utc"] = ts_txt
                    if ts_txt > str(last_dt.isoformat() if last_dt else ""):
                        try:
                            last_dt = datetime.fromisoformat(ts_txt)
                        except Exception:  # noqa: BLE001
                            last_dt = None
        return {
            "total_observations": total,
            "files": files,
            "malformed_records": malformed,
            "last_observation_utc": last_dt.isoformat() if last_dt else None,
            "last_observation_ist": (
                format_ist(last_dt, with_date=True) if last_dt else None
            ),
            "by_underlying": by_underlying,
        }

    def append(self, observation: FuturesObservation, *, force_enabled: bool = False) -> bool:
        """Persist one real observation. Refuses when capture is disabled unless explicitly forced by an authorized session."""
        if not self.enabled and not force_enabled:
            logger.warning(
                "Futures observation capture is disabled; refusing to persist %s",
                observation.instrument_key,
            )
            return False
        session_date = to_ist(observation.observed_at)
        assert session_date is not None
        path = self.path_for(observation.underlying, session_date.date())
        obs_dict = observation.to_dict()
        obs_utc = obs_dict.get("observed_at_utc")

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Deduplication: check if this instrument + timestamp already exists
            if path.is_file():
                with path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        if not line.strip():
                            continue
                        try:
                            existing = json.loads(line)
                            if (
                                existing.get("instrument_key") == observation.instrument_key
                                and existing.get("observed_at_utc") == obs_utc
                            ):
                                return True  # Already recorded; idempotent success
                        except Exception:  # noqa: BLE001
                            continue

            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(obs_dict, separators=(",", ":")) + "\n")
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not persist futures observation: %s", type(exc).__name__)
            return False

    def append_with_result(
        self, observation: FuturesObservation, *, force_enabled: bool = False
    ) -> tuple[bool, bool]:
        """Append one observation and report ``(appended, duplicate)``.

        G.11-OPT-5 (D3): ``append()`` is idempotent for duplicates but callers
        could not distinguish "stored" from "already stored", which inflated
        the capture telemetry. This variant pre-checks the file so capture
        counts only genuine appends; the legacy ``append()`` contract is
        unchanged.
        """
        if not self.enabled and not force_enabled:
            return False, False
        session_date = to_ist(observation.observed_at)
        if session_date is None:
            return False, False
        obs_utc = observation.to_dict().get("observed_at_utc")
        duplicate = self._contains(
            self.path_for(observation.underlying, session_date.date()),
            observation.instrument_key,
            obs_utc,
        )
        appended = self.append(observation, force_enabled=force_enabled)
        return appended, (appended and duplicate)

    def _contains(
        self, path: Path, instrument_key: str, obs_utc: Any
    ) -> bool:
        """True when (instrument_key, observed_at_utc) already exists in the file."""
        if not path.is_file():
            return False
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        existing = json.loads(line)
                    except Exception:  # noqa: BLE001 - malformed tail tolerated
                        continue
                    if (
                        existing.get("instrument_key") == instrument_key
                        and existing.get("observed_at_utc") == obs_utc
                    ):
                        return True
        except Exception:  # noqa: BLE001
            return False
        return False

    def read(
        self,
        underlying: str,
        session_date: date,
        instrument_key: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Read real recorded observations for one underlying + session with optional limit."""
        path = self.path_for(underlying, session_date)
        if not path.is_file():
            return []
        rows: list[dict[str, Any]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if instrument_key and row.get("instrument_key") != instrument_key:
                    continue
                rows.append(row)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read futures observations: %s", type(exc).__name__)
            return []
        rows.sort(key=lambda r: str(r.get("observed_at_utc") or ""))
        if limit is not None and limit > 0:
            return rows[-limit:]
        return rows

    def has_history(self, underlying: str, session_date: date, instrument_key: str) -> bool:
        return bool(self.read(underlying, session_date, instrument_key))

    def cleanup_older_than(self, days: int) -> dict[str, Any]:
        """Operator-authorized cleanup of observation files older than N days."""
        if not self._base_dir.exists():
            return {"deleted_files": 0, "freed_bytes": 0}
        # G.11-OPT-5 (D6): filenames encode the IST session date, so the cutoff
        # must be computed against IST's current date, not UTC's (they differ
        # between 00:00 and 05:30 IST).
        cutoff_date = (
            datetime.now(UTC) + timedelta(hours=5, minutes=30)
        ).date() - timedelta(days=days)
        deleted_count = 0
        freed_bytes = 0

        for path in self._base_dir.glob("*.jsonl"):
            # Format: UNDERLYING_YYYY-MM-DD.jsonl
            try:
                parts = path.stem.split("_")
                if len(parts) >= 2:
                    file_date = date.fromisoformat(parts[-1])
                    if file_date < cutoff_date:
                        size = path.stat().st_size
                        path.unlink()
                        deleted_count += 1
                        freed_bytes += size
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed deleting old observation file %s: %s", path.name, exc)
                continue

        return {"deleted_files": deleted_count, "freed_bytes": freed_bytes}


async def read_live_futures_quote(
    instrument_key: str,
    *,
    underlying: str,
    expiry: date | None = None,
    trading_symbol: str | None = None,
    lot_size: int | None = None,
    provider: Any | None = None,
) -> FuturesObservation | None:
    """Read the current real futures quote from the authorized provider.

    Returns ``None`` on any failure. Never falls back to a synthetic or modelled
    carry price: a missing feed stays missing.
    """
    provider = provider or _default_live_provider()
    if provider is None:
        return None
    reader = getattr(provider, "get_futures_quote", None)
    if reader is None:
        return None
    try:
        payload = await reader(instrument_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Live futures read failed: %s", type(exc).__name__)
        return None
    if not payload:
        return None
    return FuturesObservation(
        instrument_key=instrument_key,
        trading_symbol=trading_symbol or str(payload.get("symbol") or instrument_key),
        underlying=underlying.upper(),
        expiry=expiry,
        observed_at=payload.get("timestamp") or datetime.now(UTC),
        received_at=datetime.now(UTC),
        source=PROV_LIVE_OBSERVATION,
        last_price=_dec(payload.get("last_price")),
        previous_close=_dec(payload.get("previous_close")),
        day_open=_dec(payload.get("day_open")),
        day_high=_dec(payload.get("day_high")),
        day_low=_dec(payload.get("day_low")),
        volume=payload.get("volume"),
        open_interest=payload.get("open_interest"),
        oi_day_high=payload.get("oi_day_high"),
        oi_day_low=payload.get("oi_day_low"),
        net_change=_dec(payload.get("net_change")),
        total_buy_quantity=payload.get("total_buy_quantity"),
        total_sell_quantity=payload.get("total_sell_quantity"),
        lot_size=lot_size,
        quality_flags=(),
    )


def _default_live_provider() -> Any | None:
    try:
        from app.providers.upstox_live_provider import UpstoxLiveProvider

        return UpstoxLiveProvider()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Live provider unavailable: %s", type(exc).__name__)
        return None


def futures_series_from_records(
    records: Iterable[dict[str, Any]], label: str
) -> ObservationSeries:
    """Build a futures ObservationSeries from real recorded observations."""
    series = ObservationSeries(contract_key=label, label=label, source=PROV_PERSISTED_OBSERVATION)
    for row in records:
        observed_at = _parse_iso(row.get("observed_at_utc"))
        if observed_at is None:
            continue
        series.observations.append(
            ContractObservation(
                contract_key=label,
                label=label,
                observed_at=observed_at,
                available_at=_parse_iso(row.get("received_at_utc")) or observed_at,
                source=str(row.get("source") or PROV_PERSISTED_OBSERVATION),
                expiry=date.fromisoformat(row["expiry_date"]) if row.get("expiry_date") else None,
                instrument_key=row.get("instrument_key"),
                ltp=_dec(row.get("last_price")),
                open_interest=row.get("open_interest"),
                volume=row.get("volume"),
            )
        )
    ordered, issues = order_and_audit(series.observations)
    series.observations = ordered
    series.issues = issues
    if not series.is_available:
        series.reason = REASON_INSUFFICIENT_OBSERVATIONS
    return series


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return _as_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except Exception:  # noqa: BLE001
        return None


# ===========================================================================
# 6. Session helpers
# ===========================================================================

def session_bounds_ist(session_date: date) -> tuple[datetime, datetime]:
    """Regular NSE/BSE session bounds (09:15–15:30 IST) as UTC instants."""
    start_ist = datetime.combine(session_date, time(9, 15), tzinfo=UTC)
    end_ist = datetime.combine(session_date, time(15, 30), tzinfo=UTC)
    return start_ist - IST_OFFSET, end_ist - IST_OFFSET


def interval_grid(
    session_date: date, interval_minutes: int
) -> list[datetime]:
    """Session interval boundaries in IST-naive wall-clock terms (for display)."""
    boundaries: list[datetime] = []
    current = datetime.combine(session_date, time(9, 15))
    end = datetime.combine(session_date, time(15, 30))
    while current <= end:
        boundaries.append(current)
        current += timedelta(minutes=interval_minutes)
    return boundaries


def baseline_label(provenance: str, interval_minutes: int) -> str:
    """Human label that never misrepresents the baseline in use."""
    if provenance == BASELINE_INTRADAY_INTERVAL:
        return f"Measured across a real ~{interval_minutes}-minute observation pair"
    if provenance == BASELINE_PREVIOUS_CLOSE:
        return "Earliest stored observation of the session versus latest observation"
    if provenance == BASELINE_CUSTOM_RANGE:
        return "Measured across the selected custom time range"
    return "No baseline available"


def summarise_deltas(deltas: Sequence[IntervalDelta]) -> dict[str, Any]:
    """Aggregate availability for a set of deltas, without inventing values."""
    available = [d for d in deltas if d.is_available]
    return {
        "requested": len(deltas),
        "available": len(available),
        "insufficient": len(deltas) - len(available),
        "coverage_label": (
            f"{len(available)} of {len(deltas)} intervals have real comparable observations"
        ),
        "provenance": PROV_MODEL_CALCULATED if available else PROV_UNAVAILABLE,
        "baseline_provenance": (
            available[0].baseline_provenance if available else BASELINE_NONE
        ),
    }
