"""NSE Official Historical Market Data Provider — Sprint 19.1G-QA-MP.

Adapter wrapping official NSE EOD data stored in PostgreSQL database. Preserves
existing bulk ingestion services and DB repository layers.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.daily_price import DailyPrice
from app.models.derivative_contract import DerivativeContract
from app.models.derivative_daily_price import DerivativeDailyPrice
from app.models.stock import Stock
from app.providers.base import HistoricalMarketDataProvider
from app.providers.models import (
    NormalizedBar,
    NormalizedOptionChain,
    NormalizedQuote,
    PriceType,
    ProviderCapability,
    ProviderMode,
)


class NSEHistoricalProvider(HistoricalMarketDataProvider):
    """Adapter provider for stored official NSE historical data."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @property
    def provider_id(self) -> str:
        return "NSE_HISTORICAL"

    @property
    def provider_name(self) -> str:
        return "NSE Official Archive & PostgreSQL Historical Store"

    @property
    def provider_mode(self) -> ProviderMode:
        return ProviderMode.HISTORICAL_ONLY

    @property
    def capabilities(self) -> set[ProviderCapability]:
        return {
            ProviderCapability.CASH_EQUITY,
            ProviderCapability.INDEX,
            ProviderCapability.FUTURES,
            ProviderCapability.OPTIONS,
            ProviderCapability.PARTICIPANT_DATA,
            ProviderCapability.FII_STATS,
        }

    async def is_available(self) -> bool:
        """Check if database session is active and contains stored records."""
        try:
            res = await self.session.execute(select(func.count(Stock.id)))
            cnt = res.scalar() or 0
            return cnt > 0
        except Exception:
            return False

    async def fetch_historical_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        interval: str = "1d",
    ) -> list[NormalizedBar]:
        """Fetch historical bars from DailyPrice table."""
        sym_clean = symbol.strip().upper()
        stmt = (
            select(DailyPrice)
            .join(Stock)
            .where(
                Stock.symbol == sym_clean,
                DailyPrice.trade_date >= start_date,
                DailyPrice.trade_date <= end_date,
            )
            .order_by(DailyPrice.trade_date.asc())
        )
        results = (await self.session.execute(stmt)).scalars().all()

        bars: list[NormalizedBar] = []
        for dp in results:
            bars.append(
                NormalizedBar(
                    symbol=sym_clean,
                    trade_date=dp.trade_date,
                    open=dp.open,
                    high=dp.high,
                    low=dp.low,
                    close=dp.close,
                    volume=dp.volume,
                    turnover=dp.turnover,
                    delivery_quantity=dp.delivery_quantity,
                    delivery_percentage=dp.delivery_percentage,
                    price_type=PriceType.NSE_CLOSE,
                    source_name="NSE_OFFICIAL_ARCHIVE",
                    provider_id=self.provider_id,
                    timestamp=datetime.combine(dp.trade_date, datetime.min.time()),
                )
            )
        return bars

    async def fetch_option_chain_history(
        self,
        underlying_symbol: str,
        trade_date: date,
        expiry_date: date | None = None,
    ) -> NormalizedOptionChain:
        """Fetch historical option chain snapshot for an underlying symbol on a given trade date."""
        u_clean = underlying_symbol.strip().upper()
        stmt = (
            select(DerivativeDailyPrice, DerivativeContract)
            .join(DerivativeContract, DerivativeDailyPrice.contract_id == DerivativeContract.id)
            .where(
                DerivativeContract.instrument_type == "OPTION",
                DerivativeDailyPrice.trade_date == trade_date,
            )
        )
        if expiry_date:
            stmt = stmt.where(DerivativeContract.expiry_date == expiry_date)

        rows = (await self.session.execute(stmt)).all()

        calls: list[NormalizedQuote] = []
        puts: list[NormalizedQuote] = []
        expiries_set: set[date] = set()

        for ddp, contract in rows:
            expiries_set.add(contract.expiry_date)
            quote = NormalizedQuote(
                symbol=contract.trading_symbol,
                instrument_type="OPTION",
                ltp=ddp.close or Decimal("0"),
                open=ddp.open,
                high=ddp.high,
                low=ddp.low,
                close=ddp.close,
                settlement_price=ddp.settlement_price,
                volume=ddp.volume_contracts,
                open_interest=ddp.open_interest,
                change_in_open_interest=ddp.change_in_open_interest,
                strike_price=contract.strike_price,
                option_type=contract.option_type,
                expiry_date=contract.expiry_date,
                timestamp=datetime.combine(ddp.trade_date, datetime.min.time()),
                price_type=PriceType.NSE_SETTLEMENT,
                source_name="NSE_OFFICIAL_ARCHIVE",
                provider_id=self.provider_id,
            )
            if contract.option_type == "CE":
                calls.append(quote)
            elif contract.option_type == "PE":
                puts.append(quote)

        # Calculate pairing percentage
        ce_strikes = {q.strike_price for q in calls if q.strike_price is not None}
        pe_strikes = {q.strike_price for q in puts if q.strike_price is not None}
        common = ce_strikes.intersection(pe_strikes)
        union = ce_strikes.union(pe_strikes)
        paired_pct = round((len(common) / len(union) * 100.0), 1) if union else 0.0

        return NormalizedOptionChain(
            underlying_symbol=u_clean,
            as_of_timestamp=datetime.combine(trade_date, datetime.min.time()),
            spot_price=None,
            expiries=sorted(list(expiries_set)),
            calls=calls,
            puts=puts,
            ce_pe_paired_pct=paired_pct,
            provider_id=self.provider_id,
            source_name="NSE_OFFICIAL_ARCHIVE",
        )
