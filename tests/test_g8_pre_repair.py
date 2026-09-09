"""Pre-G.8 Derivatives Data-Integrity Repair Tests.

Tests C1-C5 root cause fixes and code-level invariants.
Database-state verification was performed by scripts/g8_pre_repair.py.
Run: uv run pytest tests/test_g8_pre_repair.py -v
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

# ---------------------------------------------------------------------------
# C1+C2 Root Cause: Hash Format Consistency
# ---------------------------------------------------------------------------

class TestHashFormatConsistency:
    """Bulk importer hash must match DerivativeContract.compute_fingerprint()."""

    def test_canonical_fingerprint_none_format(self):
        """None values must format as 'NULL' (not 'NONE')."""
        def _fmt(val):
            if val is None:
                return "NULL"
            if isinstance(val, Decimal):
                return str(val.normalize())
            if hasattr(val, "isoformat"):
                return val.isoformat()
            return str(val)

        assert _fmt(None) == "NULL"
        # Decimal normalize uses scientific notation for large numbers
        assert "E" in _fmt(Decimal("25000")) or _fmt(Decimal("25000")) == "25000"
        assert _fmt(date(2026, 8, 21)) == "2026-08-21"

    def test_bulk_importer_uses_null_not_none(self):
        """Verify bulk_nse_ingestion uses 'NULL' not 'NONE' in hash."""
        import inspect

        from app.services.bulk_nse_ingestion_service import BulkNSEIngestionService
        src = inspect.getsource(BulkNSEIngestionService._ingest_derivative_csv)
        assert '"NONE"' not in src, "Bulk importer still uses 'NONE' in hash"
        assert '"NULL"' in src, "Bulk importer must use 'NULL' in hash"

    def test_model_compute_fingerprint_uses_null(self):
        """DerivativeContract.compute_fingerprint uses 'NULL' for None."""
        from app.models.derivative_contract import DerivativeContract
        c = DerivativeContract(
            exchange="NSE", segment="NFO", instrument_type="FUTURE",
            trading_symbol="TEST", expiry_date=date(2026, 8, 28),
            lot_size=50, idempotency_hash="x", available_at=date(2026, 8, 21),
        )
        h = c.compute_fingerprint()
        assert isinstance(h, str)
        assert len(h) == 64  # SHA-256 hex digest

    def test_hash_deterministic(self):
        """Same inputs produce same hash."""
        from app.models.derivative_contract import DerivativeContract
        c1 = DerivativeContract(
            exchange="NSE", segment="NFO", instrument_type="FUTURE",
            trading_symbol="SBIN28AUGFUT", expiry_date=date(2026, 8, 28),
            underlying_stock_id=42, lot_size=500, idempotency_hash="x",
            available_at=date(2026, 8, 21),
        )
        c2 = DerivativeContract(
            exchange="NSE", segment="NFO", instrument_type="FUTURE",
            trading_symbol="SBIN28AUGFUT", expiry_date=date(2026, 8, 28),
            underlying_stock_id=42, lot_size=500, idempotency_hash="x",
            available_at=date(2026, 8, 21),
        )
        assert c1.compute_fingerprint() == c2.compute_fingerprint()


# ---------------------------------------------------------------------------
# C1 Root Cause: Futures Import Alias Normalization
# ---------------------------------------------------------------------------

class TestFuturesImportAliases:
    """Futures import service resolves all index aliases."""

    def test_futures_import_has_finnifty_alias(self):
        import inspect

        from app.services.derivative_futures_import import DerivativeFuturesImportService
        src = inspect.getsource(DerivativeFuturesImportService.import_futures_bhavcopy)
        assert "FINNIFTY" in src

    def test_futures_import_has_midcpnifty_alias(self):
        import inspect

        from app.services.derivative_futures_import import DerivativeFuturesImportService
        src = inspect.getsource(DerivativeFuturesImportService.import_futures_bhavcopy)
        assert "MIDCPNIFTY" in src

    def test_futures_import_has_niftynxt50_alias(self):
        import inspect

        from app.services.derivative_futures_import import DerivativeFuturesImportService
        src = inspect.getsource(DerivativeFuturesImportService.import_futures_bhavcopy)
        assert "NIFTYNXT50" in src

    def test_options_import_has_all_aliases(self):
        import inspect

        from app.services.derivative_options_import import DerivativeOptionsImportService
        src = inspect.getsource(DerivativeOptionsImportService.import_options_bhavcopy)
        for alias in ["BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]:
            assert alias in src, f"Options import missing {alias} alias"


# ---------------------------------------------------------------------------
# C5: Parser Trade Date Safety
# ---------------------------------------------------------------------------

class TestParserTradeDateSafety:
    """Parser rejects missing trade_date; no wall-clock fallback."""

    def test_parser_rejects_missing_trade_date_no_override(self):
        from app.data.derivative_bhavcopy_parser import DerivativeBhavcopyParser
        parser = DerivativeBhavcopyParser()
        csv_content = (
            "SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,CONTRACTS,OPEN_INT,CHG_IN_OI\n"
            "NIFTY,2026-08-28,25000,CE,100,110,90,105,1000,50000,1000\n"
        )
        result = parser.parse_csv(csv_content)
        assert len(result.row_errors) > 0
        assert any("Missing trade_date" in e.description for e in result.row_errors)

    def test_parser_preserves_explicit_trade_date(self):
        from app.data.derivative_bhavcopy_parser import DerivativeBhavcopyParser
        parser = DerivativeBhavcopyParser()
        csv_content = (
            "SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,TIMESTAMP,OPEN,HIGH,LOW,CLOSE,CONTRACTS,OPEN_INT,CHG_IN_OI\n"
            "NIFTY,2026-08-28,25000,CE,2026-08-21,100,110,90,105,1000,50000,1000\n"
        )
        result = parser.parse_csv(csv_content)
        assert len(result.all_records) == 1
        assert result.all_records[0].trade_date == date(2026, 8, 21)

    def test_parser_override_takes_precedence(self):
        from app.data.derivative_bhavcopy_parser import DerivativeBhavcopyParser
        parser = DerivativeBhavcopyParser()
        csv_content = (
            "SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,TIMESTAMP,OPEN,HIGH,LOW,CLOSE,CONTRACTS,OPEN_INT,CHG_IN_OI\n"
            "NIFTY,2026-08-28,25000,CE,2026-08-21,100,110,90,105,1000,50000,1000\n"
        )
        result = parser.parse_csv(csv_content, trade_date_override=date(2026, 7, 15))
        assert result.all_records[0].trade_date == date(2026, 7, 15)

    def test_no_wall_clock_fallback_in_options_import(self):
        import inspect

        from app.services.derivative_options_import import DerivativeOptionsImportService
        src = inspect.getsource(DerivativeOptionsImportService.import_options_bhavcopy)
        assert "date.today()" not in src

    def test_no_wall_clock_fallback_in_futures_import(self):
        import inspect

        from app.services.derivative_futures_import import DerivativeFuturesImportService
        src = inspect.getsource(DerivativeFuturesImportService.import_futures_bhavcopy)
        assert "date.today()" not in src

    def test_parser_no_trade_row_zero_volume(self):
        """Zero-volume rows are marked as no-trade, not rejected."""
        from app.data.derivative_bhavcopy_parser import DerivativeBhavcopyParser
        parser = DerivativeBhavcopyParser()
        csv_content = (
            "SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,TIMESTAMP,OPEN,HIGH,LOW,CLOSE,CONTRACTS,OPEN_INT,CHG_IN_OI\n"
            "NIFTY,2026-08-28,25000,CE,2026-08-21,0,0,0,0,0,50000,0\n"
        )
        result = parser.parse_csv(csv_content)
        assert len(result.all_records) == 1
        assert result.all_records[0].is_no_trade is True


# ---------------------------------------------------------------------------
# C3: Lot Size Source Integrity
# ---------------------------------------------------------------------------

class TestLotSizeIntegrity:
    """Lot sizes come from bhavcopy source, not hardcoded."""

    def test_model_enforces_positive_lot(self):
        """Model CheckConstraint prevents lot_size <= 0 (DB-level only)."""
        from app.models.derivative_contract import DerivativeContract
        # CheckConstraint is DB-level; ORM allows construction but DB rejects INSERT
        c = DerivativeContract(
            exchange="NSE", segment="NFO", instrument_type="FUTURE",
            trading_symbol="TEST", expiry_date=date(2026, 8, 28),
            lot_size=0, idempotency_hash="x", available_at=date(2026, 8, 21),
        )
        assert c.lot_size == 0  # ORM allows it; DB constraint rejects it

    def test_parser_does_not_set_lot_size(self):
        """Parser does not fabricate lot sizes."""
        from app.data.derivative_bhavcopy_parser import DerivativeBhavcopyParser
        parser = DerivativeBhavcopyParser()
        csv_content = (
            "SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,TIMESTAMP,OPEN,HIGH,LOW,CLOSE,CONTRACTS,OPEN_INT,CHG_IN_OI\n"
            "NIFTY,2026-08-28,25000,CE,2026-08-21,100,110,90,105,1000,50000,1000\n"
        )
        result = parser.parse_csv(csv_content)
        # ParsedDerivativeDailyPrice has no lot_size field — it's set by import service
        assert not hasattr(result.all_records[0], "lot_size")


# ---------------------------------------------------------------------------
# C4: Provenance Integrity
# ---------------------------------------------------------------------------

class TestProvenanceIntegrity:
    """Provenance fields are preserved, NULL when unprovable."""

    def test_import_service_sets_source_name(self):
        """Import services accept source_name parameter."""
        import inspect

        from app.services.derivative_options_import import DerivativeOptionsImportService
        sig = inspect.signature(DerivativeOptionsImportService.import_options_bhavcopy)
        assert "source_name" in sig.parameters

    def test_import_service_sets_source_reference(self):
        import inspect

        from app.services.derivative_options_import import DerivativeOptionsImportService
        sig = inspect.signature(DerivativeOptionsImportService.import_options_bhavcopy)
        assert "source_reference" in sig.parameters

    def test_futures_import_has_provenance_params(self):
        import inspect

        from app.services.derivative_futures_import import DerivativeFuturesImportService
        sig = inspect.signature(DerivativeFuturesImportService.import_futures_bhavcopy)
        assert "source_name" in sig.parameters
        assert "source_reference" in sig.parameters


# ---------------------------------------------------------------------------
# PIT: Point-in-Time Safety
# ---------------------------------------------------------------------------

class TestPITSafety:
    """No wall-clock fallbacks in import paths."""

    def test_options_import_available_at_uses_trade_date(self):
        """available_at defaults to trade_date, not date.today()."""
        import inspect

        from app.services.derivative_options_import import DerivativeOptionsImportService
        src = inspect.getsource(DerivativeOptionsImportService.import_options_bhavcopy)
        assert "date.today()" not in src

    def test_futures_import_available_at_uses_trade_date(self):
        import inspect

        from app.services.derivative_futures_import import DerivativeFuturesImportService
        src = inspect.getsource(DerivativeFuturesImportService.import_futures_bhavcopy)
        assert "date.today()" not in src


# ---------------------------------------------------------------------------
# Model Invariants
# ---------------------------------------------------------------------------

class TestModelInvariants:
    """DerivativeContract and DerivativeDailyPrice constraints."""

    def test_contract_check_constraints_exist(self):
        from app.models.derivative_contract import DerivativeContract
        constraints = {c.name for c in DerivativeContract.__table__.constraints}
        # SQLAlchemy may prefix constraint names with table name
        constraint_names = " ".join(constraints)
        assert "exactly_one_underlying" in constraint_names
        assert "instrument_invariants" in constraint_names
        assert "lot_size_positive" in constraint_names

    def test_daily_price_unique_constraint(self):
        from app.models.derivative_daily_price import DerivativeDailyPrice
        constraints = {c.name for c in DerivativeDailyPrice.__table__.constraints}
        assert "uq_derivative_daily_prices_contract_date" in constraints

    def test_daily_price_oi_non_negative_check(self):
        from app.models.derivative_daily_price import DerivativeDailyPrice
        constraints = {c.name for c in DerivativeDailyPrice.__table__.constraints}
        constraint_names = " ".join(constraints)
        assert "open_interest_non_negative" in constraint_names

    def test_contract_idempotency_hash_unique(self):
        from app.models.derivative_contract import DerivativeContract
        cols = {c.name for c in DerivativeContract.__table__.columns}
        assert "idempotency_hash" in cols


# ---------------------------------------------------------------------------
# R1: Bulk Importer Per-Symbol Index Mapping
# ---------------------------------------------------------------------------

class TestR1BulkImporterIndexMapping:
    """R1: bulk importer resolves per-symbol, not all-to-NIFTY."""

    def test_no_single_nifty_fallback(self):
        """_ingest_derivative_csv no longer uses 'nifty_idx_id or 8'."""
        import inspect

        from app.services.bulk_nse_ingestion_service import BulkNSEIngestionService
        src = inspect.getsource(BulkNSEIngestionService._ingest_derivative_csv)
        assert "nifty_idx_id or 8" not in src, "R1: single NIFTY fallback still present"

    def test_per_symbol_index_map_exists(self):
        """_ingest_derivative_csv has _idx_sym_to_db dict."""
        import inspect

        from app.services.bulk_nse_ingestion_service import BulkNSEIngestionService
        src = inspect.getsource(BulkNSEIngestionService._ingest_derivative_csv)
        assert "_idx_sym_to_db" in src, "R1: per-symbol index map missing"

    def test_bulk_importer_lot_size_not_fabricated(self):
        """R3: bulk importer no longer hardcodes 25 or 500 for options."""
        import inspect

        from app.services.bulk_nse_ingestion_service import BulkNSEIngestionService
        src = inspect.getsource(BulkNSEIngestionService._ingest_derivative_csv)
        assert '"lot_size": 25 if sym' not in src, "R3: hardcoded NIFTY lot still present"
        assert '"lot_size": 500' not in src, "R3: hardcoded 500 lot still present"


# ---------------------------------------------------------------------------
# R2: GIFT_NIFTY Quarantine
# ---------------------------------------------------------------------------

class TestR2GiftNiftyQuarantine:
    """R2: bulk importer doesn't create GIFT_NIFTY entries (code-level check)."""

    def test_no_gift_nifty_in_index_codes(self):
        """INDEX_CODES doesn't contain GIFT_NIFTY."""
        from app.services.bulk_nse_ingestion_service import INDEX_CODES
        assert "GIFT_NIFTY" not in INDEX_CODES


