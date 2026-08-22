"""Historical Options Analytics Service — Sprint 19.1D-6.

Provides deterministic, point-in-time safe historical options analytics:
  - PCR by Open Interest and Volume
  - Lot-size weighted payout minimization Max Pain calculation with tie handling
  - Static Call Wall and Static Put Wall (highest valid OI)
  - Fresh Call Wall and Fresh Put Wall (highest positive ΔOI)
  - Call and Put OI unwinding levels (largest negative ΔOI)
  - Ranked Call and Put OI concentration shares
  - ATM-distance analytics (absolute & percentage)
  - Cross-expiry term structure analytics & front-expiry dominance
  - Transparent methodology versioning and data quality warnings

Strict Invariants:
  - ZERO database table additions (ORM models remain at 49)
  - NO raw data mutations
  - NO IV, Greeks, GEX, or participant inference
  - NO directional Buy/Sell forecasts
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.derivative_contract import DerivativeContract
from app.models.derivative_daily_price import DerivativeDailyPrice
from app.schemas.derivative_options_analytics import (
    ExpiryOptionAnalyticsItem,
    ExpiryOptionsAnalytics,
    MaxPainAnalytics,
    MaxPainCandidate,
    OIConcentrationItem,
    OIWall,
    OptionChainAnalytics,
    PCRAnalytics,
)
from app.services.historical_option_chain import (
    HistoricalOptionChain,
    HistoricalOptionChainService,
)


class HistoricalOptionsAnalyticsService:
    """Service for deterministic point-in-time historical options analytics."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.chain_service = HistoricalOptionChainService(session)

    async def get_option_chain_analytics(
        self,
        symbol: str,
        trade_date: date,
        expiry_date: date,
        as_of_date: date | None = None,
        chain: HistoricalOptionChain | None = None,
    ) -> OptionChainAnalytics:
        """Calculate single-expiry options analytics from a point-in-time option chain."""
        as_of = as_of_date or trade_date

        if chain is None:
            chain = await self.chain_service.get_historical_option_chain(
                symbol=symbol,
                trade_date=trade_date,
                expiry_date=expiry_date,
                as_of_date=as_of,
            )

        quality_warnings: list[str] = list(chain.quality_warnings)
        spot_val = chain.resolved_spot

        # 1. Fetch contract lot sizes for accuracy
        contract_ids: set[int] = set()
        for row in chain.rows:
            if row.call:
                contract_ids.add(row.call.contract_id)
            if row.put:
                contract_ids.add(row.put.contract_id)

        lot_size_map: dict[int, int] = {}
        if contract_ids:
            c_stmt = select(DerivativeContract.id, DerivativeContract.lot_size).where(
                DerivativeContract.id.in_(list(contract_ids))
            )
            c_res = await self.session.execute(c_stmt)
            for c_id, l_size in c_res.all():
                lot_size_map[c_id] = l_size or 1

        # 2. Compute PCR & Observations counts
        total_call_oi = 0
        total_put_oi = 0
        total_call_vol = 0
        total_put_vol = 0
        valid_ce_count = 0
        valid_pe_count = 0
        missing_ce_count = 0
        missing_pe_count = 0

        for row in chain.rows:
            if row.call:
                valid_ce_count += 1
                if row.call.open_interest is not None:
                    total_call_oi += row.call.open_interest
                total_call_vol += row.call.volume_contracts or 0
            else:
                missing_ce_count += 1

            if row.put:
                valid_pe_count += 1
                if row.put.open_interest is not None:
                    total_put_oi += row.put.open_interest
                total_put_vol += row.put.volume_contracts or 0
            else:
                missing_pe_count += 1

        pcr_oi: Decimal | None = None
        if total_call_oi > 0:
            pcr_oi = Decimal(str(total_put_oi)) / Decimal(str(total_call_oi))
        elif total_call_oi == 0 and total_put_oi == 0:
            pcr_oi = Decimal("0")
        else:
            quality_warnings.append("OPTIONS_ANALYTICS_PCR_OI_UNAVAILABLE")

        pcr_vol: Decimal | None = None
        if total_call_vol > 0:
            pcr_vol = Decimal(str(total_put_vol)) / Decimal(str(total_call_vol))
        elif total_call_vol == 0 and total_put_vol == 0:
            pcr_vol = Decimal("0")
        else:
            quality_warnings.append("OPTIONS_ANALYTICS_PCR_VOLUME_UNAVAILABLE")

        pcr_res = PCRAnalytics(
            pcr_oi=pcr_oi,
            pcr_volume=pcr_vol,
            total_call_oi=total_call_oi,
            total_put_oi=total_put_oi,
            total_call_volume=total_call_vol,
            total_put_volume=total_put_vol,
            valid_ce_oi_count=valid_ce_count,
            valid_pe_oi_count=valid_pe_count,
            missing_ce_count=missing_ce_count,
            missing_pe_count=missing_pe_count,
        )

        if total_call_oi == 0:
            quality_warnings.append("OPTIONS_ANALYTICS_NO_CALL_OI")
        if total_put_oi == 0:
            quality_warnings.append("OPTIONS_ANALYTICS_NO_PUT_OI")
        if spot_val is None:
            quality_warnings.append("OPTIONS_ANALYTICS_NO_SPOT")

        # 3. Max Pain Calculation
        candidate_strikes = sorted([r.strike_price for r in chain.rows])
        max_pain_candidates: list[MaxPainCandidate] = []

        for candidate_s in candidate_strikes:
            call_payout = Decimal("0")
            put_payout = Decimal("0")

            for row in chain.rows:
                k = row.strike_price
                if candidate_s > k and row.call and row.call.open_interest:
                    l_size = lot_size_map.get(row.call.contract_id, 1)
                    payout = (
                        (candidate_s - k)
                        * Decimal(str(row.call.open_interest))
                        * Decimal(str(l_size))
                    )
                    call_payout += payout

                if candidate_s < k and row.put and row.put.open_interest:
                    l_size = lot_size_map.get(row.put.contract_id, 1)
                    payout = (
                        (k - candidate_s)
                        * Decimal(str(row.put.open_interest))
                        * Decimal(str(l_size))
                    )
                    put_payout += payout

            tot_payout = call_payout + put_payout
            max_pain_candidates.append(
                MaxPainCandidate(
                    candidate_strike=candidate_s,
                    call_payout=call_payout,
                    put_payout=put_payout,
                    total_payout=tot_payout,
                )
            )

        max_pain_res = MaxPainAnalytics(spot_value=spot_val, candidates=max_pain_candidates)

        if max_pain_candidates:
            min_payout = min(c.total_payout for c in max_pain_candidates)
            tied_candidates = [
                c.candidate_strike
                for c in max_pain_candidates
                if c.total_payout == min_payout
            ]
            max_pain_res.tie_count = len(tied_candidates)
            max_pain_res.tied_strikes = tied_candidates

            # Primary choice: nearest to spot; lower strike if distance tied
            if spot_val is not None and spot_val > Decimal("0"):
                selected_strike = min(
                    tied_candidates,
                    key=lambda k: (abs(k - spot_val), k),
                )
            else:
                selected_strike = tied_candidates[0]

            max_pain_res.max_pain_strike = selected_strike
            if spot_val is not None and spot_val > Decimal("0"):
                dist_abs = selected_strike - spot_val
                max_pain_res.distance_from_spot = dist_abs
                max_pain_res.distance_pct = (dist_abs / spot_val) * Decimal("100")
        else:
            quality_warnings.append("OPTIONS_ANALYTICS_MAX_PAIN_UNAVAILABLE")

        # 4. Static Walls (Highest OI)
        static_call_wall = self._calculate_static_wall(
            chain=chain,
            option_type="CE",
            total_oi=total_call_oi,
            spot_val=spot_val,
        )
        static_put_wall = self._calculate_static_wall(
            chain=chain,
            option_type="PE",
            total_oi=total_put_oi,
            spot_val=spot_val,
        )

        # 5. Fresh Walls (Highest Positive ΔOI > 0)
        fresh_call_wall = self._calculate_fresh_wall(
            chain=chain,
            option_type="CE",
            total_oi=total_call_oi,
            spot_val=spot_val,
        )
        fresh_put_wall = self._calculate_fresh_wall(
            chain=chain,
            option_type="PE",
            total_oi=total_put_oi,
            spot_val=spot_val,
        )

        # 6. Unwinding Levels (Largest Negative ΔOI < 0)
        call_unwinding = self._calculate_unwinding_level(
            chain=chain,
            option_type="CE",
            total_oi=total_call_oi,
            spot_val=spot_val,
        )
        put_unwinding = self._calculate_unwinding_level(
            chain=chain,
            option_type="PE",
            total_oi=total_put_oi,
            spot_val=spot_val,
        )

        # 7. Ranked OI Concentrations
        call_concentrations = self._calculate_oi_concentrations(
            chain=chain, option_type="CE", total_oi=total_call_oi
        )
        put_concentrations = self._calculate_oi_concentrations(
            chain=chain, option_type="PE", total_oi=total_put_oi
        )

        return OptionChainAnalytics(
            underlying_symbol=chain.underlying_symbol,
            trade_date=chain.trade_date,
            expiry_date=chain.expiry_date,
            as_of_date=as_of,
            resolved_spot=spot_val,
            pcr=pcr_res,
            max_pain=max_pain_res,
            static_call_wall=static_call_wall,
            static_put_wall=static_put_wall,
            fresh_call_wall=fresh_call_wall,
            fresh_put_wall=fresh_put_wall,
            largest_call_unwinding=call_unwinding,
            largest_put_unwinding=put_unwinding,
            call_oi_concentrations=call_concentrations,
            put_oi_concentrations=put_concentrations,
            quality_warnings=quality_warnings,
            algorithm_version="OPTIONS_ANALYTICS_V1",
        )

    async def get_expiry_options_analytics(
        self,
        symbol: str,
        trade_date: date,
        as_of_date: date | None = None,
    ) -> ExpiryOptionsAnalytics:
        """Calculate cross-expiry term structure analytics for an underlying on a trade date."""
        as_of = as_of_date or trade_date

        # Resolve underlying
        underlying_info = await self.chain_service._resolve_underlying(symbol)
        if not underlying_info:
            raise ValueError(f"Unknown underlying symbol '{symbol}'")

        u_type, u_id, u_symbol, _ = underlying_info

        # Query all option contracts & daily prices on trade_date
        stmt = (
            select(DerivativeDailyPrice, DerivativeContract)
            .join(DerivativeContract, DerivativeDailyPrice.contract_id == DerivativeContract.id)
            .where(DerivativeContract.instrument_type == "OPTION")
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

        # Group by expiry
        exp_groups: dict[date, list[tuple[DerivativeDailyPrice, DerivativeContract]]] = {}
        for p_row, c_row in rows:
            exp_groups.setdefault(c_row.expiry_date, []).append((p_row, c_row))

        sorted_expiries = sorted(exp_groups.keys())
        tot_oi_all = sum(p.open_interest or 0 for p, _ in rows)
        tot_vol_all = sum(p.volume_contracts or 0 for p, _ in rows)

        items: list[ExpiryOptionAnalyticsItem] = []

        for exp in sorted_expiries:
            exp_rows = exp_groups[exp]
            c_oi = 0
            p_oi = 0
            c_vol = 0
            p_vol = 0
            distinct_strikes: set[Decimal] = set()

            for p_row, c_row in exp_rows:
                distinct_strikes.add(c_row.strike_price)
                if c_row.option_type == "CE":
                    c_oi += p_row.open_interest or 0
                    c_vol += p_row.volume_contracts or 0
                elif c_row.option_type == "PE":
                    p_oi += p_row.open_interest or 0
                    p_vol += p_row.volume_contracts or 0

            exp_tot_oi = c_oi + p_oi
            exp_tot_vol = c_vol + p_vol

            oi_share = (
                (Decimal(str(exp_tot_oi)) / Decimal(str(tot_oi_all))) * Decimal("100")
                if tot_oi_all > 0
                else Decimal("0")
            )
            vol_share = (
                (Decimal(str(exp_tot_vol)) / Decimal(str(tot_vol_all))) * Decimal("100")
                if tot_vol_all > 0
                else Decimal("0")
            )

            exp_pcr_oi: Decimal | None = None
            if c_oi > 0:
                exp_pcr_oi = Decimal(str(p_oi)) / Decimal(str(c_oi))

            exp_pcr_vol: Decimal | None = None
            if c_vol > 0:
                exp_pcr_vol = Decimal(str(p_vol)) / Decimal(str(c_vol))

            items.append(
                ExpiryOptionAnalyticsItem(
                    expiry_date=exp,
                    calendar_days_to_expiry=(exp - trade_date).days,
                    total_call_oi=c_oi,
                    total_put_oi=p_oi,
                    total_oi=exp_tot_oi,
                    total_call_volume=c_vol,
                    total_put_volume=p_vol,
                    total_volume=exp_tot_vol,
                    oi_share_pct=oi_share,
                    volume_share_pct=vol_share,
                    pcr_oi=exp_pcr_oi,
                    pcr_volume=exp_pcr_vol,
                    total_strikes=len(distinct_strikes),
                )
            )

        near_oi_share = items[0].oi_share_pct if items else Decimal("0")
        near_vol_share = items[0].volume_share_pct if items else Decimal("0")

        return ExpiryOptionsAnalytics(
            underlying_symbol=u_symbol,
            trade_date=trade_date,
            as_of_date=as_of,
            total_oi_across_expiries=tot_oi_all,
            total_volume_across_expiries=tot_vol_all,
            expiries=items,
            nearest_expiry_oi_share_pct=near_oi_share,
            nearest_expiry_volume_share_pct=near_vol_share,
            algorithm_version="OPTIONS_ANALYTICS_V1",
        )

    def _calculate_static_wall(
        self,
        chain: HistoricalOptionChain,
        option_type: str,
        total_oi: int,
        spot_val: Decimal | None,
    ) -> OIWall | None:
        """Find static wall level (highest valid OI)."""
        valid_items: list[tuple[Decimal, int, int | None]] = []

        for row in chain.rows:
            side = row.call if option_type == "CE" else row.put
            if side and side.open_interest and side.open_interest > 0:
                valid_items.append(
                    (row.strike_price, side.open_interest, side.change_in_open_interest)
                )

        if not valid_items:
            return None

        max_oi = max(item[1] for item in valid_items)
        max_candidates = [item for item in valid_items if item[1] == max_oi]

        # Tie-break nearest to spot
        if spot_val is not None and spot_val > Decimal("0"):
            best_item = min(max_candidates, key=lambda item: (abs(item[0] - spot_val), item[0]))
        else:
            best_item = max_candidates[0]

        strike, oi, delta_oi = best_item
        oi_share = (
            (Decimal(str(oi)) / Decimal(str(total_oi))) * Decimal("100")
            if total_oi > 0
            else Decimal("0")
        )

        dist_abs: Decimal | None = None
        dist_pct: Decimal | None = None
        if spot_val is not None and spot_val > Decimal("0"):
            dist_abs = strike - spot_val
            dist_pct = (dist_abs / spot_val) * Decimal("100")

        return OIWall(
            strike_price=strike,
            open_interest=oi,
            oi_share_pct=oi_share,
            change_in_open_interest=delta_oi,
            distance_from_spot=dist_abs,
            distance_pct=dist_pct,
            option_type=option_type,
            expiry_date=chain.expiry_date,
            trade_date=chain.trade_date,
        )

    def _calculate_fresh_wall(
        self,
        chain: HistoricalOptionChain,
        option_type: str,
        total_oi: int,
        spot_val: Decimal | None,
    ) -> OIWall | None:
        """Find fresh wall level (highest positive ΔOI > 0)."""
        positive_items: list[tuple[Decimal, int, int]] = []

        for row in chain.rows:
            side = row.call if option_type == "CE" else row.put
            if (
                side
                and side.open_interest
                and side.change_in_open_interest
                and side.change_in_open_interest > 0
            ):
                positive_items.append(
                    (row.strike_price, side.open_interest, side.change_in_open_interest)
                )

        if not positive_items:
            return None

        max_delta = max(item[2] for item in positive_items)
        max_candidates = [item for item in positive_items if item[2] == max_delta]

        if spot_val is not None and spot_val > Decimal("0"):
            best_item = min(max_candidates, key=lambda item: (abs(item[0] - spot_val), item[0]))
        else:
            best_item = max_candidates[0]

        strike, oi, delta_oi = best_item
        oi_share = (
            (Decimal(str(oi)) / Decimal(str(total_oi))) * Decimal("100")
            if total_oi > 0
            else Decimal("0")
        )

        dist_abs: Decimal | None = None
        dist_pct: Decimal | None = None
        if spot_val is not None and spot_val > Decimal("0"):
            dist_abs = strike - spot_val
            dist_pct = (dist_abs / spot_val) * Decimal("100")

        return OIWall(
            strike_price=strike,
            open_interest=oi,
            oi_share_pct=oi_share,
            change_in_open_interest=delta_oi,
            distance_from_spot=dist_abs,
            distance_pct=dist_pct,
            option_type=option_type,
            expiry_date=chain.expiry_date,
            trade_date=chain.trade_date,
        )

    def _calculate_unwinding_level(
        self,
        chain: HistoricalOptionChain,
        option_type: str,
        total_oi: int,
        spot_val: Decimal | None,
    ) -> OIWall | None:
        """Find unwinding level (largest negative ΔOI < 0)."""
        negative_items: list[tuple[Decimal, int, int]] = []

        for row in chain.rows:
            side = row.call if option_type == "CE" else row.put
            if (
                side
                and side.open_interest
                and side.change_in_open_interest
                and side.change_in_open_interest < 0
            ):
                negative_items.append(
                    (row.strike_price, side.open_interest, side.change_in_open_interest)
                )

        if not negative_items:
            return None

        # Most negative value
        min_delta = min(item[2] for item in negative_items)
        min_candidates = [item for item in negative_items if item[2] == min_delta]

        if spot_val is not None and spot_val > Decimal("0"):
            best_item = min(min_candidates, key=lambda item: (abs(item[0] - spot_val), item[0]))
        else:
            best_item = min_candidates[0]

        strike, oi, delta_oi = best_item
        oi_share = (
            (Decimal(str(oi)) / Decimal(str(total_oi))) * Decimal("100")
            if total_oi > 0
            else Decimal("0")
        )

        dist_abs: Decimal | None = None
        dist_pct: Decimal | None = None
        if spot_val is not None and spot_val > Decimal("0"):
            dist_abs = strike - spot_val
            dist_pct = (dist_abs / spot_val) * Decimal("100")

        return OIWall(
            strike_price=strike,
            open_interest=oi,
            oi_share_pct=oi_share,
            change_in_open_interest=delta_oi,
            distance_from_spot=dist_abs,
            distance_pct=dist_pct,
            option_type=option_type,
            expiry_date=chain.expiry_date,
            trade_date=chain.trade_date,
        )

    def _calculate_oi_concentrations(
        self,
        chain: HistoricalOptionChain,
        option_type: str,
        total_oi: int,
        top_n: int = 5,
    ) -> list[OIConcentrationItem]:
        """Rank top strike-level OI concentration shares."""
        items: list[OIConcentrationItem] = []

        for row in chain.rows:
            side = row.call if option_type == "CE" else row.put
            if side and side.open_interest and side.open_interest > 0:
                share = (
                    (Decimal(str(side.open_interest)) / Decimal(str(total_oi))) * Decimal("100")
                    if total_oi > 0
                    else Decimal("0")
                )
                items.append(
                    OIConcentrationItem(
                        strike_price=row.strike_price,
                        open_interest=side.open_interest,
                        oi_share_pct=share,
                        change_in_open_interest=side.change_in_open_interest,
                        option_type=option_type,
                    )
                )

        items.sort(key=lambda item: item.open_interest, reverse=True)
        return items[:top_n]
