from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.data_quality_issue import DataQualityIssue
from app.repositories.institutional_flow import InstitutionalFlowRepository

ROLLING_HORIZONS = (1, 3, 5, 20)
DEFAULT_SOURCE_NAME = "NSE_CM_CATEGORY_WISE_TURNOVER"


@dataclass(frozen=True, slots=True)
class InstitutionalImportResult:
    total_records: int
    inserted: int
    updated: int
    sessions_count: int
    source_file: str


class NseInstitutionalImportService:
    """Import official NSE institutional cash-market flow data idempotently.

    Acquires and computes chronological rolling cumulatives (1D, 3D, 5D, 20D)
    for FII/FPI and DII categories and persists them to `institutional_flows`.
    """

    def __init__(
        self,
        session: AsyncSession,
        repository: InstitutionalFlowRepository | None = None,
    ) -> None:
        self._session = session
        self._repository = repository or InstitutionalFlowRepository(session)

    async def import_from_file(
        self,
        file_path: Path | str,
        *,
        source_name: str = DEFAULT_SOURCE_NAME,
    ) -> InstitutionalImportResult:
        """Read canonical reference file (.csv or .json) and persist all records."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Institutional reference file not found: {path}")

        if path.suffix.lower() == ".json":
            raw_content = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw_content, dict) and ("_meta" in raw_content or any(isinstance(v, dict) and "categories" in v for v in raw_content.values())):
                return await self.import_from_manifest(path, source_name=source_name)
            records = self._parse_json(path)
        elif path.suffix.lower() == ".csv":
            records = self._parse_csv(path)
        else:
            raise ValueError(f"Unsupported file format: {path.suffix}")

        return await self.import_records(records, source_name=source_name, source_file=str(path))

    async def import_from_manifest(
        self,
        manifest_path: Path | str,
        *,
        source_name: str = DEFAULT_SOURCE_NAME,
        record_dq_issues: bool = True,
    ) -> InstitutionalImportResult:
        """Parse valid sessions from manifest with fail-closed stale-echo validation and persist all records."""
        path = Path(manifest_path)
        if not path.exists():
            raise FileNotFoundError(f"Institutional manifest not found: {path}")

        data = json.loads(path.read_text(encoding="utf-8"))
        records: list[dict[str, Any]] = []
        seen_crcs: dict[str, date] = {}
        seen_signatures: dict[str, date] = {}
        rejected_sessions: list[tuple[date, str]] = []

        for k, entry in data.items():
            if k == "_meta":
                continue

            trade_date_raw = entry.get("trade_date")
            if not trade_date_raw:
                continue

            try:
                t_date = date.fromisoformat(trade_date_raw)
            except Exception:
                continue

            is_valid, reject_reason = self.validate_session_entry(
                entry, t_date, seen_crcs=seen_crcs, seen_signatures=seen_signatures
            )
            if not is_valid:
                logger.warning("Rejected institutional session %s: %s", t_date, reject_reason)
                rejected_sessions.append((t_date, reject_reason or "Unknown validation failure"))
                if record_dq_issues:
                    now_utc = datetime.now(UTC)
                    dq = DataQualityIssue(
                        issue_code="SOURCE_STALE_CONTENT",
                        severity="error",
                        exchange="NSE",
                        symbol=None,
                        trade_date=t_date,
                        description=f"Rejected institutional session: {reject_reason}",
                        detected_at=now_utc,
                        resolved=False,
                    )
                    self._session.add(dq)
                continue

            categories = entry.get("categories", [])
            cat_map = {c["category"]: c for c in categories}
            fpi_entry = cat_map.get("FPI", {})
            fii_buy = Decimal(str(fpi_entry.get("buy_value_crore", 0.0)))
            fii_sell = Decimal(str(fpi_entry.get("sell_value_crore", 0.0)))

            dii_cats = ("Bank", "Insurance Companies", "Mutual Funds", "AIF", "PMS")
            dii_buy = sum(Decimal(str(cat_map[c].get("buy_value_crore", 0.0))) for c in dii_cats if c in cat_map)
            dii_sell = sum(Decimal(str(cat_map[c].get("sell_value_crore", 0.0))) for c in dii_cats if c in cat_map)

            records.append({
                "trade_date": t_date,
                "investor_type": "FII",
                "buy_value": fii_buy,
                "sell_value": fii_sell,
                "net_value": fii_buy - fii_sell,
                "observed_at": t_date,
                "fetched_at": t_date,
            })
            records.append({
                "trade_date": t_date,
                "investor_type": "DII",
                "buy_value": dii_buy,
                "sell_value": dii_sell,
                "net_value": dii_buy - dii_sell,
                "observed_at": t_date,
                "fetched_at": t_date,
            })

        return await self.import_records(records, source_name=source_name, source_file=str(path))

    def validate_session_entry(
        self,
        entry: dict[str, Any],
        t_date: date,
        *,
        seen_crcs: dict[str, date],
        seen_signatures: dict[str, date],
    ) -> tuple[bool, str | None]:
        """Generic fail-closed validation for institutional turnover archives."""
        status = entry.get("source_status")
        if status in ("SOURCE_UNAVAILABLE", "SOURCE_STALE_CONTENT", "STALE_CONTENT"):
            return False, f"Source status is {status}"
        if status not in ("VALID", "GENUINE", "GENUINE_PRIOR_TRUSTED", "GENUINE_ACQUIRED"):
            return False, f"Unrecognized source status: {status}"

        inner_file = entry.get("inner_file")
        if inner_file:
            m1 = re.search(r"cat_turnover_(\d{2})(\d{2})(\d{2,4})\.xls", inner_file, re.IGNORECASE)
            m2 = re.search(r"category_turnover_(\d{4})(\d{2})(\d{2})\.xls", inner_file, re.IGNORECASE)
            if m1:
                dd_s, mm_s, yy_s = m1.groups()
                year = 2000 + int(yy_s) if len(yy_s) == 2 else int(yy_s)
                inner_d = date(year, int(mm_s), int(dd_s))
                if inner_d != t_date:
                    return False, f"Inner filename date mismatch: inner {inner_d} != requested {t_date}"
            elif m2:
                yyyy_s, mm_s, dd_s = m2.groups()
                inner_d = date(int(yyyy_s), int(mm_s), int(dd_s))
                if inner_d != t_date:
                    return False, f"Inner filename date mismatch: inner {inner_d} != requested {t_date}"

        inner_crc = entry.get("inner_crc32")
        if inner_crc:
            inner_crc_clean = str(inner_crc).lower().strip()
            if inner_crc_clean in seen_crcs and seen_crcs[inner_crc_clean] != t_date:
                return False, f"Duplicate inner CRC32 '{inner_crc}' already seen on {seen_crcs[inner_crc_clean]}"
            seen_crcs[inner_crc_clean] = t_date

        categories = entry.get("categories", [])
        if not categories:
            return False, "Missing category turnover records in payload"

        cat_map = {c.get("category"): c for c in categories}
        if "FPI" not in cat_map:
            return False, "Missing required FPI category in turnover payload"

        fpi_entry = cat_map.get("FPI", {})
        fii_buy = Decimal(str(fpi_entry.get("buy_value_crore", 0.0)))
        fii_sell = Decimal(str(fpi_entry.get("sell_value_crore", 0.0)))

        dii_cats = ("Bank", "Insurance Companies", "Mutual Funds", "AIF", "PMS")
        dii_buy = sum(Decimal(str(cat_map[c].get("buy_value_crore", 0.0))) for c in dii_cats if c in cat_map)
        dii_sell = sum(Decimal(str(cat_map[c].get("sell_value_crore", 0.0))) for c in dii_cats if c in cat_map)

        sig = f"{fii_buy:.2f}:{fii_sell:.2f}:{dii_buy:.2f}:{dii_sell:.2f}"
        if (fii_buy != 0 or fii_sell != 0 or dii_buy != 0 or dii_sell != 0):
            if sig in seen_signatures and seen_signatures[sig] != t_date:
                return False, f"Identical turnover payload signature repeated from {seen_signatures[sig]}"
            seen_signatures[sig] = t_date

        return True, None

    async def import_records(
        self,
        records: list[dict[str, Any]],
        *,
        source_name: str = DEFAULT_SOURCE_NAME,
        source_file: str = "memory",
    ) -> InstitutionalImportResult:
        """Idempotently upsert institutional flow records with rolling cumulatives."""
        if not records:
            return InstitutionalImportResult(
                total_records=0, inserted=0, updated=0, sessions_count=0, source_file=source_file
            )

        # Sort chronologically
        records = sorted(records, key=lambda r: (r["trade_date"], r["investor_type"]))
        distinct_dates = sorted({r["trade_date"] for r in records})

        # Organize by investor category
        by_category: dict[str, list[dict[str, Any]]] = {"FII": [], "DII": []}
        for r in records:
            cat = r["investor_type"]
            if cat in by_category:
                by_category[cat].append(r)

        inserted_count = 0
        updated_count = 0

        for investor_type, cat_records in by_category.items():
            cat_records.sort(key=lambda r: r["trade_date"])
            history_nets: list[Decimal] = []

            for row in cat_records:
                t_date: date = row["trade_date"]
                buy_val = Decimal(str(row["buy_value"]))
                sell_val = Decimal(str(row["sell_value"]))
                net_val = Decimal(str(row["net_value"])) if "net_value" in row else (buy_val - sell_val)

                history_nets.append(net_val)

                # Compute rolling cumulatives
                c_1d = float(net_val)
                c_3d = float(sum(history_nets[-3:])) if len(history_nets) >= 3 else None
                c_5d = float(sum(history_nets[-5:])) if len(history_nets) >= 5 else None
                c_20d = float(sum(history_nets[-20:])) if len(history_nets) >= 20 else None

                obs_at = row.get("observed_at")
                if isinstance(obs_at, str):
                    obs_at = date.fromisoformat(obs_at[:10])
                elif not isinstance(obs_at, date):
                    obs_at = t_date

                fetch_at = row.get("fetched_at")
                if isinstance(fetch_at, str):
                    fetch_at = date.fromisoformat(fetch_at[:10])
                elif not isinstance(fetch_at, date):
                    fetch_at = t_date

                existing = await self._repository.get_for_date(t_date, investor_type)
                if existing is None:
                    inserted_count += 1
                else:
                    updated_count += 1

                await self._repository.upsert(
                    trade_date=t_date,
                    investor_type=investor_type,
                    buy_value=float(buy_val),
                    sell_value=float(sell_val),
                    net_value=float(net_val),
                    one_day_cumulative=c_1d,
                    three_day_cumulative=c_3d,
                    five_day_cumulative=c_5d,
                    twenty_day_cumulative=c_20d,
                    source_name=source_name,
                    observed_at=obs_at,
                    fetched_at=fetch_at,
                    freshness_status="recent",
                )

        await self._session.flush()

        logger.info(
            f"Imported {len(records)} institutional flow records across {len(distinct_dates)} sessions: "
            f"{inserted_count} inserted, {updated_count} updated."
        )

        return InstitutionalImportResult(
            total_records=len(records),
            inserted=inserted_count,
            updated=updated_count,
            sessions_count=len(distinct_dates),
            source_file=source_file,
        )

    def _parse_json(self, path: Path) -> list[dict[str, Any]]:
        data = json.loads(path.read_text(encoding="utf-8"))
        records: list[dict[str, Any]] = []
        for item in data:
            t_date = date.fromisoformat(item["trade_date"])
            obs = item.get("observed_at")
            fetch = item.get("available_at") or obs

            # FII
            fii = item["fii"]
            records.append({
                "trade_date": t_date,
                "investor_type": "FII",
                "buy_value": fii["buy"],
                "sell_value": fii["sell"],
                "net_value": fii["net"],
                "observed_at": obs,
                "fetched_at": fetch,
            })
            # DII
            dii = item["dii"]
            records.append({
                "trade_date": t_date,
                "investor_type": "DII",
                "buy_value": dii["buy"],
                "sell_value": dii["sell"],
                "net_value": dii["net"],
                "observed_at": obs,
                "fetched_at": fetch,
            })
        return records

    def _parse_csv(self, path: Path) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                t_date = date.fromisoformat(row["trade_date"])
                records.append({
                    "trade_date": t_date,
                    "investor_type": row["investor_type"].strip(),
                    "buy_value": float(row["buy_value"]),
                    "sell_value": float(row["sell_value"]),
                    "net_value": float(row["buy_value"]) - float(row["sell_value"]),
                    "observed_at": row.get("observed_at"),
                    "fetched_at": row.get("available_at"),
                })
        return records