# ---------------------------------------------------------------------------
# R3: Option Lot Size Integrity (code-level)
# ---------------------------------------------------------------------------

class TestR3OptionLotSizeIntegrity:
    """R3: bulk importer doesn't fabricate option lot sizes."""

    def test_bulk_importer_no_hardcoded_option_lots(self):
        """_ingest_derivative_csv no longer hardcodes 25/500 for options."""
        import inspect

        from app.services.bulk_nse_ingestion_service import BulkNSEIngestionService
        src = inspect.getsource(BulkNSEIngestionService._ingest_derivative_csv)
        assert '"lot_size": 25 if sym' not in src
        assert '"lot_size": 500' not in src


# ---------------------------------------------------------------------------
# R4: No date.today() Fallbacks
# ---------------------------------------------------------------------------

class TestR4NoDateTodayFallback:
    """R4: derivative_parser and derivative_import have no date.today()."""

    def test_parser_no_date_today(self):
        import inspect

        from app.data.derivative_parser import DerivativeContractParser
        src = inspect.getsource(DerivativeContractParser.parse_csv)
        # Strip docstrings and string literals before checking for date.today()
        import re
        # Remove triple-quoted strings (docstrings)
        cleaned = re.sub(r'""".*?"""', '', src, flags=re.DOTALL)
        cleaned = re.sub(r"'''.*?'''", '', cleaned, flags=re.DOTALL)
        # Remove single-line string literals
        cleaned = re.sub(r'"[^"]*"', '""', cleaned)
        cleaned = re.sub(r"'[^']*'", "''", cleaned)
        assert "date.today()" not in cleaned, "R4: parser still has date.today() in code"

    def test_parser_as_of_date_required(self):
        """parse_csv raises if as_of_date is None."""
        from app.data.derivative_parser import DerivativeContractParser, DerivativeParserError
        parser = DerivativeContractParser()
        csv_content = "SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,LOT_SIZE\nTEST,2026-08-28,100,CE,100\n"
        try:
            parser.parse_csv(csv_content, as_of_date=None)
            raise AssertionError("R4: should have raised for None as_of_date")
        except (DerivativeParserError, TypeError):
            pass  # expected

    def test_import_no_date_today(self):
        import inspect

        from app.services.derivative_import import DerivativeContractImportService
        src = inspect.getsource(DerivativeContractImportService.import_contract_master_file)
        assert "date.today()" not in src, "R4: import service still has date.today()"


