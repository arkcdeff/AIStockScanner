"""NSE Derivative Historical Extension Service — Stage G.8.

Extends real NSE historical derivatives (F&O) price coverage backwards through
Q4-2025 (2025-10-01 through 2025-12-31, 62 actual trading sessions) using official
NSE F&O UDiFF Bhavcopy archives.

Key Policies & Invariants:
  1. Strict Trading Calendar & Non-Fabrication: Rejects weekend and holiday trade dates.
  2. Point-in-time Integrity: available_at = trade_date (strictly no lookahead).
  3. Canonical Contract Master Upsert Before Daily Prices: Contracts are resolved and
     upserted using compute_fingerprint() to prevent duplicate contract records.
  4. Fail-Closed Lot Size Policy:
     - All option contracts have lot_size = 1 and source_name = None (lot quality UNAVAILABLE).
     - Futures contracts receive lot_size from NewBrdLotQty with source_name = 'FO_UDIFF_BHAVCOPY'.
  5. Idempotent Bulk Upsert: Prices are upserted cleanly on (contract_id, trade_date).
"""
from __future__ import annotations

import io
import json
import logging
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.derivative_bhavcopy_parser import (
    DerivativeBhavcopyParser,
    ParsedDerivativeDailyPrice,
)
from app.data.exchange_constants import EXCHANGE_NSE
from app.models.derivative_contract import DerivativeContract
from app.models.derivative_daily_price import DerivativeDailyPrice
from app.models.market_index import MarketIndex
from app.models.stock import Stock
from app.repositories.derivative_daily_price import DerivativeDailyPriceRepository

logger = logging.getLogger(__name__)

INDEX_SYMBOL_TO_CODE = {
    "NIFTY": "NIFTY",
    "NIFTY 50": "NIFTY",
    "NIFTY50": "NIFTY",
    "BANKNIFTY": "NIFTY_BANK",
    "NIFTY BANK": "NIFTY_BANK",
    "FINNIFTY": "NIFTY_FIN_SERV",
    "NIFTY FIN SERVICE": "NIFTY_FIN_SERV",
    "MIDCPNIFTY": "NIFTY_MID_SELECT",
    "NIFTY MID SELECT": "NIFTY_MID_SELECT",
    "NIFTYNXT50": "NIFTY_NEXT_50",
    "NIFTY NEXT 50": "NIFTY_NEXT_50",
}


@dataclass
class DerivativeSessionResult:
    """Result of importing a single F&O trading session."""

    trade_date: date
    filename: str
    total_rows: int = 0
    futures_count: int = 0
    options_count: int = 0
    contracts_upserted: int = 0
    prices_upserted: int = 0
    unresolved_count: int = 0
    rejected_count: int = 0
    status: str = "SUCCESS"
    error_message: str | None = None


@dataclass
class DerivativeExtensionBatchReport:
    """Summary of batch F&O historical extension run."""

    sessions_processed: int = 0
    successful_sessions: int = 0
    failed_sessions: int = 0
    skipped_sessions: int = 0
    total_source_rows: int = 0
    total_contracts_upserted: int = 0
    total_prices_upserted: int = 0
    session_results: list[DerivativeSessionResult] = field(default_factory=list)


