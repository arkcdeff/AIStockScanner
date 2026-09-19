"""What Changed Screening Schema — G.11-D-4 Multi-Stock Universe Screener Models.

All models are immutable (frozen) and deterministic, consistent with G.11-D-3.

The screener is stateless: it accepts a request, resolves a universe, fans out
D-3 interpret() calls with bounded concurrency, then collects + filters results.
No new DB schema is required.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.what_changed_interpretation_config import (
    DirectionalBias,
    OverallState,
)
from app.services.what_changed_screening_config import (
    SCREENING_MAX_CUSTOM_SYMBOLS,
    ScreeningFilterOperator,
    ScreeningOutcomeStatus,
    ScreeningUniverse,
)

# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ScreeningFilterCriteria(BaseModel):
    """Declarative filter criteria applied to each symbol's D-3 interpretation.

    All fields are optional — absent fields are not evaluated.
    Multiple fields are combined with ``operator`` (default AND).
    """

    model_config = ConfigDict(frozen=True)

    operator: ScreeningFilterOperator = ScreeningFilterOperator.AND

    # OverallState filter — match any of the provided states
    overall_states: list[OverallState] | None = Field(
        default=None,
        description="Match symbols whose overall_state is in this list",
    )

    # DirectionalBias filter
    directional_biases: list[DirectionalBias] | None = Field(
        default=None,
        description="Match symbols whose directional_bias is in this list",
    )

    # Convergence ratio filter (inclusive lower bound)
    min_convergence_ratio: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Minimum convergence_ratio (0.0–1.0 inclusive)",
    )

    # Supporting domain count filter (inclusive lower bound)
    min_supporting_domains: int | None = Field(
        default=None,
        ge=0,
        description="Minimum number of supporting domains",
    )

    # PIT-safe filter
    require_pit_safe: bool | None = Field(
        default=None,
        description="If True, only include symbols where pit_safe is True",
    )


class ScreeningRequest(BaseModel):
    """Incoming request for a what-changed screening run.

    One of ``universe``, ``watchlist_id``, or ``symbols`` must be provided.
    When ``universe`` is WATCHLIST, ``watchlist_id`` is required.
    """

    model_config = ConfigDict(frozen=True)

    # Universe selection (mutually exclusive but validated together)
    universe: ScreeningUniverse | None = Field(
        default=None,
        description="Predefined universe to screen",
    )
    watchlist_id: int | None = Field(
        default=None,
        description="Watchlist ID — required when universe=WATCHLIST",
    )
    symbols: list[str] | None = Field(
        default=None,
        max_length=SCREENING_MAX_CUSTOM_SYMBOLS,
        description="Custom symbol list (max 100)",
    )

    # Temporal parameters
    as_of_date: date | None = Field(
        default=None,
        description="Point-in-time evaluation date (defaults to latest available)",
    )
    comparison_window: int = Field(
        default=1,
        ge=1,
        le=30,
        description="Trading-day look-back window forwarded to D-3",
    )

    # Filter
    filter_criteria: ScreeningFilterCriteria = Field(
        default_factory=ScreeningFilterCriteria,
        description="Declarative criteria to include a symbol in matched results",
    )

    # Result controls
    include_non_matched: bool = Field(
        default=False,
        description="Include NO_MATCH / NO_DATA / ERROR symbols in response",
    )
    max_results: int | None = Field(
        default=None,
        ge=1,
        le=500,
        description="Truncate matched results to this count (None = unlimited)",
    )

    @model_validator(mode="after")
    def _validate_universe_selection(self) -> ScreeningRequest:
        has_universe = self.universe is not None
        has_watchlist = self.watchlist_id is not None
        has_symbols = self.symbols is not None and len(self.symbols) > 0

        # At least one selection source must be provided
        if not any([has_universe, has_watchlist, has_symbols]):
            raise ValueError(
                "One of 'universe', 'watchlist_id', or 'symbols' must be provided"
            )

        # WATCHLIST universe requires watchlist_id
        if self.universe == ScreeningUniverse.WATCHLIST and not has_watchlist:
            raise ValueError("watchlist_id is required when universe=WATCHLIST")

        # watchlist_id without universe=WATCHLIST is ignored but not an error

        # Custom symbols length validated by Field(max_length=...) but
        # we also guard against None in symbols list
        if has_symbols:
            clean = [s for s in (self.symbols or []) if s and s.strip()]
            if len(clean) == 0:
                raise ValueError("symbols list must contain at least one non-empty symbol")
            if len(clean) > SCREENING_MAX_CUSTOM_SYMBOLS:
                raise ValueError(
                    f"symbols list exceeds maximum of {SCREENING_MAX_CUSTOM_SYMBOLS}"
                )

        return self


# ---------------------------------------------------------------------------
# Per-symbol result model
# ---------------------------------------------------------------------------


class SymbolScreeningResult(BaseModel):
    """Per-symbol outcome from a screening run."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    company_name: str | None = None
    status: ScreeningOutcomeStatus
    matched: bool

    # D-3 fields — present when status=MATCHED or NO_MATCH
    evaluation_date: date | None = None
    comparison_date: date | None = None
    overall_state: OverallState | None = None
    directional_bias: DirectionalBias | None = None
    convergence_ratio: float | None = None
    supporting_domains: list[str] = Field(default_factory=list)
    conflicting_domains: list[str] = Field(default_factory=list)
    context_domains: list[str] = Field(default_factory=list)
    pit_safe: bool | None = None

    # Error detail — present when status=ERROR
    error_detail: str | None = None

    # Metadata passthrough for UI
    extra: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Screening response
# ---------------------------------------------------------------------------


class ScreeningRunSummary(BaseModel):
    """Aggregate statistics for a completed screening run."""

    model_config = ConfigDict(frozen=True)

    total_universe_size: int
    matched_count: int
    no_match_count: int
    no_data_count: int
    error_count: int
    as_of_date: date | None


class ScreeningResponse(BaseModel):
    """Full response from a what-changed screening run."""

    model_config = ConfigDict(frozen=True)

    request_echo: ScreeningRequest
    summary: ScreeningRunSummary
    results: list[SymbolScreeningResult]
