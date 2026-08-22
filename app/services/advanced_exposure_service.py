"""Advanced Options Exposure Analytics Service — Sprint 19.1G-A2.

Provides deterministic exposure calculations:
  - Contract, Strike, Expiry, and Aggregate Gross GEX, DEX, Vanna, Charm
  - Historical Lot Size resolution via DerivativeLotSizeHistory (PIT safe)
  - Scenario-Signed GEX & DEX under explicit assumption profiles
  - Spot Scenario Gamma Profile & Grid (S +/- 10%, 0.5% steps, STATIC_IV)
  - Scenario Gamma Flip (Zero Gamma level) detection & linear interpolation
  - Gamma Concentration (Top 1/3/5 strikes share of exposure)
  - Advanced Exposure Context hook for Stock 360°
"""
from __future__ import annotations

import math
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.derivative_contract import (
    DerivativeContract,
    DerivativeLotSizeHistory,
)
from app.schemas.advanced_exposure import (
    AdvancedExposureContext,
    AdvancedExposureSnapshot,
    GammaConcentration,
    GammaFlipResult,
    GammaProfilePoint,
    OptionExposureRow,
    StrikeExposureRow,
)
from app.schemas.advanced_options import RiskFreeRateInfo
from app.services.advanced_options_service import AdvancedOptionsService
from app.services.black_scholes import (
    DEFAULT_RISK_FREE_RATE,
    _norm_pdf,
)
from app.services.historical_option_chain import HistoricalOptionChainService

# Assumption Profiles
PROFILE_GROSS_UNSIGNED = "GROSS_UNSIGNED"
PROFILE_DEALER_HEURISTIC = "DEALER_SHORT_CALL_LONG_PUT_HEURISTIC"


