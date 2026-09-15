"""Bulk & Block Deals Historical Extension Service — Stage G.10.

Extends official NSE Cash Market Bulk Deals and Block Deals coverage
backwards through Q4-2025 (2025-10-01 through 2025-12-31, 62 trading sessions).

Key Policies & Invariants:
  1. Unified Canonical Storage:
     - Persists to `bulk_block_deals` table.
     - `deal_type` in ('BULK', 'BLOCK').
     - `buy_sell` in ('BUY', 'SELL').
  2. Strict Source Provenance:
     - source_name = 'NSE_INDIA'
     - source_reference = exact raw filename (e.g. bulk_deals_20251001.csv)
     - source_checksum = SHA-256 hash verified against manifest
  3. Point-in-Time Availability:
     - available_at = trade_date + 1 calendar day (post-market date granularity).
     - Queries enforce trade_date <= as_of AND available_at <= as_of AND is_current = True.
  4. Primary Identity & Deduplication:
     - Unique on (source_reference, source_line_index) where is_current = True.
     - deal_row_hash is non-unique SHA-256 over 8 normalized content fields.
     - Exact duplicate source rows (e.g. GEOJITFSL) and multi-tranche executions (e.g. ARSSBL)
       are preserved as separate rows.
  5. Amendment Model:
     - If row exists and deal_row_hash unchanged -> NO-OP.
     - If row exists and deal_row_hash changed -> supersede old row (is_current=False,
       superseded_at=now()) and insert new row (is_current=True, supersedes_id=old.id).
     - If old row absent in refreshed source -> retain current, log DQ_DEALS_SOURCE_CHANGED.
  6. Entity Resolution:
     - Exact Stock.symbol (NSE) -> SecurityAlias as-of trade_date -> stock_id = NULL.
     - NEVER create a Stock entity.
     - Unresolved securities emit grouped DQ_DEALS_SYMBOL_UNRESOLVED warning.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.bulk_block_parser import BulkBlockParser, compute_deal_row_hash
from app.models.bulk_block_deal import BulkBlockDeal
from app.models.data_quality_issue import DataQualityIssue
from app.models.security_alias import SecurityAlias
from app.models.stock import Stock
from app.repositories.bulk_block_deal import BulkBlockDealRepository

logger = logging.getLogger(__name__)


@dataclass
class DealSessionResult:
    """Ingestion result for a single deal session (bulk or block)."""

    trade_date: date
    deal_type: str  # 'BULK' or 'BLOCK'
    filename: str
    rows_parsed: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_unchanged: int = 0
    symbols_resolved: int = 0
    symbols_unresolved: int = 0
    unresolved_symbols: list[str] = field(default_factory=list)
    quality_warnings: list[str] = field(default_factory=list)
    status: str = "SUCCESS"  # SUCCESS, SKIPPED_ALREADY_PRESENT, FAILED, VERIFIED_ONLY
    error_message: str | None = None


@dataclass
class DealBatchReport:
    """Aggregate report of batch deals historical extension run."""

    total_sessions: int = 0
    successful_sessions: int = 0
    failed_sessions: int = 0
    skipped_sessions: int = 0
    total_bulk_rows: int = 0
    total_block_rows: int = 0
    total_rows_inserted: int = 0
    total_rows_updated: int = 0
    total_rows_unchanged: int = 0
    unique_symbols: int = 0
    resolved_stock_rows: int = 0
    unresolved_rows: int = 0
    unresolved_symbols: list[str] = field(default_factory=list)
    quality_warnings_count: int = 0
    session_results: list[DealSessionResult] = field(default_factory=list)


class BulkBlockExtensionService:
    """Service to ingest and manage historical Bulk & Block Deals for Q4-2025."""

    def __init__(
        self,
        session: AsyncSession,
        cache_dir: Path | str | None = None,
        manifest_path: Path | str | None = None,
    ) -> None:
        self.session = session
        self.cache_dir = Path(cache_dir or "data/reference/deals/raw")
        self.manifest_path = Path(manifest_path or "data/reference/deals/manifest_q4_2025.json")
        self.parser = BulkBlockParser()
        self.repo = BulkBlockDealRepository(session)
        self._stock_id_cache: dict[str, int] | None = None
        self._alias_cache: list[tuple[str, int, date, date | None]] | None = None

    async def _init_symbol_cache(self) -> None:
        """Preload active NSE stock symbols and security aliases into memory."""
        if self._stock_id_cache is None:
            # Query all NSE stocks
            stock_stmt = select(Stock.symbol, Stock.id).where(Stock.exchange == "NSE")
            res = await self.session.execute(stock_stmt)
            self._stock_id_cache = {row[0].upper(): row[1] for row in res.all()}

        if self._alias_cache is None:
            # Query security aliases
            alias_stmt = select(
                SecurityAlias.symbol,
                SecurityAlias.stock_id,
                SecurityAlias.valid_from,
                SecurityAlias.valid_to,
            ).where(SecurityAlias.exchange == "NSE")
            res_alias = await self.session.execute(alias_stmt)
            self._alias_cache = [
                (row[0].upper(), row[1], row[2], row[3])
                for row in res_alias.all()
            ]

    def resolve_stock_id(self, symbol: str, trade_date: date) -> int | None:
        """Resolve stock_id by exact symbol, then SecurityAlias as-of trade_date.

        Never creates a Stock entity. Unresolved returns None.
        """
        sym = symbol.strip().upper()
        # 1. Exact stock symbol
        if self._stock_id_cache and sym in self._stock_id_cache:
            return self._stock_id_cache[sym]

        # 2. Security alias valid on trade_date
        if self._alias_cache:
            for alias_sym, stock_id, valid_from, valid_to in self._alias_cache:
                if alias_sym == sym and valid_from <= trade_date:
                    if valid_to is None or valid_to >= trade_date:
                        return stock_id

        # 3. Unresolved
        return None

    def load_manifest(self) -> dict:
        """Load manifest file."""
        if not self.manifest_path.exists():
            raise FileNotFoundError(f"Manifest not found at {self.manifest_path}")
        with open(self.manifest_path, encoding="utf-8") as f:
            return json.load(f)

    async def ingest_session_file(
        self,
        trade_date: date,
        deal_type: str,
        filename: str,
        expected_sha256: str | None = None,
        expected_record_count: int | None = None,
        skip_if_present: bool = False,
    ) -> DealSessionResult:
        """Ingest a single daily deal file (BULK or BLOCK) atomically."""
        await self._init_symbol_cache()
        deal_type_upper = deal_type.strip().upper()

        # 1. Check if source file exists
        source_path = self.cache_dir / filename
        if not source_path.exists():
            source_path = Path("data/reference/deals/raw") / filename
            if not source_path.exists():
                return DealSessionResult(
                    trade_date=trade_date,
                    deal_type=deal_type_upper,
                    filename=filename,
                    status="FAILED",
                    error_message=f"Deal source file not found: {source_path}",
                )

        # 2. Read and verify SHA-256
        try:
            content_bytes = await asyncio.to_thread(source_path.read_bytes)
        except OSError as e:
            return DealSessionResult(
                trade_date=trade_date,
                deal_type=deal_type_upper,
                filename=filename,
                status="FAILED",
                error_message=f"Failed reading file {source_path}: {e}",
            )

        actual_sha256 = hashlib.sha256(content_bytes).hexdigest()
        if expected_sha256 and actual_sha256.lower() != expected_sha256.lower():
            return DealSessionResult(
                trade_date=trade_date,
                deal_type=deal_type_upper,
                filename=filename,
                status="FAILED",
                error_message=f"SHA-256 mismatch for {filename}: expected {expected_sha256}, got {actual_sha256}",
            )

        # 3. Skip-if-present check
        if skip_if_present:
            existing_count = (
                await self.session.execute(
                    select(func.count(BulkBlockDeal.id)).where(
                        BulkBlockDeal.source_reference == filename,
                        BulkBlockDeal.is_current.is_(True),
                    )
                )
            ).scalar_one()

            if expected_record_count is not None and existing_count == expected_record_count:
                return DealSessionResult(
                    trade_date=trade_date,
                    deal_type=deal_type_upper,
                    filename=filename,
                    rows_parsed=existing_count,
                    rows_inserted=0,
                    rows_updated=0,
                    rows_unchanged=existing_count,
                    status="SKIPPED_ALREADY_PRESENT",
                )

        # 4. Parse file
        try:
            parsed_result = self.parser.parse_csv(
                content=content_bytes,
                deal_type=deal_type_upper,
                trade_date=trade_date,
                source_reference=filename,
            )
        except Exception as e:
            return DealSessionResult(
                trade_date=trade_date,
                deal_type=deal_type_upper,
                filename=filename,
                status="FAILED",
                error_message=f"Parser failure on {filename}: {e}",
            )

        if parsed_result.rejected_rows:
            logger.warning(
                "Parsed %s with %d rejected rows: %s",
                filename,
                len(parsed_result.rejected_rows),
                parsed_result.quality_warnings,
            )

        # 5. Fetch existing active rows for this source_reference
        existing_deals = await self.repo.get_active_by_source_reference(filename)
        existing_by_line: dict[int, BulkBlockDeal] = {d.source_line_index: d for d in existing_deals}

        rows_inserted = 0
        rows_updated = 0
        rows_unchanged = 0
        symbols_resolved = 0
        symbols_unresolved = 0
        unresolved_symbols_set: set[str] = set()
        quality_warnings: list[str] = []

        now_utc = datetime.now(UTC)
        incoming_line_indices: set[int] = set()

        for row in parsed_result.rows:
            incoming_line_indices.add(row.source_line_index)
            # Resolve stock entity
            stock_id = self.resolve_stock_id(row.symbol, row.trade_date)
            if stock_id is not None:
                symbols_resolved += 1
            else:
                symbols_unresolved += 1
                unresolved_symbols_set.add(row.symbol)

            # Compute content row hash
            row_hash = compute_deal_row_hash(
                trade_date=row.trade_date,
                deal_type=deal_type_upper,
                symbol=row.symbol,
                client_name=row.client_name,
                buy_sell=row.buy_sell,
                quantity=row.quantity,
                trade_price=row.trade_price,
                remarks=row.remarks,
            )

            # Check if row already exists for this line index
            existing = existing_by_line.get(row.source_line_index)
            if existing is None:
                # Insert new deal
                deal = BulkBlockDeal(
                    trade_date=row.trade_date,
                    deal_type=deal_type_upper,
                    symbol=row.symbol,
                    security_name=row.security_name,
                    client_name=row.client_name,
                    buy_sell=row.buy_sell,
                    quantity=row.quantity,
                    trade_price=row.trade_price,
                    deal_value=row.deal_value,
                    remarks=row.remarks,
                    stock_id=stock_id,
                    source_line_index=row.source_line_index,
                    deal_row_hash=row_hash,
                    is_current=True,
                    available_at=row.trade_date + timedelta(days=1),
                    source_name="NSE_INDIA",
                    source_reference=filename,
                    source_checksum=actual_sha256,
                    fetched_at=now_utc,
                )
                self.session.add(deal)
                rows_inserted += 1
            else:
                # Row exists: check if content hash matches
                if existing.deal_row_hash == row_hash:
                    # Idempotent no-op
                    rows_unchanged += 1
                else:
                    # Amendment detected!
                    new_deal = BulkBlockDeal(
                        trade_date=row.trade_date,
                        deal_type=deal_type_upper,
                        symbol=row.symbol,
                        security_name=row.security_name,
                        client_name=row.client_name,
                        buy_sell=row.buy_sell,
                        quantity=row.quantity,
                        trade_price=row.trade_price,
                        deal_value=row.deal_value,
                        remarks=row.remarks,
                        stock_id=stock_id,
                        source_line_index=row.source_line_index,
                        deal_row_hash=row_hash,
                        is_current=True,
                        supersedes_id=existing.id,
                        available_at=row.trade_date + timedelta(days=1),
                        source_name="NSE_INDIA",
                        source_reference=filename,
                        source_checksum=actual_sha256,
                        fetched_at=now_utc,
                    )
                    await self.repo.save_deal_amendment(existing, new_deal)
                    rows_updated += 1
                    msg = (
                        f"Deal disclosure amended: {filename} line {row.source_line_index} "
                        f"({row.symbol} {row.client_name})"
                    )
                    quality_warnings.append(msg)
                    self.session.add(
                        DataQualityIssue(
                            issue_code="DQ_DEALS_DISCLOSURE_AMENDED",
                            severity="warning",
                            exchange="NSE",
                            symbol=row.symbol,
                            trade_date=row.trade_date,
                            description=msg,
                            detected_at=now_utc,
                            resolved=False,
                        )
                    )

        # Check for deleted rows (present in existing DB active rows but absent from incoming source)
        for line_idx, existing_row in existing_by_line.items():
            if line_idx not in incoming_line_indices:
                msg = (
                    f"Deal row absent in refreshed source: {filename} line {line_idx} "
                    f"({existing_row.symbol} {existing_row.client_name})"
                )
                quality_warnings.append(msg)
                self.session.add(
                    DataQualityIssue(
                        issue_code="DQ_DEALS_SOURCE_CHANGED",
                        severity="warning",
                        exchange="NSE",
                        symbol=existing_row.symbol,
                        trade_date=existing_row.trade_date,
                        description=msg,
                        detected_at=now_utc,
                        resolved=False,
                    )
                )

        # Record grouped unresolved symbol warnings
        for unres_sym in sorted(unresolved_symbols_set):
            self.session.add(
                DataQualityIssue(
                    issue_code="DQ_DEALS_SYMBOL_UNRESOLVED",
                    severity="warning",
                    exchange="NSE",
                    symbol=unres_sym,
                    trade_date=trade_date,
                    description=f"Unresolved security in {deal_type_upper} deals on {trade_date}: symbol {unres_sym}",
                    detected_at=now_utc,
                    resolved=False,
                )
            )

        # Commit atomic session
        await self.session.commit()

        return DealSessionResult(
            trade_date=trade_date,
            deal_type=deal_type_upper,
            filename=filename,
            rows_parsed=len(parsed_result.rows),
            rows_inserted=rows_inserted,
            rows_updated=rows_updated,
            rows_unchanged=rows_unchanged,
            symbols_resolved=symbols_resolved,
            symbols_unresolved=symbols_unresolved,
            unresolved_symbols=sorted(unresolved_symbols_set),
            quality_warnings=quality_warnings,
            status="SUCCESS",
        )

    async def ingest_manifest(
        self,
        manifest_path: Path | str | None = None,
        skip_if_present: bool = False,
        verify_only: bool = False,
    ) -> DealBatchReport:
        """Ingest or verify all Bulk and Block Deal sessions from a manifest."""
        m_path = Path(manifest_path or self.manifest_path)
        if not m_path.exists():
            raise FileNotFoundError(f"Manifest not found at {m_path}")
        manifest = json.loads(m_path.read_text(encoding="utf-8"))

        if "sessions" in manifest:
            sessions = manifest["sessions"]
        else:
            sessions = {k: v for k, v in manifest.items() if k != "_meta"}

        report = DealBatchReport(total_sessions=len(sessions))
        all_unresolved_symbols: set[str] = set()

        for date_str in sorted(sessions.keys()):
            session_meta = sessions[date_str]
            sess_date = date.fromisoformat(date_str)

            # Ingest Bulk file
            bulk_meta = session_meta.get("bulk", {})
            bulk_filename = bulk_meta.get("filename")
            bulk_sha = bulk_meta.get("sha256")
            bulk_count = bulk_meta.get("record_count") if "record_count" in bulk_meta else bulk_meta.get("row_count", 0)

            if verify_only:
                # File verification only
                source_path = self.cache_dir / bulk_filename
                if not source_path.exists():
                    report.failed_sessions += 1
                    report.session_results.append(
                        DealSessionResult(
                            trade_date=sess_date,
                            deal_type="BULK",
                            filename=bulk_filename,
                            status="FAILED",
                            error_message="File not found",
                        )
                    )
                    continue
                file_bytes = source_path.read_bytes()
                h = hashlib.sha256(file_bytes).hexdigest()
                if h.lower() != bulk_sha.lower():
                    report.failed_sessions += 1
                    report.session_results.append(
                        DealSessionResult(
                            trade_date=sess_date,
                            deal_type="BULK",
                            filename=bulk_filename,
                            status="FAILED",
                            error_message="SHA256 mismatch",
                        )
                    )
                    continue
                report.total_bulk_rows += bulk_count
                report.session_results.append(
                    DealSessionResult(
                        trade_date=sess_date,
                        deal_type="BULK",
                        filename=bulk_filename,
                        rows_parsed=bulk_count,
                        status="VERIFIED_ONLY",
                    )
                )
            else:
                bulk_res = await self.ingest_session_file(
                    trade_date=sess_date,
                    deal_type="BULK",
                    filename=bulk_filename,
                    expected_sha256=bulk_sha,
                    expected_record_count=bulk_count,
                    skip_if_present=skip_if_present,
                )
                report.session_results.append(bulk_res)
                if bulk_res.status == "SUCCESS":
                    report.total_bulk_rows += bulk_res.rows_parsed
                    report.total_rows_inserted += bulk_res.rows_inserted
                    report.total_rows_updated += bulk_res.rows_updated
                    report.total_rows_unchanged += bulk_res.rows_unchanged
                    report.resolved_stock_rows += bulk_res.symbols_resolved
                    report.unresolved_rows += bulk_res.symbols_unresolved
                    all_unresolved_symbols.update(bulk_res.unresolved_symbols)
                    report.quality_warnings_count += len(bulk_res.quality_warnings)
                elif bulk_res.status == "SKIPPED_ALREADY_PRESENT":
                    report.skipped_sessions += 1
                    report.total_bulk_rows += bulk_res.rows_parsed
                    report.total_rows_unchanged += bulk_res.rows_unchanged
                else:
                    report.failed_sessions += 1

            # Ingest Block file (if present and record_count > 0 or status == VALID)
            block_meta = session_meta.get("block", {})
            block_filename = block_meta.get("filename")
            block_sha = block_meta.get("sha256")
            block_count = block_meta.get("record_count") if "record_count" in block_meta else block_meta.get("row_count", 0)

            if block_filename:
                if verify_only:
                    source_path = self.cache_dir / block_filename
                    if source_path.exists():
                        file_bytes = source_path.read_bytes()
                        h = hashlib.sha256(file_bytes).hexdigest()
                        if h.lower() != block_sha.lower():
                            report.failed_sessions += 1
                        else:
                            report.total_block_rows += block_count
                            report.session_results.append(
                                DealSessionResult(
                                    trade_date=sess_date,
                                    deal_type="BLOCK",
                                    filename=block_filename,
                                    rows_parsed=block_count,
                                    status="VERIFIED_ONLY",
                                )
                            )
                else:
                    block_res = await self.ingest_session_file(
                        trade_date=sess_date,
                        deal_type="BLOCK",
                        filename=block_filename,
                        expected_sha256=block_sha,
                        expected_record_count=block_count,
                        skip_if_present=skip_if_present,
                    )
                    report.session_results.append(block_res)
                    if block_res.status == "SUCCESS":
                        report.total_block_rows += block_res.rows_parsed
                        report.total_rows_inserted += block_res.rows_inserted
                        report.total_rows_updated += block_res.rows_updated
                        report.total_rows_unchanged += block_res.rows_unchanged
                        report.resolved_stock_rows += block_res.symbols_resolved
                        report.unresolved_rows += block_res.symbols_unresolved
                        all_unresolved_symbols.update(block_res.unresolved_symbols)
                        report.quality_warnings_count += len(block_res.quality_warnings)
                    elif block_res.status == "SKIPPED_ALREADY_PRESENT":
                        report.total_block_rows += block_res.rows_parsed
                        report.total_rows_unchanged += block_res.rows_unchanged
                    else:
                        report.failed_sessions += 1

            if not verify_only and (
                (bulk_res.status in ("SUCCESS", "SKIPPED_ALREADY_PRESENT"))
                and (not block_filename or block_res.status in ("SUCCESS", "SKIPPED_ALREADY_PRESENT"))
            ):
                report.successful_sessions += 1

        report.unresolved_symbols = sorted(all_unresolved_symbols)
        return report

    async def ingest_all_q4_2025(
        self,
        skip_if_present: bool = False,
        verify_only: bool = False,
    ) -> DealBatchReport:
        """Ingest or verify all Q4-2025 Bulk and Block Deal sessions from default manifest."""
        return await self.ingest_manifest(
            manifest_path=self.manifest_path,
            skip_if_present=skip_if_present,
            verify_only=verify_only,
        )

