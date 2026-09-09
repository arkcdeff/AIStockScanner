"""Advanced Options Foundation Service — Sprint 19.1G-A1.

Provides deterministic, point-in-time safe advanced quantitative options analytics:
  - Enriched Option Chain with IV, Greeks, Moneyness, Breakeven, Liquidity, & Risk Profile
  - Volatility Structure Analytics: IV Smile, IV Skew, IV Term Structure
  - Expected Move Engine (Primary IV S*sqrt(T) + Straddle-derived method)
  - Option Liquidity & Spread Assessment
  - Advanced Options Context hook for Stock 360° / Opportunity Engine
"""
from __future__ import annotations

import math
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.derivative_contract import (
    DerivativeContract,
    DerivativeLotSizeHistory,
)
from app.schemas.advanced_options import (
    AdvancedOptionsContext,
    EnrichedOptionChainRow,
    EnrichedOptionChainSide,
    ExpectedMoveAnalytics,
    IVSkewAnalytics,
    IVSmileItem,
    IVTermStructureItem,
    OptionBreakevenRead,
    OptionIVResult,
    OptionLiquidityAssessment,
    OptionMoneynessRead,
    OptionRiskProfile,
    RiskFreeRateInfo,
)
from app.services.black_scholes import (
    DEFAULT_RISK_FREE_RATE,
    calculate_greeks,
    implied_volatility,
)
from app.services.historical_option_chain import (
    HistoricalOptionChainService,
    HistoricalOptionChainSide,
)