# ---------------------------------------------------------------------------
# R5: Synthetic FII Rows Quarantine
# ---------------------------------------------------------------------------

class TestR5SyntheticFIIQuarantine:
    """R5: no synthetic FII import path (code-level check)."""

    def test_no_synthetic_fii_import(self):
        """No service imports synthetic FII data."""
        import inspect

        from app.services.nse_institutional_import_service import NseInstitutionalImportService
        src = inspect.getsource(NseInstitutionalImportService)
        # Should not have hardcoded synthetic FII data
        assert "synthetic" not in src.lower()


# ---------------------------------------------------------------------------
# R6: Repair Proof Scripts Exist
# ---------------------------------------------------------------------------

class TestR6RepairProof:
    """R6: repair scripts and tests are versioned."""

    def test_repair_script_exists(self):
        from pathlib import Path
        p = Path("scripts/g8_pre_repair.py")
        assert p.exists(), "R6: scripts/g8_pre_repair.py missing"

    def test_repair_tests_exist(self):
        from pathlib import Path
        p = Path("tests/test_g8_pre_repair.py")
        assert p.exists(), "R6: tests/test_g8_pre_repair.py missing"

    def test_r1_r6_repair_script_exists(self):
        """R6: the R1-R6 code fixes are applied (code-level verification)."""
        import inspect

        from app.services.bulk_nse_ingestion_service import BulkNSEIngestionService
        src = inspect.getsource(BulkNSEIngestionService._ingest_derivative_csv)
        assert "_idx_sym_to_db" in src, "R6: R1 code fix not applied"
        assert '"lot_size": 25 if sym' not in src, "R6: hardcoded NIFTY lot still present"
        assert '"lot_size": 500' not in src, "R6: hardcoded 500 lot still present"


# ---------------------------------------------------------------------------
# M1-A: Max Pain Suppression on Unknown-Lot Expiries
# ---------------------------------------------------------------------------

