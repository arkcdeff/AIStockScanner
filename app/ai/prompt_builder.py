import hashlib
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.schemas.ai import (
    AIAnalyticsContext,
    AIIndicatorContext,
    AIPipelineContext,
    AIScannerMatchContext,
    AIStockContext,
)

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

_TEMPLATE_NAMES = {
    "stock_summary": "stock_summary.md",
    "scanner_summary": "scanner_summary.md",
    "market_summary": "market_summary.md",
    "watchlist_summary": "watchlist_summary.md",
}


@lru_cache
def _env() -> Environment:
    return Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)), autoescape=False)


def hash_prompt(prompt: str) -> str:
    """Deterministic hash so cache invalidates when any input value changes."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def build_stock_summary_prompt(
    stock: AIStockContext,
    analytics: AIAnalyticsContext,
    indicator: AIIndicatorContext | None,
    matches: list[AIScannerMatchContext],
    pipeline: AIPipelineContext,
) -> str:
    template = _env().get_template(_TEMPLATE_NAMES["stock_summary"])
    return template.render(
        stock=stock,
        analytics=analytics,
        indicator=indicator,
        matches=matches or [],
        pipeline=pipeline,
    )


def build_scanner_summary_prompt(
    stocks: list[AIStockContext],
    pipeline: AIPipelineContext,
) -> str:
    template = _env().get_template(_TEMPLATE_NAMES["scanner_summary"])
    return template.render(stocks=stocks, pipeline=pipeline)


def build_market_summary_prompt(
    stocks: list[AIStockContext],
    pipeline: AIPipelineContext,
    stock_count: int,
) -> str:
    template = _env().get_template(_TEMPLATE_NAMES["market_summary"])
    return template.render(stocks=stocks, pipeline=pipeline, stock_count=stock_count)


def build_watchlist_summary_prompt(
    stocks: list[AIStockContext],
) -> str:
    template = _env().get_template(_TEMPLATE_NAMES["watchlist_summary"])
    return template.render(stocks=stocks)


def _validate_templates() -> None:
    for name in _TEMPLATE_NAMES.values():
        _env().get_template(name)


_validate_templates()


def templates_dir() -> Path:
    return _TEMPLATES_DIR
