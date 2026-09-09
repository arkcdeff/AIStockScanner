"""Deterministic Historical OI Intelligence Service — Stage G.8.

Provides quantitative open-interest intelligence across historical EOD option chains:
  - Net OI flow (Call vs Put)
  - PCR (OI PCR, Volume PCR, ΔOI PCR)
  - Static Call Wall and Put Wall movement tracking between session T and T-1
  - Strike-level & contract-level buildup classification (LONG_BUILDUP, SHORT_BUILDUP, etc.)
  - Multi-strike concentration (Top 1, Top 3, Top 5)
  - Active strikes and OI spurt detection
  - Comparative session 'What changed?' summary
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.derivative_contract import DerivativeContract
from app.models.derivative_daily_price import DerivativeDailyPrice
from app.schemas.derivative_oi_intelligence import (
    ActiveStrikeItem,
    ComparativeSessionSummary,
    MultiStrikeConcentration,
    OIIntelligenceSnapshot,
    OISpurtItem,
    OIWallMovement,
    StrikeBuildupClassification,
)
from app.services.historical_option_chain import (
    HistoricalOptionChain,
    HistoricalOptionChainRow,
    HistoricalOptionChainService,
)

logger = logging.getLogger(__name__)


class HistoricalOIIntelligenceService:
    """Service generating deterministic EOD Open Interest Intelligence."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.chain_service = HistoricalOptionChainService(session)

    async def get_oi_intelligence(
        self,
        symbol: str,
        trade_date: date,
        expiry_date: date | None = None,
        as_of_date: date | None = None,
    ) -> OIIntelligenceSnapshot:
        """Assemble comprehensive EOD Open Interest Intelligence Snapshot."""
        as_of = as_of_date or trade_date

        # 1. Resolve Target Expiry
        if expiry_date is None:
            available_expiries = await self.chain_service.get_available_expiries(symbol, trade_date, as_of)
            if not available_expiries:
                raise ValueError(f"No available option expiries found for {symbol} on {trade_date} as of {as_of}")
            target_expiry = available_expiries[0]
        else:
            target_expiry = expiry_date

        # 2. Fetch Current Session Option Chain
        curr_chain: HistoricalOptionChain = await self.chain_service.get_historical_option_chain(
            symbol=symbol,
            trade_date=trade_date,
            expiry_date=target_expiry,
            as_of_date=as_of,
        )

        # 3. Find Previous Trading Session Date
        prev_date = await self._get_previous_session_date(
            underlying_id=curr_chain.underlying_id,
            underlying_type=curr_chain.underlying_type,
            expiry_date=target_expiry,
            current_trade_date=trade_date,
            as_of_date=as_of,
        )

        prev_chain: HistoricalOptionChain | None = None
        if prev_date is not None:
            try:
                prev_chain = await self.chain_service.get_historical_option_chain(
                    symbol=symbol,
                    trade_date=prev_date,
                    expiry_date=target_expiry,
                    as_of_date=as_of,
                )
            except Exception as exc:
                logger.warning("Could not load previous chain on %s: %s", prev_date, exc)
                prev_chain = None

        # 4. Aggregate Totals and Compute PCR
        total_call_oi = 0
        total_put_oi = 0
        total_call_vol = 0
        total_put_vol = 0
        net_call_oi_change = 0
        net_put_oi_change = 0

        # Build strike maps for easy comparative analysis
        curr_ce_by_strike: dict[Decimal, HistoricalOptionChainRow] = {}
        curr_pe_by_strike: dict[Decimal, HistoricalOptionChainRow] = {}

        for r in curr_chain.rows:
            if r.call and r.call.open_interest is not None:
                total_call_oi += r.call.open_interest
                total_call_vol += r.call.volume_contracts or 0
                net_call_oi_change += r.call.change_in_open_interest or 0
                curr_ce_by_strike[r.strike_price] = r
            if r.put and r.put.open_interest is not None:
                total_put_oi += r.put.open_interest
                total_put_vol += r.put.volume_contracts or 0
                net_put_oi_change += r.put.change_in_open_interest or 0
                curr_pe_by_strike[r.strike_price] = r

        # PCR calculations
        curr_pcr_oi: Decimal | None = None
        if total_call_oi > 0:
            curr_pcr_oi = (Decimal(str(total_put_oi)) / Decimal(str(total_call_oi))).quantize(Decimal("0.0001"))

        curr_pcr_vol: Decimal | None = None
        if total_call_vol > 0:
            curr_pcr_vol = (Decimal(str(total_put_vol)) / Decimal(str(total_call_vol))).quantize(Decimal("0.0001"))

        # ΔOI PCR (sum of Put ΔOI / sum of Call ΔOI)
        delta_oi_pcr: Decimal | None = None
        if net_call_oi_change > 0:
            delta_oi_pcr = (Decimal(str(net_put_oi_change)) / Decimal(str(net_call_oi_change))).quantize(Decimal("0.0001"))

        # 5. Wall Movement Analysis (Session T vs T-1)
        prev_ce_by_strike: dict[Decimal, HistoricalOptionChainRow] = {}
        prev_pe_by_strike: dict[Decimal, HistoricalOptionChainRow] = {}
        prev_total_call_oi = 0
        prev_total_put_oi = 0

        if prev_chain is not None:
            for r in prev_chain.rows:
                if r.call and r.call.open_interest is not None:
                    prev_total_call_oi += r.call.open_interest
                    prev_ce_by_strike[r.strike_price] = r
                if r.put and r.put.open_interest is not None:
                    prev_total_put_oi += r.put.open_interest
                    prev_pe_by_strike[r.strike_price] = r

        prev_pcr_oi: Decimal | None = None
        if prev_total_call_oi > 0:
            prev_pcr_oi = (Decimal(str(prev_total_put_oi)) / Decimal(str(prev_total_call_oi))).quantize(Decimal("0.0001"))

        pcr_change: Decimal | None = None
        if curr_pcr_oi is not None and prev_pcr_oi is not None:
            pcr_change = (curr_pcr_oi - prev_pcr_oi).quantize(Decimal("0.0001"))

        call_wall_movement = self._compute_wall_movement(
            wall_type="CALL_WALL",
            curr_strikes=curr_ce_by_strike,
            prev_strikes=prev_ce_by_strike,
            total_side_oi=total_call_oi,
            side_accessor=lambda r: r.call,
        )

        put_wall_movement = self._compute_wall_movement(
            wall_type="PUT_WALL",
            curr_strikes=curr_pe_by_strike,
            prev_strikes=prev_pe_by_strike,
            total_side_oi=total_put_oi,
            side_accessor=lambda r: r.put,
        )

        # 6. Multi-Strike Concentration
        conc_call = self._compute_concentration(curr_ce_by_strike, total_call_oi, lambda r: r.call)
        conc_put = self._compute_concentration(curr_pe_by_strike, total_put_oi, lambda r: r.put)

        # 7. Strike-Level Buildup Classification
        strike_buildups: list[StrikeBuildupClassification] = []
        long_buildup_cnt = 0
        short_buildup_cnt = 0
        short_cov_cnt = 0
        long_unw_cnt = 0

        for r in curr_chain.rows:
            # Call side
            if r.call and r.call.open_interest is not None:
                b_ce = self._classify_buildup(
                    strike=r.strike_price,
                    opt_type="CE",
                    side_curr=r.call,
                    side_prev=prev_ce_by_strike.get(r.strike_price, None),
                )
                strike_buildups.append(b_ce)
                if b_ce.buildup_state == "LONG_BUILDUP":
                    long_buildup_cnt += 1
                elif b_ce.buildup_state == "SHORT_BUILDUP":
                    short_buildup_cnt += 1
                elif b_ce.buildup_state == "SHORT_COVERING":
                    short_cov_cnt += 1
                elif b_ce.buildup_state == "LONG_UNWINDING":
                    long_unw_cnt += 1

            # Put side
            if r.put and r.put.open_interest is not None:
                b_pe = self._classify_buildup(
                    strike=r.strike_price,
                    opt_type="PE",
                    side_curr=r.put,
                    side_prev=prev_pe_by_strike.get(r.strike_price, None),
                )
                strike_buildups.append(b_pe)
                if b_pe.buildup_state == "LONG_BUILDUP":
                    long_buildup_cnt += 1
                elif b_pe.buildup_state == "SHORT_BUILDUP":
                    short_buildup_cnt += 1
                elif b_pe.buildup_state == "SHORT_COVERING":
                    short_cov_cnt += 1
                elif b_pe.buildup_state == "LONG_UNWINDING":
                    long_unw_cnt += 1

        # Determine predominant buildup bias
        buildup_counts = {
            "LONG_BUILDUP": long_buildup_cnt,
            "SHORT_BUILDUP": short_buildup_cnt,
            "SHORT_COVERING": short_cov_cnt,
            "LONG_UNWINDING": long_unw_cnt,
        }
        predominant_buildup = max(buildup_counts, key=buildup_counts.get) if any(buildup_counts.values()) else "NEUTRAL"

        # 8. Active Strikes & OI Spurts
        active_vol: list[ActiveStrikeItem] = []
        active_oi: list[ActiveStrikeItem] = []
        oi_spurts: list[OISpurtItem] = []

        for r in curr_chain.rows:
            if r.call and r.call.open_interest:
                item_ce = ActiveStrikeItem(
                    strike_price=r.strike_price,
                    option_type="CE",
                    volume=r.call.volume_contracts or 0,
                    open_interest=r.call.open_interest,
                    change_in_oi=r.call.change_in_open_interest or 0,
                    turnover=r.call.turnover,
                    ltp=r.call.last_price or r.call.close,
                )
                active_vol.append(item_ce)
                active_oi.append(item_ce)

                # Check OI spurt
                prev_row_ce = prev_ce_by_strike.get(r.strike_price)
                if prev_row_ce and prev_row_ce.call and prev_row_ce.call.open_interest:
                    p_oi = prev_row_ce.call.open_interest
                    c_oi = r.call.open_interest
                    diff = c_oi - p_oi
                    if p_oi > 0 and diff > 0:
                        growth_pct = (Decimal(str(diff)) / Decimal(str(p_oi)) * Decimal("100")).quantize(Decimal("0.01"))
                        if growth_pct >= Decimal("50.0"):
                            oi_spurts.append(
                                OISpurtItem(
                                    strike_price=r.strike_price,
                                    option_type="CE",
                                    current_oi=c_oi,
                                    previous_oi=p_oi,
                                    oi_change=diff,
                                    oi_growth_pct=growth_pct,
                                    volume=r.call.volume_contracts or 0,
                                )
                            )

            if r.put and r.put.open_interest:
                item_pe = ActiveStrikeItem(
                    strike_price=r.strike_price,
                    option_type="PE",
                    volume=r.put.volume_contracts or 0,
                    open_interest=r.put.open_interest,
                    change_in_oi=r.put.change_in_open_interest or 0,
                    turnover=r.put.turnover,
                    ltp=r.put.last_price or r.put.close,
                )
                active_vol.append(item_pe)
                active_oi.append(item_pe)

                # Check OI spurt
                prev_row_pe = prev_pe_by_strike.get(r.strike_price)
                if prev_row_pe and prev_row_pe.put and prev_row_pe.put.open_interest:
                    p_oi = prev_row_pe.put.open_interest
                    c_oi = r.put.open_interest
                    diff = c_oi - p_oi
                    if p_oi > 0 and diff > 0:
                        growth_pct = (Decimal(str(diff)) / Decimal(str(p_oi)) * Decimal("100")).quantize(Decimal("0.01"))
                        if growth_pct >= Decimal("50.0"):
                            oi_spurts.append(
                                OISpurtItem(
                                    strike_price=r.strike_price,
                                    option_type="PE",
                                    current_oi=c_oi,
                                    previous_oi=p_oi,
                                    oi_change=diff,
                                    oi_growth_pct=growth_pct,
                                    volume=r.put.volume_contracts or 0,
                                )
                            )

        active_vol.sort(key=lambda x: x.volume, reverse=True)
        active_oi.sort(key=lambda x: x.open_interest, reverse=True)
        oi_spurts.sort(key=lambda x: x.oi_growth_pct, reverse=True)

        # 9. Comparative Spot Analysis & Narrative
        curr_spot = curr_chain.resolved_spot
        prev_spot = prev_chain.resolved_spot if prev_chain else None
        spot_change: Decimal | None = None
        spot_change_pct: Decimal | None = None
        if curr_spot is not None and prev_spot is not None and prev_spot > Decimal("0"):
            spot_change = (curr_spot - prev_spot).quantize(Decimal("0.01"))
            spot_change_pct = ((spot_change / prev_spot) * Decimal("100")).quantize(Decimal("0.01"))

        narrative = self._generate_narrative(
            symbol=curr_chain.underlying_symbol,
            trade_date=trade_date,
            prev_date=prev_date,
            curr_spot=curr_spot,
            spot_change=spot_change,
            spot_change_pct=spot_change_pct,
            curr_pcr=curr_pcr_oi,
            prev_pcr=prev_pcr_oi,
            call_wall=call_wall_movement,
            put_wall=put_wall_movement,
            predominant_buildup=predominant_buildup,
            long_cnt=long_buildup_cnt,
            short_cnt=short_buildup_cnt,
            cov_cnt=short_cov_cnt,
            unw_cnt=long_unw_cnt,
        )

        summary = ComparativeSessionSummary(
            underlying_symbol=curr_chain.underlying_symbol,
            trade_date=trade_date,
            previous_trade_date=prev_date,
            current_spot=curr_spot,
            previous_spot=prev_spot,
            spot_change=spot_change,
            spot_change_pct=spot_change_pct,
            total_call_oi=total_call_oi,
            total_put_oi=total_put_oi,
            net_call_oi_change=net_call_oi_change,
            net_put_oi_change=net_put_oi_change,
            current_pcr_oi=curr_pcr_oi,
            previous_pcr_oi=prev_pcr_oi,
            pcr_oi_change=pcr_change,
            delta_oi_pcr=delta_oi_pcr,
            volume_pcr=curr_pcr_vol,
            call_wall_movement=call_wall_movement,
            put_wall_movement=put_wall_movement,
            long_buildup_count=long_buildup_cnt,
            short_buildup_count=short_buildup_cnt,
            short_covering_count=short_cov_cnt,
            long_unwinding_count=long_unw_cnt,
            predominant_buildup=predominant_buildup,
            narrative_summary=narrative,
        )

        return OIIntelligenceSnapshot(
            underlying_symbol=curr_chain.underlying_symbol,
            underlying_name=curr_chain.underlying_name,
            trade_date=trade_date,
            expiry_date=target_expiry,
            calendar_days_to_expiry=curr_chain.calendar_days_to_expiry,
            as_of_date=as_of,
            resolved_spot=curr_spot,
            summary=summary,
            call_wall=call_wall_movement,
            put_wall=put_wall_movement,
            concentration_call=conc_call,
            concentration_put=conc_put,
            active_strikes_by_volume=active_vol[:10],
            active_strikes_by_oi=active_oi[:10],
            oi_spurts=oi_spurts[:10],
            strike_buildups=strike_buildups,
            quality_warnings=curr_chain.quality_warnings,
            provenance={
                "symbol": symbol,
                "trade_date": str(trade_date),
                "previous_trade_date": str(prev_date) if prev_date else None,
                "expiry_date": str(target_expiry),
                "as_of_date": str(as_of),
            },
        )

    def _compute_wall_movement(
        self,
        wall_type: str,
        curr_strikes: dict[Decimal, HistoricalOptionChainRow],
        prev_strikes: dict[Decimal, HistoricalOptionChainRow],
        total_side_oi: int,
        side_accessor: Any,
    ) -> OIWallMovement:
        """Locate current and previous wall strikes and compute directional shift."""
        if not curr_strikes:
            return OIWallMovement(
                wall_type=wall_type,
                current_strike=Decimal("0"),
                shift_direction="NONE",
            )

        # Current wall strike
        curr_wall_k, curr_wall_row = max(
            curr_strikes.items(),
            key=lambda item: getattr(side_accessor(item[1]), "open_interest", 0) or 0,
        )
        curr_wall_side = side_accessor(curr_wall_row)
        curr_oi = curr_wall_side.open_interest or 0
        share_pct = (
            (Decimal(str(curr_oi)) / Decimal(str(total_side_oi)) * Decimal("100")).quantize(Decimal("0.01"))
            if total_side_oi > 0
            else None
        )

        prev_strike: Decimal | None = None
        prev_oi: int | None = None
        shift_pts: Decimal | None = None
        shift_dir = "UNCHANGED"

        if prev_strikes:
            prev_wall_k, prev_wall_row = max(
                prev_strikes.items(),
                key=lambda item: getattr(side_accessor(item[1]), "open_interest", 0) or 0,
            )
            prev_strike = prev_wall_k
            prev_side = side_accessor(prev_wall_row)
            prev_oi = prev_side.open_interest if prev_side else None
            shift_pts = curr_wall_k - prev_wall_k
            if shift_pts > Decimal("0"):
                shift_dir = "UP"
            elif shift_pts < Decimal("0"):
                shift_dir = "DOWN"
            else:
                shift_dir = "UNCHANGED"
        else:
            shift_dir = "NEW"

        oi_diff = curr_oi - prev_oi if prev_oi is not None else None

        return OIWallMovement(
            wall_type=wall_type,
            current_strike=curr_wall_k,
            previous_strike=prev_strike,
            shift_points=shift_pts,
            shift_direction=shift_dir,
            current_oi=curr_oi,
            previous_oi=prev_oi,
            oi_change=oi_diff,
            oi_share_pct=share_pct,
        )

    def _compute_concentration(
        self,
        strikes_map: dict[Decimal, HistoricalOptionChainRow],
        total_oi: int,
        side_accessor: Any,
    ) -> MultiStrikeConcentration:
        """Compute Top 1, Top 3, and Top 5 strike open interest concentration."""
        if not strikes_map or total_oi <= 0:
            return MultiStrikeConcentration()

        sorted_items = sorted(
            strikes_map.items(),
            key=lambda it: getattr(side_accessor(it[1]), "open_interest", 0) or 0,
            reverse=True,
        )

        top1_k = sorted_items[0][0]
        top1_oi = getattr(side_accessor(sorted_items[0][1]), "open_interest", 0) or 0
        top1_share = (Decimal(str(top1_oi)) / Decimal(str(total_oi)) * Decimal("100")).quantize(Decimal("0.01"))

        top3_items = sorted_items[:3]
        top3_ks = [it[0] for it in top3_items]
        top3_oi = sum(getattr(side_accessor(it[1]), "open_interest", 0) or 0 for it in top3_items)
        top3_share = (Decimal(str(top3_oi)) / Decimal(str(total_oi)) * Decimal("100")).quantize(Decimal("0.01"))

        top5_items = sorted_items[:5]
        top5_ks = [it[0] for it in top5_items]
        top5_oi = sum(getattr(side_accessor(it[1]), "open_interest", 0) or 0 for it in top5_items)
        top5_share = (Decimal(str(top5_oi)) / Decimal(str(total_oi)) * Decimal("100")).quantize(Decimal("0.01"))

        return MultiStrikeConcentration(
            top1_strike=top1_k,
            top1_oi=top1_oi,
            top1_share_pct=top1_share,
            top3_strikes=top3_ks,
            top3_oi=top3_oi,
            top3_share_pct=top3_share,
            top5_strikes=top5_ks,
            top5_oi=top5_oi,
            top5_share_pct=top5_share,
        )

    def _classify_buildup(
        self,
        strike: Decimal,
        opt_type: str,
        side_curr: Any,
        side_prev: HistoricalOptionChainRow | None,
    ) -> StrikeBuildupClassification:
        """Classify buildup state (LONG_BUILDUP, SHORT_BUILDUP, etc.) for a strike."""
        c_price = side_curr.last_price or side_curr.close or side_curr.settlement_price
        c_oi = side_curr.open_interest or 0
        c_chg_oi = side_curr.change_in_open_interest or 0
        vol = side_curr.volume_contracts or 0

        p_price: Decimal | None = None
        if side_prev:
            prev_side = getattr(side_prev, "call" if opt_type == "CE" else "put", None)
            if prev_side:
                p_price = prev_side.last_price or prev_side.close or prev_side.settlement_price

        if p_price is None and side_curr.previous_close:
            p_price = side_curr.previous_close

        p_diff: Decimal | None = None
        p_pct: Decimal | None = None
        if c_price is not None and p_price is not None and p_price > Decimal("0"):
            p_diff = (c_price - p_price).quantize(Decimal("0.01"))
            p_pct = ((p_diff / p_price) * Decimal("100")).quantize(Decimal("0.01"))

        oi_pct: Decimal | None = None
        prev_oi = c_oi - c_chg_oi
        if prev_oi > 0:
            oi_pct = (Decimal(str(c_chg_oi)) / Decimal(str(prev_oi)) * Decimal("100")).quantize(Decimal("0.01"))

        # Noise threshold of 0.1% price change
        is_price_up = p_diff is not None and p_diff > Decimal("0.05")
        is_price_down = p_diff is not None and p_diff < Decimal("-0.05")
        is_oi_up = c_chg_oi > 0
        is_oi_down = c_chg_oi < 0

        if is_price_up and is_oi_up:
            state = "LONG_BUILDUP"
        elif is_price_down and is_oi_up:
            state = "SHORT_BUILDUP"
        elif is_price_up and is_oi_down:
            state = "SHORT_COVERING"
        elif is_price_down and is_oi_down:
            state = "LONG_UNWINDING"
        elif is_price_up:
            state = "PRICE_UP_OI_FLAT"
        elif is_price_down:
            state = "PRICE_DOWN_OI_FLAT"
        elif is_oi_up:
            state = "PRICE_FLAT_OI_UP"
        elif is_oi_down:
            state = "PRICE_FLAT_OI_DOWN"
        else:
            state = "NEUTRAL"

        return StrikeBuildupClassification(
            strike_price=strike,
            option_type=opt_type,
            ltp=c_price,
            price_change=p_diff,
            price_change_pct=p_pct,
            open_interest=c_oi,
            change_in_oi=c_chg_oi,
            oi_change_pct=oi_pct,
            volume=vol,
            buildup_state=state,
        )

    def _generate_narrative(
        self,
        symbol: str,
        trade_date: date,
        prev_date: date | None,
        curr_spot: Decimal | None,
        spot_change: Decimal | None,
        spot_change_pct: Decimal | None,
        curr_pcr: Decimal | None,
        prev_pcr: Decimal | None,
        call_wall: OIWallMovement,
        put_wall: OIWallMovement,
        predominant_buildup: str,
        long_cnt: int,
        short_cnt: int,
        cov_cnt: int,
        unw_cnt: int,
    ) -> str:
        """Generate human-readable comparative summary narrative."""
        parts = []

        # Spot movement
        if curr_spot is not None:
            if spot_change is not None and spot_change_pct is not None:
                sign = "+" if spot_change >= Decimal("0") else ""
                parts.append(
                    f"{symbol} closed at {curr_spot:,.2f} ({sign}{spot_change:,.2f}, {sign}{spot_change_pct:.2f}%) "
                    f"vs {prev_date}."
                )
            else:
                parts.append(f"{symbol} spot reference at {curr_spot:,.2f}.")

        # PCR narrative
        if curr_pcr is not None:
            if prev_pcr is not None:
                pcr_diff = curr_pcr - prev_pcr
                sign = "+" if pcr_diff >= Decimal("0") else ""
                sentiment = "bullish/supportive" if pcr_diff > Decimal("0.05") else ("bearish/defensive" if pcr_diff < Decimal("-0.05") else "stable")
                parts.append(
                    f"PCR (OI) moved from {prev_pcr:.2f} to {curr_pcr:.2f} ({sign}{pcr_diff:.2f}, {sentiment})."
                )
            else:
                parts.append(f"PCR (OI) stood at {curr_pcr:.2f}.")

        # Wall shifts
        c_shift_str = (
            f"Call wall shifted {call_wall.shift_direction} by {call_wall.shift_points:,.0f} pts to {call_wall.current_strike:,.0f}"
            if call_wall.shift_direction in ("UP", "DOWN")
            else f"Call wall steady at {call_wall.current_strike:,.0f}"
        )
        p_shift_str = (
            f"Put wall shifted {put_wall.shift_direction} by {put_wall.shift_points:,.0f} pts to {put_wall.current_strike:,.0f}"
            if put_wall.shift_direction in ("UP", "DOWN")
            else f"Put wall steady at {put_wall.current_strike:,.0f}"
        )
        parts.append(f"{c_shift_str}; {p_shift_str}.")

        # Buildup summary
        parts.append(
            f"Prevailing buildup was {predominant_buildup} "
            f"(Long: {long_cnt}, Short: {short_cnt}, Covering: {cov_cnt}, Unwinding: {unw_cnt})."
        )

        return " ".join(parts)

    async def _get_previous_session_date(
        self,
        underlying_id: int,
        underlying_type: str,
        expiry_date: date,
        current_trade_date: date,
        as_of_date: date,
    ) -> date | None:
        """Find the immediately preceding trading session date with derivative daily prices."""
        cond = (
            DerivativeContract.underlying_index_id == underlying_id
            if underlying_type == "INDEX"
            else DerivativeContract.underlying_stock_id == underlying_id
        )

        stmt = (
            select(DerivativeDailyPrice.trade_date)
            .join(DerivativeContract, DerivativeDailyPrice.contract_id == DerivativeContract.id)
            .where(cond)
            .where(DerivativeContract.expiry_date == expiry_date)
            .where(DerivativeDailyPrice.trade_date < current_trade_date)
            .where(DerivativeDailyPrice.available_at <= as_of_date)
            .order_by(DerivativeDailyPrice.trade_date.desc())
            .limit(1)
        )

        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()
