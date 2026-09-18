"""Interpretation Narrative Service — G.11-D-3 optional AI narrative layer.

Reuses the existing AI provider abstraction (``app.ai.base.BaseAIProvider``).

Hard boundaries:

* The deterministic D-3 result is authoritative. The LLM may generate ONLY
  ``narrative.text`` — never overall_state, directional_bias, evidence
  classification, convergence, lifecycle, missing evidence or PIT status.
* The LLM receives only structured deterministic facts (no DB access).
* Every narrative is validated against safety rules (no trading terms,
  predictions, intent attribution, unsupported certainty) and against
  invented numbers. Any failure ⇒ ``None`` ⇒ deterministic fallback.
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.ai.base import BaseAIProvider
from app.ai.provider import get_provider
from app.core.config import settings

# ---------------------------------------------------------------------------
# Safety validation patterns (case-insensitive)
# ---------------------------------------------------------------------------

_FORBIDDEN_TRADING_TERMS = re.compile(
    r"\b(buy|sell|entry|exit|target|stop\s*-?\s*loss|book\s+profit|book\s+loss)\b",
    re.IGNORECASE,
)
_FORBIDDEN_PREDICTIONS = re.compile(
    r"\b(will\s+(rise|fall|gain|drop|increase|decline|go\s+up|go\s+down|move)"
    r"|expected\s+to|likely\s+to|probability\s+of|forecast|predict|anticipat\w*"
    r"|poised\s+to|set\s+to|should\s+(rise|fall|rebound|recover))\b",
    re.IGNORECASE,
)
_FORBIDDEN_INTENT = re.compile(
    r"\b(insider|manipulation|intended|planned|betting|speculat\w*|rigging)\b",
    re.IGNORECASE,
)
_FORBIDDEN_CERTAINTY = re.compile(
    r"\b(guaranteed|certain(ly)?|definitely|100%|no\s+doubt|assured|sure\s+thing)\b",
    re.IGNORECASE,
)
_NUMBER_PATTERN = re.compile(r"-?\d+(?:\.\d+)?")


class InterpretationNarrativeService:
    """Generate and validate an optional AI narrative for the D-3 interpretation."""

    def __init__(self, provider: BaseAIProvider | None = None) -> None:
        self.provider = provider if provider is not None else get_provider()

    # -------------------------------------------------------------------
    # Prompt
    # -------------------------------------------------------------------

    @staticmethod
    def build_prompt(facts: dict[str, Any]) -> str:
        """Deterministic prompt built ONLY from structured facts."""
        payload = json.dumps(facts, ensure_ascii=False, sort_keys=True)
        return (
            "You are writing a short factual narrative about deterministic "
            "stock-evidence analysis.\n\n"
            "STRICT RULES:\n"
            "1. Use ONLY the facts in the provided JSON. Never invent numbers, "
            "dates, prices, percentages, or events.\n"
            "2. Do NOT give trading advice. Never write BUY, SELL, ENTRY, EXIT, "
            "TARGET, or STOP LOSS.\n"
            "3. Do NOT predict the future. No 'will rise', 'expected to', "
            "'likely to', or probability statements.\n"
            "4. Do NOT attribute intent. No insider, manipulation, or planned-"
            "activity claims.\n"
            "5. Do NOT claim causality between context events (corporate "
            "announcements, institutional flows, deals) and price movement.\n"
            "6. Do NOT express certainty beyond the evidence ('guaranteed', "
            "'certain', 'definitely', '100%').\n"
            "7. Describe only what the evidence shows, including missing data "
            "and conflicts.\n\n"
            "Return JSON exactly like: {\"narrative\": \"<2-5 sentence narrative>\"}\n\n"
            f"FACTS:\n{payload}"
        )

    # -------------------------------------------------------------------
    # Validation
    # -------------------------------------------------------------------

    @staticmethod
    def extract_allowed_numbers(facts: dict[str, Any]) -> set[float]:
        """Extract legitimate market-fact numbers from structured facts dictionary.

        Builds the whitelist ONLY from legitimate numeric market facts:
        - metadata values (prices, percentages, volume, OI, counts)
        - convergence counts and ratios
        - overall lifecycle counts
        - numeric values inside factual reason strings (with dates stripped)

        Explicitly excludes:
        - evaluation_date, comparison_date, and any calendar date components
        - timestamps, symbols, and status strings
        """
        allowed: set[float] = set()

        def add_val(v: Any) -> None:
            if isinstance(v, bool):
                return
            if isinstance(v, (int, float)):
                f = float(v)
                allowed.add(f)
                allowed.add(abs(f))
            elif isinstance(v, str):
                # Strip calendar dates like YYYY-MM-DD or YYYY/MM/DD before extracting numbers
                cleaned = re.sub(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b", " ", v)
                for m in _NUMBER_PATTERN.findall(cleaned):
                    try:
                        f = float(m)
                        allowed.add(f)
                        allowed.add(abs(f))
                    except ValueError:
                        pass

        # 1. Direct metadata dict (if present in facts)
        direct_meta = facts.get("metadata")
        if isinstance(direct_meta, dict):
            for v in direct_meta.values():
                add_val(v)

        # 2. Convergence counts and ratios
        conv = facts.get("convergence")
        if isinstance(conv, dict):
            add_val(conv.get("directional_value"))
            add_val(conv.get("directional_max_possible"))
            ratio = conv.get("convergence_ratio")
            if ratio is not None and not isinstance(ratio, bool):
                add_val(ratio)
                if isinstance(ratio, (int, float)):
                    add_val(round(float(ratio) * 100, 2))

        # 3. Overall lifecycle counts
        lc = facts.get("overall_lifecycle")
        if isinstance(lc, dict):
            for k in (
                "pattern_count",
                "new_count",
                "continuing_count",
                "strengthening_count",
                "weakening_count",
                "resolved_count",
            ):
                add_val(lc.get(k))

        # 4. Evidence buckets: metadata and reason strings
        for bucket in (
            "primary_evidence",
            "supporting_evidence",
            "conflicting_evidence",
            "context_evidence",
        ):
            items = facts.get(bucket, [])
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    meta = item.get("metadata")
                    if isinstance(meta, dict):
                        for v in meta.values():
                            add_val(v)
                    reason = item.get("reason")
                    if isinstance(reason, str):
                        add_val(reason)

        return allowed

    @staticmethod
    def validate_narrative(
        narrative: str, facts: dict[str, Any]
    ) -> tuple[bool, list[str]]:
        """Validate an LLM narrative against deterministic facts.

        Returns ``(valid, errors)``. Ambiguous or unsafe narratives are
        rejected (fail-closed) rather than allowed.
        """
        errors: list[str] = []
        text = narrative.strip()
        if not text:
            return False, ["empty narrative"]

        if _FORBIDDEN_TRADING_TERMS.search(text):
            errors.append("forbidden trading term")
        if _FORBIDDEN_PREDICTIONS.search(text):
            errors.append("forbidden prediction language")
        if _FORBIDDEN_INTENT.search(text):
            errors.append("forbidden intent attribution")
        if _FORBIDDEN_CERTAINTY.search(text):
            errors.append("unsupported certainty")

        # Strip calendar dates (YYYY-MM-DD or YYYY/MM/DD) from text so that legitimate
        # references to evaluation/comparison dates are not parsed as invented standalone numbers.
        text_without_dates = re.sub(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b", " ", text)

        allowed = InterpretationNarrativeService.extract_allowed_numbers(facts)
        for match in _NUMBER_PATTERN.findall(text_without_dates):
            value = float(match)
            if not any(abs(value - a) < 1e-9 for a in allowed):
                errors.append(f"invented number: {match}")
                break

        return (len(errors) == 0), errors

    # -------------------------------------------------------------------
    # Generation
    # -------------------------------------------------------------------

    async def generate(self, facts: dict[str, Any]) -> dict[str, Any] | None:
        """Generate a validated narrative. Returns ``None`` on any failure.

        The caller (deterministic D-3 service) always keeps its fallback
        narrative, so the application remains functional without AI.
        """
        prompt = self.build_prompt(facts)
        raw = await self.provider.generate(prompt, timeout_seconds=settings.ai_timeout)

        text = self._extract_narrative_text(raw)
        if text is None:
            return None

        valid, errors = self.validate_narrative(text, facts)
        return {
            "text": text,
            "source": "ai",
            "validated": valid,
            "validation_errors": errors,
            "provider": self.provider.name,
            "model": self.provider.model_name(),
        }

    @staticmethod
    def _extract_narrative_text(raw: str) -> str | None:
        """Extract the narrative text from a provider response.

        Accepts JSON {"narrative": "..."} or plain text. Malformed JSON falls
        back to the raw text, which is then still safety-validated.
        """
        if not raw or not raw.strip():
            return None
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw.strip()
        if isinstance(parsed, dict):
            narrative = parsed.get("narrative")
            if isinstance(narrative, str) and narrative.strip():
                return narrative.strip()
            return None
        return None