class AdvancedExposureService:
    """Service for computing deterministic options exposure analytics."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.chain_service = HistoricalOptionChainService(session)
        self.options_service = AdvancedOptionsService(session)

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

    async def get_contract_exposures(
        self,
        symbol: str,
        trade_date: date,
        expiry_date: date,
        as_of_date: date | None = None,
        assumption_profile: str = PROFILE_GROSS_UNSIGNED,
        risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    ) -> list[OptionExposureRow]:
        """Compute contract-level exposure metrics for an underlying and expiry."""
        as_of = as_of_date or trade_date
        enriched_rows = await self.options_service.get_enriched_option_chain(
            symbol, trade_date, expiry_date, as_of
        )
        chain = await self.chain_service.get_historical_option_chain(
            symbol, trade_date, expiry_date, as_of
        )

        spot = float(chain.resolved_spot or Decimal("0"))
        if spot <= 0:
            return []

        u_type = chain.underlying_type
        u_id = chain.underlying_id

        hist_lot, lot_source, lot_quality = await self._resolve_historical_lot_size(u_type, u_id, trade_date, as_of)

        if hist_lot is None:
            # Check contract source provably effective for T
            col_cond = (
                DerivativeContract.underlying_index_id == u_id
                if u_type == "INDEX"
                else DerivativeContract.underlying_stock_id == u_id
            )
            c_res = await self.session.execute(
                select(DerivativeContract.lot_size)
                .where(col_cond)
                .where(DerivativeContract.available_at <= trade_date)
                .where(DerivativeContract.expiry_date == expiry_date)
            )
            c_lot = c_res.scalar_one_or_none()
            if c_lot is not None:
                hist_lot = c_lot
                lot_source = "SOURCE_VERIFIED"
                lot_quality = "SOURCE_VERIFIED"

        exposures: list[OptionExposureRow] = []
        for r in enriched_rows:
            for side in (r.call, r.put):
                if side is None:
                    continue

                lot_size = hist_lot
                oi = side.open_interest or 0
                vol = side.volume_contracts or 0
                greeks = side.greeks

                if lot_size is None or lot_size <= 0:
                    # Lot size is unavailable - MISSING != ZERO
                    gross_gex_dec = None
                    scenario_gex_dec = None
                    gross_dex_dec = None
                    scenario_dex_dec = None
                    gross_vanna_dec = None
                    scenario_vanna_dec = None
                    gross_charm_dec = None
                    scenario_charm_dec = None
                else:
                    # Gross GEX = Gamma * OI * Lot * Spot^2 * 0.01 (INR Notional per 1% spot move)
                    if greeks.gamma is not None and oi > 0:
                        gross_gex_flt = greeks.gamma * float(oi) * float(lot_size) * (spot**2) * 0.01
                        gross_gex_dec = Decimal(str(round(gross_gex_flt, 2)))
                    else:
                        gross_gex_dec = Decimal("0.00")

                    # Scenario Signed GEX
                    if assumption_profile == PROFILE_DEALER_HEURISTIC:
                        if side.option_type == "CE":
                            scenario_gex_dec = -gross_gex_dec
                        else:
                            scenario_gex_dec = +gross_gex_dec
                    else:
                        scenario_gex_dec = gross_gex_dec

                    # Gross DEX = |Delta| * OI * Lot * Spot (INR Notional)
                    if greeks.delta is not None and oi > 0:
                        gross_dex_flt = abs(greeks.delta) * float(oi) * float(lot_size) * spot
                        gross_dex_dec = Decimal(str(round(gross_dex_flt, 2)))
                        scenario_dex_flt = greeks.delta * float(oi) * float(lot_size) * spot
                        scenario_dex_dec = Decimal(str(round(scenario_dex_flt, 2)))
                    else:
                        gross_dex_dec = Decimal("0.00")
                        scenario_dex_dec = Decimal("0.00")

                    # Gross Vanna Exposure = |Vanna_1%| * OI * Lot * Spot
                    if greeks.vanna_per_pct is not None and oi > 0:
                        gross_vanna_flt = abs(greeks.vanna_per_pct) * float(oi) * float(lot_size) * spot
                        gross_vanna_dec = Decimal(str(round(gross_vanna_flt, 2)))
                        scenario_vanna_dec = Decimal(str(round(greeks.vanna_per_pct * float(oi) * float(lot_size) * spot, 2)))
                    else:
                        gross_vanna_dec = Decimal("0.00")
                        scenario_vanna_dec = Decimal("0.00")

                    # Gross Charm Exposure = |Charm_day| * OI * Lot * Spot
                    if greeks.charm_per_day is not None and oi > 0:
                        gross_charm_flt = abs(greeks.charm_per_day) * float(oi) * float(lot_size) * spot
                        gross_charm_dec = Decimal(str(round(gross_charm_flt, 2)))
                        scenario_charm_dec = Decimal(str(round(greeks.charm_per_day * float(oi) * float(lot_size) * spot, 2)))
                    else:
                        gross_charm_dec = Decimal("0.00")
                        scenario_charm_dec = Decimal("0.00")

                exposures.append(
                    OptionExposureRow(
                        contract_id=side.contract_id,
                        trading_symbol=side.trading_symbol,
                        option_type=side.option_type,
                        strike_price=side.strike_price,
                        expiry_date=expiry_date,
                        dte=side.risk_profile.dte,
                        open_interest=side.open_interest,
                        volume_contracts=vol,
                        lot_size=lot_size,
                        lot_size_source=lot_source,
                        lot_size_quality=lot_quality,
                        price_used=side.close,
                        iv=side.iv_result.iv,
                        greeks=greeks,
                        gross_gamma_exposure=gross_gex_dec,
                        scenario_gex=scenario_gex_dec,
                        gross_delta_exposure=gross_dex_dec,
                        scenario_dex=scenario_dex_dec,
                        gross_vanna_exposure=gross_vanna_dec,
                        scenario_vanna_exposure=scenario_vanna_dec,
                        gross_charm_exposure=gross_charm_dec,
                        scenario_charm_exposure=scenario_charm_dec,
                    )
                )

        return exposures

    async def get_strike_exposure_table(
        self,
        symbol: str,
        trade_date: date,
        expiry_date: date,
        as_of_date: date | None = None,
        assumption_profile: str = PROFILE_GROSS_UNSIGNED,
        risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    ) -> list[StrikeExposureRow]:
        """Aggregate contract-level exposure metrics by paired strike price."""
        contracts = await self.get_contract_exposures(
            symbol, trade_date, expiry_date, as_of_date, assumption_profile, risk_free_rate
        )
        if not contracts:
            return []

        chain = await self.chain_service.get_historical_option_chain(
            symbol, trade_date, expiry_date, as_of_date
        )
        spot = chain.resolved_spot or Decimal("0")

        strike_map: dict[Decimal, dict[str, Any]] = {}
        for row in contracts:
            k = row.strike_price
            if k not in strike_map:
                strike_map[k] = {
                    "strike": k,
                    "ce_oi": 0,
                    "pe_oi": 0,
                    "ce_gross_gex": Decimal("0.00"),
                    "pe_gross_gex": Decimal("0.00"),
                    "ce_scenario_gex": Decimal("0.00"),
                    "pe_scenario_gex": Decimal("0.00"),
                    "gross_dex": Decimal("0.00"),
                    "net_scenario_dex": Decimal("0.00"),
                    "gross_vanna": Decimal("0.00"),
                    "gross_charm": Decimal("0.00"),
                }
            entry = strike_map[k]
            oi = row.open_interest or 0
            if row.option_type == "CE":
                entry["ce_oi"] += oi
                entry["ce_gross_gex"] += row.gross_gamma_exposure or Decimal("0.00")
                entry["ce_scenario_gex"] += row.scenario_gex or Decimal("0.00")
            else:
                entry["pe_oi"] += oi
                entry["pe_gross_gex"] += row.gross_gamma_exposure or Decimal("0.00")
                entry["pe_scenario_gex"] += row.scenario_gex or Decimal("0.00")

            entry["gross_dex"] += row.gross_delta_exposure or Decimal("0.00")
            entry["net_scenario_dex"] += row.scenario_dex or Decimal("0.00")
            entry["gross_vanna"] += row.gross_vanna_exposure or Decimal("0.00")
            entry["gross_charm"] += row.gross_charm_exposure or Decimal("0.00")

        sorted_strikes = sorted(strike_map.keys())

        # Determine ATM strike (nearest to spot)
        atm_strike = min(sorted_strikes, key=lambda s: abs(s - spot)) if sorted_strikes else Decimal("0")

        result: list[StrikeExposureRow] = []
        for k in sorted_strikes:
            e = strike_map[k]
            tot_oi = e["ce_oi"] + e["pe_oi"]
            tot_gross_gex = e["ce_gross_gex"] + e["pe_gross_gex"]
            net_scen_gex = e["ce_scenario_gex"] + e["pe_scenario_gex"]

            result.append(
                StrikeExposureRow(
                    strike_price=k,
                    is_atm=(k == atm_strike),
                    ce_open_interest=e["ce_oi"],
                    pe_open_interest=e["pe_oi"],
                    total_open_interest=tot_oi,
                    ce_gross_gex=e["ce_gross_gex"],
                    pe_gross_gex=e["pe_gross_gex"],
                    total_gross_gex=tot_gross_gex,
                    ce_scenario_gex=e["ce_scenario_gex"],
                    pe_scenario_gex=e["pe_scenario_gex"],
                    net_scenario_gex=net_scen_gex,
                    gross_dex=e["gross_dex"],
                    net_scenario_dex=e["net_scenario_dex"],
                    gross_vanna=e["gross_vanna"],
                    gross_charm=e["gross_charm"],
                )
            )

        return result

    async def get_gamma_profile(
        self,
        symbol: str,
        trade_date: date,
        expiry_date: date,
        as_of_date: date | None = None,
        assumption_profile: str = PROFILE_DEALER_HEURISTIC,
        spot_range_pct: float = 10.0,
        spot_step_pct: float = 0.5,
        risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    ) -> list[GammaProfilePoint]:
        """Compute Scenario Gamma Profile across a bounded spot grid (S +/- range_pct, STATIC_IV)."""
        if spot_range_pct <= 0 or spot_step_pct <= 0:
            raise ValueError("spot_range_pct and spot_step_pct must be strictly positive")

        range_pct = max(1.0, min(30.0, spot_range_pct))
        step_pct = max(0.1, min(5.0, spot_step_pct))

        num_steps = int(round((2 * range_pct) / step_pct))
        if num_steps > 200:
            raise ValueError(f"spot_step_pct {spot_step_pct}% generates excessive grid points ({num_steps + 1} > 200)")

        as_of = as_of_date or trade_date
        contracts = await self.get_contract_exposures(
            symbol, trade_date, expiry_date, as_of, assumption_profile, risk_free_rate
        )
        if not contracts:
            return []

        chain = await self.chain_service.get_historical_option_chain(
            symbol, trade_date, expiry_date, as_of
        )
        base_spot = float(chain.resolved_spot or Decimal("0"))
        if base_spot <= 0:
            return []

        grid_points: list[GammaProfilePoint] = []
        for i in range(num_steps + 1):
            pct_chg = round(-range_pct + (i * step_pct), 4)
            hypo_spot = base_spot * (1.0 + (pct_chg / 100.0))

            tot_gross_gex = 0.0
            tot_scen_gex = 0.0

            for c in contracts:
                if c.iv is None or c.iv <= 0 or c.open_interest is None or c.open_interest <= 0 or c.lot_size is None or c.lot_size <= 0:
                    continue

                t = max(1e-4, c.dte / 365.0)
                sqrt_t = math.sqrt(t)
                strike = float(c.strike_price)

                # Reprice d1 and Gamma holding IV constant (STATIC_IV)
                d1 = (math.log(hypo_spot / strike) + (risk_free_rate + 0.5 * c.iv**2) * t) / (c.iv * sqrt_t)
                pdf_d1 = _norm_pdf(d1)
                gamma = pdf_d1 / (hypo_spot * c.iv * sqrt_t)

                # Gross GEX for contract at hypo_spot
                gex = gamma * float(c.open_interest) * float(c.lot_size) * (hypo_spot**2) * 0.01
                tot_gross_gex += gex

                if assumption_profile == PROFILE_DEALER_HEURISTIC:
                    if c.option_type == "CE":
                        tot_scen_gex -= gex
                    else:
                        tot_scen_gex += gex
                else:
                    tot_scen_gex += gex

            grid_points.append(
                GammaProfilePoint(
                    spot_level=Decimal(str(round(hypo_spot, 2))),
                    spot_change_pct=Decimal(str(round(pct_chg, 2))),
                    gross_gex=Decimal(str(round(tot_gross_gex, 2))),
                    scenario_net_gex=Decimal(str(round(tot_scen_gex, 2))),
                )
            )

        return grid_points

    async def get_gamma_flip(
        self,
        symbol: str,
        trade_date: date,
        expiry_date: date,
        as_of_date: date | None = None,
        assumption_profile: str = PROFILE_DEALER_HEURISTIC,
        spot_range_pct: float = 10.0,
        spot_step_pct: float = 0.5,
    ) -> GammaFlipResult:
        """Locate Scenario Gamma Flip (Zero Gamma level) via spot grid zero-crossing detection."""
        if assumption_profile == PROFILE_GROSS_UNSIGNED:
            grid = await self.get_gamma_profile(
                symbol, trade_date, expiry_date, as_of_date, assumption_profile, spot_range_pct, spot_step_pct
            )
            grid_lower = grid[0].spot_level if grid else Decimal("0.00")
            grid_upper = grid[-1].spot_level if grid else Decimal("0.00")
            return GammaFlipResult(
                gamma_flip_level=None,
                status="SIGNED_ASSUMPTION_REQUIRED",
                grid_lower_bound=grid_lower,
                grid_upper_bound=grid_upper,
                crossing_count=0,
            )

        grid = await self.get_gamma_profile(
            symbol, trade_date, expiry_date, as_of_date, assumption_profile, spot_range_pct, spot_step_pct
        )
        if not grid or len(grid) < 2:
            return GammaFlipResult(
                status="INSUFFICIENT_DATA",
                grid_lower_bound=Decimal("0.00"),
                grid_upper_bound=Decimal("0.00"),
            )

        chain = await self.chain_service.get_historical_option_chain(
            symbol, trade_date, expiry_date, as_of_date
        )
        base_spot = float(chain.resolved_spot or Decimal("0"))

        lower_bnd = grid[0].spot_level
        upper_bnd = grid[-1].spot_level

        crossings: list[Decimal] = []

        for i in range(len(grid) - 1):
            p1 = grid[i]
            p2 = grid[i + 1]

            g1 = float(p1.scenario_net_gex)
            g2 = float(p2.scenario_net_gex)

            # Check if sign crosses zero
            if (g1 <= 0 and g2 >= 0) or (g1 >= 0 and g2 <= 0):
                if g1 == g2:
                    flip_s = p1.spot_level
                else:
                    # Linear interpolation for zero crossing
                    s1 = float(p1.spot_level)
                    s2 = float(p2.spot_level)
                    flip_s_flt = s1 + (0.0 - g1) * (s2 - s1) / (g2 - g1)
                    flip_s = Decimal(str(round(flip_s_flt, 2)))
                crossings.append(flip_s)

        if not crossings:
            return GammaFlipResult(
                gamma_flip_level=None,
                status="NO_FLIP_IN_RANGE",
                grid_lower_bound=lower_bnd,
                grid_upper_bound=upper_bnd,
                crossing_count=0,
            )

        base_spot_dec = Decimal(str(round(base_spot, 2)))
        primary_flip = min(crossings, key=lambda c: (abs(c - base_spot_dec), c))
        dist = primary_flip - base_spot_dec
        dist_pct = (dist / base_spot_dec) * Decimal("100.0") if base_spot_dec > 0 else Decimal("0.00")

        status_str = "FLIP_FOUND" if len(crossings) == 1 else "MULTIPLE_FLIPS_FOUND"

        return GammaFlipResult(
            gamma_flip_level=primary_flip,
            status=status_str,
            distance_from_spot=Decimal(str(round(dist, 2))),
            distance_pct=Decimal(str(round(dist_pct, 2))),
            grid_lower_bound=lower_bnd,
            grid_upper_bound=upper_bnd,
            crossing_count=len(crossings),
        )

    async def get_exposure_snapshot(
        self,
        symbol: str,
        trade_date: date,
        expiry_date: date | None = None,
        as_of_date: date | None = None,
        assumption_profile: str = PROFILE_GROSS_UNSIGNED,
    ) -> AdvancedExposureSnapshot:
        """Assemble comprehensive Advanced Exposure Snapshot."""
        as_of = as_of_date or trade_date

        if expiry_date is None:
            # Resolve front expiry
            available_expiries = await self.chain_service.get_available_expiries(symbol, trade_date, as_of)
            if not available_expiries:
                # Return empty snapshot
                return AdvancedExposureSnapshot(
                    symbol=symbol,
                    trade_date=trade_date,
                    as_of_date=as_of,
                    resolved_spot=Decimal("0.00"),
                    exposure_quality="INSUFFICIENT",
                    gamma_flip=GammaFlipResult(
                        grid_lower_bound=Decimal("0.00"),
                        grid_upper_bound=Decimal("0.00"),
                    ),
                    concentration=GammaConcentration(),
                    warnings=["NO_AVAILABLE_EXPIRIES"],
                )
            target_expiry = available_expiries[0]
            expiry_scope_str = "FRONT_EXPIRY"
        else:
            target_expiry = expiry_date
            expiry_scope_str = "SINGLE_EXPIRY"

        chain = await self.chain_service.get_historical_option_chain(symbol, trade_date, target_expiry, as_of)
        spot = chain.resolved_spot or Decimal("0.00")

        strikes = await self.get_strike_exposure_table(
            symbol, trade_date, target_expiry, as_of, assumption_profile
        )

        gross_call_gex = sum((r.ce_gross_gex for r in strikes), Decimal("0.00"))
        gross_put_gex = sum((r.pe_gross_gex for r in strikes), Decimal("0.00"))
        gross_tot_gex = gross_call_gex + gross_put_gex

        scen_call_gex = sum((r.ce_scenario_gex for r in strikes), Decimal("0.00"))
        scen_put_gex = sum((r.pe_scenario_gex for r in strikes), Decimal("0.00"))
        scen_net_gex = scen_call_gex + scen_put_gex

        gross_tot_dex = sum((r.gross_dex for r in strikes), Decimal("0.00"))
        scen_net_dex = sum((r.net_scenario_dex for r in strikes), Decimal("0.00"))

        gross_tot_vanna = sum((r.gross_vanna for r in strikes), Decimal("0.00"))
        gross_tot_charm = sum((r.gross_charm for r in strikes), Decimal("0.00"))

        # Concentration metrics
        sorted_by_gex = sorted(strikes, key=lambda r: r.total_gross_gex, reverse=True)
        top1_k = sorted_by_gex[0].strike_price if sorted_by_gex else None
        top1_gex = sorted_by_gex[0].total_gross_gex if sorted_by_gex else Decimal("0.00")
        top1_share = (top1_gex / gross_tot_gex * Decimal("100.0")).quantize(Decimal("0.01")) if gross_tot_gex > 0 else Decimal("0.00")

        top3_ks = [r.strike_price for r in sorted_by_gex[:3]]
        top3_gex = sum((r.total_gross_gex for r in sorted_by_gex[:3]), Decimal("0.00"))
        top3_share = (top3_gex / gross_tot_gex * Decimal("100.0")).quantize(Decimal("0.01")) if gross_tot_gex > 0 else Decimal("0.00")

        top5_ks = [r.strike_price for r in sorted_by_gex[:5]]
        top5_gex = sum((r.total_gross_gex for r in sorted_by_gex[:5]), Decimal("0.00"))
        top5_share = (top5_gex / gross_tot_gex * Decimal("100.0")).quantize(Decimal("0.01")) if gross_tot_gex > 0 else Decimal("0.00")

        concentration = GammaConcentration(
            top1_strike=top1_k,
            top1_gex=top1_gex,
            top1_share_pct=top1_share,
            top3_strikes=top3_ks,
            top3_share_pct=top3_share,
            top5_strikes=top5_ks,
            top5_share_pct=top5_share,
        )

        # Gamma Flip (computed under DEALER_SHORT_CALL_LONG_PUT_HEURISTIC profile for zero-crossing analysis)
        flip_res = await self.get_gamma_flip(
            symbol, trade_date, target_expiry, as_of, PROFILE_DEALER_HEURISTIC
        )

        contracts_list = await self.get_contract_exposures(symbol, trade_date, target_expiry, as_of, assumption_profile)
        has_unavailable_lot = any(c.lot_size_quality == "UNAVAILABLE" for c in contracts_list)
        if has_unavailable_lot or not contracts_list:
            quality_str = "INSUFFICIENT" if not contracts_list else "LOW"
        elif gross_tot_gex == Decimal("0.00"):
            quality_str = "MEDIUM"
        else:
            quality_str = "HIGH"

        return AdvancedExposureSnapshot(
            symbol=symbol,
            trade_date=trade_date,
            as_of_date=as_of,
            resolved_spot=spot.quantize(Decimal("0.01")),
            expiry_scope=expiry_scope_str,
            assumption_profile=assumption_profile,
            gross_call_gex=gross_call_gex,
            gross_put_gex=gross_put_gex,
            gross_total_gex=gross_tot_gex,
            scenario_call_gex=scen_call_gex,
            scenario_put_gex=scen_put_gex,
            scenario_net_gex=scen_net_gex,
            gross_total_dex=gross_tot_dex,
            scenario_net_dex=scen_net_dex,
            gross_total_vanna=gross_tot_vanna,
            gross_total_charm=gross_tot_charm,
            gamma_flip=flip_res,
            concentration=concentration,
            exposure_quality=quality_str,
            rate_info=RiskFreeRateInfo(),
            dividend_assumption="ZERO_DIVIDEND_ASSUMPTION",
            warnings=chain.quality_warnings,
            provenance={"symbol": symbol, "expiry_date": str(target_expiry), "as_of_date": str(as_of)},
        )

    async def get_advanced_exposure_context(
        self,
        symbol: str,
        trade_date: date,
        as_of_date: date | None = None,
    ) -> AdvancedExposureContext:
        """Fetch Advanced Exposure Hook for Stock 360° & Opportunity Engine."""
        as_of = as_of_date or trade_date

        try:
            snapshot = await self.get_exposure_snapshot(
                symbol, trade_date, None, as_of, PROFILE_DEALER_HEURISTIC
            )
            return AdvancedExposureContext(
                symbol=symbol,
                trade_date=trade_date,
                resolved_spot=snapshot.resolved_spot,
                is_fno_security=True,
                gross_total_gex=snapshot.gross_total_gex,
                scenario_net_gex=snapshot.scenario_net_gex,
                gamma_flip_level=snapshot.gamma_flip.gamma_flip_level,
                distance_to_gamma_flip_pct=snapshot.gamma_flip.distance_pct,
                gross_total_dex=snapshot.gross_total_dex,
                top1_gamma_strike=snapshot.concentration.top1_strike,
                top1_gamma_share_pct=snapshot.concentration.top1_share_pct,
                exposure_quality=snapshot.exposure_quality,
                warnings=snapshot.warnings,
                provenance=snapshot.provenance,
            )
        except Exception as exc:
            return AdvancedExposureContext(
                symbol=symbol,
                trade_date=trade_date,
                resolved_spot=Decimal("0.00"),
                is_fno_security=False,
                exposure_quality="INSUFFICIENT",
                warnings=[f"EXPOSURE_CALCULATION_FAILED: {exc}"],
            )