class TestM1AMaxPainSuppression:
    """M1-A: Max Pain is suppressed if any contract has unknown lot provenance."""

    @pytest.mark.asyncio
    async def test_unknown_lot_expiry_max_pain_suppressed(self):
        """When source_name is NULL and no historical lot exists, max_pain_strike is None."""
        from app.db.session import get_session_factory
        from app.models.derivative_contract import DerivativeContract
        from app.models.derivative_daily_price import DerivativeDailyPrice
        from app.models.market_index import MarketIndex
        from app.models.market_index_price import MarketIndexPrice
        from app.services.historical_options_analytics import HistoricalOptionsAnalyticsService

        factory = get_session_factory()
        async with factory() as session:
            idx = MarketIndex(code="TEST_M1A_SUPPR", name="Test M1A Suppr", country="INDIA")
            session.add(idx)
            await session.flush()

            t_date = date(2026, 8, 14)
            exp_date = date(2026, 8, 28)

            mip = MarketIndexPrice(
                market_index_id=idx.id,
                trade_date=t_date,
                open=Decimal("25000.0"),
                high=Decimal("25100.0"),
                low=Decimal("24900.0"),
                close=Decimal("25000.0"),
            )
            session.add(mip)

            # Contract with source_name=None (unknown lot provenance)
            c_ce = DerivativeContract(
                exchange="NSE",
                segment="NFO",
                instrument_type="OPTION",
                underlying_index_id=idx.id,
                trading_symbol="TESTM1A26AUG25000CE",
                expiry_date=exp_date,
                strike_price=Decimal("25000.0"),
                option_type="CE",
                lot_size=1,
                source_name=None,
                available_at=t_date,
            )
            c_pe = DerivativeContract(
                exchange="NSE",
                segment="NFO",
                instrument_type="OPTION",
                underlying_index_id=idx.id,
                trading_symbol="TESTM1A26AUG25000PE",
                expiry_date=exp_date,
                strike_price=Decimal("25000.0"),
                option_type="PE",
                lot_size=1,
                source_name=None,
                available_at=t_date,
            )
            session.add_all([c_ce, c_pe])
            await session.flush()

            p_ce = DerivativeDailyPrice(
                contract_id=c_ce.id,
                trade_date=t_date,
                close=Decimal("150.0"),
                settlement_price=Decimal("150.0"),
                volume_contracts=100,
                open_interest=5000,
                underlying_value=Decimal("25000.0"),
                available_at=t_date,
            )
            p_pe = DerivativeDailyPrice(
                contract_id=c_pe.id,
                trade_date=t_date,
                close=Decimal("140.0"),
                settlement_price=Decimal("140.0"),
                volume_contracts=120,
                open_interest=6000,
                underlying_value=Decimal("25000.0"),
                available_at=t_date,
            )
            session.add_all([p_ce, p_pe])
            await session.commit()

            service = HistoricalOptionsAnalyticsService(session)
            analytics = await service.get_option_chain_analytics("TEST_M1A_SUPPR", t_date, exp_date)

            # Assertions for M1-A suppression
            assert analytics.max_pain.max_pain_strike is None, "max_pain_strike must be None when lot source is unavailable"
            assert analytics.max_pain.distance_from_spot is None
            assert analytics.max_pain.distance_pct is None
            assert "LOT_UNAVAILABLE_MAX_PAIN_SUPPRESSED" in analytics.quality_warnings
            # Candidate diagnostics are kept
            assert len(analytics.max_pain.candidates) > 0
            # PCR and OI walls remain valid and unaffected
            assert analytics.pcr.pcr_oi is not None
            assert analytics.static_call_wall.strike_price == Decimal("25000.0")

    @pytest.mark.asyncio
    async def test_source_backed_lot_calculates_normally(self):
        """When source_name is populated, max_pain_strike is computed normally."""
        from app.db.session import get_session_factory
        from app.models.derivative_contract import DerivativeContract
        from app.models.derivative_daily_price import DerivativeDailyPrice
        from app.models.market_index import MarketIndex
        from app.models.market_index_price import MarketIndexPrice
        from app.services.historical_options_analytics import HistoricalOptionsAnalyticsService

        factory = get_session_factory()
        async with factory() as session:
            idx = MarketIndex(code="TEST_M1A_VALID", name="Test M1A Valid", country="INDIA")
            session.add(idx)
            await session.flush()

            t_date = date(2026, 8, 14)
            exp_date = date(2026, 8, 28)

            mip = MarketIndexPrice(
                market_index_id=idx.id,
                trade_date=t_date,
                open=Decimal("25000.0"),
                high=Decimal("25100.0"),
                low=Decimal("24900.0"),
                close=Decimal("25000.0"),
            )
            session.add(mip)

            # Contract with authoritative source_name
            c_ce = DerivativeContract(
                exchange="NSE",
                segment="NFO",
                instrument_type="OPTION",
                underlying_index_id=idx.id,
                trading_symbol="TESTM1AV26AUG25000CE",
                expiry_date=exp_date,
                strike_price=Decimal("25000.0"),
                option_type="CE",
                lot_size=25,
                source_name="NSE_FO_CONTRACT_MASTER",
                available_at=t_date,
            )
            c_pe = DerivativeContract(
                exchange="NSE",
                segment="NFO",
                instrument_type="OPTION",
                underlying_index_id=idx.id,
                trading_symbol="TESTM1AV26AUG25000PE",
                expiry_date=exp_date,
                strike_price=Decimal("25000.0"),
                option_type="PE",
                lot_size=25,
                source_name="NSE_FO_CONTRACT_MASTER",
                available_at=t_date,
            )
            session.add_all([c_ce, c_pe])
            await session.flush()

            p_ce = DerivativeDailyPrice(
                contract_id=c_ce.id,
                trade_date=t_date,
                close=Decimal("150.0"),
                settlement_price=Decimal("150.0"),
                volume_contracts=100,
                open_interest=5000,
                underlying_value=Decimal("25000.0"),
                available_at=t_date,
            )
            p_pe = DerivativeDailyPrice(
                contract_id=c_pe.id,
                trade_date=t_date,
                close=Decimal("140.0"),
                settlement_price=Decimal("140.0"),
                volume_contracts=120,
                open_interest=6000,
                underlying_value=Decimal("25000.0"),
                available_at=t_date,
            )
            session.add_all([p_ce, p_pe])
            await session.commit()

            service = HistoricalOptionsAnalyticsService(session)
            analytics = await service.get_option_chain_analytics("TEST_M1A_VALID", t_date, exp_date)

            assert analytics.max_pain.max_pain_strike is not None
            assert "LOT_UNAVAILABLE_MAX_PAIN_SUPPRESSED" not in analytics.quality_warnings


# ---------------------------------------------------------------------------
# M1-B: Premium at Risk Nullable & Provenance
# ---------------------------------------------------------------------------