class NseDerivativeHistoricalExtensionService:
    """Service to ingest historical NSE F&O bhavcopies for Q4-2025 extension."""

    def __init__(
        self,
        session: AsyncSession,
        cache_dir: Path | str | None = None,
    ) -> None:
        self.session = session
        self.cache_dir = Path(cache_dir or "data/reference/derivatives/raw")
        self.parser = DerivativeBhavcopyParser()
        self.price_repo = DerivativeDailyPriceRepository(session)
        self._stocks_by_symbol: dict[str, Stock] = {}
        self._indices_by_code: dict[str, MarketIndex] = {}
        self._contract_id_cache: dict[str, int] = {}
        self._cache_loaded = False

    async def ensure_caches(self) -> None:
        """Preload stocks, market indices, and existing derivative contract fingerprints."""
        if self._cache_loaded:
            return

        # 1. Stocks
        s_res = await self.session.execute(
            select(Stock).where(Stock.exchange == EXCHANGE_NSE)
        )
        for s in s_res.scalars().all():
            if s.symbol:
                self._stocks_by_symbol[s.symbol.strip().upper()] = s

        # 2. Market Indices
        idx_res = await self.session.execute(select(MarketIndex))
        for idx in idx_res.scalars().all():
            if idx.code:
                self._indices_by_code[idx.code.strip().upper()] = idx

        # 3. Existing contract fingerprints
        c_res = await self.session.execute(
            select(DerivativeContract.id, DerivativeContract.idempotency_hash)
        )
        for cid, fprint in c_res.all():
            if fprint:
                self._contract_id_cache[fprint] = cid

        self._cache_loaded = True
        logger.info(
            "Derivative extension caches loaded: %d stocks, %d indices, %d contracts",
            len(self._stocks_by_symbol),
            len(self._indices_by_code),
            len(self._contract_id_cache),
        )

    def validate_session_date(self, trade_date: date) -> str | None:
        """Enforce strict exchange calendar rules (no weekends, Q4-2025 window)."""
        if trade_date.weekday() >= 5:
            return f"Weekend trade date rejected: {trade_date} ({trade_date.strftime('%A')})"
        if trade_date < date(2025, 10, 1) or trade_date > date(2025, 12, 31):
            return f"Trade date {trade_date} outside authorized Q4-2025 window (2025-10-01 to 2025-12-31)"
        return None

    def obtain_bhavcopy_payload(
        self,
        trade_date: date,
        filename: str,
        source_url: str | None = None,
    ) -> bytes:
        """Load bhavcopy payload from local cache or download from official NSE archive."""
        local_path = self.cache_dir / filename
        if local_path.exists():
            return local_path.read_bytes()

        # Fallback check under data/raw/nse
        alt_path = Path("data/raw/nse/2025/fo") / filename
        if alt_path.exists():
            return alt_path.read_bytes()

        if not source_url:
            clean_d = trade_date.strftime("%Y%m%d")
            source_url = f"https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{clean_d}_F_0000.csv.zip"

        logger.info("Downloading official NSE F&O bhavcopy for %s from %s", trade_date, source_url)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
        }
        req = urllib.request.Request(source_url, headers=headers)
        data = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = resp.read()
                break
            except Exception as exc:
                if attempt == 2:
                    raise RuntimeError(f"Failed to download F&O archive from {source_url}: {exc}") from exc
                logger.warning(
                    "Download attempt %d failed for %s (%s). Retrying...",
                    attempt + 1,
                    source_url,
                    exc,
                )
                time.sleep(1.5 * (attempt + 1))

        # Save to local cache
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(data)
        return data

    def extract_csv_from_payload(self, payload: bytes, filename: str) -> str:
        """Extract CSV text from raw zip bytes or direct CSV bytes."""
        if payload[:2] == b"PK":  # Zip file magic bytes
            with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
                if not csv_names:
                    raise ValueError(f"No CSV file found inside zip archive {filename}")
                with zf.open(csv_names[0]) as f:
                    return f.read().decode("utf-8", errors="replace")
        return payload.decode("utf-8", errors="replace")

    def resolve_underlying_entity(
        self,
        symbol: str,
        instrument_type: str,
    ) -> tuple[str, int] | None:
        """Resolve underlying symbol to ('INDEX' | 'STOCK', primary_key_id)."""
        sym_clean = symbol.strip().upper()

        # 1. Check if index alias or direct code
        mapped_idx_code = INDEX_SYMBOL_TO_CODE.get(sym_clean)
        if mapped_idx_code:
            idx = self._indices_by_code.get(mapped_idx_code)
            if idx:
                return ("INDEX", idx.id)

        # Direct index lookup
        if sym_clean in self._indices_by_code:
            return ("INDEX", self._indices_by_code[sym_clean].id)

        # 2. Check stock by symbol
        if sym_clean in self._stocks_by_symbol:
            return ("STOCK", self._stocks_by_symbol[sym_clean].id)

        return None

    async def import_session(
        self,
        trade_date: date,
        payload: bytes | None = None,
        filename: str | None = None,
        source_url: str | None = None,
        skip_existing: bool = False,
        commit: bool = True,
    ) -> DerivativeSessionResult:
        """Import a single F&O trading session for Q4-2025."""
        clean_d = trade_date.strftime("%Y%m%d")
        fname = filename or f"BhavCopy_NSE_FO_0_0_0_{clean_d}_F_0000.csv.zip"

        # Validate date
        val_err = self.validate_session_date(trade_date)
        if val_err:
            return DerivativeSessionResult(
                trade_date=trade_date,
                filename=fname,
                status="REJECTED",
                rejected_count=1,
                error_message=val_err,
            )

        if skip_existing:
            stmt = select(func.count(DerivativeDailyPrice.id)).where(
                DerivativeDailyPrice.trade_date == trade_date
            )
            existing_count = (await self.session.execute(stmt)).scalar() or 0
            if existing_count > 0:
                logger.info(
                    "Session %s already present (%d daily prices in DB) - skipping",
                    trade_date,
                    existing_count,
                )
                return DerivativeSessionResult(
                    trade_date=trade_date,
                    filename=fname,
                    status="SKIPPED_ALREADY_PRESENT",
                    total_rows=existing_count,
                    prices_upserted=0,
                    contracts_upserted=0,
                )

        await self.ensure_caches()

        # Load payload
        if payload is None:
            try:
                payload = self.obtain_bhavcopy_payload(trade_date, fname, source_url)
            except Exception as exc:
                return DerivativeSessionResult(
                    trade_date=trade_date,
                    filename=fname,
                    status="FAILED",
                    error_message=str(exc),
                )

        # Extract and parse CSV
        try:
            csv_text = self.extract_csv_from_payload(payload, fname)
        except Exception as exc:
            return DerivativeSessionResult(
                trade_date=trade_date,
                filename=fname,
                status="FAILED",
                error_message=f"Archive extraction failed: {exc}",
            )

        parse_res = self.parser.parse_csv(csv_text, trade_date_override=trade_date)
        total_rows = parse_res.total_rows
        all_records = parse_res.all_records

        res = DerivativeSessionResult(
            trade_date=trade_date,
            filename=fname,
            total_rows=total_rows,
            futures_count=parse_res.futures_skipped_count,
            options_count=parse_res.options_skipped_count,
            rejected_count=len(parse_res.row_errors),
        )

        if not all_records:
            return res

        # 1. Resolve Underlyings & Prepare Contracts for Batch Upsert
        contracts_to_insert: list[dict[str, Any]] = []
        parsed_record_meta: list[tuple[ParsedDerivativeDailyPrice, str, int, str]] = []

        for rec in all_records:
            u_info = self.resolve_underlying_entity(rec.underlying_symbol, rec.instrument_type)
            if not u_info:
                res.unresolved_count += 1
                continue

            u_type, u_id = u_info
            stock_id = u_id if u_type == "STOCK" else None
            index_id = u_id if u_type == "INDEX" else None

            # Build canonical contract fingerprint
            c_temp = DerivativeContract(
                exchange=EXCHANGE_NSE,
                segment="NFO",
                instrument_type=rec.instrument_type,
                underlying_stock_id=stock_id,
                underlying_index_id=index_id,
                trading_symbol=rec.raw_row.get("FinInstrmNm") or rec.underlying_symbol,
                expiry_date=rec.expiry_date,
                strike_price=rec.strike_price,
                option_type=rec.option_type,
                lot_size=1,
                available_at=trade_date,
            )
            fprint = c_temp.compute_fingerprint()
            parsed_record_meta.append((rec, fprint, u_id, u_type))

            # If not already seen or known in DB, schedule insertion
            if fprint not in self._contract_id_cache:
                # Lot size policy:
                # For options: lot_size = 1, source_name = None (UNAVAILABLE quality)
                # For futures: lot_size = NewBrdLotQty, source_name = 'FO_UDIFF_BHAVCOPY'
                if rec.instrument_type == "OPTION":
                    c_lot = 1
                    c_source = None
                else:
                    raw_lot = rec.raw_row.get("NewBrdLotQty")
                    try:
                        c_lot = int(float(raw_lot)) if raw_lot else 1
                        if c_lot <= 0:
                            c_lot = 1
                    except (ValueError, TypeError):
                        c_lot = 1
                    c_source = "FO_UDIFF_BHAVCOPY"

                lifecycle = "EXPIRED" if rec.expiry_date < trade_date else "ACTIVE"

                contracts_to_insert.append(
                    {
                        "exchange": EXCHANGE_NSE,
                        "segment": "NFO",
                        "instrument_type": rec.instrument_type,
                        "underlying_stock_id": stock_id,
                        "underlying_index_id": index_id,
                        "trading_symbol": rec.raw_row.get("FinInstrmNm") or rec.underlying_symbol,
                        "source_contract_id": rec.raw_row.get("FinInstrmId"),
                        "expiry_date": rec.expiry_date,
                        "strike_price": rec.strike_price,
                        "option_type": rec.option_type,
                        "lot_size": c_lot,
                        "tick_size": Decimal("0.05"),
                        "lifecycle_status": lifecycle,
                        "idempotency_hash": fprint,
                        "available_at": trade_date,
                        "source_name": c_source,
                        "source_reference": fname,
                        "fetched_at": datetime.now(UTC),
                    }
                )
                # Mark as present in cache with sentinel 0 until flushed
                self._contract_id_cache[fprint] = 0

        # Batch insert new contracts with ON CONFLICT (idempotency_hash) DO NOTHING
        try:
            if contracts_to_insert:
                # Deduplicate within batch by idempotency_hash
                unique_contracts_dict = {c["idempotency_hash"]: c for c in contracts_to_insert}
                unique_contracts = list(unique_contracts_dict.values())

                batch_size = 1000
                for i in range(0, len(unique_contracts), batch_size):
                    chunk = unique_contracts[i : i + batch_size]
                    stmt = insert(DerivativeContract).values(chunk)
                    stmt = stmt.on_conflict_do_nothing(index_elements=["idempotency_hash"])
                    await self.session.execute(stmt)

                await self.session.flush()
                res.contracts_upserted = len(unique_contracts)

                # Query the newly inserted contracts to update contract_id cache
                needed_hashes = list(unique_contracts_dict.keys())
                for i in range(0, len(needed_hashes), batch_size):
                    chunk_hashes = needed_hashes[i : i + batch_size]
                    stmt_q = select(DerivativeContract.id, DerivativeContract.idempotency_hash).where(
                        DerivativeContract.idempotency_hash.in_(chunk_hashes)
                    )
                    q_res = await self.session.execute(stmt_q)
                    for cid, h in q_res.all():
                        self._contract_id_cache[h] = cid

            # 2. Build Daily Price Records
            prices_to_upsert: list[DerivativeDailyPrice] = []
            for rec, fprint, _, _ in parsed_record_meta:
                cid = self._contract_id_cache.get(fprint)
                if not cid or cid <= 0:
                    continue

                prices_to_upsert.append(
                    DerivativeDailyPrice(
                        contract_id=cid,
                        trade_date=trade_date,
                        open=rec.open,
                        high=rec.high,
                        low=rec.low,
                        close=rec.close,
                        last_price=rec.last_price,
                        settlement_price=rec.settlement_price,
                        previous_close=rec.previous_close,
                        volume_contracts=rec.volume_contracts,
                        turnover=rec.turnover,
                        open_interest=rec.open_interest,
                        change_in_open_interest=rec.change_in_open_interest,
                        underlying_value=rec.underlying_value,
                        available_at=trade_date,  # Strict PIT cutoff
                        source_name="FO_UDIFF_BHAVCOPY",
                        source_reference=fname,
                        fetched_at=datetime.now(UTC),
                    )
                )

            if prices_to_upsert:
                upserted = await self.price_repo.bulk_upsert(prices_to_upsert)
                res.prices_upserted = upserted

            if commit:
                await self.session.commit()
            else:
                await self.session.flush()
        except Exception as exc:
            await self.session.rollback()
            res.status = "FAILED"
            res.error_message = f"Database operation failed: {exc}"
            logger.error("Database operation failed for session %s: %s", trade_date, exc, exc_info=True)
            return res

        return res

    async def run_manifest(
        self,
        manifest_path: Path | str | None = None,
        skip_existing: bool = False,
        commit: bool = True,
    ) -> DerivativeExtensionBatchReport:
        """Ingest all sessions defined in manifest."""
        m_path = Path(manifest_path or "data/reference/derivatives/manifest_q4_2025.json")
        if not m_path.exists():
            raise FileNotFoundError(f"Derivative manifest not found at {m_path}")

        manifest = json.loads(m_path.read_text(encoding="utf-8"))
        dates = sorted(manifest.keys())

        report = DerivativeExtensionBatchReport()
        for d_str in dates:
            entry = manifest[d_str]
            t_date = datetime.strptime(d_str, "%Y-%m-%d").date()
            res = await self.import_session(
                trade_date=t_date,
                filename=entry.get("filename"),
                source_url=entry.get("source_url"),
                skip_existing=skip_existing,
                commit=commit,
            )
            report.sessions_processed += 1
            report.total_source_rows += res.total_rows
            report.total_contracts_upserted += res.contracts_upserted
            report.total_prices_upserted += res.prices_upserted
            if res.status == "SUCCESS":
                report.successful_sessions += 1
            elif res.status == "SKIPPED_ALREADY_PRESENT":
                report.skipped_sessions += 1
            else:
                report.failed_sessions += 1
            report.session_results.append(res)
            logger.info(
                "Ingested F&O session %s: %d prices, %d contracts (%s)",
                d_str,
                res.prices_upserted,
                res.contracts_upserted,
                res.status,
            )

        return report
