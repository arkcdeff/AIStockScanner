"""Historical Option Chain Reconstruction Service — Sprint 19.1D-4.

Reconstructs an authoritative, point-in-time safe EOD historical option chain
for any requested underlying (Index or Stock), trade date, and expiry date.

Reuses stored raw observations in DerivativeContract and DerivativeDailyPrice
without duplicating raw data in persistent chain tables.

Key Features:
  - Polymorphic underlying resolution (MarketIndex, Stock, SecurityAlias)
  - Point-in-time cutoff (price.trade_date == T, price.available_at <= as_of_date)
  - Exact Decimal strike pairing (CE | STRIKE | PE)
  - Missing-side preservation (MISSING != ZERO; call/put = None)
  - Spot reference determination & inconsistency detection across strikes
  - Structural ATM reference calculation (nearest strike to spot)
  - Moneyness classification (ITM, OTM, ATM) per option side
  - Completeness and data quality warning metadata
  - Expiry and date discovery queries
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.exchange_constants import EXCHANGE_NSE
from app.models.daily_price import DailyPrice
from app.models.derivative_contract import DerivativeContract
from app.models.derivative_daily_price import DerivativeDailyPrice
from app.models.market_index import MarketIndex
from app.models.market_index_price import MarketIndexPrice
from app.models.security_alias import SecurityAlias
from app.models.stock import Stock


@dataclass
class HistoricalOptionChainSide:
    """Option observation facts for one side (Call or Put) at a strike."""

    contract_id: int
    trading_symbol: str
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    close: Decimal | None = None
    last_price: Decimal | None = None
    settlement_price: Decimal | None = None
    previous_close: Decimal | None = None
    volume_contracts: int = 0
    turnover: Decimal | None = None
    open_interest: int | None = None
    change_in_open_interest: int | None = None
    moneyness: str | None = None  # ITM, OTM, ATM
    available_at: date | None = None
    source: str | None = None


@dataclass
class HistoricalOptionChainRow:
    """Paired strike row containing CALL side, STRIKE price, and PUT side."""

    strike_price: Decimal
    call: HistoricalOptionChainSide | None = None
    put: HistoricalOptionChainSide | None = None
    is_paired: bool = False
    is_atm: bool = False


@dataclass
class HistoricalOptionChainCompleteness:
    """Coverage and completeness statistics for a reconstructed chain."""

    total_strikes: int = 0
    paired_strikes: int = 0
    ce_only_strikes: int = 0
    pe_only_strikes: int = 0
    ce_row_count: int = 0
    pe_row_count: int = 0
    min_strike: Decimal | None = None
    max_strike: Decimal | None = None


@dataclass
class HistoricalOptionChain:
    """Authoritative reconstructed point-in-time EOD Historical Option Chain."""

    exchange: str
    underlying_type: str  # "INDEX" or "STOCK"
    underlying_id: int
    underlying_symbol: str
    underlying_name: str
    trade_date: date
    expiry_date: date
    calendar_days_to_expiry: int
    source_underlying_value: Decimal | None
    independent_spot: Decimal | None
    resolved_spot: Decimal | None
    atm_reference_strike: Decimal | None
    rows: list[HistoricalOptionChainRow]
    completeness: HistoricalOptionChainCompleteness
    quality_warnings: list[str] = field(default_factory=list)
    as_of_date: date = field(default_factory=date.today)


class HistoricalOptionChainService:
    """Service to reconstruct historical option chains on demand."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_historical_option_chain(
        self,
        symbol: str,
        trade_date: date,
        expiry_date: date,
        as_of_date: date | None = None,
    ) -> HistoricalOptionChain:
        """Reconstruct historical option chain for an underlying, trade date, and expiry."""
        as_of = as_of_date or trade_date

        if trade_date > expiry_date:
            raise ValueError(f"trade_date {trade_date} cannot be after expiry_date {expiry_date}")

        # 1. Resolve underlying identity
        underlying_info = await self._resolve_underlying(symbol)
        if not underlying_info:
            raise ValueError(f"Unknown underlying symbol '{symbol}'")

        u_type, u_id, u_symbol, u_name = underlying_info

        # 2. Query Joined DerivativeContract + DerivativeDailyPrice
        stmt = (
            select(DerivativeDailyPrice, DerivativeContract)
            .join(DerivativeContract, DerivativeDailyPrice.contract_id == DerivativeContract.id)
            .where(DerivativeContract.instrument_type == "OPTION")
            .where(DerivativeContract.expiry_date == expiry_date)
            .where(DerivativeDailyPrice.trade_date == trade_date)
            .where(DerivativeDailyPrice.available_at <= as_of)
            .where(DerivativeContract.available_at <= as_of)
        )

        if u_type == "INDEX":
            stmt = stmt.where(DerivativeContract.underlying_index_id == u_id)
        else:
            stmt = stmt.where(DerivativeContract.underlying_stock_id == u_id)

        stmt = stmt.order_by(
            DerivativeContract.strike_price.asc(),
            DerivativeContract.option_type.asc(),
        )

        res = await self.session.execute(stmt)
        joined_rows = res.all()

        quality_warnings: list[str] = []

        if not joined_rows:
            quality_warnings.append("OPTION_CHAIN_EMPTY")
            completeness = HistoricalOptionChainCompleteness()
            return HistoricalOptionChain(
                exchange=EXCHANGE_NSE,
                underlying_type=u_type,
                underlying_id=u_id,
                underlying_symbol=u_symbol,
                underlying_name=u_name,
                trade_date=trade_date,
                expiry_date=expiry_date,
                calendar_days_to_expiry=(expiry_date - trade_date).days,
                source_underlying_value=None,
                independent_spot=None,
                resolved_spot=None,
                atm_reference_strike=None,
                rows=[],
                completeness=completeness,
                quality_warnings=quality_warnings,
                as_of_date=as_of,
            )

        # 3. Process Source Underlying Values across strikes
        source_underlyings: list[Decimal] = []
        for p_row, _ in joined_rows:
            if p_row.underlying_value is not None:
                source_underlyings.append(Decimal(str(p_row.underlying_value)))

        distinct_sources = sorted(list(set(source_underlyings)))
        source_underlying_val: Decimal | None = None

        if len(distinct_sources) == 1:
            source_underlying_val = distinct_sources[0]
        elif len(distinct_sources) > 1:
            source_underlying_val = distinct_sources[0]
            quality_warnings.append("OPTION_CHAIN_INCONSISTENT_UNDERLYING")

        # 4. Resolve Independent Spot Fallback
        independent_spot = await self._fetch_independent_spot(u_type, u_id, trade_date, as_of)
        resolved_spot = source_underlying_val or independent_spot

        if resolved_spot is None:
            quality_warnings.append("OPTION_CHAIN_NO_SPOT_REFERENCE")

        # 5. Strike Pairing & Side Construction
        strike_map: dict[Decimal, tuple[HistoricalOptionChainSide | None, HistoricalOptionChainSide | None]] = {}

        ce_count = 0
        pe_count = 0

        for p_row, c_row in joined_rows:
            strike = Decimal(str(c_row.strike_price))
            opt_type = c_row.option_type

            # Calculate Moneyness
            moneyness: str | None = None
            if resolved_spot is not None:
                if strike == resolved_spot:
                    moneyness = "ATM"
                elif opt_type == "CE":
                    moneyness = "ITM" if strike < resolved_spot else "OTM"
                elif opt_type == "PE":
                    moneyness = "ITM" if strike > resolved_spot else "OTM"

            side = HistoricalOptionChainSide(
                contract_id=c_row.id,
                trading_symbol=c_row.trading_symbol,
                open=p_row.open,
                high=p_row.high,
                low=p_row.low,
                close=p_row.close,
                last_price=p_row.last_price,
                settlement_price=p_row.settlement_price,
                previous_close=p_row.previous_close,
                volume_contracts=p_row.volume_contracts,
                turnover=p_row.turnover,
                open_interest=p_row.open_interest,
                change_in_open_interest=p_row.change_in_open_interest,
                moneyness=moneyness,
                available_at=p_row.available_at,
                source=p_row.source_name,
            )

            current_ce, current_pe = strike_map.get(strike, (None, None))
            if opt_type == "CE":
                ce_count += 1
                strike_map[strike] = (side, current_pe)
            elif opt_type == "PE":
                pe_count += 1
                strike_map[strike] = (current_ce, side)

        if ce_count == 0:
            quality_warnings.append("OPTION_CHAIN_MISSING_CALL_SIDE")
        if pe_count == 0:
            quality_warnings.append("OPTION_CHAIN_MISSING_PUT_SIDE")

        # 6. Determine ATM Reference Strike (nearest strike to resolved_spot)
        sorted_strikes = sorted(strike_map.keys())
        atm_strike: Decimal | None = None
        if resolved_spot is not None and sorted_strikes:
            atm_strike = min(sorted_strikes, key=lambda s: (abs(s - resolved_spot), s))

        # 7. Construct Final Chain Rows
        chain_rows: list[HistoricalOptionChainRow] = []
        paired_count = 0
        ce_only_count = 0
        pe_only_count = 0

        for strike in sorted_strikes:
            ce_side, pe_side = strike_map[strike]
            is_paired = ce_side is not None and pe_side is not None
            is_atm = strike == atm_strike

            if is_paired:
                paired_count += 1
            elif ce_side is not None:
                ce_only_count += 1
            elif pe_side is not None:
                pe_only_count += 1

            chain_rows.append(
                HistoricalOptionChainRow(
                    strike_price=strike,
                    call=ce_side,
                    put=pe_side,
                    is_paired=is_paired,
                    is_atm=is_atm,
                )
            )

        completeness = HistoricalOptionChainCompleteness(
            total_strikes=len(sorted_strikes),
            paired_strikes=paired_count,
            ce_only_strikes=ce_only_count,
            pe_only_strikes=pe_only_count,
            ce_row_count=ce_count,
            pe_row_count=pe_count,
            min_strike=sorted_strikes[0] if sorted_strikes else None,
            max_strike=sorted_strikes[-1] if sorted_strikes else None,
        )

        return HistoricalOptionChain(
            exchange=EXCHANGE_NSE,
            underlying_type=u_type,
            underlying_id=u_id,
            underlying_symbol=u_symbol,
            underlying_name=u_name,
            trade_date=trade_date,
            expiry_date=expiry_date,
            calendar_days_to_expiry=(expiry_date - trade_date).days,
            source_underlying_value=source_underlying_val,
            independent_spot=independent_spot,
            resolved_spot=resolved_spot,
            atm_reference_strike=atm_strike,
            rows=chain_rows,
            completeness=completeness,
            quality_warnings=quality_warnings,
            as_of_date=as_of,
        )

    async def get_available_expiries(
        self,
        symbol: str,
        trade_date: date,
        as_of_date: date | None = None,
    ) -> list[date]:
        """Return available expiries with actual option price observations on trade_date."""
        as_of = as_of_date or trade_date

        underlying_info = await self._resolve_underlying(symbol)
        if not underlying_info:
            return []

        u_type, u_id, _, _ = underlying_info

        stmt = (
            select(DerivativeContract.expiry_date)
            .join(DerivativeDailyPrice, DerivativeDailyPrice.contract_id == DerivativeContract.id)
            .where(DerivativeContract.instrument_type == "OPTION")
            .where(DerivativeDailyPrice.trade_date == trade_date)
            .where(DerivativeDailyPrice.available_at <= as_of)
            .where(DerivativeContract.available_at <= as_of)
        )

        if u_type == "INDEX":
            stmt = stmt.where(DerivativeContract.underlying_index_id == u_id)
        else:
            stmt = stmt.where(DerivativeContract.underlying_stock_id == u_id)

        stmt = stmt.distinct().order_by(DerivativeContract.expiry_date.asc())

        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_available_chain_dates(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        as_of_date: date | None = None,
    ) -> list[date]:
        """Return distinct trade dates in [start_date, end_date] with option price data."""
        as_of = as_of_date or end_date

        underlying_info = await self._resolve_underlying(symbol)
        if not underlying_info:
            return []

        u_type, u_id, _, _ = underlying_info

        stmt = (
            select(DerivativeDailyPrice.trade_date)
            .join(DerivativeContract, DerivativeDailyPrice.contract_id == DerivativeContract.id)
            .where(DerivativeContract.instrument_type == "OPTION")
            .where(DerivativeDailyPrice.trade_date >= start_date)
            .where(DerivativeDailyPrice.trade_date <= end_date)
            .where(DerivativeDailyPrice.available_at <= as_of)
            .where(DerivativeContract.available_at <= as_of)
        )

        if u_type == "INDEX":
            stmt = stmt.where(DerivativeContract.underlying_index_id == u_id)
        else:
            stmt = stmt.where(DerivativeContract.underlying_stock_id == u_id)

        stmt = stmt.distinct().order_by(DerivativeDailyPrice.trade_date.asc())

        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def _resolve_underlying(self, symbol: str) -> tuple[str, int, str, str] | None:
        """Resolve symbol to (type, id, canonical_symbol, name)."""
        sym = symbol.strip().upper()

        # Check MarketIndex first
        idx_res = await self.session.execute(
            select(MarketIndex).where(MarketIndex.code == sym)
        )
        idx = idx_res.scalar_one_or_none()
        if idx:
            return ("INDEX", idx.id, idx.code, idx.name)

        # Index alias mapping
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

        # Check Stock
        stk_res = await self.session.execute(
            select(Stock).where(Stock.symbol == sym)
        )
        stk = stk_res.scalar_one_or_none()
        if stk:
            return ("STOCK", stk.id, stk.symbol, stk.company_name)

        # Check SecurityAlias
        alias_res = await self.session.execute(
            select(SecurityAlias, Stock)
            .join(Stock, SecurityAlias.stock_id == Stock.id)
            .where(SecurityAlias.symbol == sym)
            .where(SecurityAlias.exchange == EXCHANGE_NSE)
        )
        row = alias_res.first()
        if row:
            alias, s = row
            return ("STOCK", s.id, s.symbol, s.company_name)

        return None

    async def _fetch_independent_spot(
        self,
        u_type: str,
        u_id: int,
        trade_date: date,
        as_of_date: date,
    ) -> Decimal | None:
        """Fetch spot price reference from MarketIndexPrice or DailyPrice."""
        if u_type == "INDEX":
            stmt = (
                select(MarketIndexPrice.close)
                .where(MarketIndexPrice.market_index_id == u_id)
                .where(MarketIndexPrice.trade_date == trade_date)
            )
            res = await self.session.execute(stmt)
            val = res.scalar_one_or_none()
            if val is None:
                # Check canonical index aliases (e.g. NIFTY <-> NIFTY_50)
                alias_code_map = {
                    "NIFTY": "NIFTY_50",
                    "NIFTY_50": "NIFTY",
                    "BANKNIFTY": "NIFTY_BANK",
                    "FINNIFTY": "NIFTY_FIN_SERV",
                    "MIDCPNIFTY": "NIFTY_MID_SELECT",
                }
                idx_res = await self.session.execute(
                    select(MarketIndex.code).where(MarketIndex.id == u_id)
                )
                curr_code = idx_res.scalar_one_or_none()
                target_code = alias_code_map.get(curr_code) if curr_code else None
                if target_code:
                    stmt_alias = (
                        select(MarketIndexPrice.close)
                        .join(MarketIndex, MarketIndex.id == MarketIndexPrice.market_index_id)
                        .where(MarketIndex.code == target_code)
                        .where(MarketIndexPrice.trade_date == trade_date)
                    )
                    res_alias = await self.session.execute(stmt_alias)
                    val = res_alias.scalar_one_or_none()
            return Decimal(str(val)) if val is not None else None
        else:
            stmt = (
                select(DailyPrice.close)
                .where(DailyPrice.stock_id == u_id)
                .where(DailyPrice.trade_date == trade_date)
            )
            res = await self.session.execute(stmt)
            val = res.scalar_one_or_none()
            return Decimal(str(val)) if val is not None else None