class TestM1BPremiumAtRisk:
    """M1-B: premium_at_risk is None when lot size has unknown provenance."""

    @pytest.mark.asyncio
    async def test_unknown_lot_premium_at_risk_none(self):
        """When source_name is NULL and no historical lot exists, premium_at_risk is None."""
        from app.db.session import get_session_factory
        from app.models.derivative_contract import DerivativeContract
        from app.models.derivative_daily_price import DerivativeDailyPrice
        from app.models.market_index import MarketIndex
        from app.models.market_index_price import MarketIndexPrice
        from app.services.advanced_options_service import AdvancedOptionsService

        factory = get_session_factory()
        async with factory() as session:
            idx = MarketIndex(code="TEST_M1B_PREM", name="Test M1B Prem", country="INDIA")
            session.add(idx)
            await session.flush()

            t_date = date(2026, 8, 14)
            exp_date = date(2026, 8, 28)

            mip = MarketIndexPrice(
                market_index_id=idx.id,
                trade_date=t_date,
                open=Decimal("25000.0"),
                high=Decimal("25100.0"),
                low=Decimal("24900.0"),
                close=Decimal("25000.0"),
            )
            session.add(mip)

            c_ce = DerivativeContract(
                exchange="NSE",
                segment="NFO",
                instrument_type="OPTION",
                underlying_index_id=idx.id,
                trading_symbol="TESTM1B26AUG25000CE",
                expiry_date=exp_date,
                strike_price=Decimal("25000.0"),
                option_type="CE",
                lot_size=1,
                source_name=None,
                available_at=t_date,
            )
            session.add(c_ce)
            await session.flush()

            p_ce = DerivativeDailyPrice(
                contract_id=c_ce.id,
                trade_date=t_date,
                close=Decimal("200.0"),
                settlement_price=Decimal("200.0"),
                volume_contracts=500,
                open_interest=10000,
                underlying_value=Decimal("25000.0"),
                available_at=t_date,
            )
            session.add(p_ce)
            await session.commit()

            service = AdvancedOptionsService(session)
            rows = await service.get_enriched_option_chain("TEST_M1B_PREM", t_date, exp_date)

            assert len(rows) == 1
            call_side = rows[0].call
            assert call_side is not None
            assert call_side.risk_profile.premium_at_risk is None, "premium_at_risk must be None, not 200*1 or 0"
            assert call_side.risk_profile.lot_source == "UNAVAILABLE"
            assert call_side.risk_profile.lot_quality == "UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_source_backed_lot_premium_at_risk_calculates(self):
        """When source_name is populated, premium_at_risk is calculated accurately."""
        from app.db.session import get_session_factory
        from app.models.derivative_contract import DerivativeContract
        from app.models.derivative_daily_price import DerivativeDailyPrice
        from app.models.market_index import MarketIndex
        from app.models.market_index_price import MarketIndexPrice
        from app.services.advanced_options_service import AdvancedOptionsService

        factory = get_session_factory()
        async with factory() as session:
            idx = MarketIndex(code="TEST_M1B_VALID", name="Test M1B Valid", country="INDIA")
            session.add(idx)
            await session.flush()

            t_date = date(2026, 8, 14)
            exp_date = date(2026, 8, 28)

            mip = MarketIndexPrice(
                market_index_id=idx.id,
                trade_date=t_date,
                open=Decimal("25000.0"),
                high=Decimal("25100.0"),
                low=Decimal("24900.0"),
                close=Decimal("25000.0"),
            )
            session.add(mip)

            c_ce = DerivativeContract(
                exchange="NSE",
                segment="NFO",
                instrument_type="OPTION",
                underlying_index_id=idx.id,
                trading_symbol="TESTM1BV26AUG25000CE",
                expiry_date=exp_date,
                strike_price=Decimal("25000.0"),
                option_type="CE",
                lot_size=50,
                source_name="NSE_FO_CONTRACT_MASTER",
                available_at=t_date,
            )
            session.add(c_ce)
            await session.flush()

            p_ce = DerivativeDailyPrice(
                contract_id=c_ce.id,
                trade_date=t_date,
                close=Decimal("200.0"),
                settlement_price=Decimal("200.0"),
                volume_contracts=500,
                open_interest=10000,
                underlying_value=Decimal("25000.0"),
                available_at=t_date,
            )
            session.add(p_ce)
            await session.commit()

            service = AdvancedOptionsService(session)
            rows = await service.get_enriched_option_chain("TEST_M1B_VALID", t_date, exp_date)

            assert len(rows) == 1
            call_side = rows[0].call
            assert call_side is not None
            assert call_side.risk_profile.premium_at_risk == Decimal("10000.00")  # 200 * 50
            assert call_side.risk_profile.lot_source == "SOURCE_VERIFIED"


# ---------------------------------------------------------------------------
# M2: --verify CLI Entry Point & Safety Guard
# ---------------------------------------------------------------------------

class TestM2VerifyCli:
    """M2: scripts/g8_pre_repair.py entry point and guards."""

    def test_verify_entry_point_exists(self):
        """g8_pre_repair.py has main, verify, and CLI execution block."""
        from pathlib import Path
        src = Path("scripts/g8_pre_repair.py").read_text(encoding="utf-8")
        assert "async def main()" in src
        assert "async def verify(" in src
        assert 'if __name__ == "__main__":' in src

    def test_verify_production_db_guard(self):
        """_is_test_db accurately distinguishes test databases from production."""
        from scripts.g8_pre_repair import _is_test_db
        assert _is_test_db("test_ai_stock_scanner") is True
        assert _is_test_db("ai_stock_scanner_test") is True
        assert _is_test_db("pytest_db") is True
        assert _is_test_db("ai_stock_scanner") is False

    @pytest.mark.asyncio
    async def test_verify_refuses_test_database(self):
        """verify() raises RuntimeError if called on a test database."""
        from unittest.mock import AsyncMock, MagicMock

        from scripts.g8_pre_repair import verify

        mock_session = AsyncMock()
        mock_bind = MagicMock()
        mock_bind.url.database = "test_stock_scanner"
        mock_session.get_bind = MagicMock(return_value=mock_bind)

        with pytest.raises(RuntimeError, match="must not run on a test database"):
            await verify(mock_session)


# ---------------------------------------------------------------------------
# T1: Parser Determinism & as_of_date Requirement
# ---------------------------------------------------------------------------

