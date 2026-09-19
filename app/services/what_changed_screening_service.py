"""What Changed Screening Service — G.11-D-4 Multi-Stock Universe Screener.

Stateless, on-demand screener. Accepts a :class:`ScreeningRequest`, resolves
the requested universe (PIT-safe), fans out D-3 ``interpret()`` calls with
bounded async concurrency (semaphore, max 5 workers), and collects filtered
:class:`ScreeningResponse` results.

Key design constraints (from G.11-D-4 architecture decision):
- No AI at screening layer (``include_ai_narrative=False`` always).
- No new DB schema changes.
- Each concurrent worker uses its own isolated ``AsyncSession``.
- Watchlist > 100 symbols → silently truncated (not rejected).
- Custom symbols list > 100 → rejected upstream by Pydantic validator.
- PIT-safe universe resolution via ``HistoricalQueryService``.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.derivative_contract import DerivativeContract
from app.models.market_index import MarketIndex
from app.models.stock import Stock
from app.schemas.what_changed_screening import (
    ScreeningFilterCriteria,
    ScreeningRequest,
    ScreeningResponse,
    ScreeningRunSummary,
    SymbolScreeningResult,
)
from app.services.historical_query import HistoricalQueryService
from app.services.watchlist import WatchlistService
from app.services.what_changed_interpretation_service import (
    WhatChangedInterpretationService,
)
from app.services.what_changed_screening_config import (
    SCREENING_MAX_WORKERS,
    SCREENING_WATCHLIST_SOFT_CAP,
    ScreeningOutcomeStatus,
    ScreeningUniverse,
)

logger = logging.getLogger(__name__)

# Index universes that can be resolved via HistoricalQueryService (PIT-safe).
_INDEX_UNIVERSE_CODES: frozenset[str] = frozenset(
    {
        ScreeningUniverse.NIFTY_50,
        ScreeningUniverse.NIFTY_NEXT_50,
        ScreeningUniverse.NIFTY_MIDCAP_100,
        ScreeningUniverse.NIFTY_BANK,
    }
)


class WhatChangedScreeningService:
    """G.11-D-4 multi-stock what-changed screener.

    Parameters
    ----------
    session:
        The *coordinator* session used for universe resolution (index lookups,
        watchlist resolution, F&O contract queries).  Worker coroutines open
        their own isolated sessions from ``session_factory``.
    session_factory:
        Factory returned by :func:`app.db.session.get_session_factory`.
        Required so each concurrent worker can open its own ``AsyncSession``.
    """

    def __init__(
        self,
        session: AsyncSession,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session = session
        self._session_factory = session_factory

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def screen(self, request: ScreeningRequest) -> ScreeningResponse:
        """Execute a screening run and return the aggregated response."""
        # 1. Resolve the universe of symbols
        as_of = request.as_of_date
        symbols_with_names = await self._resolve_universe(request, as_of)

        # 2. Fan out D-3 calls with bounded concurrency
        semaphore = asyncio.Semaphore(SCREENING_MAX_WORKERS)
        tasks = [
            self._evaluate_symbol(
                symbol=sym,
                company_name=name,
                request=request,
                semaphore=semaphore,
            )
            for sym, name in symbols_with_names
        ]
        raw_results: list[SymbolScreeningResult] = list(
            await asyncio.gather(*tasks, return_exceptions=False)
        )

        # 3. Filter first, then deterministic ranking, then truncate.
        #    Ranking (F.11-D-4 remediation, G11D4-FIND-03): matched results are
        #    ordered by D-3 convergence_ratio DESC with symbol ASC as the
        #    deterministic tie-break. None convergence sorts after every
        #    defined ratio, also by symbol ASC. No predictive score is
        #    introduced — only certified D-3 fields.
        matched = [r for r in raw_results if r.matched]
        matched.sort(key=self._rank_key)
        non_matched = [r for r in raw_results if not r.matched]

        # 4. Apply max_results cap AFTER ranking (never before).
        if request.max_results is not None:
            matched = matched[: request.max_results]

        # 5. Build summary
        summary = ScreeningRunSummary(
            total_universe_size=len(symbols_with_names),
            matched_count=len([r for r in raw_results if r.status == ScreeningOutcomeStatus.MATCHED]),
            no_match_count=len([r for r in raw_results if r.status == ScreeningOutcomeStatus.NO_MATCH]),
            no_data_count=len([r for r in raw_results if r.status == ScreeningOutcomeStatus.NO_DATA]),
            error_count=len([r for r in raw_results if r.status == ScreeningOutcomeStatus.ERROR]),
            as_of_date=as_of,
        )

        # 6. Compose results list
        results = matched + (non_matched if request.include_non_matched else [])

        return ScreeningResponse(
            request_echo=request,
            summary=summary,
            results=results,
        )

    # ------------------------------------------------------------------
    # Universe resolution
    # ------------------------------------------------------------------

    async def _resolve_universe(
        self,
        request: ScreeningRequest,
        as_of: date | None,
    ) -> list[tuple[str, str | None]]:
        """Return an ordered list of ``(symbol, company_name)`` tuples.

        Resolution priority:
        1. ``symbols`` — custom list, deduped, stripped.
        2. ``universe == WATCHLIST`` — via WatchlistService.
        3. ``universe == FNO`` — F&O underlying stocks.
        4. Index universe — via HistoricalQueryService (PIT-safe).
        """
        if request.symbols:
            # Custom list — already validated ≤ 100 by schema
            clean = list(dict.fromkeys(s.strip().upper() for s in request.symbols if s.strip()))
            # Fetch company names in one query
            names = await self._fetch_company_names(clean)
            return [(sym, names.get(sym)) for sym in clean]

        if request.universe == ScreeningUniverse.WATCHLIST:
            return await self._resolve_watchlist(request.watchlist_id)

        if request.universe == ScreeningUniverse.FNO:
            return await self._resolve_fno(as_of)

        if request.universe in _INDEX_UNIVERSE_CODES:
            return await self._resolve_index(str(request.universe), as_of)

        raise ValueError(f"Unresolvable universe: {request.universe!r}")

    async def _resolve_index(
        self,
        index_code: str,
        as_of: date | None,
    ) -> list[tuple[str, str | None]]:
        """Resolve an index universe via HistoricalQueryService (PIT-safe)."""
        # Look up index id from the market_indices table
        result = await self._session.execute(
            select(MarketIndex.id).where(MarketIndex.code == index_code)
        )
        index_id = result.scalar_one_or_none()
        if index_id is None:
            logger.warning("Index code %r not found in market_indices table", index_code)
            return []

        effective_date = as_of or date.today()
        hist_svc = HistoricalQueryService(self._session)
        stocks = await hist_svc.get_universe_as_of(
            as_of_date=effective_date,
            index_id=index_id,
        )
        return [(s.symbol, s.company_name if hasattr(s, "company_name") else None) for s in stocks]

    async def _resolve_fno(
        self,
        as_of: date | None,
    ) -> list[tuple[str, str | None]]:
        """Resolve the F&O underlying stock universe.

        Point-in-time semantics: when ``as_of`` is supplied, only derivative
        contracts available on or before that date are considered
        (``available_at <= as_of``), so future contract listings never leak
        into a historical universe. When ``as_of`` is ``None`` the live
        (unfiltered) universe is preserved.
        """
        fno_stmt = select(DerivativeContract.underlying_stock_id).where(
            DerivativeContract.underlying_stock_id.is_not(None)
        )
        if as_of is not None:
            fno_stmt = fno_stmt.where(DerivativeContract.available_at <= as_of)
        fno_res = await self._session.execute(fno_stmt.distinct())
        fno_stock_ids = {row[0] for row in fno_res.all()}

        if not fno_stock_ids:
            return []

        stocks_result = await self._session.execute(
            select(Stock)
            .where(Stock.id.in_(fno_stock_ids))
            .where(Stock.is_active.is_(True))
            .order_by(Stock.symbol)
        )
        stocks = list(stocks_result.scalars())
        return [(s.symbol, getattr(s, "company_name", None)) for s in stocks]

    async def _resolve_watchlist(
        self,
        watchlist_id: int | None,
    ) -> list[tuple[str, str | None]]:
        """Resolve a watchlist to a list of (symbol, company_name) tuples."""
        if watchlist_id is None:
            raise ValueError("watchlist_id is required for WATCHLIST universe")

        wl_svc = WatchlistService(self._session)
        watchlist = await wl_svc.get(watchlist_id)
        if watchlist is None:
            raise ValueError(f"Watchlist {watchlist_id} not found")

        # Collect stock_ids, respecting soft cap
        entries = watchlist.stocks or []
        if len(entries) > SCREENING_WATCHLIST_SOFT_CAP:
            logger.info(
                "Watchlist %d has %d stocks — truncating to %d",
                watchlist_id,
                len(entries),
                SCREENING_WATCHLIST_SOFT_CAP,
            )
            entries = entries[:SCREENING_WATCHLIST_SOFT_CAP]

        stock_ids = [e.stock_id for e in entries]
        if not stock_ids:
            return []

        # Fetch Stock rows for company names
        res = await self._session.execute(
            select(Stock).where(Stock.id.in_(stock_ids)).order_by(Stock.symbol)
        )
        stocks_by_id = {s.id: s for s in res.scalars()}

        result: list[tuple[str, str | None]] = []
        for entry in entries:
            stock = stocks_by_id.get(entry.stock_id)
            if stock is not None:
                result.append((stock.symbol, getattr(stock, "company_name", None)))
        return result

    async def _fetch_company_names(self, symbols: list[str]) -> dict[str, str | None]:
        """Batch-fetch company names for a list of symbols."""
        if not symbols:
            return {}
        res = await self._session.execute(
            select(Stock.symbol, Stock.company_name).where(Stock.symbol.in_(symbols))
        )
        return {row[0]: row[1] for row in res.all()}

    # ------------------------------------------------------------------
    # Per-symbol evaluation
    # ------------------------------------------------------------------

    async def _evaluate_symbol(
        self,
        symbol: str,
        company_name: str | None,
        request: ScreeningRequest,
        semaphore: asyncio.Semaphore,
    ) -> SymbolScreeningResult:
        """Evaluate a single symbol using its own isolated AsyncSession."""
        async with semaphore:
            try:
                async with self._session_factory() as worker_session:
                    svc = WhatChangedInterpretationService(worker_session)
                    interpretation = await svc.interpret(
                        symbol=symbol,
                        evaluation_date=request.as_of_date,
                        comparison_window=request.comparison_window,
                        include_ai_narrative=False,
                    )

                # Resolve convergence_ratio safely
                convergence_ratio: float | None = None
                if interpretation.convergence is not None:
                    convergence_ratio = interpretation.convergence.convergence_ratio

                supporting_domains: list[str] = []
                conflicting_domains: list[str] = []
                context_domains: list[str] = []
                if interpretation.convergence is not None:
                    supporting_domains = list(interpretation.convergence.supporting_domains)
                    conflicting_domains = list(interpretation.convergence.conflicting_domains)
                    context_domains = list(interpretation.convergence.context_domains)

                # Apply filter
                matched = self._apply_filter(
                    request.filter_criteria,
                    overall_state=interpretation.overall_state,
                    directional_bias=interpretation.directional_bias,
                    convergence_ratio=convergence_ratio,
                    supporting_domains=supporting_domains,
                    pit_safe=interpretation.pit_safe,
                )

                status = (
                    ScreeningOutcomeStatus.MATCHED
                    if matched
                    else ScreeningOutcomeStatus.NO_MATCH
                )

                return SymbolScreeningResult(
                    symbol=symbol,
                    company_name=company_name,
                    status=status,
                    matched=matched,
                    evaluation_date=interpretation.evaluation_date,
                    comparison_date=interpretation.comparison_date,
                    overall_state=interpretation.overall_state,
                    directional_bias=interpretation.directional_bias,
                    convergence_ratio=convergence_ratio,
                    supporting_domains=supporting_domains,
                    conflicting_domains=conflicting_domains,
                    context_domains=context_domains,
                    pit_safe=interpretation.pit_safe,
                )

            except Exception as exc:  # noqa: BLE001
                logger.warning("Screening error for %s: %s", symbol, exc)
                return SymbolScreeningResult(
                    symbol=symbol,
                    company_name=company_name,
                    status=ScreeningOutcomeStatus.ERROR,
                    matched=False,
                    error_detail=str(exc),
                )

    # ------------------------------------------------------------------
    # Deterministic ranking (G11D4-FIND-03 remediation)
    # ------------------------------------------------------------------

    @staticmethod
    def _rank_key(result: SymbolScreeningResult) -> tuple[int, float, str]:
        """Deterministic sort key for matched screening results.

        Contract: ``convergence_ratio DESC``, then ``symbol ASC`` as the
        tie-break. ``None`` convergence is deterministic and sorts after
        every defined ratio (never before, never non-deterministically).
        Uses only certified D-3 fields — no predictive score, no AI.
        """
        # Descending ratio via negation; None sorts last via the leading flag
        # (0 = has ratio, ranks first; 1 = None ratio, ranks last).
        ratio = result.convergence_ratio
        has_ratio = 0 if ratio is not None else 1
        return (has_ratio, -(ratio if ratio is not None else 0.0), result.symbol)

    # ------------------------------------------------------------------
    # Filter application
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_filter(
        criteria: ScreeningFilterCriteria,
        *,
        overall_state: object,
        directional_bias: object,
        convergence_ratio: float | None,
        supporting_domains: list[str],
        pit_safe: bool,
    ) -> bool:
        """Return True if the symbol satisfies all active filter criteria.

        Each active criterion is evaluated independently.  The final verdict is
        determined by the ``operator`` (AND = all must pass, OR = any must pass).
        """
        # Collect per-criterion results (only for active criteria)
        checks: list[bool] = []

        if criteria.overall_states is not None:
            checks.append(overall_state in criteria.overall_states)

        if criteria.directional_biases is not None:
            checks.append(directional_bias in criteria.directional_biases)

        if criteria.min_convergence_ratio is not None:
            ratio = convergence_ratio if convergence_ratio is not None else 0.0
            checks.append(ratio >= criteria.min_convergence_ratio)

        if criteria.min_supporting_domains is not None:
            checks.append(len(supporting_domains) >= criteria.min_supporting_domains)

        if criteria.require_pit_safe is True:
            checks.append(pit_safe is True)

        # No criteria → match everything
        if not checks:
            return True

        from app.services.what_changed_screening_config import ScreeningFilterOperator

        if criteria.operator == ScreeningFilterOperator.OR:
            return any(checks)
        return all(checks)
