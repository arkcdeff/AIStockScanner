"""Futures OI Ledger — Milestone G.11-OPT-3 (Phase 5, A–F).

Builds the detailed futures interval ledger from **real provider observations
only**. The layout follows the OIData-style futures ledger supplied as a visual
reference, with three non-negotiable rules:

  1. No synthetic futures price or open interest may ever populate the ledger.
     When the provider does not supply a real futures observation the ledger
     reports an explicit unavailable state instead.
  2. Interval OI change is measured between two real, comparable observations of
     the *same* contract. Missing comparable data yields ``INSUFFICIENT_DATA`` —
     never a pro-rata estimate and never a forward-fill.
  3. Buildup interpretation requires valid comparable price *and* OI
     observations for the same contract and interval. A stale or missing
     observation is never classified as a genuine buildup.

Session positioning cards aggregate **descriptive interval classifications**.
They are not verified trader identities and must not be presented as such.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from app.services.derivatives_observation_service import (
    AVAILABILITY_INSUFFICIENT,
    AVAILABILITY_OK,
    AVAILABILITY_UNAVAILABLE,
    ISSUE_STALE,
    ContractObservation,
    FuturesObservation,
    FuturesObservationStore,
    IntervalDelta,
    ObservationSeries,
    compute_interval_deltas,
    format_ist,
    order_and_audit,
    read_live_futures_quote,
    tolerance_seconds_for,
)
from app.services.derivatives_scope import (
    BASELINE_NONE,
    OI_UNIT_CONTRACTS,
    PROV_LIVE_OBSERVATION,
    PROV_MODEL_CALCULATED,
    PROV_PERSISTED_OBSERVATION,
    PROV_UNAVAILABLE,
    convert_oi_units,
)

logger = logging.getLogger(__name__)

#: The fifteen ledger columns required by the milestone, in order.
FUTURES_LEDGER_COLUMNS: tuple[dict[str, str], ...] = (
    {"key": "row_number", "label": "#", "tooltip": "Sequential row number for the selected interval"},
    {"key": "interval_label", "label": "Date / Time (Interval)", "tooltip": "Interval start and end in IST"},
    {"key": "total_oi", "label": "Total OI", "tooltip": "Futures open interest at interval end"},
    {"key": "oi_change", "label": "Total OI Change", "tooltip": "Measured OI change across the selected interval"},
    {"key": "day_high", "label": "Session Day High", "tooltip": "Session high price (provider day OHLC)"},
    {"key": "day_low", "label": "Session Day Low", "tooltip": "Session low price (provider day OHLC)"},
    {"key": "level_break", "label": "Level Break", "tooltip": "Day-high break, day-low break, both or none"},
    {"key": "oi_churn", "label": "OI Churn", "tooltip": "Absolute interval OI change (activity, not direction)"},
    {"key": "interval_volume", "label": "Interval Volume", "tooltip": "Contracts traded during the interval"},
    {"key": "ltp", "label": "Futures LTP", "tooltip": "Futures last traded price at interval end"},
    {"key": "ltp_change", "label": "LTP Change", "tooltip": "Measured price change across the interval"},
    {"key": "ltp_change_pct", "label": "LTP %", "tooltip": "Price change as a percentage of the interval start LTP"},
    {"key": "direction", "label": "Direction", "tooltip": "Up, down, unchanged or unavailable"},
    {"key": "interpretation", "label": "OI Interpretation", "tooltip": "Descriptive interval classification, not a trade recommendation"},
    {"key": "quality", "label": "Source / Timestamp", "tooltip": "Provider, observation timestamp and data-quality status"},
)

DIRECTION_UP = "UP"
DIRECTION_DOWN = "DOWN"
DIRECTION_UNCHANGED = "UNCHANGED"
DIRECTION_UNAVAILABLE = "UNAVAILABLE"

INTERPRETATION_LONG_BUILDUP = "LONG_BUILDUP"
INTERPRETATION_SHORT_BUILDUP = "SHORT_BUILDUP"
INTERPRETATION_SHORT_COVERING = "SHORT_COVERING"
INTERPRETATION_LONG_UNWINDING = "LONG_UNWINDING"
INTERPRETATION_NEUTRAL = "NEUTRAL"
INTERPRETATION_INSUFFICIENT = "INSUFFICIENT_DATA"

LEVEL_BREAK_NONE = "NONE"
LEVEL_BREAK_HIGH = "DAY_HIGH_BREAK"
LEVEL_BREAK_LOW = "DAY_LOW_BREAK"
LEVEL_BREAK_BOTH = "BOTH"

#: Explicit, user-facing statement that these classifications are descriptive.
POSITIONING_DISCLAIMER = (
    "These are descriptive interval classifications derived from observed futures "
    "price and open-interest changes. They are not verified trader identities, not "
    "position data from any exchange participant file, and not a guarantee of "
    "market direction."
)

#: Documented denominator and assumptions for the basis columns.
BASIS_DENOMINATOR_NOTE = "Basis percentage denominator is the real underlying spot observation of the same underlying."
CARRY_ASSUMPTIONS_NOTE = (
    "Annualised carry is a linear calendar-day extrapolation of the observed basis "
    "against the underlying spot (365 days). It assumes constant carry, no "
    "dividends, no financing or transaction costs and no early-exercise effects, "
    "and is therefore a model estimate rather than an observable."
)


def classify_futures_interval(
    price_change: Decimal | None,
    oi_change: int | None,
    *,
    is_stale: bool = False,
) -> dict[str, Any]:
    """Four-quadrant futures interpretation over one real interval.

    ``Long Buildup`` price↑ OI↑ · ``Short Buildup`` price↓ OI↑ ·
    ``Short Covering`` price↑ OI↓ · ``Long Unwinding`` price↓ OI↓.

    Returns ``INSUFFICIENT_DATA`` whenever either observation is missing or stale,
    so missing data can never masquerade as a genuine buildup.
    """
    base = {
        "price_change": float(price_change) if price_change is not None else None,
        "oi_change": oi_change,
    }
    if is_stale:
        return {
            **base,
            "classification": INTERPRETATION_INSUFFICIENT,
            "label": "Insufficient data",
            "code": "—",
            "color_class": "secondary",
            "reason": ISSUE_STALE,
            "tooltip": "A stale observation cannot be classified as a genuine buildup.",
        }
    if price_change is None or oi_change is None:
        return {
            **base,
            "classification": INTERPRETATION_INSUFFICIENT,
            "label": "Insufficient data",
            "code": "—",
            "color_class": "secondary",
            "reason": "MISSING_COMPARABLE_OBSERVATION",
            "tooltip": "Requires valid comparable price and OI observations for the same contract and interval.",
        }
    if price_change == 0 or oi_change == 0:
        return {
            **base,
            "classification": INTERPRETATION_NEUTRAL,
            "label": "Neutral",
            "code": "—",
            "color_class": "secondary",
            "reason": None,
            "tooltip": "Price and/or open interest were unchanged over the interval.",
        }

    if price_change > 0 and oi_change > 0:
        classification, label, code, colour = (
            INTERPRETATION_LONG_BUILDUP, "Long Buildup", "LB", "success",
        )
    elif price_change < 0 and oi_change > 0:
        classification, label, code, colour = (
            INTERPRETATION_SHORT_BUILDUP, "Short Buildup", "SB", "danger",
        )
    elif price_change > 0 and oi_change < 0:
        classification, label, code, colour = (
            INTERPRETATION_SHORT_COVERING, "Short Covering", "SC", "info",
        )
    else:
        classification, label, code, colour = (
            INTERPRETATION_LONG_UNWINDING, "Long Unwinding", "LU", "warning",
        )

    return {
        **base,
        "classification": classification,
        "label": label,
        "code": code,
        "color_class": colour,
        "reason": None,
        "tooltip": (
            f"{label}: futures price moved {float(price_change):+,.2f} while open "
            f"interest changed {oi_change:+,d} contracts over the interval."
        ),
    }


def classify_direction(price_change: Decimal | None) -> dict[str, Any]:
    """Interval direction from a measured price change."""
    if price_change is None:
        return {
            "code": DIRECTION_UNAVAILABLE,
            "label": "Unavailable",
            "color_class": "secondary",
            "tooltip": "No comparable price observation for this interval.",
        }
    if price_change > 0:
        return {"code": DIRECTION_UP, "label": "Up", "color_class": "success", "tooltip": None}
    if price_change < 0:
        return {"code": DIRECTION_DOWN, "label": "Down", "color_class": "danger", "tooltip": None}
    return {
        "code": DIRECTION_UNCHANGED,
        "label": "Unchanged",
        "color_class": "secondary",
        "tooltip": None,
    }


def compute_level_break(
    ltp: Decimal | None,
    day_high: Decimal | None,
    day_low: Decimal | None,
) -> dict[str, Any]:
    """Day-high / day-low break classification from real session extremes."""
    if ltp is None or (day_high is None and day_low is None):
        return {
            "code": LEVEL_BREAK_NONE,
            "label": "Unavailable",
            "broke_high": False,
            "broke_low": False,
            "reason": "MISSING_SESSION_EXTREMES",
        }
    broke_high = day_high is not None and ltp >= day_high
    broke_low = day_low is not None and ltp <= day_low
    if broke_high and broke_low:
        code, label = LEVEL_BREAK_BOTH, "Both levels"
    elif broke_high:
        code, label = LEVEL_BREAK_HIGH, "Day-high break"
    elif broke_low:
        code, label = LEVEL_BREAK_LOW, "Day-low break"
    else:
        # G.11-OPT-9: this human-readable label was the literal string "None",
        # which reads as a *missing value* rather than as the "no level break"
        # state. The machine-readable ``code`` (LEVEL_BREAK_NONE) is unchanged.
        code, label = LEVEL_BREAK_NONE, "No break"
    return {
        "code": code,
        "label": label,
        "broke_high": broke_high,
        "broke_low": broke_low,
        "reason": None,
    }


def compute_oi_churn(oi_change: int | None) -> dict[str, Any]:
    """OI churn: the magnitude of interval OI change (activity, not direction)."""
    if oi_change is None:
        return {
            "value": None,
            "available": False,
            "definition": "Absolute interval open-interest change",
            "reason": "MISSING_COMPARABLE_OBSERVATION",
        }
    return {
        "value": abs(oi_change),
        "available": True,
        "definition": "Absolute interval open-interest change",
        "reason": None,
    }


# ===========================================================================
# Ledger rows
# ===========================================================================

@dataclass
class FuturesLedgerRow:
    """One interval row of the futures ledger. Every field is traceable."""

    row_number: int
    interval_label: str
    start_at: datetime | None
    end_at: datetime | None
    total_oi: int | None
    oi_change: int | None
    day_high: Decimal | None
    day_low: Decimal | None
    level_break: dict[str, Any]
    oi_churn: dict[str, Any]
    interval_volume: int | None
    ltp: Decimal | None
    ltp_change: Decimal | None
    ltp_change_pct: Decimal | None
    direction: dict[str, Any]
    interpretation: dict[str, Any]
    source: str
    source_timestamp: datetime | None
    availability: str
    reason: str | None = None
    quality_flags: tuple[str, ...] = ()
    baseline_provenance: str = BASELINE_NONE
    effective_minutes: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_number": self.row_number,
            "interval_label": self.interval_label,
            "interval_start_utc": self.start_at.astimezone(UTC).isoformat() if self.start_at else None,
            "interval_end_utc": self.end_at.astimezone(UTC).isoformat() if self.end_at else None,
            "interval_start_ist": format_ist(self.start_at),
            "interval_end_ist": format_ist(self.end_at),
            "total_oi": self.total_oi,
            "oi_change": self.oi_change,
            "day_high": float(self.day_high) if self.day_high is not None else None,
            "day_low": float(self.day_low) if self.day_low is not None else None,
            "level_break": self.level_break,
            "oi_churn": self.oi_churn,
            "interval_volume": self.interval_volume,
            "ltp": float(self.ltp) if self.ltp is not None else None,
            "ltp_change": float(self.ltp_change) if self.ltp_change is not None else None,
            "ltp_change_pct": (
                float(self.ltp_change_pct) if self.ltp_change_pct is not None else None
            ),
            "direction": self.direction,
            "interpretation": self.interpretation,
            "source": self.source,
            "source_timestamp_utc": (
                self.source_timestamp.astimezone(UTC).isoformat() if self.source_timestamp else None
            ),
            "source_timestamp_ist": format_ist(self.source_timestamp),
            "availability": self.availability,
            "reason": self.reason,
            "quality_flags": list(self.quality_flags),
            "baseline_provenance": self.baseline_provenance,
            "effective_minutes": self.effective_minutes,
        }


def build_futures_ledger(
    observations: Sequence[ContractObservation],
    *,
    interval_minutes: int,
    day_high: Decimal | None = None,
    day_low: Decimal | None = None,
    source: str = PROV_PERSISTED_OBSERVATION,
    stale_seconds: int | None = None,
    evaluation_at: datetime | None = None,
    label: str = "FUTURES",
    tolerance_ratio: float | None = None,
) -> dict[str, Any]:
    """Build the ledger from real futures observations.

    ``observations`` must be real. With fewer than two comparable observations the
    interval columns are reported as unavailable rather than approximated, and the
    single observed state is still surfaced so the view is useful and honest.
    """
    series = ObservationSeries(contract_key=label, label=label, source=source)
    ordered, issues = order_and_audit(observations)
    series.observations = ordered
    series.issues = issues

    if not ordered:
        return {
            "availability": AVAILABILITY_UNAVAILABLE,
            "reason": "NO_REAL_FUTURES_OBSERVATIONS",
            "rows": [],
            "columns": [dict(c) for c in FUTURES_LEDGER_COLUMNS],
            "summary": _empty_summary(interval_minutes),
            "coverage": series.coverage(),
        }

    latest = ordered[-1]
    evaluation = evaluation_at or datetime.now(UTC)
    # Session extremes are supplied by the caller from the provider's day OHLC.
    # A contract observation does not carry them, so when the caller has none the
    # extremes stay unavailable rather than being derived from a single price.
    d_high = day_high
    d_low = day_low

    deltas = [
        d
        for d in compute_interval_deltas(
            series, interval_minutes, tolerance_ratio=tolerance_ratio, evaluation_at=evaluation
        )
        if d.is_available
    ]

    rows: list[FuturesLedgerRow] = []
    if deltas:
        for index, delta in enumerate(deltas, start=1):
            end_obs = _observation_at(ordered, delta.end_at)
            rows.append(
                _row_from_delta(
                    row_number=index,
                    delta=delta,
                    end_observation=end_obs or latest,
                    day_high=d_high,
                    day_low=d_low,
                    effective_minutes=(delta.effective_seconds or 0) / 60.0,
                )
            )
        availability = AVAILABILITY_OK
        reason = None
    else:
        rows.append(
            _single_state_row(
                latest,
                day_high=d_high,
                day_low=d_low,
                interval_minutes=interval_minutes,
                reason=series.reason or "INSUFFICIENT_COMPARABLE_OBSERVATIONS",
            )
        )
        availability = AVAILABILITY_INSUFFICIENT
        reason = (
            "Only one real futures observation is available for this session, so "
            f"interval OI change for a {interval_minutes}-minute window cannot be "
            "measured. Columns that require a comparable pair are left unavailable "
            "rather than estimated."
        )

    available_deltas = deltas
    return {
        "availability": availability,
        "reason": reason,
        "rows": [r.to_dict() for r in rows],
        "columns": [dict(c) for c in FUTURES_LEDGER_COLUMNS],
        "observation_count": len(ordered),
        "interval_minutes": interval_minutes,
        "tolerance_seconds": tolerance_seconds_for(interval_minutes, tolerance_ratio),
        "summary": _ledger_summary(ordered, available_deltas, d_high, d_low, interval_minutes),
        "positioning": compute_session_positioning(
            [_delta_to_classification_input(d) for d in available_deltas]
        ),
        "coverage": series.coverage(),
    }


def _observation_at(
    ordered: Sequence[ContractObservation], at: datetime | None
) -> ContractObservation | None:
    if at is None:
        return None
    for obs in ordered:
        if obs.observed_at == at:
            return obs
    return None


def _delta_to_classification_input(delta: IntervalDelta) -> dict[str, Any]:
    return {
        "price_change": delta.ltp_change,
        "oi_change": delta.oi_change,
        "issues": delta.issues,
        "start_ist": format_ist(delta.start_at),
        "end_ist": format_ist(delta.end_at),
    }


def _row_from_delta(
    *,
    row_number: int,
    delta: IntervalDelta,
    end_observation: ContractObservation,
    day_high: Decimal | None,
    day_low: Decimal | None,
    effective_minutes: float,
) -> FuturesLedgerRow:
    interpretation = classify_futures_interval(delta.ltp_change, delta.oi_change)
    start_label = format_ist(delta.start_at)
    end_label = format_ist(delta.end_at)
    return FuturesLedgerRow(
        row_number=row_number,
        interval_label=f"{start_label} – {end_label} IST" if start_label else "—",
        start_at=delta.start_at,
        end_at=delta.end_at,
        total_oi=end_observation.open_interest,
        oi_change=delta.oi_change,
        day_high=day_high,
        day_low=day_low,
        level_break=compute_level_break(end_observation.ltp, day_high, day_low),
        oi_churn=compute_oi_churn(delta.oi_change),
        interval_volume=delta.volume_change,
        ltp=end_observation.ltp,
        ltp_change=delta.ltp_change,
        ltp_change_pct=delta.ltp_change_pct,
        direction=classify_direction(delta.ltp_change),
        interpretation=interpretation,
        source=end_observation.source,
        source_timestamp=end_observation.observed_at,
        availability=AVAILABILITY_OK,
        reason=None,
        quality_flags=delta.issues,
        baseline_provenance=delta.baseline_provenance,
        effective_minutes=effective_minutes,
    )


def _single_state_row(
    observation: ContractObservation,
    *,
    day_high: Decimal | None,
    day_low: Decimal | None,
    interval_minutes: int,
    reason: str,
) -> FuturesLedgerRow:
    """One honest row for a session that has a real state but no real history."""
    return FuturesLedgerRow(
        row_number=1,
        interval_label=(
            f"{format_ist(observation.observed_at)} IST (current observed state — no "
            f"comparable {interval_minutes}-minute baseline)"
        ),
        start_at=None,
        end_at=observation.observed_at,
        total_oi=observation.open_interest,
        oi_change=None,
        day_high=day_high,
        day_low=day_low,
        level_break=compute_level_break(observation.ltp, day_high, day_low),
        oi_churn=compute_oi_churn(None),
        interval_volume=None,
        ltp=observation.ltp,
        ltp_change=None,
        ltp_change_pct=None,
        direction=classify_direction(None),
        interpretation=classify_futures_interval(None, None),
        source=observation.source,
        source_timestamp=observation.observed_at,
        availability=AVAILABILITY_INSUFFICIENT,
        reason=reason,
        quality_flags=observation.quality_flags,
        baseline_provenance=BASELINE_NONE,
        effective_minutes=None,
    )


def _empty_summary(interval_minutes: int) -> dict[str, Any]:
    return {
        "interval_minutes": interval_minutes,
        "availability": AVAILABILITY_UNAVAILABLE,
        "current_oi": None,
        "session_oi_change": None,
        "oi_session_range": None,
        "session_volume": None,
        "day_high": None,
        "day_low": None,
        "current_ltp": None,
        "direction": classify_direction(None),
        "interpretation": classify_futures_interval(None, None),
    }


def _ledger_summary(
    ordered: Sequence[ContractObservation],
    deltas: Sequence[IntervalDelta],
    day_high: Decimal | None,
    day_low: Decimal | None,
    interval_minutes: int,
) -> dict[str, Any]:
    latest = ordered[-1]
    last_delta = deltas[-1] if deltas else None

    # A change needs two real observations. A single observation must never be
    # reported as a zero change, which would read as "nothing happened today".
    has_history = len(ordered) >= 2
    oi_high, oi_low = _oi_session_range(ordered) if has_history else (None, None)

    session_oi_change = None
    if has_history and ordered[0].open_interest is not None and latest.open_interest is not None:
        session_oi_change = latest.open_interest - ordered[0].open_interest

    session_volume = None
    if has_history and ordered[0].volume is not None and latest.volume is not None:
        session_volume = latest.volume - ordered[0].volume

    return {
        "interval_minutes": interval_minutes,
        "availability": AVAILABILITY_OK if deltas else AVAILABILITY_INSUFFICIENT,
        "current_oi": latest.open_interest,
        "session_oi_change": session_oi_change,
        "session_oi_change_basis": (
            "Latest minus earliest stored observation of this session"
            if session_oi_change is not None
            else "Requires at least two real observations of the same futures contract"
        ),
        "session_oi_change_provenance": (
            PROV_MODEL_CALCULATED if session_oi_change is not None else PROV_UNAVAILABLE
        ),
        "oi_session_range": (
            {"high": oi_high, "low": oi_low, "basis": "Range of stored observations"}
            if oi_high is not None
            else None
        ),
        "session_volume": session_volume,
        "session_volume_provenance": (
            PROV_MODEL_CALCULATED if session_volume is not None else PROV_UNAVAILABLE
        ),
        "session_volume_basis": (
            "Latest minus earliest stored volume observation"
            if session_volume is not None
            else None
        ),
        "day_high": float(day_high) if day_high is not None else None,
        "day_low": float(day_low) if day_low is not None else None,
        "current_ltp": float(latest.ltp) if latest.ltp is not None else None,
        "direction": classify_direction(last_delta.ltp_change if last_delta else None),
        "interpretation": classify_futures_interval(
            last_delta.ltp_change if last_delta else None,
            last_delta.oi_change if last_delta else None,
        ),
        "observation_count": len(ordered),
        "interval_count": len(deltas),
    }


def _oi_session_range(
    ordered: Sequence[ContractObservation],
) -> tuple[int | None, int | None]:
    values = [o.open_interest for o in ordered if o.open_interest is not None]
    if not values:
        return None, None
    return max(values), min(values)


# ===========================================================================
# Session positioning cards (Phase 5.E)
# ===========================================================================

def compute_session_positioning(classifications: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate interval classifications into descriptive session cards.

    Reports **both** the count of intervals and the aggregated OI change, and
    labels the aggregation method. Absolute OI levels are never summed across
    intervals as though each interval were a new position.
    """
    counts = {
        INTERPRETATION_LONG_BUILDUP: 0,
        INTERPRETATION_SHORT_BUILDUP: 0,
        INTERPRETATION_SHORT_COVERING: 0,
        INTERPRETATION_LONG_UNWINDING: 0,
        INTERPRETATION_NEUTRAL: 0,
        INTERPRETATION_INSUFFICIENT: 0,
    }
    oi_sums = {key: 0 for key in counts}
    price_sums: dict[str, Decimal] = {}
    net_oi_change = 0
    net_price_change = Decimal("0")
    measured = 0

    for item in classifications:
        result = classify_futures_interval(
            item.get("price_change"),
            item.get("oi_change"),
            is_stale=ISSUE_STALE in tuple(item.get("issues") or ()),
        )
        key = result["classification"]
        counts[key] = counts.get(key, 0) + 1
        if result["oi_change"] is not None:
            oi_sums[key] = oi_sums.get(key, 0) + result["oi_change"]
            net_oi_change += result["oi_change"]
        if result["price_change"] is not None:
            price_sums[key] = price_sums.get(key, Decimal("0")) + Decimal(
                str(result["price_change"])
            )
            net_price_change += Decimal(str(result["price_change"]))
            measured += 1

    cards = []
    for key in (
        INTERPRETATION_LONG_BUILDUP,
        INTERPRETATION_SHORT_BUILDUP,
        INTERPRETATION_SHORT_COVERING,
        INTERPRETATION_LONG_UNWINDING,
    ):
        sample = classify_futures_interval(Decimal("1") if key != INTERPRETATION_SHORT_BUILDUP else Decimal("-1"), 1)
        meta = _POSITIONING_META[key]
        cards.append(
            {
                "classification": key,
                "label": meta["label"],
                "code": meta["code"],
                "color_class": meta["color_class"],
                "direction_hint": meta["hint"],
                "interval_count": counts[key],
                "aggregated_oi_change": oi_sums[key] if counts[key] else 0,
                "aggregated_price_change": (
                    float(price_sums.get(key, Decimal("0"))) if counts[key] else 0.0
                ),
                "sample_tooltip": sample["tooltip"],
            }
        )

    return {
        "cards": cards,
        "measured_intervals": measured,
        "total_intervals": len(classifications),
        "neutral_intervals": counts[INTERPRETATION_NEUTRAL],
        "insufficient_intervals": counts[INTERPRETATION_INSUFFICIENT],
        "net_oi_change": net_oi_change if measured else None,
        "net_price_change": float(net_price_change) if measured else None,
        "aggregation_method": "AGGREGATED_INTERVAL_OI_CHANGE",
        "aggregation_label": (
            "Aggregation: sum of measured interval open-interest changes with matching "
            "price direction. Interval counts are shown alongside so a single large "
            "interval cannot be mistaken for many."
        ),
        "absolute_level_note": (
            "Absolute open-interest levels are never summed across intervals: that "
            "would count the same position repeatedly. Absolute OI is shown only as "
            "the current level and as a session high/low range."
        ),
        "disclaimer": POSITIONING_DISCLAIMER,
        "available": bool(measured),
        "reason": None if measured else "NO_CLASSIFIABLE_INTERVALS",
    }


