from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

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
            records = self._parse_json(path)
        elif path.suffix.lower() == ".csv":
            records = self._parse_csv(path)
        else:
            raise ValueError(f"Unsupported file format: {path.suffix}")

        return await self.import_records(records, source_name=source_name, source_file=str(path))

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
