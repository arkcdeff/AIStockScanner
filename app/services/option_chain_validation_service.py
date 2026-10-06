"""Option Chain Validation Service — Reusable Validation Primitives.

Milestone: G.11-OPT-1 (SPEC-R2 & Implementation Specification §29, §30, §31, §42).
Provides deterministic, reusable validation primitives for:
  - Strike-grid configuration & sequence validation
  - Chain completeness and truncation detection
  - Multi-timestamp ordering and clock-skew tolerance validation
  - Consolidated snapshot quality status and flags determination
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.services.option_chain_config import (
    DEFAULT_CLOCK_SKEW_TOLERANCE_MS,
    DQ_FLAG_AVAILABLE_AT_INVERSION,
    DQ_FLAG_CLOCK_SKEW_EXCESSIVE,
    DQ_FLAG_CLOCK_SKEW_TOLERATED,
    DQ_FLAG_CLOCK_SKEW_WARNING,
    DQ_FLAG_DUPLICATE_STRIKE,
    DQ_FLAG_EMPTY_CHAIN,
    DQ_FLAG_FUTURE_MARKET_TIMESTAMP,
    DQ_FLAG_INCOMPLETE_CHAIN,
    DQ_FLAG_INSUFFICIENT_STRIKES,
    DQ_FLAG_INVALID_NUMERIC_STRIKE,
    DQ_FLAG_IRREGULAR_STRIKE_SPACING,
    DQ_FLAG_NON_POSITIVE_STRIKE,
    DQ_FLAG_OFF_GRID_STRIKE,
    DQ_FLAG_STRUCTURAL_COUNT_ANOMALY,
    DQ_FLAG_TRUNCATED_BY_SOURCE,
    DQ_FLAG_UNORDERED_STRIKES,
    DQ_STATUS_ACCEPT_WITH_FLAG,
    DQ_STATUS_REJECTED,
    DQ_STATUS_VALID,
    DQ_STATUS_WARNING,
    MAX_ALLOWABLE_CLOCK_SKEW_MS,
    get_strike_step,
)


@dataclass
class ValidationResult:
    """Standardized validation outcome across all OPT-1 data quality checks."""

    status: str = DQ_STATUS_VALID
    is_valid: bool = True
    flags: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def merge(self, other: ValidationResult) -> ValidationResult:
        """Merge another validation result with strict priority resolution.

        Status Priority:
            REJECTED > WARNING > ACCEPT_WITH_FLAG > VALID
        """
        priority = {
            DQ_STATUS_REJECTED: 4,
            DQ_STATUS_WARNING: 3,
            DQ_STATUS_ACCEPT_WITH_FLAG: 2,
            DQ_STATUS_VALID: 1,
        }

        new_status = (
            self.status
            if priority.get(self.status, 0) >= priority.get(other.status, 0)
            else other.status
        )
        new_is_valid = self.is_valid and other.is_valid and new_status != DQ_STATUS_REJECTED

        merged_flags = {**self.flags, **other.flags}
        merged_errors = self.errors + [e for e in other.errors if e not in self.errors]
        merged_warnings = self.warnings + [w for w in other.warnings if w not in self.warnings]

        return ValidationResult(
            status=new_status,
            is_valid=new_is_valid,
            flags=merged_flags,
            errors=merged_errors,
            warnings=merged_warnings,
        )


class OptionChainValidationService:
    """Authoritative validation primitives for option chains in G.11-OPT-1."""

    # -----------------------------------------------------------------------
    # 1. Strike-Grid Validation
    # -----------------------------------------------------------------------

    @staticmethod
    def validate_strike(
        strike: Decimal | int | float | str,
        underlying: str,
        strike_step: Decimal | None = None,
    ) -> ValidationResult:
        """Validate a single strike against configured index strike grid.

        Checks:
          1. Numeric parseable to Decimal.
          2. Positive (> 0).
          3. Exact alignment with index strike step (strike % step == 0).

        Args:
            strike: Strike price value.
            underlying: Underlying index symbol.
            strike_step: Optional override for strike step; resolved from config if None.

        Returns:
            ValidationResult with DQ status and flags.
        """
        try:
            val = Decimal(str(strike))
        except (InvalidOperation, TypeError, ValueError):
            return ValidationResult(
                status=DQ_STATUS_REJECTED,
                is_valid=False,
                flags={DQ_FLAG_INVALID_NUMERIC_STRIKE: {"strike": str(strike)}},
                errors=[f"Strike '{strike}' is not a valid numeric decimal"],
            )

        if val <= Decimal("0"):
            return ValidationResult(
                status=DQ_STATUS_REJECTED,
                is_valid=False,
                flags={DQ_FLAG_NON_POSITIVE_STRIKE: {"strike": float(val)}},
                errors=[f"Strike '{val}' must be strictly positive"],
            )

        step = strike_step or get_strike_step(underlying)
        if step <= Decimal("0"):
            raise ValueError(f"Strike step for '{underlying}' must be positive, got {step}")

        remainder = val % step
        if remainder != Decimal("0"):
            return ValidationResult(
                status=DQ_STATUS_REJECTED,
                is_valid=False,
                flags={
                    DQ_FLAG_OFF_GRID_STRIKE: {
                        "strike": float(val),
                        "strike_step": float(step),
                        "underlying": underlying,
                        "remainder": float(remainder),
                    }
                },
                errors=[
                    f"Strike {val} is off-grid for {underlying} (step {step}, remainder {remainder})"
                ],
            )

        return ValidationResult(status=DQ_STATUS_VALID, is_valid=True)

    @classmethod
    def validate_strike_sequence(
        cls,
        strikes: list[Decimal | int | float | str],
        underlying: str,
        strike_step: Decimal | None = None,
        check_ordering: bool = True,
        check_spacing: bool = True,
    ) -> ValidationResult:
        """Validate a sequence of strikes across an entire option chain.

        Validates individual strikes, duplicate strikes, sequence ordering, and spacing.
        """
        if not strikes:
            return ValidationResult(
                status=DQ_STATUS_REJECTED,
                is_valid=False,
                flags={DQ_FLAG_EMPTY_CHAIN: True},
                errors=["Strike sequence cannot be empty"],
            )

        step = strike_step or get_strike_step(underlying)
        result = ValidationResult(status=DQ_STATUS_VALID, is_valid=True)

        parsed_strikes: list[Decimal] = []
        duplicates: set[Decimal] = set()
        seen: set[Decimal] = set()

        for s in strikes:
            strike_res = cls.validate_strike(s, underlying=underlying, strike_step=step)
            result = result.merge(strike_res)
            if strike_res.is_valid:
                val = Decimal(str(s))
                if val in seen:
                    duplicates.add(val)
                seen.add(val)
                parsed_strikes.append(val)

        if duplicates:
            dup_list = sorted([float(d) for d in duplicates])
            result = result.merge(
                ValidationResult(
                    status=DQ_STATUS_REJECTED,
                    is_valid=False,
                    flags={DQ_FLAG_DUPLICATE_STRIKE: {"duplicates": dup_list}},
                    errors=[f"Duplicate strikes detected in sequence: {dup_list}"],
                )
            )

        if not parsed_strikes:
            return result

        # Ordering check
        if check_ordering and len(parsed_strikes) > 1:
            is_sorted = all(parsed_strikes[i] < parsed_strikes[i + 1] for i in range(len(parsed_strikes) - 1))
            if not is_sorted:
                result = result.merge(
                    ValidationResult(
                        status=DQ_STATUS_WARNING,
                        is_valid=result.is_valid,
                        flags={DQ_FLAG_UNORDERED_STRIKES: True},
                        warnings=["Strikes in chain observation are not strictly ascending"],
                    )
                )

        # Spacing check on unique sorted strikes
        if check_spacing and len(seen) > 1:
            sorted_unique = sorted(seen)
            irregular_gaps: list[dict[str, float]] = []
            for i in range(len(sorted_unique) - 1):
                gap = sorted_unique[i + 1] - sorted_unique[i]
                if gap != step:
                    irregular_gaps.append(
                        {
                            "from_strike": float(sorted_unique[i]),
                            "to_strike": float(sorted_unique[i + 1]),
                            "gap": float(gap),
                            "expected_step": float(step),
                        }
                    )
            if irregular_gaps:
                result = result.merge(
                    ValidationResult(
                        status=DQ_STATUS_WARNING,
                        is_valid=result.is_valid,
                        flags={DQ_FLAG_IRREGULAR_STRIKE_SPACING: {"gaps": irregular_gaps}},
                        warnings=[
                            f"Irregular strike spacing detected: {len(irregular_gaps)} gap(s) not aligned with step {step}"
                        ],
                    )
                )

        return result

    # -----------------------------------------------------------------------
    # 2. Chain Completeness & Truncation Validation
    # -----------------------------------------------------------------------

    @staticmethod
    def validate_chain_completeness(
        observed_strike_count: int,
        expected_strike_count: int | None = None,
        is_truncated: bool = False,
        min_expected_strikes: int = 1,
    ) -> ValidationResult:
        """Validate chain completeness and provider truncation status.

        Outcomes:
          - COMPLETE: observed == expected and not is_truncated -> VALID
          - TRUNCATED: is_truncated == True -> ACCEPT_WITH_FLAG
          - INCOMPLETE: observed < expected and not is_truncated -> WARNING
          - STRUCTURAL ANOMALY: negative counts, zero strikes, or observed > expected -> REJECTED
        """
        if observed_strike_count < 0:
            return ValidationResult(
                status=DQ_STATUS_REJECTED,
                is_valid=False,
                flags={DQ_FLAG_STRUCTURAL_COUNT_ANOMALY: {"observed_strike_count": observed_strike_count}},
                errors=["Observed strike count cannot be negative"],
            )

        if observed_strike_count == 0:
            return ValidationResult(
                status=DQ_STATUS_REJECTED,
                is_valid=False,
                flags={DQ_FLAG_EMPTY_CHAIN: True},
                errors=["Zero strikes observed in chain; empty option chains cannot be accepted"],
            )

        if expected_strike_count is not None:
            if expected_strike_count <= 0:
                return ValidationResult(
                    status=DQ_STATUS_REJECTED,
                    is_valid=False,
                    flags={
                        DQ_FLAG_STRUCTURAL_COUNT_ANOMALY: {
                            "expected_strike_count": expected_strike_count
                        }
                    },
                    errors=["Expected strike count must be positive when specified"],
                )

            if observed_strike_count > expected_strike_count:
                return ValidationResult(
                    status=DQ_STATUS_REJECTED,
                    is_valid=False,
                    flags={
                        DQ_FLAG_STRUCTURAL_COUNT_ANOMALY: {
                            "observed_strike_count": observed_strike_count,
                            "expected_strike_count": expected_strike_count,
                        }
                    },
                    errors=[
                        f"Observed strike count ({observed_strike_count}) exceeds expected strike count ({expected_strike_count})"
                    ],
                )

            if observed_strike_count < expected_strike_count:
                if is_truncated:
                    return ValidationResult(
                        status=DQ_STATUS_ACCEPT_WITH_FLAG,
                        is_valid=True,
                        flags={
                            DQ_FLAG_TRUNCATED_BY_SOURCE: {
                                "observed_count": observed_strike_count,
                                "expected_count": expected_strike_count,
                            }
                        },
                        warnings=[
                            f"Chain is truncated by source: {observed_strike_count} of {expected_strike_count} strikes observed"
                        ],
                    )
                else:
                    return ValidationResult(
                        status=DQ_STATUS_WARNING,
                        is_valid=True,
                        flags={
                            DQ_FLAG_INCOMPLETE_CHAIN: {
                                "observed_count": observed_strike_count,
                                "expected_count": expected_strike_count,
                                "missing_count": expected_strike_count - observed_strike_count,
                            }
                        },
                        warnings=[
                            f"Incomplete chain: observed {observed_strike_count} of {expected_strike_count} strikes without truncation flag"
                        ],
                    )

            # observed == expected
            if is_truncated:
                return ValidationResult(
                    status=DQ_STATUS_ACCEPT_WITH_FLAG,
                    is_valid=True,
                    flags={
                        DQ_FLAG_TRUNCATED_BY_SOURCE: {
                            "observed_count": observed_strike_count,
                            "expected_count": expected_strike_count,
                        }
                    },
                    warnings=["Chain marked truncated despite observed matching expected count"],
                )

            return ValidationResult(status=DQ_STATUS_VALID, is_valid=True)

        # expected_strike_count is None
        if is_truncated:
            return ValidationResult(
                status=DQ_STATUS_ACCEPT_WITH_FLAG,
                is_valid=True,
                flags={
                    DQ_FLAG_TRUNCATED_BY_SOURCE: {
                        "observed_count": observed_strike_count,
                        "expected_count": None,
                    }
                },
                warnings=["Chain marked truncated by source with unspecified expected count"],
            )

        if observed_strike_count < min_expected_strikes:
            return ValidationResult(
                status=DQ_STATUS_WARNING,
                is_valid=True,
                flags={
                    DQ_FLAG_INSUFFICIENT_STRIKES: {
                        "observed_count": observed_strike_count,
                        "min_required": min_expected_strikes,
                    }
                },
                warnings=[
                    f"Observed strike count ({observed_strike_count}) is below minimum recommended threshold ({min_expected_strikes})"
                ],
            )

        return ValidationResult(status=DQ_STATUS_VALID, is_valid=True)

    # -----------------------------------------------------------------------
    # 3. Timestamp Data Quality Validation
    # -----------------------------------------------------------------------

    @staticmethod
    def validate_timestamps(
        market_timestamp: datetime,
        source_timestamp: datetime | None,
        ingestion_timestamp: datetime,
        available_at: datetime,
        skew_tolerance_ms: int = DEFAULT_CLOCK_SKEW_TOLERANCE_MS,
    ) -> ValidationResult:
        """Validate multi-timestamp ordering, availability, and clock skew.

        Relationships verified:
          1. market_timestamp <= ingestion_timestamp (with tolerance)
          2. source_timestamp clock skew against market_timestamp
          3. available_at >= ingestion_timestamp (knowledge availability)
        """
        # Ensure UTC timezone awareness
        def _to_utc(dt: datetime) -> datetime:
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)

        mkt = _to_utc(market_timestamp)
        ing = _to_utc(ingestion_timestamp)
        avail = _to_utc(available_at)
        src = _to_utc(source_timestamp) if source_timestamp is not None else None

        result = ValidationResult(status=DQ_STATUS_VALID, is_valid=True)

        # 1. Market timestamp cannot be in the far future relative to ingestion
        mkt_lead_ms = (mkt - ing).total_seconds() * 1000.0
        if mkt_lead_ms > skew_tolerance_ms:
            return ValidationResult(
                status=DQ_STATUS_REJECTED,
                is_valid=False,
                flags={
                    DQ_FLAG_FUTURE_MARKET_TIMESTAMP: {
                        "market_timestamp": mkt.isoformat(),
                        "ingestion_timestamp": ing.isoformat(),
                        "lead_ms": mkt_lead_ms,
                    }
                },
                errors=[
                    f"Market timestamp ({mkt.isoformat()}) exceeds ingestion time ({ing.isoformat()}) by {mkt_lead_ms:.0f}ms"
                ],
            )

        # 2. Source timestamp clock-skew check
        if src is not None:
            # Clock skew: source timestamp appearing earlier than market observation
            src_skew_ms = (mkt - src).total_seconds() * 1000.0
            if src_skew_ms > 0:
                if src_skew_ms <= skew_tolerance_ms:
                    result = result.merge(
                        ValidationResult(
                            status=DQ_STATUS_ACCEPT_WITH_FLAG,
                            is_valid=True,
                            flags={
                                DQ_FLAG_CLOCK_SKEW_TOLERATED: {
                                    "skew_ms": src_skew_ms,
                                    "tolerance_ms": skew_tolerance_ms,
                                }
                            },
                            warnings=[
                                f"Source timestamp clock skew of {src_skew_ms:.0f}ms accepted within {skew_tolerance_ms}ms tolerance"
                            ],
                        )
                    )
                elif src_skew_ms > MAX_ALLOWABLE_CLOCK_SKEW_MS:
                    return ValidationResult(
                        status=DQ_STATUS_REJECTED,
                        is_valid=False,
                        flags={
                            DQ_FLAG_CLOCK_SKEW_EXCESSIVE: {
                                "skew_ms": src_skew_ms,
                                "max_allowable_ms": MAX_ALLOWABLE_CLOCK_SKEW_MS,
                            }
                        },
                        errors=[
                            f"Source timestamp clock skew of {src_skew_ms:.0f}ms exceeds maximum allowable limit ({MAX_ALLOWABLE_CLOCK_SKEW_MS}ms)"
                        ],
                    )
                else:
                    result = result.merge(
                        ValidationResult(
                            status=DQ_STATUS_WARNING,
                            is_valid=True,
                            flags={
                                DQ_FLAG_CLOCK_SKEW_WARNING: {
                                    "skew_ms": src_skew_ms,
                                    "tolerance_ms": skew_tolerance_ms,
                                }
                            },
                            warnings=[
                                f"Source timestamp clock skew of {src_skew_ms:.0f}ms exceeds {skew_tolerance_ms}ms tolerance"
                            ],
                        )
                    )

        # 3. Available_at cannot precede ingestion_timestamp
        avail_inversion_ms = (ing - avail).total_seconds() * 1000.0
        if avail_inversion_ms > 0:
            result = result.merge(
                ValidationResult(
                    status=DQ_STATUS_WARNING,
                    is_valid=True,
                    flags={
                        DQ_FLAG_AVAILABLE_AT_INVERSION: {
                            "available_at": avail.isoformat(),
                            "ingestion_timestamp": ing.isoformat(),
                            "inversion_ms": avail_inversion_ms,
                        }
                    },
                    warnings=[
                        f"available_at ({avail.isoformat()}) precedes ingestion_timestamp ({ing.isoformat()}) by {avail_inversion_ms:.0f}ms"
                    ],
                )
            )

        return result

    # -----------------------------------------------------------------------
    # 4. Consolidated Snapshot Header Validation
    # -----------------------------------------------------------------------

    @classmethod
    def validate_snapshot_header(
        cls,
        underlying: str,
        strikes: list[Decimal | int | float | str],
        market_timestamp: datetime,
        source_timestamp: datetime | None,
        ingestion_timestamp: datetime,
        available_at: datetime,
        expected_strike_count: int | None = None,
        is_truncated: bool = False,
        skew_tolerance_ms: int = DEFAULT_CLOCK_SKEW_TOLERANCE_MS,
    ) -> ValidationResult:
        """Perform consolidated validation across strike grid, completeness, and timestamps.

        Returns:
            ValidationResult with merged status, aggregated quality flags, errors, and warnings.
        """
        # 1. Strike sequence validation
        strike_res = cls.validate_strike_sequence(strikes=strikes, underlying=underlying)

        # 2. Completeness validation
        observed_count = len(strikes)
        comp_res = cls.validate_chain_completeness(
            observed_strike_count=observed_count,
            expected_strike_count=expected_strike_count,
            is_truncated=is_truncated,
        )

        # 3. Timestamp validation
        ts_res = cls.validate_timestamps(
            market_timestamp=market_timestamp,
            source_timestamp=source_timestamp,
            ingestion_timestamp=ingestion_timestamp,
            available_at=available_at,
            skew_tolerance_ms=skew_tolerance_ms,
        )

        return strike_res.merge(comp_res).merge(ts_res)


# Module-level aliases for direct functional import
validate_strike = OptionChainValidationService.validate_strike
validate_strike_sequence = OptionChainValidationService.validate_strike_sequence
validate_chain_completeness = OptionChainValidationService.validate_chain_completeness
validate_timestamps = OptionChainValidationService.validate_timestamps
validate_snapshot_header = OptionChainValidationService.validate_snapshot_header
