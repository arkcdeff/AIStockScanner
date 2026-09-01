from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict


class StockOverviewResponse(BaseModel):
    symbol: str
    company_name: str
    exchange: str
    series: str | None
    isin: str | None
    sector: str | None
    industry: str | None
    latest_trade_date: date | None
    latest_close: float | None
    previous_close: float | None
    price_change: float | None
    price_change_pct: float | None
    open: float | None
    high: float | None
    low: float | None
    volume: int | None
    avg_volume_20d: float | None
    volume_multiple: float | None
    delivery_quantity: int | None
    delivery_percentage: float | None
    avg_delivery_20d: float | None
    history_sessions: int = 0
    history_first_date: date | None = None

    model_config = ConfigDict(from_attributes=True)


class IndicatorCardResponse(BaseModel):
    trade_date: date | None
    ema_20: float | None
    ema_50: float | None
    ema_100: float | None
    ema_200: float | None
    sma: float | None
    rsi_14: float | None
    macd: float | None
    macd_signal: float | None
    macd_hist: float | None
    atr: float | None
    adx: float | None
    supertrend: float | None
    supertrend_direction: int | None
    vwap: float | None
    obv: float | None
    bollinger_upper: float | None
    bollinger_middle: float | None
    bollinger_lower: float | None
    stochastic_k: float | None
    stochastic_d: float | None

    model_config = ConfigDict(from_attributes=True)


class ScannerMatchResponse(BaseModel):
    id: int
    scan_date: date
    matched: bool
    score: float | None
    metrics: dict
    scanner_name: str | None
    category: str | None
    reason: str | None

    model_config = ConfigDict(from_attributes=True)


class PerformanceReturns(BaseModel):
    """Period returns for Stock360 performance card."""

    return_1w_pct: float | None = None
    return_1m_pct: float | None = None
    return_3m_pct: float | None = None

    model_config = ConfigDict(from_attributes=True)


class DataHealthSummary(BaseModel):
    """Data coverage and freshness for Stock360 health card."""

    history_sessions: int = 0
    history_first_date: date | None = None
    history_latest_date: date | None = None
    indicator_latest_date: date | None = None
    indicator_available: bool = False
    analytics_available: bool = False
    scanner_matches_count: int = 0
    scanner_readiness_note: str | None = None

    model_config = ConfigDict(from_attributes=True)


class TechnicalSnapshotResponse(BaseModel):
    """Compact technical summary for Stock360 header area."""

    rsi_14: float | None = None
    rsi_label: str | None = None
    trend_state: str | None = None
    overall_score: float | None = None
    supertrend_direction: str | None = None
    adx: float | None = None
    adx_label: str | None = None

    model_config = ConfigDict(from_attributes=True)


class ScannerSnapshotResponse(BaseModel):
    """Compact scanner summary for Stock360."""

    total_matches: int = 0
    latest_date: date | None = None
    bullish_count: int = 0
    bearish_count: int = 0
    recent_scanners: list[str] = []

    model_config = ConfigDict(from_attributes=True)


class ChartDataResponse(BaseModel):
    symbol: str
    candles: list[dict[str, Any]]
    volume: list[dict[str, Any]]
    ema20: list[dict[str, Any]]
    ema50: list[dict[str, Any]]
    ema200: list[dict[str, Any]]
    scanner_markers: list[dict[str, Any]]


class FinancialStatementItem(BaseModel):
    period_end: date
    period_type: str
    statement_basis: str
    revenue: float | None = None
    operating_income: float | None = None
    ebit: float | None = None
    pbt: float | None = None
    pat: float | None = None
    eps: float | None = None
    total_equity: float | None = None
    total_debt: float | None = None
    operating_cash_flow: float | None = None
    free_cash_flow: float | None = None
    available_at: date | None = None
    reported_at: date | None = None

    model_config = ConfigDict(from_attributes=True)


class FundamentalsOverviewResponse(BaseModel):
    available: bool = False
    symbol: str
    statement_basis: str = "CONSOLIDATED"
    latest_period_end: date | None = None
    latest_period_type: str | None = None
    reported_at: date | None = None
    available_at: date | None = None

    # Key Earnings (INR Crore)
    revenue: float | None = None
    operating_income: float | None = None
    pbt: float | None = None
    pat: float | None = None
    eps: float | None = None

    # Growth YoY / QoQ (%)
    revenue_growth_yoy: float | None = None
    revenue_growth_qoq: float | None = None
    profit_growth_yoy: float | None = None
    profit_growth_qoq: float | None = None
    eps_growth_yoy: float | None = None

    # Profitability & Margins (%)
    operating_margin_pct: float | None = None
    net_profit_margin_pct: float | None = None
    roe_pct: float | None = None
    roce_pct: float | None = None
    roe_descriptor: str | None = None
    revenue_descriptor: str | None = None
    pat_descriptor: str | None = None

    # Financial Quality & Balance Sheet (INR Crore / Ratios)
    total_debt: float | None = None
    total_equity: float | None = None
    debt_to_equity: float | None = None
    cash_and_equivalents: float | None = None
    operating_cash_flow: float | None = None
    free_cash_flow: float | None = None
    operating_cash_flow_to_pat: float | None = None

    # Recent Results Trend Table
    recent_statements: list[FinancialStatementItem] = []
    quality_warnings: list[str] = []

    model_config = ConfigDict(from_attributes=True)


class ValuationOverviewResponse(BaseModel):
    available: bool = False
    symbol: str
    evaluation_date: date | None = None
    close_price: float | None = None
    financial_period_end: date | None = None
    statement_basis: str = "CONSOLIDATED"

    # Multiples
    pe_ratio: float | None = None
    pb_ratio: float | None = None
    ps_ratio: float | None = None
    book_value_per_share: float | None = None
    dividend_yield_pct: float | None = None
    ev_to_ebitda: float | None = None

    # Relative Valuation / Benchmark Context
    peer_group_type: str | None = None
    peer_count: int = 0
    industry_pe: float | None = None
    industry_pb: float | None = None
    industry_ps: float | None = None
    pe_premium_discount_pct: float | None = None
    pb_premium_discount_pct: float | None = None
    ps_premium_discount_pct: float | None = None
    valuation_classification: str | None = None
    industry_pe_note: str | None = None
    historical_band_note: str = "Historical valuation band unavailable"

    model_config = ConfigDict(from_attributes=True)
