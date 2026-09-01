"""Pydantic and dataclass schemas for Fundamental + Corporate Intelligence — Sprint 19.1E."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Pydantic Schemas for REST API
# ---------------------------------------------------------------------------


class FinancialStatementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    stock_id: int
    period_type: str
    statement_basis: str
    period_end: date
    reported_at: date | None = None
    available_at: date
    revision_type: str
    currency: str = "INR"
    unit: str = "INR_CRORE"

    revenue: Decimal | None = None
    operating_income: Decimal | None = None
    ebit: Decimal | None = None
    pbt: Decimal | None = None
    pat: Decimal | None = None
    eps: Decimal | None = None

    total_assets: Decimal | None = None
    total_liabilities: Decimal | None = None
    total_equity: Decimal | None = None
    total_debt: Decimal | None = None
    cash_and_equivalents: Decimal | None = None

    operating_cash_flow: Decimal | None = None
    investing_cash_flow: Decimal | None = None
    financing_cash_flow: Decimal | None = None
    free_cash_flow: Decimal | None = None

    shares_outstanding: Decimal | None = None
    source_name: str
    idempotency_hash: str


class CorporateAnnouncementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    stock_id: int
    category: str
    announcement_date: date
    available_at: date
    title: str
    summary: str | None = None
    corporate_action_id: int | None = None
    source_name: str


class ShareholdingPatternRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    stock_id: int
    period_end: date
    reported_at: date | None = None
    available_at: date
    promoter_holding_pct: Decimal
    fii_holding_pct: Decimal
    dii_holding_pct: Decimal
    public_holding_pct: Decimal
    promoter_pledged_pct: Decimal | None = None
    promoter_holding_change: Decimal | None = None
    source_name: str


# ---------------------------------------------------------------------------
# Dataclasses for Point-in-Time Analytics Snapshots
# ---------------------------------------------------------------------------


@dataclass
class FinancialGrowthMetrics:
    revenue_growth_yoy: Decimal | None = None
    revenue_growth_qoq: Decimal | None = None
    profit_growth_yoy: Decimal | None = None
    profit_growth_qoq: Decimal | None = None
    eps_growth_yoy: Decimal | None = None


@dataclass
class FinancialMarginMetrics:
    operating_margin_pct: Decimal | None = None
    net_profit_margin_pct: Decimal | None = None
    roe_pct: Decimal | None = None
    roce_pct: Decimal | None = None


@dataclass
class SolvencyAndCashFlowMetrics:
    debt_to_equity: Decimal | None = None
    interest_coverage: Decimal | None = None
    operating_cash_flow_to_pat: Decimal | None = None
    free_cash_flow: Decimal | None = None


@dataclass
class PITValuationMetrics:
    evaluation_date: date
    close_price: Decimal | None = None
    pe_ratio: Decimal | None = None
    pb_ratio: Decimal | None = None
    ps_ratio: Decimal | None = None
    dividend_yield_pct: Decimal | None = None


@dataclass
class QualityScoreInputs:
    fundamental_quality_score: Decimal
    growth_quality_score: Decimal
    cashflow_quality_score: Decimal
    balance_sheet_quality_score: Decimal
    corporate_risk_flags: list[str]


@dataclass
class RelativeValuationMetrics:
    peer_group_type: str  # e.g., 'INDUSTRY', 'SECTOR', 'COMPARABLE_COMPANY'
    peer_count: int
    median_pe: Decimal | None = None
    median_pb: Decimal | None = None
    median_ps: Decimal | None = None
    pe_premium_discount_pct: Decimal | None = None
    pb_premium_discount_pct: Decimal | None = None
    ps_premium_discount_pct: Decimal | None = None
    valuation_classification: str | None = None  # e.g., 'EXPENSIVE', 'FAIRLY_VALUED', 'ATTRACTIVE'


@dataclass
class CompanyFundamentalSnapshot:
    symbol: str
    stock_id: int
    evaluation_date: date
    as_of_date: date
    statement_basis: str
    latest_quarterly: FinancialStatementRead | None = None
    latest_annual: FinancialStatementRead | None = None
    growth: FinancialGrowthMetrics | None = None
    margins: FinancialMarginMetrics | None = None
    solvency_cashflow: SolvencyAndCashFlowMetrics | None = None
    valuation: PITValuationMetrics | None = None
    relative_valuation: RelativeValuationMetrics | None = None
    quality_inputs: QualityScoreInputs | None = None
    latest_shareholding: ShareholdingPatternRead | None = None
    recent_corporate_actions: list[dict[str, Any]] = Field(default_factory=list)
    recent_announcements: list[CorporateAnnouncementRead] = Field(default_factory=list)
    quality_warnings: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)


class FundamentalsCoverageRead(BaseModel):
    as_of_date: date
    total_stocks_count: int
    stocks_with_financials_count: int
    stocks_with_announcements_count: int
    stocks_with_shareholding_count: int
    first_financial_period: date | None = None
    latest_financial_period: date | None = None
    total_statements_count: int
    total_announcements_count: int
    total_shareholding_records_count: int
    quality_issues_count: int
    coverage_status: str