_POSITIONING_META: dict[str, dict[str, str]] = {
    INTERPRETATION_LONG_BUILDUP: {
        "label": "Long Buildup",
        "code": "LB",
        "color_class": "success",
        "hint": "Futures price rose while open interest expanded",
    },
    INTERPRETATION_SHORT_BUILDUP: {
        "label": "Short Buildup",
        "code": "SB",
        "color_class": "danger",
        "hint": "Futures price fell while open interest expanded",
    },
    INTERPRETATION_SHORT_COVERING: {
        "label": "Short Covering",
        "code": "SC",
        "color_class": "info",
        "hint": "Futures price rose while open interest contracted",
    },
    INTERPRETATION_LONG_UNWINDING: {
        "label": "Long Unwinding",
        "code": "LU",
        "color_class": "warning",
        "hint": "Futures price fell while open interest contracted",
    },
}


# ===========================================================================
# Basis and carry (documented denominators / assumptions)
# ===========================================================================

def compute_basis(
    futures_price: Decimal | None,
    spot_price: Decimal | None,
    *,
    days_to_expiry: int | None,
    futures_observed_at: datetime | None = None,
    spot_observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Futures basis, basis percentage and annualised carry with provenance.

    The percentage denominator is the real underlying spot of the **same
    underlying**, and it is stated in the payload so the number is never
    ambiguous. Annualised carry is returned only when the inputs validly support
    it, together with its assumptions.
    """
    out: dict[str, Any] = {
        "futures_price": float(futures_price) if futures_price is not None else None,
        "spot_price": float(spot_price) if spot_price is not None else None,
        "days_to_expiry": days_to_expiry,
        "basis": None,
        "basis_pct": None,
        "annualised_carry_pct": None,
        "denominator_note": BASIS_DENOMINATOR_NOTE,
        "carry_assumptions": CARRY_ASSUMPTIONS_NOTE,
        "futures_observed_at_ist": format_ist(futures_observed_at),
        "spot_observed_at_ist": format_ist(spot_observed_at),
        "availability": AVAILABILITY_UNAVAILABLE,
        "reason": None,
    }
    if futures_price is None or spot_price is None or spot_price <= 0:
        out["reason"] = "MISSING_REAL_FUTURES_OR_SPOT_OBSERVATION"
        return out

    basis = futures_price - spot_price
    basis_pct = (basis / spot_price) * Decimal("100")
    out["basis"] = float(basis)
    out["basis_pct"] = float(basis_pct)
    out["availability"] = AVAILABILITY_OK
    out["same_session_observations"] = (
        None
        if futures_observed_at is None or spot_observed_at is None
        else futures_observed_at.date() == spot_observed_at.date()
    )
    if days_to_expiry is not None and days_to_expiry > 0:
        out["annualised_carry_pct"] = float(basis_pct * Decimal("365") / Decimal(days_to_expiry))
        out["annualised_carry_provenance"] = PROV_MODEL_CALCULATED
        out["annualised_carry_valid"] = True
    else:
        out["annualised_carry_valid"] = False
        out["annualised_carry_reason"] = "NON_POSITIVE_OR_UNKNOWN_DAYS_TO_EXPIRY"
    return out


# ===========================================================================
# Workbench assembly
# ===========================================================================

async def build_futures_workbench(
    underlying: str,
    *,
    interval_minutes: int = 5,
    oi_unit: str = OI_UNIT_CONTRACTS,
    expiry: date | None = None,
    store: FuturesObservationStore | None = None,
    live_provider: Any | None = None,
    session_date: date | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> dict[str, Any]:
    """Assemble the futures workbench from real contract master + real reads.

    Nothing here is synthesised. If the provider does not supply a futures
    contract, or the quote read fails, the result carries an explicit unavailable
    state and the page must render that rather than a modelled price.
    """
    from app.services.derivatives_scope import (
        DerivativesContractMasterClient,
        get_underlying,
    )

    symbol = underlying.strip().upper()
    spec = get_underlying(symbol)
    client = DerivativesContractMasterClient()

    payload: dict[str, Any] = {
        "underlying": symbol,
        "underlying_name": spec.name if spec else symbol,
        "interval_minutes": interval_minutes,
        "oi_unit": oi_unit,
        "provider_entitlement": PROV_UNAVAILABLE,
        "expiries": [],
        "selected_expiry": expiry.isoformat() if expiry else None,
        "contract": None,
        "observation": None,
        "ledger": None,
        "basis": None,
        "positioning": None,
        "summary_cards": [],
        "history_available": False,
        "history_reason": None,
        "limitations": [],
        "synthetic_substitution": False,
    }

    if spec is None:
        payload["reason"] = "UNSUPPORTED_UNDERLYING"
        payload["limitations"].append(
            f"'{underlying}' is not a configured underlying, so no real futures "
            "contract can be resolved."
        )
        return payload

    futures = await client.get_futures_contracts(symbol)
    if not futures.is_available:
        payload["reason"] = futures.reason or "NO_LISTED_FUTURES_CONTRACT"
        payload["limitations"].append(
            "No provider-listed futures contract could be resolved"
            + (f" ({futures.reason})" if futures.reason else "")
            + ". The futures ledger is unavailable; no synthetic carry price is substituted."
        )
        return payload

    payload["provider_entitlement"] = PROV_LIVE_OBSERVATION
    payload["expiries"] = [
        {
            "expiry_date": f.expiry_date.isoformat(),
            "trading_symbol": f.trading_symbol,
            "instrument_key": f.instrument_key,
            "lot_size": f.lot_size,
            "days_to_expiry": (f.expiry_date - date.today()).days,
            "is_nearest": index == 0,
        }
        for index, f in enumerate(futures.rows[:12])
    ]

    contract = next((f for f in futures.rows if f.expiry_date == expiry), None) if expiry else None
    if contract is None:
        contract = futures.rows[0]
    payload["selected_expiry"] = contract.expiry_date.isoformat()
    payload["contract"] = {
        "instrument_key": contract.instrument_key,
        "trading_symbol": contract.trading_symbol,
        "expiry_date": contract.expiry_date.isoformat(),
        "lot_size": contract.lot_size,
        "provenance": futures.source,
    }
    days_to_expiry = (contract.expiry_date - date.today()).days
    payload["days_to_expiry"] = days_to_expiry

    live = await read_live_futures_quote(
        contract.instrument_key,
        underlying=symbol,
        expiry=contract.expiry_date,
        trading_symbol=contract.trading_symbol,
        lot_size=contract.lot_size,
        provider=live_provider,
    )
    if live is None:
        payload["reason"] = "FUTURES_QUOTE_UNAVAILABLE"
        payload["limitations"].append(
            "The provider did not return a real futures quote for "
            f"{contract.trading_symbol} ({contract.instrument_key}). The ledger row "
            "cannot be produced from real data, and no synthetic price is shown."
        )
        return payload

    payload["observation"] = live.to_dict()
    payload["source"] = live.source

    observation = ContractObservation(
        contract_key=contract.instrument_key,
        label=contract.trading_symbol,
        observed_at=live.observed_at,
        available_at=live.received_at,
        source=PROV_LIVE_OBSERVATION,
        expiry=contract.expiry_date,
        instrument_key=contract.instrument_key,
        ltp=live.last_price,
        open_interest=live.open_interest,
        volume=live.volume,
    )

    session = session_date or live.observed_at.date()
    store = store or FuturesObservationStore()
    records = store.read(symbol, session, contract.instrument_key)
    series_observations: list[ContractObservation] = []
    if records:
        for row in records:
            series_observations.append(
                ContractObservation(
                    contract_key=contract.instrument_key,
                    label=contract.trading_symbol,
                    observed_at=datetime.fromisoformat(str(row["observed_at_utc"])),
                    available_at=datetime.fromisoformat(str(row.get("received_at_utc") or row["observed_at_utc"])),
                    source=str(row.get("source") or PROV_PERSISTED_OBSERVATION),
                    expiry=contract.expiry_date,
                    instrument_key=contract.instrument_key,
                    ltp=Decimal(str(row["last_price"])) if row.get("last_price") is not None else None,
                    open_interest=row.get("open_interest"),
                    volume=row.get("volume"),
                )
            )
    series_observations.append(observation)

    payload["history_available"] = len(series_observations) > 1
    if len(series_observations) <= 1:
        payload["history_reason"] = (
            "The provider serves a current futures quote but publishes no historical "
            "intraday open-interest series. Interval futures OI change therefore "
            "requires accumulated real observations: enable the operator-gated "
            "capture (derivatives_observation_capture_enabled) to record real "
            "observations over a session. Until then, interval columns remain "
            "explicitly unavailable."
        )
        payload["limitations"].append(payload["history_reason"])

    ledger = build_futures_ledger(
        series_observations,
        interval_minutes=interval_minutes,
        day_high=live.day_high,
        day_low=live.day_low,
        source=PROV_LIVE_OBSERVATION,
        label=contract.trading_symbol,
    )
    payload["ledger"] = ledger
    payload["positioning"] = ledger["positioning"]
    payload["oi_unit"] = oi_unit
    payload["oi_unit_conversion"] = convert_oi_units(
        live.open_interest, contract.lot_size, oi_unit
    )

    spot, spot_provenance = await _read_spot(symbol, live_provider)
    payload["underlying_spot"] = spot
    payload["underlying_spot_provenance"] = spot_provenance if spot is not None else PROV_UNAVAILABLE
    payload["basis"] = compute_basis(
        live.last_price,
        spot,
        days_to_expiry=days_to_expiry,
        futures_observed_at=live.observed_at,
        spot_observed_at=live.received_at if spot is not None else None,
    )
    payload["summary_cards"] = _summary_cards(
        live, ledger, days_to_expiry, contract, spot, spot_provenance
    )
    payload["oi_unit_note"] = (
        "Open interest is reported in contracts unless a unit conversion is "
        "explicitly requested. Conversion uses the provider-published lot size "
        f"{contract.lot_size} for {contract.trading_symbol}."
    )
    payload["provider_entitlement"] = "REAL_PROVIDER_FUTURES_FEED"
    payload["entitlement_statement"] = (
        "Futures last price, volume, open interest and open-interest day high/low are "
        "read directly from the authorized provider quote feed. No synthetic "
        "cost-of-carry price is used anywhere in this ledger."
    )
    return payload


async def _read_spot(symbol: str, provider: Any | None) -> tuple[Decimal | None, str]:
    """Read the underlying index spot **and its real provenance string**.

    G.11-OPT-9: the provenance returned by the provider adapter was previously
    discarded, which is why the Underlying Spot summary card was hardcoded to
    ``None``/``UNAVAILABLE`` while the payload's top-level ``underlying_spot``
    carried the real value: one payload making two contradictory provenance
    claims about the very same field.
    """
    try:
        from app.providers.upstox_live_provider import UpstoxLiveProvider

        p = provider or UpstoxLiveProvider()
        reader = getattr(p, "get_index_spot", None)
        if reader is None:
            return None, PROV_UNAVAILABLE
        spot, _, provenance = await reader(symbol)
        return spot, (provenance or PROV_UNAVAILABLE)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read underlying spot for basis: %s", type(exc).__name__)
        return None, PROV_UNAVAILABLE


def _summary_cards(
    live: FuturesObservation,
    ledger: dict[str, Any],
    days_to_expiry: int,
    contract: Any,
    spot: Decimal | None,
    spot_provenance: str,
) -> list[dict[str, Any]]:
    summary = ledger.get("summary") or {}
    return [
        {
            "key": "futures_ltp",
            "label": "Futures LTP",
            "value": float(live.last_price) if live.last_price is not None else None,
            "unit": "₹",
            "provenance": live.source,
            "note": contract.trading_symbol,
        },
        {
            # G.11-OPT-9: this card used to be hardcoded to None/UNAVAILABLE even
            # when the same payload carried a real underlying spot, so a programmatic
            # consumer of summary_cards was told the spot was unavailable while the
            # UI and the top-level field showed a number. It now reports the same
            # observation the basis panel uses — the same value, the same provenance.
            "key": "underlying_spot",
            "label": "Underlying Spot",
            "value": float(spot) if spot is not None else None,
            "unit": "₹",
            "provenance": spot_provenance if spot is not None else PROV_UNAVAILABLE,
            "note": "Same-session index observation, identical to the basis panel value",
        },
        {
            "key": "total_oi",
            "label": "Futures Total OI",
            "value": live.open_interest,
            "unit": "contracts",
            "provenance": live.source,
            "note": "Provider-reported open interest at observation time",
        },
        {
            "key": "session_oi_change",
            "label": "Session OI Change",
            "value": summary.get("session_oi_change"),
            "unit": "contracts",
            "provenance": PROV_MODEL_CALCULATED if summary.get("session_oi_change") is not None else PROV_UNAVAILABLE,
            "note": summary.get("session_oi_change_basis")
            or "Requires two real observations of the same contract",
        },
        {
            "key": "session_volume",
            "label": "Session Volume",
            "value": live.volume,
            "unit": "contracts",
            "provenance": live.source,
            "note": "Provider cumulative session volume",
        },
        {
            "key": "day_range",
            "label": "Session Day High / Low",
            "value": [
                float(live.day_high) if live.day_high is not None else None,
                float(live.day_low) if live.day_low is not None else None,
            ],
            "unit": "₹",
            "provenance": live.source,
            "note": "Provider day OHLC extremes",
        },
        {
            "key": "oi_day_range",
            "label": "OI Day High / Low",
            "value": [live.oi_day_high, live.oi_day_low],
            "unit": "contracts",
            "provenance": live.source,
            "note": "Provider-published open-interest session extremes",
        },
        {
            "key": "days_to_expiry",
            "label": "Days to Expiry",
            "value": days_to_expiry,
            "unit": "days",
            "provenance": PROV_MODEL_CALCULATED,
            "note": f"{contract.expiry_date.isoformat()} ({contract.trading_symbol})",
        },
        {
            "key": "direction",
            "label": "Current Interval Direction",
            "value": summary.get("direction", {}).get("label"),
            "unit": None,
            "provenance": PROV_MODEL_CALCULATED if summary.get("direction") else PROV_UNAVAILABLE,
            "note": "Measured from comparable interval observations",
        },
        {
            "key": "interpretation",
            "label": "Buildup Classification",
            "value": summary.get("interpretation", {}).get("label"),
            "unit": None,
            "provenance": PROV_MODEL_CALCULATED if summary.get("interpretation") else PROV_UNAVAILABLE,
            "note": summary.get("interpretation", {}).get("tooltip"),
        },
        {
            "key": "source_freshness",
            "label": "Source / Freshness",
            "value": format_ist(live.observed_at),
            "unit": "IST",
            "provenance": live.source,
            "note": f"{live.trading_symbol} · {len(live.quality_flags)} quality flag(s)",
        },
    ]
