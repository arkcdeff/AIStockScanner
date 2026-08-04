import json
import re

from pydantic import ValidationError

from app.schemas.ai import (
    AICollectionPayload,
    AIExplanationPayload,
    AIMarketPayload,
)

_MAX_TEXT_LENGTH = 2000
_MAX_LIST_ITEMS = 8
_MAX_ITEM_LENGTH = 300


class AIResponseError(RuntimeError):
    """Raised when the AI output cannot be safely parsed."""


def _extract_json_object(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise AIResponseError("No JSON object found in AI response")
    return cleaned[start : end + 1]


def _clean_text(value: str) -> str:
    value = str(value).strip()
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value)
    return value[:_MAX_TEXT_LENGTH]


def _clean_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    for item in value:
        text = _clean_text(str(item))
        if text:
            cleaned.append(text[:_MAX_ITEM_LENGTH])
        if len(cleaned) >= _MAX_LIST_ITEMS:
            break
    return cleaned


def _clean_confidence(value: object, default: int = 0) -> int:
    try:
        confidence = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(100, confidence))


def _sanitize_payload(data: dict, model_type: type) -> object:
    raw = dict(data)
    if "summary" in raw:
        raw["summary"] = _clean_text(raw["summary"])
    for key in ("medium_summary", "detailed_summary", "scanner_explanation",
                "trend_explanation", "risk_explanation", "explanation"):
        if key in raw:
            raw[key] = _clean_text(raw[key])
    for key in ("strengths", "risks", "support", "resistance", "opportunities"):
        if key in raw:
            raw[key] = _clean_list(raw.get(key))
    if "confidence" in raw:
        raw["confidence"] = _clean_confidence(raw.get("confidence"), default=0)
    try:
        return model_type.model_validate(raw)
    except ValidationError as exc:
        raise AIResponseError(f"AI response failed validation: {exc}") from exc


def parse_stock_explanation(text: str) -> AIExplanationPayload:
    data = json.loads(_extract_json_object(text))
    if not isinstance(data, dict):
        raise AIResponseError("AI response is not a JSON object")
    payload = _sanitize_payload(data, AIExplanationPayload)
    if not isinstance(payload, AIExplanationPayload):
        raise AIResponseError("Unexpected payload type")
    if not payload.summary:
        raise AIResponseError("AI response is missing a summary")
    return payload


def parse_collection_explanation(text: str) -> AICollectionPayload:
    data = json.loads(_extract_json_object(text))
    if not isinstance(data, dict):
        raise AIResponseError("AI response is not a JSON object")
    payload = _sanitize_payload(data, AICollectionPayload)
    if not isinstance(payload, AICollectionPayload):
        raise AIResponseError("Unexpected payload type")
    if not payload.summary:
        raise AIResponseError("AI response is missing a summary")
    return payload


def parse_market_explanation(text: str) -> AIMarketPayload:
    data = json.loads(_extract_json_object(text))
    if not isinstance(data, dict):
        raise AIResponseError("AI response is not a JSON object")
    payload = _sanitize_payload(data, AIMarketPayload)
    if not isinstance(payload, AIMarketPayload):
        raise AIResponseError("Unexpected payload type")
    if not payload.summary:
        raise AIResponseError("AI response is missing a summary")
    return payload
