"""Deterministic OHLC and price-data validation rules.

Pure Python — no database access, no network calls.
Returns a list of issue-code strings that can be persisted as DataQualityIssue
records or logged/reported by the caller.

Rules enforced:
  - low <= open <= high
  - low <= close <= high
  - low <= high  (sanity — implied by above but explicit)
  - volume >= 0  (negative volume is always invalid)
  - zero volume is flagged as ZERO_VOLUME (not NEGATIVE_VOLUME — may be legitimate)
  - None/missing values are NOT treated as zero (missing stays missing)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from app.data.exchange_constants import (
    DQ_NEGATIVE_VOLUME,
    DQ_OHLC_INVALID,
    DQ_ZERO_VOLUME,
)


@dataclass(frozen=True, slots=True)
class OHLCValidationResult:
    """Result of a single price-row validation."""

    is_valid: bool
    issue_codes: tuple[str, ...] = field(default_factory=tuple)
    details: dict[str, Any] = field(default_factory=dict)

    def has_issue(self, code: str) -> bool:
        return code in self.issue_codes


def validate_ohlc_row(
    open_price: Decimal | float | None,
    high: Decimal | float | None,
    low: Decimal | float | None,
    close: Decimal | float | None,
    volume: int | None,
    *,
    trade_date: date | None = None,
    symbol: str | None = None,
    allow_zero_volume: bool = False,
) -> OHLCValidationResult:
    """Validate a single OHLCV price row.

    Parameters
    ----------
    open_price, high, low, close:
        Price values; None is treated as missing and skipped (no false positives).
    volume:
        Trade volume; None is treated as missing and skipped.
    trade_date:
        Optional context for error detail messages.
    symbol:
        Optional context for error detail messages.
    allow_zero_volume:
        If True, zero volume is not flagged (e.g., for index rows where volume
        is genuinely zero or not reported).

    Returns
    -------
    OHLCValidationResult
        is_valid=True iff no issues detected.
    """
    issues: list[str] = []
    details: dict[str, Any] = {}
    ctx = {}
    if trade_date is not None:
        ctx["trade_date"] = str(trade_date)
    if symbol is not None:
        ctx["symbol"] = symbol

    # Convert to Decimal for comparison (avoid float imprecision)
    def _d(v: Any) -> Decimal | None:
        if v is None:
            return None
        try:
            return Decimal(str(v))
        except Exception:
            return None

    o = _d(open_price)
    h = _d(high)
    lo = _d(low)
    c = _d(close)

    ohlc_invalid = False

    if h is not None and lo is not None and h < lo:
        ohlc_invalid = True
        details["high_lt_low"] = {"high": str(h), "low": str(lo)}

    if o is not None and h is not None and o > h:
        ohlc_invalid = True
        details["open_gt_high"] = {"open": str(o), "high": str(h)}

    if o is not None and lo is not None and o < lo:
        ohlc_invalid = True
        details["open_lt_low"] = {"open": str(o), "low": str(lo)}

    if c is not None and h is not None and c > h:
        ohlc_invalid = True
        details["close_gt_high"] = {"close": str(c), "high": str(h)}

    if c is not None and lo is not None and c < lo:
        ohlc_invalid = True
        details["close_lt_low"] = {"close": str(c), "low": str(lo)}

    if ohlc_invalid:
        issues.append(DQ_OHLC_INVALID)
        details.update(ctx)

    # Volume validation — only when volume is explicitly provided
    if volume is not None:
        if volume < 0:
            issues.append(DQ_NEGATIVE_VOLUME)
            details.update(ctx)
            details["volume"] = volume
        elif volume == 0 and not allow_zero_volume:
            issues.append(DQ_ZERO_VOLUME)
            details.update(ctx)
            details["volume"] = 0

    return OHLCValidationResult(
        is_valid=len(issues) == 0,
        issue_codes=tuple(issues),
        details=details,
    )


def validate_ohlc_batch(
    rows: list[dict[str, Any]],
    *,
    allow_zero_volume: bool = False,
) -> list[OHLCValidationResult]:
    """Validate a batch of price rows.

    Each dict should have keys: open, high, low, close, volume (all optional).
    Optional context keys: trade_date, symbol.
    """
    return [
        validate_ohlc_row(
            open_price=row.get("open"),
            high=row.get("high"),
            low=row.get("low"),
            close=row.get("close"),
            volume=row.get("volume"),
            trade_date=row.get("trade_date"),
            symbol=row.get("symbol"),
            allow_zero_volume=allow_zero_volume,
        )
        for row in rows
    ]
