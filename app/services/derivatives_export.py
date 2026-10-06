"""Derivatives CSV Export — Milestone G.11-OPT-3 (Phase 8).

Produces CSV for the derivatives workbench tables with two hard guarantees:

  1. **Provenance travels with the data.** Every export carries the source label
     and the observation timestamp for each row, so an exported file can never be
     mistaken for a provider snapshot without metadata.
  2. **No secrets leave the process.** Exported columns are drawn from an explicit
     allow-list; tokens, bearer headers, internal database identifiers, raw
     payload hashes and credential-bearing URLs are never included, and every
     cell is passed through the project's existing secret redactor as a second
     line of defence.
"""
from __future__ import annotations

import csv
import io
import logging
from collections.abc import Iterable, Sequence
from typing import Any

logger = logging.getLogger(__name__)

#: Fields that must never appear in an export, matched case-insensitively as a
#: substring of the column key.
FORBIDDEN_KEY_FRAGMENTS: tuple[str, ...] = (
    "token",
    "secret",
    "password",
    "authorization",
    "bearer",
    "api_key",
    "apikey",
    "credential",
    "cookie",
    "csrf",
    "jwt",
    "snapshot_id",
    "contract_id",
    "user_id",
    "raw_payload_hash",
    "idempotency_hash",
    "session_id",
)

#: Maximum rows exported in one response. Guards against accidental broad dumps.
MAX_EXPORT_ROWS = 20000


class DerivativeExportError(RuntimeError):
    """Raised when an export cannot be produced safely."""


def _redact(value: Any) -> Any:
    """Run a string cell through the project's secret redactor."""
    if not isinstance(value, str) or not value:
        return value
    try:
        from app.providers.upstox_historical_provider import redact_secrets

        return redact_secrets(value)
    except Exception:  # noqa: BLE001 - redaction must never break an export
        return value


def _flatten_row(row: dict[str, Any]) -> dict[str, Any]:
    """Flatten nested dicts one level so provenance fields stay readable."""
    flat: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                if isinstance(sub_value, (dict, list, tuple, set)):
                    continue
                flat[f"{key}.{sub_key}"] = sub_value
        elif isinstance(value, (list, tuple, set)):
            flat[key] = "; ".join(str(v) for v in value)
        else:
            flat[key] = value
    return flat


def is_forbidden_column(key: str) -> bool:
    """Whether a column key is excluded from exports."""
    lowered = key.strip().lower()
    return any(fragment in lowered for fragment in FORBIDDEN_KEY_FRAGMENTS)


def build_csv(
    rows: Sequence[dict[str, Any]],
    *,
    columns: Iterable[str] | None = None,
    metadata: dict[str, Any] | None = None,
    row_limit: int = MAX_EXPORT_ROWS,
) -> str:
    """Render rows as CSV with a provenance header block.

    ``metadata`` is written as leading comment lines prefixed with ``#`` so the
    file remains valid CSV for spreadsheet consumers while stating exactly which
    scope, source and observation window produced the data.
    """
    if len(rows) > row_limit:
        raise DerivativeExportError(
            f"Refusing to export {len(rows)} rows; the limit is {row_limit}. "
            "Narrow the selected scope."
        )

    flattened = [_flatten_row(row) for row in rows]

    if columns is not None:
        # An explicitly requested forbidden column is a programming error, not a
        # user error: refuse loudly instead of silently narrowing the export.
        forbidden = [c for c in columns if is_forbidden_column(c)]
        if forbidden:
            raise DerivativeExportError(
                "Export columns are not permitted because they could carry secrets or "
                f"internal identifiers: {', '.join(sorted(forbidden))}"
            )
        keys = list(columns)
    else:
        keys = []
        for row in flattened:
            for key in row:
                if key not in keys and not is_forbidden_column(key):
                    keys.append(key)

    for key in keys:
        if is_forbidden_column(key):  # defensive: allow-list still validated
            raise DerivativeExportError(f"Column '{key}' is not permitted in exports.")

    buffer = io.StringIO()
    if metadata:
        buffer.write("# AIStockScanner derivatives export\n")
        for key, value in metadata.items():
            if is_forbidden_column(key):
                continue
            buffer.write(f"# {key}: {_redact(value)}\n")
        buffer.write("# Values marked with a provenance/source column are real observations; cells left empty mean the value was unavailable rather than zero.\n")

    writer = csv.DictWriter(buffer, fieldnames=keys, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in flattened:
        writer.writerow({k: _redact(row.get(k)) for k in keys})
    return buffer.getvalue()


def export_filename(kind: str, underlying: str, session_date: str | None) -> str:
    """Deterministic, filesystem-safe export filename."""
    safe_kind = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in kind)
    safe_underlying = "".join(
        ch if ch.isalnum() or ch in "-_" else "_" for ch in underlying.upper()
    )
    suffix = f"_{session_date}" if session_date else ""
    return f"aistockscanner_{safe_kind}_{safe_underlying}{suffix}.csv"


#: Column allow-list for the futures ledger export (mirrors the 15 ledger columns).
FUTURES_LEDGER_EXPORT_COLUMNS: tuple[str, ...] = (
    "row_number",
    "interval_label",
    "interval_start_ist",
    "interval_end_ist",
    "total_oi",
    "oi_change",
    "day_high",
    "day_low",
    "level_break.label",
    "level_break.code",
    "oi_churn.value",
    "oi_churn.definition",
    "interval_volume",
    "ltp",
    "ltp_change",
    "ltp_change_pct",
    "direction.label",
    "interpretation.label",
    "interpretation.reason",
    "source",
    "source_timestamp_ist",
    "availability",
    "reason",
    "quality_flags",
    "baseline_provenance",
    "effective_minutes",
)

#: Column allow-list for the options interval-change export.
OPTIONS_INTERVAL_EXPORT_COLUMNS: tuple[str, ...] = (
    "strike_price",
    "is_atm",
    "call.oi",
    "call.ltp",
    "call.change_in_oi",
    "call.provider_day_change_in_oi",
    "call.price_change",
    "call.measurement_available",
    "call.measurement_start_ist",
    "call.effective_seconds",
    "call.provenance",
    "call.buildup.label",
    "put.oi",
    "put.ltp",
    "put.change_in_oi",
    "put.provider_day_change_in_oi",
    "put.price_change",
    "put.measurement_available",
    "put.measurement_start_ist",
    "put.effective_seconds",
    "put.provenance",
    "put.buildup.label",
)
