"""Historical Futures Analytics Service — Sprint 19.1D-5.

Provides a deterministic historical futures analytics layer over raw data
stored in DerivativeContract and DerivativeDailyPrice without mutating raw facts.

Calculates:
  - Price settlement fallback hierarchy & same-contract price change
  - Source vs derived ΔOI and mismatch detection
  - Threshold-based price/OI buildup classification
  - Spot reference resolution, absolute basis, basis %, and annualized simple basis %
  - Term structure futures curve, slope classification, and adjacent calendar spreads
  - Per-expiry OI distribution, rollover percentage formulas, and expiry window context
  - Bounded historical rollover comparison
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.daily_price import DailyPrice
from app.models.derivative_contract import DerivativeContract
from app.models.derivative_daily_price import DerivativeDailyPrice
from app.models.market_index import MarketIndex
from app.models.market_index_price import MarketIndexPrice
from app.models.security_alias import SecurityAlias
from app.models.stock import Stock
from app.schemas.derivative_futures_analytics import (
    FuturesContractAnalytics,
    FuturesContractObservation,
    FuturesCurveItem,
    FuturesCurveSnapshot,
    FuturesPriceReference,
    FuturesRolloverAnalytics,
)


class HistoricalFuturesAnalyticsService:
    """Service for deterministic historical futures analytics calculations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def resolve_price_reference(row: DerivativeDailyPrice) -> FuturesPriceReference:
        """Resolve futures price using settlement_price -> close -> last_price hierarchy."""
        if row.settlement_price is not None:
            return FuturesPriceReference(
                price_used=Decimal(str(row.settlement_price)),
                price_source_field="settlement_price",
            )
        elif row.close is not None:
            return FuturesPriceReference(
                price_used=Decimal(str(row.close)),
                price_source_field="close",
            )
        elif row.last_price is not None:
            return FuturesPriceReference(
                price_used=Decimal(str(row.last_price)),
                price_source_field="last_price",
            )
        else:
            raise ValueError(f"No price field available for DerivativeDailyPrice id={row.id}")

    async def get_contract_analytics(
        self,
        contract_id: int,
        trade_date: date,
        as_of_date: date | None = None,
        price_threshold_pct: float = 0.05,
        oi_threshold_pct: float = 0.5,
    ) -> FuturesContractAnalytics:
        """Calculate contract-level price/OI changes, buildup pattern, and basis."""
        as_of = as_of_date or trade_date

        # 1. Query contract master
        c_res = await self.session.execute(
            select(DerivativeContract).where(DerivativeContract.id == contract_id)
        )
        contract = c_res.scalar_one_or_none()
        if not contract:
            raise ValueError(f"DerivativeContract id={contract_id} not found")

        if contract.instrument_type != "FUTURE":
            raise ValueError(
                f"Contract id={contract_id} is "
                f"instrument_type='{contract.instrument_type}', expected 'FUTURE'"
            )

        # 2. Query current observation
        p_res = await self.session.execute(
            select(DerivativeDailyPrice)
            .where(DerivativeDailyPrice.contract_id == contract_id)
            .where(DerivativeDailyPrice.trade_date == trade_date)
            .where(DerivativeDailyPrice.available_at <= as_of)
        )
        curr_row = p_res.scalar_one_or_none()
        if not curr_row:
            raise ValueError(
                f"No futures daily price observation found for contract_id={contract_id} "
                f"on trade_date={trade_date} as_of={as_of}"
            )

        curr_pref = self.resolve_price_reference(curr_row)
        curr_source_spot = (
            Decimal(str(curr_row.underlying_value))
            if curr_row.underlying_value is not None
            else None
        )
        curr_obs = FuturesContractObservation(
            contract_id=contract.id,
            trading_symbol=contract.trading_symbol,
            expiry_date=contract.expiry_date,
            calendar_days_to_expiry=(contract.expiry_date - trade_date).days,
            trade_date=trade_date,
            price_reference=curr_pref,
            open_interest=curr_row.open_interest or 0,
            volume_contracts=curr_row.volume_contracts or 0,
            lot_size=contract.lot_size,
            source_change_in_oi=curr_row.change_in_open_interest,
            source_underlying_value=curr_source_spot,
            available_at=curr_row.available_at,
        )

        # 3. Query previous comparable observation for the SAME contract
        prev_stmt = (
            select(DerivativeDailyPrice)
            .where(DerivativeDailyPrice.contract_id == contract_id)
            .where(DerivativeDailyPrice.trade_date < trade_date)
            .where(DerivativeDailyPrice.available_at <= as_of)
            .order_by(DerivativeDailyPrice.trade_date.desc())
            .limit(1)
        )
        prev_res = await self.session.execute(prev_stmt)
        prev_row = prev_res.scalar_one_or_none()

        prev_obs: FuturesContractObservation | None = None
        if prev_row:
            prev_pref = self.resolve_price_reference(prev_row)
            prev_source_spot = (
                Decimal(str(prev_row.underlying_value))
                if prev_row.underlying_value is not None
                else None
            )
            prev_obs = FuturesContractObservation(
                contract_id=contract.id,
                trading_symbol=contract.trading_symbol,
                expiry_date=contract.expiry_date,
                calendar_days_to_expiry=(contract.expiry_date - prev_row.trade_date).days,
                trade_date=prev_row.trade_date,
                price_reference=prev_pref,
                open_interest=prev_row.open_interest or 0,
                volume_contracts=prev_row.volume_contracts or 0,
                lot_size=contract.lot_size,
                source_change_in_oi=prev_row.change_in_open_interest,
                source_underlying_value=prev_source_spot,
                available_at=prev_row.available_at,
            )

        quality_warnings: list[str] = []

        # 4. Price and OI changes
        price_change_abs: Decimal | None = None
        price_change_pct: Decimal | None = None
        derived_change_in_oi: int | None = None
        oi_change_pct: Decimal | None = None
        buildup_state = "INSUFFICIENT_HISTORY"

        if prev_obs:
            price_change_abs = curr_pref.price_used - prev_obs.price_reference.price_used
            if prev_obs.price_reference.price_used > Decimal("0"):
                price_change_pct = (
                    price_change_abs / prev_obs.price_reference.price_used
                ) * Decimal("100")

            derived_change_in_oi = curr_obs.open_interest - prev_obs.open_interest
            if prev_obs.open_interest > 0:
                oi_change_pct = (
                    Decimal(str(derived_change_in_oi)) / Decimal(str(prev_obs.open_interest))
                ) * Decimal("100")
            else:
                quality_warnings.append("FUTURES_ANALYTICS_ZERO_PREVIOUS_OI")

            if (
                curr_obs.source_change_in_oi is not None
                and curr_obs.source_change_in_oi != derived_change_in_oi
            ):
                quality_warnings.append("FUTURES_ANALYTICS_OI_CHANGE_MISMATCH")

            # Classification logic with thresholds
            p_thresh = Decimal(str(price_threshold_pct))
            oi_thresh = Decimal(str(oi_threshold_pct))

            is_price_up = price_change_pct is not None and price_change_pct > p_thresh
            is_price_down = price_change_pct is not None and price_change_pct < -p_thresh
            is_oi_up = oi_change_pct is not None and oi_change_pct > oi_thresh
            is_oi_down = oi_change_pct is not None and oi_change_pct < -oi_thresh

            if is_price_up and is_oi_up:
                buildup_state = "LONG_BUILDUP"
            elif is_price_down and is_oi_up:
                buildup_state = "SHORT_BUILDUP"
            elif is_price_up and is_oi_down:
                buildup_state = "SHORT_COVERING"
            elif is_price_down and is_oi_down:
                buildup_state = "LONG_UNWINDING"
            elif is_price_up and not is_oi_up and not is_oi_down:
                buildup_state = "PRICE_UP_OI_FLAT"
            elif is_price_down and not is_oi_up and not is_oi_down:
                buildup_state = "PRICE_DOWN_OI_FLAT"
            elif is_oi_up and not is_price_up and not is_price_down:
                buildup_state = "PRICE_FLAT_OI_UP"
            elif is_oi_down and not is_price_up and not is_price_down:
                buildup_state = "PRICE_FLAT_OI_DOWN"
            else:
                buildup_state = "NO_SIGNIFICANT_CHANGE"
        else:
            quality_warnings.append("FUTURES_ANALYTICS_NO_PREVIOUS_OBSERVATION")

        # 5. Spot & Basis calculations
        spot_val: Decimal | None = curr_source_spot
        spot_src: str | None = "SOURCE_UNDERLYING_VALUE" if curr_source_spot is not None else None

        if spot_val is None:
            # Fallback to independent spot
            u_type, u_id = await self._get_underlying_type_and_id(contract)
            spot_val, spot_src = await self._fetch_independent_spot(u_type, u_id, trade_date)

        basis_abs: Decimal | None = None
        basis_pct: Decimal | None = None
        annualized_basis_pct: Decimal | None = None

        if spot_val is not None and spot_val > Decimal("0"):
            basis_abs = curr_pref.price_used - spot_val
            basis_pct = (basis_abs / spot_val) * Decimal("100")

            dte = (contract.expiry_date - trade_date).days
            if dte > 0 and basis_pct is not None:
                annualized_basis_pct = (basis_pct * Decimal("365")) / Decimal(str(dte))
        else:
            quality_warnings.append("FUTURES_ANALYTICS_NO_SPOT")

        return FuturesContractAnalytics(
            contract_id=contract.id,
            trading_symbol=contract.trading_symbol,
            expiry_date=contract.expiry_date,
            calendar_days_to_expiry=(contract.expiry_date - trade_date).days,
            trade_date=trade_date,
            current_observation=curr_obs,
            previous_observation=prev_obs,
            price_change_absolute=price_change_abs,
            price_change_pct=price_change_pct,
            source_change_in_oi=curr_obs.source_change_in_oi,
            derived_change_in_oi=derived_change_in_oi,
            oi_change_pct=oi_change_pct,
            buildup_state=buildup_state,
            spot_value=spot_val,
            spot_source=spot_src,
            basis_absolute=basis_abs,
            basis_pct=basis_pct,
            annualized_simple_basis_pct=annualized_basis_pct,
            quality_warnings=quality_warnings,
        )

    async def get_underlying_futures_snapshot(
        self,
        symbol: str,
        trade_date: date,
        as_of_date: date | None = None,
    ) -> FuturesCurveSnapshot:
        """Construct term structure snapshot across available expiries for an underlying."""
        as_of = as_of_date or trade_date

        underlying_info = await self._resolve_underlying(symbol)
        if not underlying_info:
            raise ValueError(f"Unknown underlying symbol '{symbol}'")

        u_type, u_id, u_symbol, _ = underlying_info

        # Query all futures contracts & daily prices on trade_date
        stmt = (
            select(DerivativeDailyPrice, DerivativeContract)
            .join(DerivativeContract, DerivativeDailyPrice.contract_id == DerivativeContract.id)
            .where(DerivativeContract.instrument_type == "FUTURE")
            .where(DerivativeDailyPrice.trade_date == trade_date)
            .where(DerivativeDailyPrice.available_at <= as_of)
            .where(DerivativeContract.available_at <= as_of)
        )

        if u_type == "INDEX":
            stmt = stmt.where(DerivativeContract.underlying_index_id == u_id)
        else:
            stmt = stmt.where(DerivativeContract.underlying_stock_id == u_id)

        stmt = stmt.order_by(DerivativeContract.expiry_date.asc())

        res = await self.session.execute(stmt)
        rows = res.all()

        if not rows:
            return FuturesCurveSnapshot(
                underlying_type=u_type,
                underlying_id=u_id,
                underlying_symbol=u_symbol,
                trade_date=trade_date,
                spot_value=None,
                spot_source=None,
                curve_slope="INSUFFICIENT_EXPIRIES",
                total_futures_oi=0,
                items=[],
                adjacent_spreads=[],
                as_of_date=as_of,
            )

        # Total OI calculation
        total_oi = sum(p.open_interest or 0 for p, _ in rows)

        # Spot resolution
        source_spots = [
            Decimal(str(p.underlying_value))
            for p, _ in rows
            if p.underlying_value is not None
        ]
        spot_val: Decimal | None = source_spots[0] if source_spots else None
        spot_src: str | None = "SOURCE_UNDERLYING_VALUE" if spot_val is not None else None

        if spot_val is None:
            spot_val, spot_src = await self._fetch_independent_spot(u_type, u_id, trade_date)

        curve_items: list[FuturesCurveItem] = []
        rank_labels = ["NEAR", "NEXT", "FAR_1", "FAR_2", "FAR_3"]

        for idx, (p_row, c_row) in enumerate(rows):
            rank = rank_labels[idx] if idx < len(rank_labels) else f"FAR_{idx - 1}"
            pref = self.resolve_price_reference(p_row)
            oi = p_row.open_interest or 0

            oi_share = (
                (Decimal(str(oi)) / Decimal(str(total_oi))) * Decimal("100")
                if total_oi > 0
                else Decimal("0")
            )

            basis_abs: Decimal | None = None
            basis_pct: Decimal | None = None
            if spot_val is not None and spot_val > Decimal("0"):
                basis_abs = pref.price_used - spot_val
                basis_pct = (basis_abs / spot_val) * Decimal("100")

            curve_items.append(
                FuturesCurveItem(
                    expiry_rank=rank,
                    expiry_date=c_row.expiry_date,
                    calendar_days_to_expiry=(c_row.expiry_date - trade_date).days,
                    contract_id=c_row.id,
                    trading_symbol=c_row.trading_symbol,
                    price=pref.price_used,
                    price_source_field=pref.price_source_field,
                    open_interest=oi,
                    volume_contracts=p_row.volume_contracts or 0,
                    oi_share_pct=oi_share,
                    basis_absolute=basis_abs,
                    basis_pct=basis_pct,
                    lot_size=c_row.lot_size,
                )
            )

        # Determine curve slope
        curve_slope = "INSUFFICIENT_EXPIRIES"
        if len(curve_items) >= 2:
            near_price = curve_items[0].price
            far_price = curve_items[-1].price
            diff = far_price - near_price
            diff_pct = (diff / near_price) * Decimal("100") if near_price > 0 else Decimal("0")

            if diff_pct > Decimal("0.05"):
                curve_slope = "UPWARD_SLOPING"
            elif diff_pct < Decimal("-0.05"):
                curve_slope = "DOWNWARD_SLOPING"
            else:
                curve_slope = "FLAT_CURVE"

        # Adjacent spreads
        adjacent_spreads: list[dict] = []
        for i in range(len(curve_items) - 1):
            n_item = curve_items[i]
            f_item = curve_items[i + 1]
            spread_abs = f_item.price - n_item.price
            spread_pct = (
                (spread_abs / n_item.price) * Decimal("100")
                if n_item.price > 0
                else Decimal("0")
            )
            adjacent_spreads.append(
                {
                    "nearer": n_item.expiry_rank,
                    "farther": f_item.expiry_rank,
                    "spread_absolute": spread_abs,
                    "spread_pct": spread_pct,
                }
            )

        return FuturesCurveSnapshot(
            underlying_type=u_type,
            underlying_id=u_id,
            underlying_symbol=u_symbol,
            trade_date=trade_date,
            spot_value=spot_val,
            spot_source=spot_src,
            curve_slope=curve_slope,
            total_futures_oi=total_oi,
            items=curve_items,
            adjacent_spreads=adjacent_spreads,
            as_of_date=as_of,
        )

    async def get_rollover_analytics(
        self,
        symbol: str,
        trade_date: date,
        as_of_date: date | None = None,
        expiry_window_days: int = 7,
    ) -> FuturesRolloverAnalytics:
        """Calculate rollover position migration metrics across expiries."""
        as_of = as_of_date or trade_date
        snapshot = await self.get_underlying_futures_snapshot(symbol, trade_date, as_of)

        quality_warnings: list[str] = []

        if not snapshot.items:
            quality_warnings.append("FUTURES_ROLLOVER_NO_CONTRACTS")
            return FuturesRolloverAnalytics(
                underlying_symbol=symbol,
                trade_date=trade_date,
                as_of_date=as_of,
                quality_warnings=quality_warnings,
            )

        near_item = snapshot.items[0]
        next_item = snapshot.items[1] if len(snapshot.items) > 1 else None

        near_oi = near_item.open_interest
        next_oi = next_item.open_interest if next_item else 0
        far_oi = sum(item.open_interest for item in snapshot.items[2:])
        total_oi = snapshot.total_futures_oi

        near_share = (
            (Decimal(str(near_oi)) / Decimal(str(total_oi))) * Decimal("100")
            if total_oi > 0
            else Decimal("0")
        )
        next_share = (
            (Decimal(str(next_oi)) / Decimal(str(total_oi))) * Decimal("100")
            if total_oi > 0
            else Decimal("0")
        )

        rollover_next: Decimal | None = None
        if (near_oi + next_oi) > 0:
            rollover_next = (
                Decimal(str(next_oi)) / Decimal(str(near_oi + next_oi))
            ) * Decimal("100")

        rollover_later: Decimal | None = None
        if total_oi > 0:
            rollover_later = (
                Decimal(str(total_oi - near_oi)) / Decimal(str(total_oi))
            ) * Decimal("100")

        is_expiry_win = near_item.calendar_days_to_expiry <= expiry_window_days

        if len(snapshot.items) < 2:
            quality_warnings.append("FUTURES_ROLLOVER_INSUFFICIENT_CONTRACTS")

        # Historical comparison lookup (query prior trade dates)
        hist_comp = await self._fetch_historical_rollover_comparison(
            underlying_type=snapshot.underlying_type,
            underlying_id=snapshot.underlying_id,
            current_trade_date=trade_date,
            as_of_date=as_of,
        )

        return FuturesRolloverAnalytics(
            underlying_symbol=snapshot.underlying_symbol,
            trade_date=trade_date,
            as_of_date=as_of,
            near_expiry_date=near_item.expiry_date,
            near_dte=near_item.calendar_days_to_expiry,
            next_expiry_date=next_item.expiry_date if next_item else None,
            near_expiry_oi=near_oi,
            next_expiry_oi=next_oi,
            far_expiries_oi=far_oi,
            total_futures_oi=total_oi,
            near_oi_share_pct=near_share,
            next_oi_share_pct=next_share,
            rollover_next_pct=rollover_next,
            rollover_later_pct=rollover_later,
            is_expiry_window=is_expiry_win,
            historical_rollover_comparison=hist_comp,
            quality_warnings=quality_warnings,
        )

    async def _resolve_underlying(self, symbol: str) -> tuple[str, int, str, str] | None:
        """Resolve symbol to (type, id, canonical_symbol, name)."""
        sym = symbol.strip().upper()

        idx_res = await self.session.execute(
            select(MarketIndex).where(MarketIndex.code == sym)
        )
        idx = idx_res.scalar_one_or_none()
        if idx:
            return ("INDEX", idx.id, idx.code, idx.name)

        INDEX_SYM_MAP = {
            "NIFTY 50": "NIFTY",
            "NIFTY50": "NIFTY",
            "BANKNIFTY": "NIFTY_BANK",
            "NIFTY BANK": "NIFTY_BANK",
            "FINNIFTY": "NIFTY_FIN_SERV",
            "NIFTY FIN SERVICE": "NIFTY_FIN_SERV",
            "MIDCPNIFTY": "NIFTY_MID_SELECT",
            "NIFTY MID SELECT": "NIFTY_MID_SELECT",
            "NIFTYNXT50": "NIFTY_NEXT_50",
            "NIFTY NEXT 50": "NIFTY_NEXT_50",
        }
        if sym in INDEX_SYM_MAP:
            target_code = INDEX_SYM_MAP[sym]
            idx_mapped_res = await self.session.execute(
                select(MarketIndex).where(MarketIndex.code == target_code)
            )
            idx_mapped = idx_mapped_res.scalar_one_or_none()
            if idx_mapped:
                return ("INDEX", idx_mapped.id, idx_mapped.code, idx_mapped.name)

        stk_res = await self.session.execute(
            select(Stock).where(Stock.symbol == sym)
        )
        stk = stk_res.scalar_one_or_none()
        if stk:
            return ("STOCK", stk.id, stk.symbol, stk.company_name)

        alias_res = await self.session.execute(
            select(SecurityAlias, Stock)
            .join(Stock, SecurityAlias.stock_id == Stock.id)
            .where(SecurityAlias.symbol == sym)
        )
        row = alias_res.first()
        if row:
            _, s = row
            return ("STOCK", s.id, s.symbol, s.company_name)

        return None

    async def _get_underlying_type_and_id(self, contract: DerivativeContract) -> tuple[str, int]:
        """Return ('INDEX', index_id) or ('STOCK', stock_id) from DerivativeContract."""
        if contract.underlying_index_id is not None:
            return ("INDEX", contract.underlying_index_id)
        elif contract.underlying_stock_id is not None:
            return ("STOCK", contract.underlying_stock_id)
        else:
            raise ValueError(
                f"DerivativeContract id={contract.id} has no "
                "underlying_index_id or underlying_stock_id"
            )

    async def _fetch_independent_spot(
        self, u_type: str, u_id: int, trade_date: date
    ) -> tuple[Decimal | None, str | None]:
        """Fetch spot price from MarketIndexPrice or DailyPrice."""
        if u_type == "INDEX":
            stmt = (
                select(MarketIndexPrice.close)
                .where(MarketIndexPrice.market_index_id == u_id)
                .where(MarketIndexPrice.trade_date == trade_date)
            )
            res = await self.session.execute(stmt)
            val = res.scalar_one_or_none()
            src = "MARKET_INDEX_PRICE" if val is not None else None
            return (Decimal(str(val)) if val is not None else None, src)
        else:
            stmt = (
                select(DailyPrice.close)
                .where(DailyPrice.stock_id == u_id)
                .where(DailyPrice.trade_date == trade_date)
            )
            res = await self.session.execute(stmt)
            val = res.scalar_one_or_none()
            src = "DAILY_PRICE" if val is not None else None
            return (Decimal(str(val)) if val is not None else None, src)

    async def _fetch_historical_rollover_comparison(
        self,
        underlying_type: str,
        underlying_id: int,
        current_trade_date: date,
        as_of_date: date,
    ) -> dict | None:
        """Fetch sample count, average, and median rollover_next_pct for prior available dates."""
        stmt = (
            select(DerivativeDailyPrice.trade_date)
            .join(DerivativeContract, DerivativeDailyPrice.contract_id == DerivativeContract.id)
            .where(DerivativeContract.instrument_type == "FUTURE")
            .where(DerivativeDailyPrice.trade_date < current_trade_date)
            .where(DerivativeDailyPrice.available_at <= as_of_date)
        )
        if underlying_type == "INDEX":
            stmt = stmt.where(DerivativeContract.underlying_index_id == underlying_id)
        else:
            stmt = stmt.where(DerivativeContract.underlying_stock_id == underlying_id)

        stmt = stmt.distinct().order_by(DerivativeDailyPrice.trade_date.desc()).limit(10)

        res = await self.session.execute(stmt)
        prior_dates = res.scalars().all()

        if not prior_dates:
            return None

        # Return sample count metadata
        return {
            "sample_count": len(prior_dates),
            "historical_prior_dates": [d.isoformat() for d in prior_dates],
        }