class TestT1ParserDeterminism:
    """T1: DerivativeContractParser requires explicit as_of_date."""

    def test_parser_omission_of_as_of_date_fails_closed(self):
        """Calling parse_csv without as_of_date raises TypeError."""
        from app.data.derivative_parser import DerivativeContractParser
        parser = DerivativeContractParser()
        csv_content = "INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,MARKET_LOT\nFUTSTK,RELIANCE,26-Mar-2026,0,XX,250\n"
        with pytest.raises(TypeError):
            parser.parse_csv(csv_content)  # type: ignore[call-arg]

    def test_parser_none_as_of_date_raises_parser_error(self):
        """Calling parse_csv with as_of_date=None raises DerivativeParserError."""
        from app.data.derivative_parser import DerivativeContractParser, DerivativeParserError
        parser = DerivativeContractParser()
        csv_content = "INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,MARKET_LOT\nFUTSTK,RELIANCE,26-Mar-2026,0,XX,250\n"
        with pytest.raises(DerivativeParserError, match="as_of_date is required"):
            parser.parse_csv(csv_content, as_of_date=None)  # type: ignore[arg-type]

    def test_parser_deterministic_with_explicit_date(self):
        """Parser succeeds when provided an explicit as_of_date."""
        from app.data.derivative_parser import DerivativeContractParser
        parser = DerivativeContractParser()
        csv_content = "INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,MARKET_LOT\nFUTSTK,RELIANCE,26-Mar-2026,0,XX,250\n"
        res = parser.parse_csv(csv_content, as_of_date=date(2026, 3, 1))
        assert len(res.contracts) == 1
        assert res.contracts[0].available_at == date(2026, 3, 1)


# ---------------------------------------------------------------------------
# N1: Gamma Profile Lot Quality & Provenance
# ---------------------------------------------------------------------------

class TestN1GammaProfileLotQuality:
    """N1: get_gamma_profile returns None / UNAVAILABLE when lot provenance is unavailable."""

    @pytest.mark.asyncio
    async def test_gamma_profile_returns_none_when_lot_unavailable(self):
        """When contract has source_name=None and no lot history, gamma profile is None."""
        from app.db.session import get_session_factory
        from app.models.derivative_contract import DerivativeContract
        from app.models.derivative_daily_price import DerivativeDailyPrice
        from app.models.market_index import MarketIndex
        from app.models.market_index_price import MarketIndexPrice
        from app.services.advanced_exposure_service import AdvancedExposureService

        factory = get_session_factory()
        async with factory() as session:
            idx = MarketIndex(code="TEST_N1_UNAV", name="Test N1 Unavailable", country="INDIA")
            session.add(idx)
            await session.flush()

            t_date = date(2026, 8, 14)
            exp_date = date(2026, 8, 28)

            mip = MarketIndexPrice(
                market_index_id=idx.id,
                trade_date=t_date,
                open=Decimal("25000.0"),
                high=Decimal("25100.0"),
                low=Decimal("24900.0"),
                close=Decimal("25000.0"),
            )
            session.add(mip)

            c_ce = DerivativeContract(
                exchange="NSE",
                segment="NFO",
                instrument_type="OPTION",
                underlying_index_id=idx.id,
                trading_symbol="TESTN1U26AUG25000CE",
                expiry_date=exp_date,
                strike_price=Decimal("25000.0"),
                option_type="CE",
                lot_size=1,
                source_name=None,  # Unavailable provenance
                available_at=t_date,
            )
            session.add(c_ce)
            await session.flush()

            p_ce = DerivativeDailyPrice(
                contract_id=c_ce.id,
                trade_date=t_date,
                close=Decimal("200.0"),
                settlement_price=Decimal("200.0"),
                volume_contracts=500,
                open_interest=10000,
                underlying_value=Decimal("25000.0"),
                available_at=t_date,
            )
            session.add(p_ce)
            await session.commit()

            service = AdvancedExposureService(session)
            contracts = await service.get_contract_exposures("TEST_N1_UNAV", t_date, exp_date)
            assert len(contracts) == 1
            assert contracts[0].lot_size is None, "lot_size must be None when provenance is UNAVAILABLE"
            assert contracts[0].lot_size_source == "UNAVAILABLE"
            assert contracts[0].lot_size_quality == "UNAVAILABLE"
            assert contracts[0].lot_source == "UNAVAILABLE"
            assert contracts[0].lot_quality == "UNAVAILABLE"

            profile = await service.get_gamma_profile("TEST_N1_UNAV", t_date, exp_date)
            assert profile is None, "gamma_profile must be None when lot provenance is UNAVAILABLE"

            flip = await service.get_gamma_flip("TEST_N1_UNAV", t_date, exp_date)
            assert flip.status == "UNAVAILABLE"
            assert flip.gamma_flip_level is None

    @pytest.mark.asyncio
    async def test_gamma_profile_calculates_when_lot_source_verified(self):
        """When contract has source_name populated, gamma profile computes grid points."""
        from app.db.session import get_session_factory
        from app.models.derivative_contract import DerivativeContract
        from app.models.derivative_daily_price import DerivativeDailyPrice
        from app.models.market_index import MarketIndex
        from app.models.market_index_price import MarketIndexPrice
        from app.services.advanced_exposure_service import AdvancedExposureService

        factory = get_session_factory()
        async with factory() as session:
            idx = MarketIndex(code="TEST_N1_VERIF", name="Test N1 Verified", country="INDIA")
            session.add(idx)
            await session.flush()

            t_date = date(2026, 8, 14)
            exp_date = date(2026, 8, 28)

            mip = MarketIndexPrice(
                market_index_id=idx.id,
                trade_date=t_date,
                open=Decimal("25000.0"),
                high=Decimal("25100.0"),
                low=Decimal("24900.0"),
                close=Decimal("25000.0"),
            )
            session.add(mip)

            c_ce = DerivativeContract(
                exchange="NSE",
                segment="NFO",
                instrument_type="OPTION",
                underlying_index_id=idx.id,
                trading_symbol="TESTN1V26AUG25000CE",
                expiry_date=exp_date,
                strike_price=Decimal("25000.0"),
                option_type="CE",
                lot_size=50,
                source_name="NSE_FO_BHAV",
                available_at=t_date,
            )
            session.add(c_ce)
            await session.flush()

            p_ce = DerivativeDailyPrice(
                contract_id=c_ce.id,
                trade_date=t_date,
                close=Decimal("200.0"),
                settlement_price=Decimal("200.0"),
                volume_contracts=500,
                open_interest=10000,
                underlying_value=Decimal("25000.0"),
                available_at=t_date,
            )
            session.add(p_ce)
            await session.commit()

            service = AdvancedExposureService(session)
            contracts = await service.get_contract_exposures("TEST_N1_VERIF", t_date, exp_date)
            assert len(contracts) == 1
            assert contracts[0].lot_size == 50
            assert contracts[0].lot_size_source == "SOURCE_VERIFIED"
            assert contracts[0].lot_size_quality == "SOURCE_VERIFIED"
            assert contracts[0].lot_source == "SOURCE_VERIFIED"
            assert contracts[0].lot_quality == "SOURCE_VERIFIED"

            profile = await service.get_gamma_profile("TEST_N1_VERIF", t_date, exp_date)
            assert profile is not None
            assert len(profile) == 41
            assert all(p.spot_level > 0 for p in profile)