class AdvancedOptionsService:
    """Deterministic quantitative options analytics & enriched option chain service."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.chain_service = HistoricalOptionChainService(session)

    async def _resolve_historical_lot_size(
        self,
        u_type: str,
        u_id: int,
        trade_date: date,
        as_of_date: date,
    ) -> tuple[int | None, str, str]:
        """Query historical lot size from DerivativeLotSizeHistory PIT.

        Returns:
            tuple[lot_size, lot_size_source, lot_size_quality]
        """
        col = (
            DerivativeLotSizeHistory.underlying_index_id
            if u_type == "INDEX"
            else DerivativeLotSizeHistory.underlying_stock_id
        )
        stmt = (
            select(DerivativeLotSizeHistory.lot_size)
            .where(col == u_id)
            .where(DerivativeLotSizeHistory.effective_from <= trade_date)
            .where(
                (DerivativeLotSizeHistory.effective_to.is_(None))
                | (DerivativeLotSizeHistory.effective_to >= trade_date)
            )
            .where(DerivativeLotSizeHistory.available_at <= as_of_date)
            .order_by(
                DerivativeLotSizeHistory.effective_from.desc(),
                DerivativeLotSizeHistory.id.desc(),
            )
        )
        res = await self.session.execute(stmt)
        hist_lot = res.scalar_one_or_none()
        if hist_lot is not None:
            return (hist_lot, "HISTORICAL_PIT", "VERIFIED_HISTORICAL")

        return (None, "UNAVAILABLE", "UNAVAILABLE")

    async def get_enriched_option_chain(
        self,
        symbol: str,
        trade_date: date,
        expiry_date: date,
        as_of_date: date | None = None,
        risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    ) -> list[EnrichedOptionChainRow]:
        """Reconstruct point-in-time EOD option chain enriched with IV, Greeks, Moneyness, Breakeven, & Liquidity."""
        as_of = as_of_date or trade_date
        chain = await self.chain_service.get_historical_option_chain(
            symbol=symbol,
            trade_date=trade_date,
            expiry_date=expiry_date,
            as_of_date=as_of,
        )

        spot = float(chain.resolved_spot) if chain.resolved_spot and chain.resolved_spot > 0 else 0.0
        dte = chain.calendar_days_to_expiry
        time_to_expiry = max(1e-4, dte / 365.0)

        u_type = chain.underlying_type
        u_id = chain.underlying_id
        hist_lot, hist_lot_source, hist_lot_quality = await self._resolve_historical_lot_size(
            u_type, u_id, trade_date, as_of
        )

        # Batch load lot sizes and provenance
        contract_ids: set[int] = set()
        for r in chain.rows:
            if r.call:
                contract_ids.add(r.call.contract_id)
            if r.put:
                contract_ids.add(r.put.contract_id)

        contract_lot_info: dict[int, tuple[int | None, str, str]] = {}
        lot_size_map: dict[int, int] = {}
        if contract_ids:
            c_stmt = select(
                DerivativeContract.id,
                DerivativeContract.lot_size,
                DerivativeContract.source_name,
            ).where(DerivativeContract.id.in_(list(contract_ids)))
            c_res = await self.session.execute(c_stmt)
            for c_id, l_size, src_name in c_res.all():
                if hist_lot is not None and hist_lot > 0:
                    contract_lot_info[c_id] = (hist_lot, hist_lot_source, hist_lot_quality)
                    lot_size_map[c_id] = hist_lot
                elif src_name is not None and l_size is not None and l_size > 0:
                    contract_lot_info[c_id] = (l_size, "SOURCE_VERIFIED", "SOURCE_VERIFIED")
                    lot_size_map[c_id] = l_size
                else:
                    contract_lot_info[c_id] = (None, "UNAVAILABLE", "UNAVAILABLE")
                    lot_size_map[c_id] = 1

        enriched_rows: list[EnrichedOptionChainRow] = []
        for r in chain.rows:
            strike_dec = r.strike_price
            strike_flt = float(strike_dec)

            ce_enriched = self._enrich_side(
                side=r.call,
                option_type="CE",
                strike_dec=strike_dec,
                strike_flt=strike_flt,
                spot=spot,
                spot_dec=chain.resolved_spot or Decimal("0"),
                dte=dte,
                time_to_expiry=time_to_expiry,
                lot_size_map=lot_size_map,
                risk_free_rate=risk_free_rate,
                contract_lot_info=contract_lot_info,
            )

            pe_enriched = self._enrich_side(
                side=r.put,
                option_type="PE",
                strike_dec=strike_dec,
                strike_flt=strike_flt,
                spot=spot,
                spot_dec=chain.resolved_spot or Decimal("0"),
                dte=dte,
                time_to_expiry=time_to_expiry,
                lot_size_map=lot_size_map,
                risk_free_rate=risk_free_rate,
                contract_lot_info=contract_lot_info,
            )

            enriched_rows.append(
                EnrichedOptionChainRow(
                    strike_price=strike_dec,
                    is_atm=r.is_atm,
                    is_paired=r.is_paired,
                    call=ce_enriched,
                    put=pe_enriched,
                )
            )

        return enriched_rows

    def _enrich_side(
        self,
        side: HistoricalOptionChainSide | None,
        option_type: str,
        strike_dec: Decimal,
        strike_flt: float,
        spot: float,
        spot_dec: Decimal,
        dte: int,
        time_to_expiry: float,
        lot_size_map: dict[int, int] | None = None,
        risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
        contract_lot_info: dict[int, tuple[int | None, str, str]] | None = None,
    ) -> EnrichedOptionChainSide | None:
        """Enrich a single option side with quantitative IV, Greeks, Moneyness, Breakeven, Liquidity & Risk."""
        if side is None:
            return None

        # Price reference hierarchy
        price_used_dec = side.settlement_price or side.close or side.last_price
        price_source = "SETTLEMENT" if side.settlement_price is not None else ("CLOSE" if side.close is not None else "LAST")
        price_used_flt = float(price_used_dec) if price_used_dec is not None else 0.0

        # Moneyness & Intrinsic Value
        if option_type == "CE":
            intrinsic_flt = max(0.0, spot - strike_flt)
        else:
            intrinsic_flt = max(0.0, strike_flt - spot)
        intrinsic_dec = Decimal(str(round(intrinsic_flt, 4)))
        time_val_dec = Decimal(str(round(max(0.0, price_used_flt - intrinsic_flt), 4)))

        dist_from_spot = abs(spot_dec - strike_dec) if spot_dec > 0 else Decimal("0")
        dist_pct = (dist_from_spot / spot_dec * Decimal("100.0")) if spot_dec > 0 else Decimal("0")

        # Moneyness Classification
        if dist_pct <= Decimal("1.0"):
            m_class = "ATM"
        elif option_type == "CE":
            if spot_dec > strike_dec:
                m_class = "DEEP_ITM" if dist_pct > Decimal("10.0") else "ITM"
            else:
                m_class = "DEEP_OTM" if dist_pct > Decimal("10.0") else "OTM"
        else:  # PE
            if spot_dec < strike_dec:
                m_class = "DEEP_ITM" if dist_pct > Decimal("10.0") else "ITM"
            else:
                m_class = "DEEP_OTM" if dist_pct > Decimal("10.0") else "OTM"

        moneyness = OptionMoneynessRead(
            classification=m_class,
            distance_from_spot=dist_from_spot.quantize(Decimal("0.01")),
            distance_pct=dist_pct.quantize(Decimal("0.01")),
            intrinsic_value=intrinsic_dec,
            time_value=time_val_dec,
        )

        # Breakeven
        if option_type == "CE":
            bk_dec = strike_dec + price_used_dec if price_used_dec else strike_dec
        else:
            bk_dec = strike_dec - price_used_dec if price_used_dec else strike_dec
        bk_dist = abs(bk_dec - spot_dec) if spot_dec > 0 else Decimal("0")
        bk_dist_pct = (bk_dist / spot_dec * Decimal("100.0")) if spot_dec > 0 else Decimal("0")

        breakeven = OptionBreakevenRead(
            breakeven_price=bk_dec.quantize(Decimal("0.01")),
            distance_to_breakeven=bk_dist.quantize(Decimal("0.01")),
            distance_to_breakeven_pct=bk_dist_pct.quantize(Decimal("0.01")),
        )

        # Implied Volatility & Greeks
        if spot > 0 and price_used_flt > 0 and dte > 0:
            iv_res = implied_volatility(
                market_price=price_used_flt,
                spot=spot,
                strike=strike_flt,
                time_to_expiry=time_to_expiry,
                option_type=option_type,
                risk_free_rate=risk_free_rate,
            )
            greeks = calculate_greeks(
                spot=spot,
                strike=strike_flt,
                time_to_expiry=time_to_expiry,
                volatility=iv_res.iv,
                option_type=option_type,
                risk_free_rate=risk_free_rate,
            )
        else:
            status = "ZERO_DTE" if dte <= 0 else ("INVALID_PRICE" if price_used_flt <= 0 else "NO_SPOT")
            iv_res = OptionIVResult(solver_status=status)
            greeks = calculate_greeks(
                spot=spot,
                strike=strike_flt,
                time_to_expiry=time_to_expiry,
                volatility=None,
                option_type=option_type,
                risk_free_rate=risk_free_rate,
            )

        # Liquidity Assessment
        vol = side.volume_contracts or 0
        oi = side.open_interest or 0
        if vol >= 500 or oi >= 5000:
            l_class = "HIGH"
        elif vol >= 50 or oi >= 500:
            l_class = "MEDIUM"
        elif vol > 0 or oi > 0:
            l_class = "LOW"
        else:
            l_class = "ILLIQUID"

        liquidity = OptionLiquidityAssessment(
            classification=l_class,
            volume_contracts=vol,
            open_interest=side.open_interest,
            bid=None,
            ask=None,
            spread=None,
            spread_pct=None,
            has_bid_ask_data=False,
        )

        # Risk Profile
        if contract_lot_info is not None:
            lot_size, lot_source, lot_quality = contract_lot_info.get(
                side.contract_id, (None, "UNAVAILABLE", "UNAVAILABLE")
            )
        elif lot_size_map:
            lot_size = lot_size_map.get(side.contract_id)
            lot_source = "UNAVAILABLE" if lot_size is None else "CONFIGURED"
            lot_quality = "UNAVAILABLE" if lot_size is None else "ESTIMATED"
        else:
            lot_size = None
            lot_source = "UNAVAILABLE"
            lot_quality = "UNAVAILABLE"

        if lot_source == "UNAVAILABLE" or lot_size is None or lot_size <= 0:
            premium_at_risk = None
        else:
            prem_risk = (price_used_dec or Decimal("0")) * Decimal(str(lot_size))
            premium_at_risk = prem_risk.quantize(Decimal("0.01"))

        risk_profile = OptionRiskProfile(
            premium_at_risk=premium_at_risk,
            intrinsic_value=intrinsic_dec,
            time_value=time_val_dec,
            theta_per_day=greeks.theta_per_day,
            gamma=greeks.gamma,
            vega_per_pct=greeks.vega_per_pct,
            iv=iv_res.iv,
            dte=dte,
            liquidity_class=l_class,
            breakeven_distance_pct=bk_dist_pct.quantize(Decimal("0.01")),
            lot_source=lot_source,
            lot_quality=lot_quality,
        )

        rate_info = RiskFreeRateInfo(
            rate=risk_free_rate,
            rate_source="CONFIGURED_FALLBACK" if risk_free_rate == DEFAULT_RISK_FREE_RATE else "EXPLICIT_PARAMETER",
            rate_quality="APPROXIMATION" if risk_free_rate == DEFAULT_RISK_FREE_RATE else "VALID",
        )

        return EnrichedOptionChainSide(
            contract_id=side.contract_id,
            trading_symbol=side.trading_symbol,
            option_type=option_type,
            strike_price=strike_dec,
            price_used=price_used_dec,
            price_source=price_source,
            open=side.open,
            high=side.high,
            low=side.low,
            close=side.close,
            last_price=side.last_price,
            settlement_price=side.settlement_price,
            volume_contracts=vol,
            open_interest=side.open_interest,
            change_in_open_interest=side.change_in_open_interest,
            iv_result=iv_res,
            greeks=greeks,
            moneyness=moneyness,
            breakeven=breakeven,
            liquidity=liquidity,
            risk_profile=risk_profile,
            rate_info=rate_info,
            dividend_yield=0.0,
            dividend_assumption="ZERO_DIVIDEND_ASSUMPTION",
        )

    async def get_iv_smile(
        self,
        symbol: str,
        trade_date: date,
        expiry_date: date,
        as_of_date: date | None = None,
    ) -> list[IVSmileItem]:
        """Compute IV Smile across actual imported strikes for an expiry."""
        enriched = await self.get_enriched_option_chain(symbol, trade_date, expiry_date, as_of_date)
        items: list[IVSmileItem] = []

        for row in enriched:
            ce_iv = row.call.iv_result.iv if row.call else None
            pe_iv = row.put.iv_result.iv if row.put else None

            # Representative IV prefers OTM side for cleaner smile curve
            m_label = row.call.moneyness.classification if row.call else (row.put.moneyness.classification if row.put else "ATM")
            d_pct = row.call.moneyness.distance_pct if row.call else (row.put.moneyness.distance_pct if row.put else Decimal("0"))

            if m_label in ("OTM", "DEEP_OTM") and ce_iv is not None:
                rep_iv = ce_iv
            elif pe_iv is not None:
                rep_iv = pe_iv
            else:
                rep_iv = ce_iv

            items.append(
                IVSmileItem(
                    strike_price=row.strike_price,
                    moneyness=m_label,
                    distance_pct=d_pct,
                    ce_iv=ce_iv,
                    pe_iv=pe_iv,
                    representative_iv=rep_iv,
                )
            )

        return items

    async def get_iv_skew(
        self,
        symbol: str,
        trade_date: date,
        expiry_date: date,
        as_of_date: date | None = None,
    ) -> IVSkewAnalytics:
        """Compute IV Skew analytics (OTM Put IV vs OTM Call IV)."""
        enriched = await self.get_enriched_option_chain(symbol, trade_date, expiry_date, as_of_date)

        atm_row = next((r for r in enriched if r.is_atm), None)
        if not atm_row and enriched:
            atm_row = enriched[len(enriched) // 2]

        atm_strike = atm_row.strike_price if atm_row else Decimal("0")
        atm_iv = None
        if atm_row:
            if atm_row.call and atm_row.call.iv_result.iv:
                atm_iv = atm_row.call.iv_result.iv
            elif atm_row.put and atm_row.put.iv_result.iv:
                atm_iv = atm_row.put.iv_result.iv

        # Find ~5% OTM Put and ~5% OTM Call
        otm_put_row = next((r for r in enriched if r.put and r.put.moneyness.classification == "OTM"), None)
        otm_call_row = next((r for r in enriched if r.call and r.call.moneyness.classification == "OTM"), None)

        otm_put_iv = otm_put_row.put.iv_result.iv if otm_put_row and otm_put_row.put else None
        otm_call_iv = otm_call_row.call.iv_result.iv if otm_call_row and otm_call_row.call else None

        skew_spread = (otm_put_iv - otm_call_iv) if (otm_put_iv is not None and otm_call_iv is not None) else None
        skew_ratio = (otm_put_iv / otm_call_iv) if (otm_put_iv is not None and otm_call_iv is not None and otm_call_iv > 0) else None

        if skew_spread is not None:
            if skew_spread > 0.02:
                interp = "SKEW_PUT_PREMIUM"
            elif skew_spread < -0.02:
                interp = "SKEW_CALL_PREMIUM"
            else:
                interp = "SKEW_NEUTRAL"
        else:
            interp = "SKEW_NEUTRAL"

        chain = await self.chain_service.get_historical_option_chain(symbol, trade_date, expiry_date, as_of_date)

        return IVSkewAnalytics(
            expiry_date=expiry_date,
            dte=chain.calendar_days_to_expiry,
            atm_strike=atm_strike,
            atm_iv=atm_iv,
            otm_put_iv=otm_put_iv,
            otm_call_iv=otm_call_iv,
            skew_spread=round(skew_spread, 4) if skew_spread is not None else None,
            skew_ratio=round(skew_ratio, 4) if skew_ratio is not None else None,
            interpretation=interp,
        )

    async def get_iv_term_structure(
        self,
        symbol: str,
        trade_date: date,
        as_of_date: date | None = None,
    ) -> list[IVTermStructureItem]:
        """Compute IV Term Structure across actual available expiries."""
        as_of = as_of_date or trade_date
        expiries = await self.chain_service.get_available_expiries(symbol, trade_date, as_of)

        items: list[IVTermStructureItem] = []
        for idx, exp in enumerate(expiries):
            enriched = await self.get_enriched_option_chain(symbol, trade_date, exp, as_of)
            atm_row = next((r for r in enriched if r.is_atm), None)
            if not atm_row and enriched:
                atm_row = enriched[len(enriched) // 2]

            atm_strike = atm_row.strike_price if atm_row else Decimal("0")
            atm_iv = None
            if atm_row:
                if atm_row.call and atm_row.call.iv_result.iv:
                    atm_iv = atm_row.call.iv_result.iv
                elif atm_row.put and atm_row.put.iv_result.iv:
                    atm_iv = atm_row.put.iv_result.iv

            tot_oi = sum(((r.call.open_interest or 0) if r.call else 0) + ((r.put.open_interest or 0) if r.put else 0) for r in enriched)
            tot_vol = sum(((r.call.volume_contracts or 0) if r.call else 0) + ((r.put.volume_contracts or 0) if r.put else 0) for r in enriched)

            dte = (exp - trade_date).days
            items.append(
                IVTermStructureItem(
                    expiry_date=exp,
                    dte=dte,
                    atm_strike=atm_strike,
                    atm_iv=atm_iv,
                    is_front_expiry=(idx == 0),
                    total_oi=tot_oi,
                    total_volume=tot_vol,
                )
            )

        return items

    async def get_expected_move(
        self,
        symbol: str,
        trade_date: date,
        expiry_date: date,
        as_of_date: date | None = None,
    ) -> ExpectedMoveAnalytics:
        """Compute Expected Move via Primary IV (S * IV * sqrt(T)) and Straddle-derived methods."""
        enriched = await self.get_enriched_option_chain(symbol, trade_date, expiry_date, as_of_date)
        chain = await self.chain_service.get_historical_option_chain(symbol, trade_date, expiry_date, as_of_date)

        spot = chain.resolved_spot or Decimal("0")
        spot_flt = float(spot)
        dte = chain.calendar_days_to_expiry
        t = max(1e-4, dte / 365.0)

        atm_row = next((r for r in enriched if r.is_atm), None)
        if not atm_row and enriched:
            atm_row = enriched[len(enriched) // 2]

        atm_strike = atm_row.strike_price if atm_row else Decimal("0")
        atm_iv = None
        if atm_row:
            if atm_row.call and atm_row.call.iv_result.iv:
                atm_iv = atm_row.call.iv_result.iv
            elif atm_row.put and atm_row.put.iv_result.iv:
                atm_iv = atm_row.put.iv_result.iv

        # Primary IV Expected Move = S * IV * sqrt(T)
        if spot_flt > 0 and atm_iv is not None and atm_iv > 0:
            em_pts_flt = spot_flt * atm_iv * math.sqrt(t)
            em_pct_flt = (em_pts_flt / spot_flt) * 100.0
        else:
            em_pts_flt = 0.0
            em_pct_flt = 0.0

        em_pts = Decimal(str(round(em_pts_flt, 2)))
        em_pct = Decimal(str(round(em_pct_flt, 2)))

        upper = (spot + em_pts).quantize(Decimal("0.01"))
        lower = (spot - em_pts).quantize(Decimal("0.01"))

        # Straddle Expected Move (0.85 * (Call_ATM + Put_ATM))
        straddle_price_dec: Decimal | None = None
        straddle_em_pts: Decimal | None = None
        straddle_em_pct: Decimal | None = None

        if atm_row and atm_row.call and atm_row.call.price_used and atm_row.put and atm_row.put.price_used:
            straddle_price_dec = atm_row.call.price_used + atm_row.put.price_used
            straddle_em_pts = (straddle_price_dec * Decimal("0.85")).quantize(Decimal("0.01"))
            if spot > 0:
                straddle_em_pct = (straddle_em_pts / spot * Decimal("100.0")).quantize(Decimal("0.01"))

        return ExpectedMoveAnalytics(
            expiry_date=expiry_date,
            dte=dte,
            resolved_spot=spot.quantize(Decimal("0.01")),
            atm_strike=atm_strike,
            atm_iv=atm_iv,
            expected_move_points=em_pts,
            expected_move_pct=em_pct,
            upper_bound=upper,
            lower_bound=lower,
            straddle_price=straddle_price_dec,
            straddle_expected_move_points=straddle_em_pts,
            straddle_expected_move_pct=straddle_em_pct,
            methodology="PRIMARY_IV_SQRT_TIME",
            straddle_methodology="ATM_STRADDLE_85PCT_HEURISTIC" if straddle_price_dec is not None else None,
            rate_info=RiskFreeRateInfo(),
            dividend_yield=0.0,
            dividend_assumption="ZERO_DIVIDEND_ASSUMPTION",
        )

    async def get_advanced_options_context(
        self,
        symbol: str,
        trade_date: date,
        as_of_date: date | None = None,
    ) -> AdvancedOptionsContext:
        """Hook for Stock 360 & Opportunity Engine exposing quantitative options state."""
        as_of = as_of_date or trade_date
        expiries = await self.chain_service.get_available_expiries(symbol, trade_date, as_of)

        if not expiries:
            return AdvancedOptionsContext(
                symbol=symbol.upper(),
                trade_date=trade_date,
                resolved_spot=Decimal("0"),
                is_fno_security=False,
                warnings=["NO_EXPIRIES_AVAILABLE"],
                provenance={"as_of_date": str(as_of)},
            )

        front_exp = expiries[0]
        chain = await self.chain_service.get_historical_option_chain(symbol, trade_date, front_exp, as_of)
        spot = chain.resolved_spot or Decimal("0")

        em = await self.get_expected_move(symbol, trade_date, front_exp, as_of)
        skew = await self.get_iv_skew(symbol, trade_date, front_exp, as_of)
        ts = await self.get_iv_term_structure(symbol, trade_date, as_of)

        atm_iv = em.atm_iv
        if atm_iv is not None:
            if atm_iv > 0.35:
                regime = "HIGH_IV"
            elif atm_iv < 0.14:
                regime = "LOW_IV"
            else:
                regime = "NORMAL_IV"
        else:
            regime = "UNKNOWN"

        # Term structure slope
        if len(ts) >= 2 and ts[0].atm_iv and ts[-1].atm_iv:
            if ts[-1].atm_iv > ts[0].atm_iv + 0.02:
                slope = "CONTANGO"
            elif ts[0].atm_iv > ts[-1].atm_iv + 0.02:
                slope = "BACKWARDATION"
            else:
                slope = "FLAT"
        else:
            slope = "FLAT"

        return AdvancedOptionsContext(
            symbol=symbol.upper(),
            trade_date=trade_date,
            resolved_spot=spot,
            is_fno_security=True,
            front_expiry=front_exp,
            dte=chain.calendar_days_to_expiry,
            atm_iv=atm_iv,
            iv_regime=regime,
            expected_move_pct=em.expected_move_pct,
            skew_spread=skew.skew_spread,
            term_structure_slope=slope,
            liquidity_quality="HIGH" if chain.rows else "LOW",
            provenance={"pit_trade_date": str(trade_date), "pit_as_of_date": str(as_of)},
        )
