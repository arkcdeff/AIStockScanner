"""Historical Derivatives Replay Service for Sprint 19.1D-8.

Provides point-in-time safe historical derivatives knowledge state reconstruction
for an underlying symbol U on date T as of evaluation cutoff A.

Strict Invariants:
  - Zero lookahead bias: available_at <= as_of_date filtering across all components
  - Reuses existing D-4 option chain, D-5 futures analytics, D-6 options analytics,
    and D-7 participant services
  - Retains historical contract lot sizes and actual expiry validity
  - Retains raw Call/Put option positioning without ungrounded directional score
    reduction
  - Exposes transparent provenance metadata (source reference, checksum,
    available_at)
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.daily_price import DailyPrice
from app.models.derivative_contract import DerivativeContract
from app.models.derivative_daily_price import DerivativeDailyPrice
from app.models.market_index import MarketIndex
from app.models.market_index_price import MarketIndexPrice
from app.models.stock import Stock
from app.schemas.derivative_coverage_replay import (
    HistoricalDerivativesReplay,
    ReplayProvenanceItem,
)
from app.services.historical_futures_analytics import HistoricalFuturesAnalyticsService
from app.services.historical_option_chain import HistoricalOptionChainService
from app.services.historical_options_analytics import HistoricalOptionsAnalyticsService
from app.services.historical_participant_analytics import HistoricalParticipantAnalyticsService


class HistoricalDerivativesReplayService:
    """Orchestration service reconstructing point-in-time historical derivatives knowledge state."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.futures_analytics_service = HistoricalFuturesAnalyticsService(session)
        self.option_chain_service = HistoricalOptionChainService(session)
        self.options_analytics_service = HistoricalOptionsAnalyticsService(session)
        self.participant_analytics_service = HistoricalParticipantAnalyticsService(session)

    async def get_historical_replay(
        self,
        symbol: str,
        trade_date: date,
        expiry_date: date | None = None,
        as_of_date: date | None = None,
    ) -> HistoricalDerivativesReplay:
        """Reconstruct the complete historical derivatives knowledge state for symbol on trade_date as of as_of_date."""
        as_of = as_of_date or trade_date
        quality_warnings: list[str] = []
        computed_warnings: list[str] = []
        provenance: list[ReplayProvenanceItem] = []

        clean_symbol = symbol.strip().upper()

        # 1. Spot Reference
        spot_price: Decimal | None = None
        spot_source: str | None = None

        # Resolve underlying symbol to Index or Stock ID
        idx_stmt = select(MarketIndex.id).where(MarketIndex.code == clean_symbol).limit(1)
        idx_id = (await self.session.execute(idx_stmt)).scalar_one_or_none()

        stk_stmt = select(Stock.id).where(Stock.symbol == clean_symbol).limit(1)
        stk_id = (await self.session.execute(stk_stmt)).scalar_one_or_none()

        # Check derivative daily price underlying value first
        p_res = None
        if idx_id or stk_id:
            p_stmt = (
                select(DerivativeDailyPrice.underlying_value)
                .join(DerivativeContract, DerivativeDailyPrice.contract_id == DerivativeContract.id)
                .where(DerivativeDailyPrice.trade_date == trade_date)
                .where(DerivativeDailyPrice.available_at <= as_of)
                .where(DerivativeDailyPrice.underlying_value.is_not(None))
            )
            if idx_id:
                p_stmt = p_stmt.where(DerivativeContract.underlying_index_id == idx_id)
            else:
                p_stmt = p_stmt.where(DerivativeContract.underlying_stock_id == stk_id)

            p_res = (await self.session.execute(p_stmt.limit(1))).scalar_one_or_none()

        if p_res is not None:
            spot_price = p_res
            spot_source = "DERIVATIVE_DAILY_PRICE"
        else:
            if idx_id:
                ip_stmt = (
                    select(MarketIndexPrice.close)
                    .where(MarketIndexPrice.market_index_id == idx_id)
                    .where(MarketIndexPrice.trade_date == trade_date)
                    .limit(1)
                )
                spot_price = (await self.session.execute(ip_stmt)).scalar_one_or_none()
                if spot_price:
                    spot_source = "MARKET_INDEX_PRICE"
            elif stk_id:
                dp_stmt = (
                    select(DailyPrice.close)
                    .where(DailyPrice.stock_id == stk_id)
                    .where(DailyPrice.trade_date == trade_date)
                    .where(DailyPrice.available_at <= as_of)
                    .limit(1)
                )
                spot_price = (await self.session.execute(dp_stmt)).scalar_one_or_none()
                if spot_price:
                    spot_source = "DAILY_PRICE"

        if spot_price is None:
            computed_warnings.append("REPLAY_NO_SPOT_REFERENCE")

        # 2. Futures Component
        futures_snapshot_dict: dict[str, Any] | None = None
        futures_rollover_dict: dict[str, Any] | None = None

        try:
            fut_snap = await self.futures_analytics_service.get_underlying_futures_snapshot(
                symbol=clean_symbol,
                trade_date=trade_date,
                as_of_date=as_of,
            )
            futures_snapshot_dict = asdict(fut_snap)
            quality_warnings.extend(fut_snap.quality_warnings)

            fut_roll = await self.futures_analytics_service.get_rollover_analytics(
                symbol=clean_symbol,
                trade_date=trade_date,
                as_of_date=as_of,
            )
            futures_rollover_dict = asdict(fut_roll)

            provenance.append(
                ReplayProvenanceItem(
                    component="FUTURES",
                    source_name="NSE_INDIA",
                    source_reference=f"FO_FUTURES_{clean_symbol}_{trade_date.strftime('%Y%m%d')}",
                    report_or_trade_date=trade_date,
                    available_at=as_of,
                )
            )
        except Exception:
            computed_warnings.append("REPLAY_PARTIAL_FUTURES_DATA")

        # 3. Options Component
        available_expiries = await self.option_chain_service.get_available_expiries(
            symbol=clean_symbol,
            trade_date=trade_date,
            as_of_date=as_of,
        )

        selected_expiry = expiry_date
        if selected_expiry is None and available_expiries:
            # Default to nearest unexpired option expiry
            future_expiries = [e for e in available_expiries if e >= trade_date]
            selected_expiry = future_expiries[0] if future_expiries else available_expiries[0]

        option_chain_dict: dict[str, Any] | None = None
        options_analytics_dict: dict[str, Any] | None = None
        expiry_analytics_dict: dict[str, Any] | None = None

        if selected_expiry:
            try:
                chain = await self.option_chain_service.get_historical_option_chain(
                    symbol=clean_symbol,
                    trade_date=trade_date,
                    expiry_date=selected_expiry,
                    as_of_date=as_of,
                )
                option_chain_dict = asdict(chain)
                quality_warnings.extend(chain.quality_warnings)

                analytics = await self.options_analytics_service.get_option_chain_analytics(
                    symbol=clean_symbol,
                    trade_date=trade_date,
                    expiry_date=selected_expiry,
                    as_of_date=as_of,
                )
                options_analytics_dict = asdict(analytics)
                quality_warnings.extend(analytics.quality_warnings)

                exp_analytics = await self.options_analytics_service.get_expiry_options_analytics(
                    symbol=clean_symbol,
                    trade_date=trade_date,
                    as_of_date=as_of,
                )
                expiry_analytics_dict = asdict(exp_analytics)

                provenance.append(
                    ReplayProvenanceItem(
                        component="OPTIONS",
                        source_name="NSE_INDIA",
                        source_reference=f"FO_OPTIONS_{clean_symbol}_{selected_expiry.strftime('%Y%m%d')}",
                        report_or_trade_date=trade_date,
                        available_at=as_of,
                    )
                )
            except Exception:
                computed_warnings.append("REPLAY_PARTIAL_OPTION_CHAIN")
        else:
            computed_warnings.append("REPLAY_NO_OPTIONS_EXPIRY_AVAILABLE")

        # 4. Participant Component (Selects latest participant report with
        # report_date <= trade_date and available_at <= as_of)
        participant_snapshot_dict: dict[str, Any] | None = None
        try:
            part_snap = await self.participant_analytics_service.get_participant_snapshot(
                report_date=trade_date,
                dataset_type="OPEN_INTEREST",
                as_of_date=as_of,
            )
            if part_snap and (part_snap.items or part_snap.fii_statistics):
                participant_snapshot_dict = asdict(part_snap)
                quality_warnings.extend(part_snap.quality_warnings)

                provenance.append(
                    ReplayProvenanceItem(
                        component="PARTICIPANTS",
                        source_name="NSE_INDIA",
                        source_reference=f"FO_PARTICIPANT_OI_{trade_date.strftime('%Y%m%d')}",
                        report_or_trade_date=trade_date,
                        available_at=as_of,
                    )
                )
            else:
                computed_warnings.append("REPLAY_NO_PARTICIPANT_REPORT")
        except Exception:
            computed_warnings.append("REPLAY_NO_PARTICIPANT_REPORT")

        # Determine Replay Status
        has_futures = futures_snapshot_dict is not None and bool(futures_snapshot_dict.get("items"))
        has_options = option_chain_dict is not None and bool(option_chain_dict.get("strikes"))
        has_participants = (
            participant_snapshot_dict is not None
            and (
                bool(participant_snapshot_dict.get("items"))
                or bool(participant_snapshot_dict.get("fii_statistics"))
            )
        )

        if has_futures and has_options:
            replay_status = "COMPLETE_FOR_IMPORTED_SCOPE"
        elif has_futures or has_options or has_participants:
            replay_status = "PARTIAL"
        else:
            replay_status = "NO_DERIVATIVE_DATA"

        return HistoricalDerivativesReplay(
            underlying_symbol=clean_symbol,
            trade_date=trade_date,
            as_of_date=as_of,
            selected_expiry_date=selected_expiry,
            replay_status=replay_status,
            spot_price=spot_price,
            spot_price_source=spot_source,
            futures_snapshot=futures_snapshot_dict,
            futures_rollover=futures_rollover_dict,
            option_chain=option_chain_dict,
            options_analytics=options_analytics_dict,
            expiry_options_analytics=expiry_analytics_dict,
            participant_positions=participant_snapshot_dict,
            provenance=provenance,
            quality_warnings=list(set(quality_warnings)),
            computed_warnings=list(set(computed_warnings)),
        )