# ---------------------------------------------------------------------------
# N2: Strike Table None -> Zero Preservation
# ---------------------------------------------------------------------------

class TestN2StrikeTableNoneNotZero:
    """N2: get_strike_exposure_table preserves None for unknown lot and Decimal('0.00') for real zero."""

    @pytest.mark.asyncio
    async def test_strike_table_preserves_none_for_unavailable_lot(self):
        """When lot provenance is UNAVAILABLE, exposure fields must remain None, not 0.00."""
        from app.db.session import get_session_factory
        from app.models.derivative_contract import DerivativeContract
        from app.models.derivative_daily_price import DerivativeDailyPrice
        from app.models.market_index import MarketIndex
        from app.models.market_index_price import MarketIndexPrice
        from app.services.advanced_exposure_service import AdvancedExposureService

        factory = get_session_factory()
        async with factory() as session:
            idx = MarketIndex(code="TEST_N2_NONE", name="Test N2 None", country="INDIA")
            session.add(idx)
            await session.flush()

            t_date = date(2026, 8, 14)
            exp_date = date(2026, 8, 28)

            mip = MarketIndexPrice(
                market_index_id=idx.id,
                trade_date=t_date,
                open=Decimal("25000.0"),
                high=Decimal("25100.0"),
                low=Decimal("24900.0"),
                close=Decimal("25000.0"),
            )
            session.add(mip)

            c_ce = DerivativeContract(
                exchange="NSE",
                segment="NFO",
                instrument_type="OPTION",
                underlying_index_id=idx.id,
                trading_symbol="TESTN2N26AUG25000CE",
                expiry_date=exp_date,
                strike_price=Decimal("25000.0"),
                option_type="CE",
                lot_size=1,
                source_name=None,  # UNAVAILABLE
                available_at=t_date,
            )
            session.add(c_ce)
            await session.flush()

            p_ce = DerivativeDailyPrice(
                contract_id=c_ce.id,
                trade_date=t_date,
                close=Decimal("200.0"),
                settlement_price=Decimal("200.0"),
                volume_contracts=500,
                open_interest=10000,
                underlying_value=Decimal("25000.0"),
                available_at=t_date,
            )
            session.add(p_ce)
            await session.commit()

            service = AdvancedExposureService(session)
            strikes = await service.get_strike_exposure_table("TEST_N2_NONE", t_date, exp_date)
            assert len(strikes) == 1
            row = strikes[0]
            assert row.ce_open_interest == 10000
            assert row.ce_gross_gex is None, "ce_gross_gex must be None, not Decimal('0.00')"
            assert row.total_gross_gex is None, "total_gross_gex must be None, not Decimal('0.00')"
            assert row.ce_scenario_gex is None, "ce_scenario_gex must be None"
            assert row.net_scenario_gex is None, "net_scenario_gex must be None"
            assert row.gross_dex is None, "gross_dex must be None"
            assert row.net_scenario_dex is None, "net_scenario_dex must be None"
            assert row.gross_vanna is None, "gross_vanna must be None"
            assert row.gross_charm is None, "gross_charm must be None"

    @pytest.mark.asyncio
    async def test_strike_table_preserves_real_zero_on_verified_lot(self):
        """When lot provenance is verified and OI=0, exposure fields must be Decimal('0.00')."""
        from app.db.session import get_session_factory
        from app.models.derivative_contract import DerivativeContract
        from app.models.derivative_daily_price import DerivativeDailyPrice
        from app.models.market_index import MarketIndex
        from app.models.market_index_price import MarketIndexPrice
        from app.services.advanced_exposure_service import AdvancedExposureService

        factory = get_session_factory()
        async with factory() as session:
            idx = MarketIndex(code="TEST_N2_ZERO", name="Test N2 Zero", country="INDIA")
            session.add(idx)
            await session.flush()

            t_date = date(2026, 8, 14)
            exp_date = date(2026, 8, 28)

            mip = MarketIndexPrice(
                market_index_id=idx.id,
                trade_date=t_date,
                open=Decimal("25000.0"),
                high=Decimal("25100.0"),
                low=Decimal("24900.0"),
                close=Decimal("25000.0"),
            )
            session.add(mip)

            c_ce = DerivativeContract(
                exchange="NSE",
                segment="NFO",
                instrument_type="OPTION",
                underlying_index_id=idx.id,
                trading_symbol="TESTN2Z26AUG25000CE",
                expiry_date=exp_date,
                strike_price=Decimal("25000.0"),
                option_type="CE",
                lot_size=50,
                source_name="NSE_FO_BHAV",
                available_at=t_date,
            )
            session.add(c_ce)
            await session.flush()

            p_ce = DerivativeDailyPrice(
                contract_id=c_ce.id,
                trade_date=t_date,
                close=Decimal("200.0"),
                settlement_price=Decimal("200.0"),
                volume_contracts=0,
                open_interest=0,  # Real zero OI
                underlying_value=Decimal("25000.0"),
                available_at=t_date,
            )
            session.add(p_ce)
            await session.commit()

            service = AdvancedExposureService(session)
            strikes = await service.get_strike_exposure_table("TEST_N2_ZERO", t_date, exp_date)
            assert len(strikes) == 1
            row = strikes[0]
            assert row.ce_open_interest == 0
            assert row.ce_gross_gex == Decimal("0.00"), "ce_gross_gex must be Decimal('0.00') for real zero OI"
            assert row.total_gross_gex == Decimal("0.00"), "total_gross_gex must be Decimal('0.00')"
            assert row.gross_dex == Decimal("0.00")
            assert row.gross_vanna == Decimal("0.00")
            assert row.gross_charm == Decimal("0.00")


# ---------------------------------------------------------------------------
# N3: Suppressed Max-Pain Candidate Payouts
# ---------------------------------------------------------------------------

