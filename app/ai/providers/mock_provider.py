import re

from app.ai.base import BaseAIProvider

_SYMBOL_RE = re.compile(r"^Symbol:\s*([A-Z0-9.\-]+)", re.IGNORECASE | re.MULTILINE)
_HEADER_SYMBOL_RE = re.compile(r"^##\s*([A-Z0-9.\-]+)", re.IGNORECASE | re.MULTILINE)
_OVERALL_RE = re.compile(r"Overall Score:\s*(\d{1,3})", re.IGNORECASE | re.MULTILINE)
_TRADE_DATE_RE = re.compile(r"Trade Date:\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE | re.MULTILINE)


class MockAIProvider(BaseAIProvider):
    """Deterministic provider for development and tests. Never calls a network."""

    @property
    def name(self) -> str:
        return "mock"

    def model_name(self) -> str:
        return "mock-analyst"

    async def generate(self, prompt: str, *, timeout_seconds: int) -> str:
        symbol = "UNKNOWN"
        match = _SYMBOL_RE.search(prompt) or _HEADER_SYMBOL_RE.search(prompt)
        if match:
            symbol = match.group(1)

        trade_date = "2026-01-01"
        match = _TRADE_DATE_RE.search(prompt)
        if match:
            trade_date = match.group(1)

        overall = 75
        match = _OVERALL_RE.search(prompt)
        if match:
            overall = max(0, min(100, int(match.group(1))))

        confidence = max(50, min(99, 50 + overall // 4))
        return (
            "{\n"
            f'  "summary": "{symbol} is in a {confidence}th-percentile setup on {trade_date}.",\n'
            f'  "medium_summary": "{symbol} scores {overall}/100 overall.",\n'
            f'  "detailed_summary": "Mock explanation for {symbol} based on the supplied '
            'analytics only.",\n'
            f'  "strengths": ["Momentum above baseline", "Positive overall rating"],\n'
            f'  "risks": ["Sector rotation", "Elevated volatility"],\n'
            f'  "support": ["Near recent pivot low"],\n'
            f'  "resistance": ["Near recent swing high"],\n'
            f'  "scanner_explanation": "Matched scanners are consistent with the supplied '
            'signals.",\n'
            f'  "trend_explanation": "Trend appears constructive on supplied indicators.",\n'
            f'  "risk_explanation": "Risk is moderate for {symbol}.",\n'
            f'  "confidence": {confidence}\n'
            "}"
        )

    async def health(self) -> bool:
        return True