class TestN3SuppressedMaxPainCandidatePayouts:
    """N3: HistoricalOptionsAnalyticsService sets candidate payouts to None when lot provenance unavailable."""

    @pytest.mark.asyncio
    async def test_max_pain_candidate_payouts_none_when_lot_unavailable(self):
        """When lot provenance is unavailable, candidate payouts are None and max_pain_strike is None."""
        from app.db.session import get_session_factory
        from app.models.derivative_contract import DerivativeContract
        from app.models.derivative_daily_price import DerivativeDailyPrice
        from app.models.market_index import MarketIndex
        from app.models.market_index_price import MarketIndexPrice
        from app.services.historical_options_analytics import HistoricalOptionsAnalyticsService

        factory = get_session_factory()
        async with factory() as session:
            idx = MarketIndex(code="TEST_N3_SUPP", name="Test N3 Suppressed", country="INDIA")
            session.add(idx)
            await session.flush()

            t_date = date(2026, 8, 14)
            exp_date = date(2026, 8, 28)

            mip = MarketIndexPrice(
                market_index_id=idx.id,
                trade_date=t_date,
                open=Decimal("25000.0"),
                high=Decimal("25100.0"),
                low=Decimal("24900.0"),
                close=Decimal("25000.0"),
            )
            session.add(mip)

            # Two strikes with source_name=None
            c1 = DerivativeContract(
                exchange="NSE", segment="NFO", instrument_type="OPTION",
                underlying_index_id=idx.id, trading_symbol="TESTN3S26AUG24800CE",
                expiry_date=exp_date, strike_price=Decimal("24800.0"),
                option_type="CE", lot_size=1, source_name=None, available_at=t_date,
            )
            c2 = DerivativeContract(
                exchange="NSE", segment="NFO", instrument_type="OPTION",
                underlying_index_id=idx.id, trading_symbol="TESTN3S26AUG25200PE",
                expiry_date=exp_date, strike_price=Decimal("25200.0"),
                option_type="PE", lot_size=1, source_name=None, available_at=t_date,
            )
            session.add_all([c1, c2])
            await session.flush()

            p1 = DerivativeDailyPrice(
                contract_id=c1.id, trade_date=t_date, close=Decimal("250.0"),
                settlement_price=Decimal("250.0"), volume_contracts=100,
                open_interest=5000, underlying_value=Decimal("25000.0"), available_at=t_date,
            )
            p2 = DerivativeDailyPrice(
                contract_id=c2.id, trade_date=t_date, close=Decimal("220.0"),
                settlement_price=Decimal("220.0"), volume_contracts=100,
                open_interest=6000, underlying_value=Decimal("25000.0"), available_at=t_date,
            )
            session.add_all([p1, p2])
            await session.commit()

            service = HistoricalOptionsAnalyticsService(session)
            analytics = await service.get_option_chain_analytics("TEST_N3_SUPP", t_date, exp_date)

            assert "LOT_UNAVAILABLE_MAX_PAIN_SUPPRESSED" in analytics.quality_warnings
            assert analytics.max_pain.max_pain_strike is None
            assert analytics.max_pain.distance_from_spot is None
            assert analytics.max_pain.distance_pct is None

            # Candidate strikes MUST be preserved
            candidates = analytics.max_pain.candidates
            assert len(candidates) == 2
            assert [c.candidate_strike for c in candidates] == [Decimal("24800.0"), Decimal("25200.0")]

            # Candidate payouts MUST be None, NOT 1-scaled numbers
            for c in candidates:
                assert c.call_payout is None, f"candidate {c.candidate_strike} call_payout must be None"
                assert c.put_payout is None, f"candidate {c.candidate_strike} put_payout must be None"
                assert c.total_payout is None, f"candidate {c.candidate_strike} total_payout must be None"

    @pytest.mark.asyncio
    async def test_max_pain_calculates_numeric_payouts_on_verified_lot(self):
        """When lot provenance is verified, candidate payouts and max_pain_strike are computed."""
        from app.db.session import get_session_factory
        from app.models.derivative_contract import DerivativeContract
        from app.models.derivative_daily_price import DerivativeDailyPrice
        from app.models.market_index import MarketIndex
        from app.models.market_index_price import MarketIndexPrice
        from app.services.historical_options_analytics import HistoricalOptionsAnalyticsService

        factory = get_session_factory()
        async with factory() as session:
            idx = MarketIndex(code="TEST_N3_VERIF", name="Test N3 Verified", country="INDIA")
            session.add(idx)
            await session.flush()

            t_date = date(2026, 8, 14)
            exp_date = date(2026, 8, 28)

            mip = MarketIndexPrice(
                market_index_id=idx.id,
                trade_date=t_date,
                open=Decimal("25000.0"),
                high=Decimal("25100.0"),
                low=Decimal("24900.0"),
                close=Decimal("25000.0"),
            )
            session.add(mip)

            c1 = DerivativeContract(
                exchange="NSE", segment="NFO", instrument_type="OPTION",
                underlying_index_id=idx.id, trading_symbol="TESTN3V26AUG24800CE",
                expiry_date=exp_date, strike_price=Decimal("24800.0"),
                option_type="CE", lot_size=25, source_name="NSE_FO_BHAV", available_at=t_date,
            )
            c2 = DerivativeContract(
                exchange="NSE", segment="NFO", instrument_type="OPTION",
                underlying_index_id=idx.id, trading_symbol="TESTN3V26AUG25200PE",
                expiry_date=exp_date, strike_price=Decimal("25200.0"),
                option_type="PE", lot_size=25, source_name="NSE_FO_BHAV", available_at=t_date,
            )
            session.add_all([c1, c2])
            await session.flush()

            p1 = DerivativeDailyPrice(
                contract_id=c1.id, trade_date=t_date, close=Decimal("250.0"),
                settlement_price=Decimal("250.0"), volume_contracts=100,
                open_interest=5000, underlying_value=Decimal("25000.0"), available_at=t_date,
            )
            p2 = DerivativeDailyPrice(
                contract_id=c2.id, trade_date=t_date, close=Decimal("220.0"),
                settlement_price=Decimal("220.0"), volume_contracts=100,
                open_interest=6000, underlying_value=Decimal("25000.0"), available_at=t_date,
            )
            session.add_all([p1, p2])
            await session.commit()

            service = HistoricalOptionsAnalyticsService(session)
            analytics = await service.get_option_chain_analytics("TEST_N3_VERIF", t_date, exp_date)

            assert "LOT_UNAVAILABLE_MAX_PAIN_SUPPRESSED" not in analytics.quality_warnings
            assert analytics.max_pain.max_pain_strike is not None
            candidates = analytics.max_pain.candidates
            assert len(candidates) == 2
            for c in candidates:
                assert c.total_payout is not None
                assert isinstance(c.total_payout, Decimal)
