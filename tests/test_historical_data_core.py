"""Sprint 19.1C — Point-in-Time Historical Data Core — focused test suite.

Coverage:
  - exchange_constants: constant values, membership, type tuples
  - ohlc_validator: all OHLC rules, edge cases, Decimal/float/None handling
  - SecurityAlias: model, repo (resolve_as_of, overlap detection, point-in-time)
  - CorporateAction: model, repo (idempotency key, available_at gate)
  - IndexMembership: model, repo (point-in-time, survivorship-bias-safe)
  - DataQualityIssue: model, repo (open/resolve/count_by_severity)
  - HistoricalStockRepository: lifecycle_status, universe
  - PriceAdjustmentService: split factor, bonus factor, cumulative, adjust_price_series
  - HistoricalQueryService: resolve_symbol_as_of, get_price_history,
      get_adjusted_price_history, get_corporate_actions, get_index_members,
      get_universe_as_of, get_data_quality_issues
  - HistoricalCoverageService: coverage summaries
  - Preservation: existing Stock, DailyPrice, Market data imports unaffected
  - Zero network: no HTTP calls anywhere in this file

Style: pytest async with the project's conftest session management pattern.
       All DB operations use the shared async session factory from conftest.
"""
from __future__ import annotations

from dataclasses import dataclass as _dc
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

# ---------------------------------------------------------------------------
# Section 1 — Exchange Constants (pure Python, no DB)
# ---------------------------------------------------------------------------


def test_exchange_nse_value() -> None:
    from app.data.exchange_constants import EXCHANGE_NSE
    assert EXCHANGE_NSE == "NSE"


def test_exchange_bse_value() -> None:
    from app.data.exchange_constants import EXCHANGE_BSE
    assert EXCHANGE_BSE == "BSE"


def test_supported_exchanges_contains_nse_bse() -> None:
    from app.data.exchange_constants import SUPPORTED_EXCHANGES
    assert "NSE" in SUPPORTED_EXCHANGES
    assert "BSE" in SUPPORTED_EXCHANGES


def test_nse_eligible_series_contains_required() -> None:
    from app.data.exchange_constants import NSE_CASH_ELIGIBLE_SERIES
    for s in ("EQ", "BE", "BZ", "SM", "ST"):
        assert s in NSE_CASH_ELIGIBLE_SERIES


def test_nse_skip_series_contains_gb() -> None:
    from app.data.exchange_constants import NSE_SKIP_SERIES
    assert "GB" in NSE_SKIP_SERIES


def test_lifecycle_statuses_complete() -> None:
    from app.data.exchange_constants import LIFECYCLE_STATUSES
    assert "active" in LIFECYCLE_STATUSES
    assert "delisted" in LIFECYCLE_STATUSES
    assert "suspended" in LIFECYCLE_STATUSES
    assert "inactive" in LIFECYCLE_STATUSES
    assert "unknown" in LIFECYCLE_STATUSES


def test_corporate_action_types_complete() -> None:
    from app.data.exchange_constants import CORPORATE_ACTION_TYPES
    for t in ("split", "bonus", "dividend", "rights", "merger",
              "demerger", "buyback", "symbol_change", "name_change"):
        assert t in CORPORATE_ACTION_TYPES


def test_adjustable_action_types_are_split_and_bonus() -> None:
    from app.data.exchange_constants import ADJUSTABLE_ACTION_TYPES
    assert ADJUSTABLE_ACTION_TYPES == frozenset({"split", "bonus"})


def test_deferred_adjustment_types_do_not_overlap_adjustable() -> None:
    from app.data.exchange_constants import ADJUSTABLE_ACTION_TYPES, DEFERRED_ADJUSTMENT_TYPES
    assert ADJUSTABLE_ACTION_TYPES.isdisjoint(DEFERRED_ADJUSTMENT_TYPES)


def test_data_quality_issue_codes_exist() -> None:
    from app.data.exchange_constants import (
        DATA_QUALITY_ISSUE_CODES,
        DQ_MISSING_PRICE,
        DQ_NEGATIVE_VOLUME,
        DQ_OHLC_INVALID,
        DQ_ZERO_VOLUME,
    )
    for code in (DQ_OHLC_INVALID, DQ_NEGATIVE_VOLUME, DQ_MISSING_PRICE, DQ_ZERO_VOLUME):
        assert code in DATA_QUALITY_ISSUE_CODES


# ---------------------------------------------------------------------------
# Section 2 — OHLC Validator (pure Python, no DB)
# ---------------------------------------------------------------------------


def test_ohlc_valid_row() -> None:
    from app.data.ohlc_validator import validate_ohlc_row
    r = validate_ohlc_row(
        open_price=Decimal("100"), high=Decimal("110"),
        low=Decimal("95"), close=Decimal("105"), volume=10000
    )
    assert r.is_valid
    assert len(r.issue_codes) == 0


def test_ohlc_high_lt_low_invalid() -> None:
    from app.data.exchange_constants import DQ_OHLC_INVALID
    from app.data.ohlc_validator import validate_ohlc_row
    r = validate_ohlc_row(
        open_price=Decimal("100"), high=Decimal("90"),
        low=Decimal("95"), close=Decimal("100"), volume=100
    )
    assert not r.is_valid
    assert DQ_OHLC_INVALID in r.issue_codes


def test_ohlc_open_gt_high_invalid() -> None:
    from app.data.exchange_constants import DQ_OHLC_INVALID
    from app.data.ohlc_validator import validate_ohlc_row
    r = validate_ohlc_row(
        open_price=Decimal("120"), high=Decimal("115"),
        low=Decimal("90"), close=Decimal("100"), volume=100
    )
    assert not r.is_valid
    assert DQ_OHLC_INVALID in r.issue_codes


def test_ohlc_close_lt_low_invalid() -> None:
    from app.data.exchange_constants import DQ_OHLC_INVALID
    from app.data.ohlc_validator import validate_ohlc_row
    r = validate_ohlc_row(
        open_price=Decimal("100"), high=Decimal("115"),
        low=Decimal("90"), close=Decimal("80"), volume=100
    )
    assert not r.is_valid
    assert DQ_OHLC_INVALID in r.issue_codes


def test_ohlc_close_gt_high_invalid() -> None:
    from app.data.exchange_constants import DQ_OHLC_INVALID
    from app.data.ohlc_validator import validate_ohlc_row
    r = validate_ohlc_row(
        open_price=Decimal("100"), high=Decimal("110"),
        low=Decimal("90"), close=Decimal("120"), volume=100
    )
    assert not r.is_valid
    assert DQ_OHLC_INVALID in r.issue_codes


def test_ohlc_negative_volume() -> None:
    from app.data.exchange_constants import DQ_NEGATIVE_VOLUME
    from app.data.ohlc_validator import validate_ohlc_row
    r = validate_ohlc_row(
        open_price=Decimal("100"), high=Decimal("110"),
        low=Decimal("90"), close=Decimal("105"), volume=-1
    )
    assert not r.is_valid
    assert DQ_NEGATIVE_VOLUME in r.issue_codes


def test_ohlc_zero_volume_flagged() -> None:
    from app.data.exchange_constants import DQ_ZERO_VOLUME
    from app.data.ohlc_validator import validate_ohlc_row
    r = validate_ohlc_row(
        open_price=Decimal("100"), high=Decimal("110"),
        low=Decimal("90"), close=Decimal("105"), volume=0
    )
    assert not r.is_valid
    assert DQ_ZERO_VOLUME in r.issue_codes


def test_ohlc_zero_volume_allowed_when_flag_set() -> None:
    from app.data.ohlc_validator import validate_ohlc_row
    r = validate_ohlc_row(
        open_price=Decimal("100"), high=Decimal("110"),
        low=Decimal("90"), close=Decimal("105"), volume=0,
        allow_zero_volume=True,
    )
    assert r.is_valid


def test_ohlc_none_price_not_false_positive() -> None:
    """Missing price values must not trigger OHLC_INVALID."""
    from app.data.ohlc_validator import validate_ohlc_row
    # Only close provided — all comparisons are skipped
    r = validate_ohlc_row(
        open_price=None, high=None, low=None, close=Decimal("100"), volume=None
    )
    assert r.is_valid


def test_ohlc_batch_returns_one_result_per_row() -> None:
    from app.data.ohlc_validator import validate_ohlc_batch
    rows = [
        {"open": Decimal("100"), "high": Decimal("110"), "low": Decimal("90"),
         "close": Decimal("105"), "volume": 1000},
        {"open": Decimal("100"), "high": Decimal("90"), "low": Decimal("95"),
         "close": Decimal("100"), "volume": 100},  # invalid
    ]
    results = validate_ohlc_batch(rows)
    assert len(results) == 2
    assert results[0].is_valid
    assert not results[1].is_valid


# ---------------------------------------------------------------------------
# Section 3 — PriceAdjustmentService (pure Python, no DB)
# ---------------------------------------------------------------------------


@_dc
class _FakeCorporateAction:
    """Minimal duck-typed stub for CorporateAction used in pure adjustment tests."""
    id: int
    action_type: str
    ex_date: date
    ratio_numerator: Decimal | None
    ratio_denominator: Decimal | None
    available_at: date


@_dc
class _FakeDailyPrice:
    """Minimal duck-typed stub for DailyPrice used in pure adjustment tests."""
    stock_id: int
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


def _make_ca(action_type: str, ex_date: date, num: str, den: str,
             available_at: date | None = None, ca_id: int = 1) -> _FakeCorporateAction:
    return _FakeCorporateAction(
        id=ca_id,
        action_type=action_type,
        ex_date=ex_date,
        ratio_numerator=Decimal(num),
        ratio_denominator=Decimal(den),
        available_at=available_at or ex_date,
    )


def _make_dp(stock_id: int, trade_date: date, close: str) -> _FakeDailyPrice:
    c = Decimal(close)
    return _FakeDailyPrice(
        stock_id=stock_id, trade_date=trade_date,
        open=c, high=c, low=c, close=c,
    )


def test_split_factor_1_for_2() -> None:
    """1:2 split → price halved → factor = 2.0"""
    from app.services.price_adjustment import PriceAdjustmentService
    ca = _make_ca("split", date(2023, 1, 15), num="1", den="2")
    af = PriceAdjustmentService.compute_factor(ca)
    assert af is not None
    assert af.factor == Decimal("2.000000")


def test_split_factor_2_for_5() -> None:
    """2:5 split → factor = 5/2 = 2.5"""
    from app.services.price_adjustment import PriceAdjustmentService
    ca = _make_ca("split", date(2023, 1, 15), num="2", den="5")
    af = PriceAdjustmentService.compute_factor(ca)
    assert af is not None
    assert af.factor == Decimal("2.500000")


def test_bonus_factor_1_for_2() -> None:
    """Bonus 1:2 (1 free per 2 held) → (2+1)/2 = 1.5"""
    from app.services.price_adjustment import PriceAdjustmentService
    ca = _make_ca("bonus", date(2023, 1, 15), num="1", den="2")
    af = PriceAdjustmentService.compute_factor(ca)
    assert af is not None
    assert af.factor == Decimal("1.500000")


def test_bonus_factor_1_for_1() -> None:
    """Bonus 1:1 → (1+1)/1 = 2.0"""
    from app.services.price_adjustment import PriceAdjustmentService
    ca = _make_ca("bonus", date(2023, 1, 15), num="1", den="1")
    af = PriceAdjustmentService.compute_factor(ca)
    assert af is not None
    assert af.factor == Decimal("2.000000")


def test_dividend_returns_none() -> None:
    """Dividend adjustment is deferred — must return None."""
    from app.services.price_adjustment import PriceAdjustmentService
    ca = _make_ca("dividend", date(2023, 1, 15), num="1", den="1")
    af = PriceAdjustmentService.compute_factor(ca)
    assert af is None


def test_factor_none_ratio() -> None:
    """Action with no ratio returns None."""
    from app.services.price_adjustment import PriceAdjustmentService
    ca = _FakeCorporateAction(
        id=1, action_type="split",
        ex_date=date(2023, 1, 15),
        ratio_numerator=None,
        ratio_denominator=None,
        available_at=date(2023, 1, 15),
    )
    af = PriceAdjustmentService.compute_factor(ca)
    assert af is None



def test_adjust_price_series_no_actions() -> None:
    """With no actions, adjusted prices equal raw prices."""
    from app.services.price_adjustment import PriceAdjustmentService
    rows = [_make_dp(1, date(2023, 1, 10), "100")]
    result = PriceAdjustmentService.adjust_price_series(rows, factors=[])
    assert len(result) == 1
    assert result[0].adj_close == Decimal("100")
    assert result[0].cumulative_factor == Decimal("1")
    assert result[0].actions_applied == 0


def test_adjust_price_series_split_applied_before_ex_date() -> None:
    """1:2 split on 2023-01-15 → price on 2023-01-10 (before ex_date) is halved."""
    from app.services.price_adjustment import PriceAdjustmentService
    ca = _make_ca("split", date(2023, 1, 15), num="1", den="2")
    factors = PriceAdjustmentService.compute_cumulative_factors([ca])
    rows = [
        _make_dp(1, date(2023, 1, 10), "200"),   # before ex_date → adjusted
        _make_dp(1, date(2023, 1, 20), "100"),   # after ex_date → not adjusted
    ]
    result = PriceAdjustmentService.adjust_price_series(rows, factors)
    assert result[0].adj_close == Decimal("100.0000")   # 200 / 2
    assert result[0].actions_applied == 1
    assert result[1].adj_close == Decimal("100.0000")   # 100 / 1
    assert result[1].actions_applied == 0


def test_cumulative_split_two_actions() -> None:
    """Two 1:2 splits → cumulative factor = 4 → price / 4."""
    from app.services.price_adjustment import PriceAdjustmentService
    ca1 = _make_ca("split", date(2020, 1, 1), num="1", den="2", ca_id=1)
    ca2 = _make_ca("split", date(2022, 1, 1), num="1", den="2", ca_id=2)
    factors = PriceAdjustmentService.compute_cumulative_factors([ca1, ca2])
    rows = [_make_dp(1, date(2019, 1, 1), "400")]
    result = PriceAdjustmentService.adjust_price_series(rows, factors)
    assert result[0].adj_close == Decimal("100.0000")  # 400 / 4
    assert result[0].actions_applied == 2


def test_point_in_time_available_at_gates_factor() -> None:
    """Action with available_at > as_of_date must not be applied."""
    from app.services.price_adjustment import PriceAdjustmentService
    future_available = date(2024, 1, 1)
    ca = _make_ca("split", date(2023, 6, 1), num="1", den="2", available_at=future_available)
    factors = PriceAdjustmentService.compute_cumulative_factors(
        [ca], as_of_date=date(2023, 12, 31)
    )
    # available_at (2024-01-01) > as_of_date (2023-12-31) → must be excluded
    assert len(factors) == 0

    rows = [_make_dp(1, date(2023, 1, 1), "200")]
    result = PriceAdjustmentService.adjust_price_series(rows, factors)
    assert result[0].adj_close == Decimal("200")  # unadjusted


def test_raw_price_unchanged_after_adjustment() -> None:
    """Raw prices must not be modified by the adjustment service."""
    from app.services.price_adjustment import PriceAdjustmentService
    original_close = Decimal("200")
    ca = _make_ca("split", date(2023, 1, 15), num="1", den="2")
    factors = PriceAdjustmentService.compute_cumulative_factors([ca])
    row = _make_dp(1, date(2023, 1, 10), str(original_close))
    PriceAdjustmentService.adjust_price_series([row], factors)
    # raw_close of the DailyPrice object is not mutated
    assert row.close == original_close


# ---------------------------------------------------------------------------
# Section 4 — SecurityAlias Model + Repository (DB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_security_alias_add_and_get() -> None:
    from app.db.session import get_session_factory
    from app.models.sector import Sector
    from app.models.security_alias import SecurityAlias
    from app.models.stock import Stock
    from app.repositories.historical import SecurityAliasRepository

    factory = get_session_factory()
    async with factory() as session:
        sector = Sector(name="Technology")
        session.add(sector)
        stock = Stock(symbol="TESTCO", company_name="Test Co", exchange="NSE",
                      lifecycle_status="active")
        session.add(stock)
        await session.flush()

        alias = SecurityAlias(
            stock_id=stock.id,
            exchange="NSE",
            symbol="TESTCO",
            valid_from=date(2020, 1, 1),
            available_at=date(2020, 1, 1),
        )
        repo = SecurityAliasRepository(session)
        await repo.add(alias)
        await session.commit()

        fetched = await repo.get_by_id(alias.id)
        assert fetched is not None
        assert fetched.exchange == "NSE"
        assert fetched.symbol == "TESTCO"


@pytest.mark.asyncio
async def test_security_alias_resolve_as_of_found() -> None:
    from app.db.session import get_session_factory
    from app.models.security_alias import SecurityAlias
    from app.models.stock import Stock
    from app.repositories.historical import SecurityAliasRepository

    factory = get_session_factory()
    async with factory() as session:
        stock = Stock(symbol="OLDCO", company_name="Old Co", exchange="NSE",
                      lifecycle_status="active")
        session.add(stock)
        await session.flush()

        alias = SecurityAlias(
            stock_id=stock.id, exchange="NSE", symbol="OLDCO",
            valid_from=date(2019, 1, 1), valid_to=date(2022, 12, 31),
            available_at=date(2019, 1, 1),
        )
        repo = SecurityAliasRepository(session)
        await repo.add(alias)
        await session.commit()

        found = await repo.resolve_as_of("NSE", "OLDCO", date(2021, 6, 1))
        assert found is not None
        assert found.stock_id == stock.id


@pytest.mark.asyncio
async def test_security_alias_resolve_as_of_not_found_after_expiry() -> None:
    from app.db.session import get_session_factory
    from app.models.security_alias import SecurityAlias
    from app.models.stock import Stock
    from app.repositories.historical import SecurityAliasRepository

    factory = get_session_factory()
    async with factory() as session:
        stock = Stock(symbol="EXPIREDCO", company_name="Expired Co", exchange="NSE",
                      lifecycle_status="delisted")
        session.add(stock)
        await session.flush()

        alias = SecurityAlias(
            stock_id=stock.id, exchange="NSE", symbol="EXPIREDCO",
            valid_from=date(2019, 1, 1), valid_to=date(2020, 12, 31),
            available_at=date(2019, 1, 1),
        )
        repo = SecurityAliasRepository(session)
        await repo.add(alias)
        await session.commit()

        # After valid_to — should not be found
        found = await repo.resolve_as_of("NSE", "EXPIREDCO", date(2021, 1, 1))
        assert found is None


@pytest.mark.asyncio
async def test_security_alias_point_in_time_available_at() -> None:
    """Alias not returned if available_at > as_of_date (no look-ahead)."""
    from app.db.session import get_session_factory
    from app.models.security_alias import SecurityAlias
    from app.models.stock import Stock
    from app.repositories.historical import SecurityAliasRepository

    factory = get_session_factory()
    async with factory() as session:
        stock = Stock(symbol="FUTURESTOCK", company_name="Future Co", exchange="NSE",
                      lifecycle_status="active")
        session.add(stock)
        await session.flush()

        alias = SecurityAlias(
            stock_id=stock.id, exchange="NSE", symbol="FUTURESTOCK",
            valid_from=date(2023, 1, 1),
            available_at=date(2023, 6, 1),  # published 6 months later
        )
        repo = SecurityAliasRepository(session)
        await repo.add(alias)
        await session.commit()

        # Query before available_at — must not be found
        found = await repo.resolve_as_of("NSE", "FUTURESTOCK", date(2023, 3, 1))
        assert found is None

        # Query after available_at — must be found
        found2 = await repo.resolve_as_of("NSE", "FUTURESTOCK", date(2023, 7, 1))
        assert found2 is not None


# ---------------------------------------------------------------------------
# Section 5 — CorporateAction Model + Repository (DB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_corporate_action_add_and_fingerprint() -> None:
    from app.db.session import get_session_factory
    from app.models.corporate_action import CorporateAction
    from app.models.stock import Stock
    from app.repositories.historical import CorporateActionRepository

    factory = get_session_factory()
    async with factory() as session:
        stock = Stock(symbol="SPLITCO", company_name="Split Co", exchange="NSE",
                      lifecycle_status="active")
        session.add(stock)
        await session.flush()

        ca = CorporateAction(
            stock_id=stock.id, action_type="split",
            ex_date=date(2023, 6, 15),
            ratio_numerator=Decimal("1"), ratio_denominator=Decimal("2"),
            available_at=date(2023, 6, 1),
        )
        repo = CorporateActionRepository(session)
        await repo.add(ca)
        await session.commit()

        # Fetch by idempotency key
        fetched = await repo.get_by_fingerprint(ca.idempotency_hash)
        assert fetched is not None
        assert fetched.ratio_denominator == Decimal("2")


@pytest.mark.asyncio
async def test_corporate_action_available_at_gate() -> None:
    """Actions with available_at > as_of_date must not appear in list_for_stock_as_of."""
    from app.db.session import get_session_factory
    from app.models.corporate_action import CorporateAction
    from app.models.stock import Stock
    from app.repositories.historical import CorporateActionRepository

    factory = get_session_factory()
    async with factory() as session:
        stock = Stock(symbol="CAVSTOCK", company_name="CA Stock", exchange="NSE",
                      lifecycle_status="active")
        session.add(stock)
        await session.flush()

        ca_visible = CorporateAction(
            stock_id=stock.id, action_type="split",
            ex_date=date(2023, 3, 1),
            ratio_numerator=Decimal("1"), ratio_denominator=Decimal("2"),
            available_at=date(2023, 2, 1),  # knowable early
        )
        ca_future = CorporateAction(
            stock_id=stock.id, action_type="bonus",
            ex_date=date(2023, 7, 1),
            ratio_numerator=Decimal("1"), ratio_denominator=Decimal("2"),
            available_at=date(2024, 1, 1),  # not yet knowable at as_of_date
        )
        repo = CorporateActionRepository(session)
        await repo.add(ca_visible)
        await repo.add(ca_future)
        await session.commit()

        results = await repo.list_for_stock_as_of(
            stock.id, as_of_date=date(2023, 6, 30)
        )
        assert len(results) == 1
        assert results[0].action_type == "split"


@pytest.mark.asyncio
async def test_corporate_action_idempotency_application_level() -> None:
    """Exact identical economic imports are deduplicated (1 and 2), distinct ones retained (3)."""
    from app.db.session import get_session_factory
    from app.models.corporate_action import CorporateAction
    from app.models.stock import Stock
    from app.repositories.historical import CorporateActionRepository

    factory = get_session_factory()
    async with factory() as session:
        stock = Stock(symbol="IDEMPCA", company_name="Idemp CA", exchange="NSE", lifecycle_status="active")
        session.add(stock)
        await session.flush()

        repo = CorporateActionRepository(session)

        # 1 & 2. Exact same split imported twice -> one logical CorporateAction
        ca1 = CorporateAction(stock_id=stock.id, action_type="split", ex_date=date(2023, 6, 15), ratio_numerator=Decimal("1"), ratio_denominator=Decimal("2"), available_at=date(2023, 6, 1))
        ca2 = CorporateAction(stock_id=stock.id, action_type="split", ex_date=date(2023, 6, 15), ratio_numerator=Decimal("1"), ratio_denominator=Decimal("2"), available_at=date(2023, 6, 1))
        await repo.add(ca1)
        await repo.add(ca2)
        await session.commit()
        
        splits = await repo.list_for_stock_as_of(stock.id, date(2023, 7, 1), ["split"])
        assert len(splits) == 1
        
        # 3. Two different dividends on same stock/type/ex-date -> both retained
        div1 = CorporateAction(stock_id=stock.id, action_type="dividend", ex_date=date(2023, 8, 1), cash_amount=Decimal("5.50"), available_at=date(2023, 7, 1))
        div2 = CorporateAction(stock_id=stock.id, action_type="dividend", ex_date=date(2023, 8, 1), cash_amount=Decimal("2.00"), available_at=date(2023, 7, 1))
        await repo.add(div1)
        await repo.add(div2)
        await session.commit()
        
        divs = await repo.list_for_stock_as_of(stock.id, date(2023, 9, 1), ["dividend"])
        assert len(divs) == 2

        # 4. Two actions with NULL/non-applicable ex_date -> deterministic handling
        nc1 = CorporateAction(stock_id=stock.id, action_type="name_change", ex_date=None, new_symbol="NEWNAME", available_at=date(2023, 9, 1))
        nc2 = CorporateAction(stock_id=stock.id, action_type="name_change", ex_date=None, new_symbol="NEWNAME", available_at=date(2023, 9, 1))
        await repo.add(nc1)
        await repo.add(nc2)
        await session.commit()

        ncs = await repo.list_for_stock_as_of(stock.id, date(2023, 10, 1), ["name_change"])
        assert len(ncs) == 1

        # 5. Same apparent action from different source records -> behavior explicitly defined
        # Since source_reference is NOT part of the fingerprint, a new record with the same economic values but different source is skipped!
        div_src1 = CorporateAction(stock_id=stock.id, action_type="dividend", ex_date=date(2023, 10, 1), cash_amount=Decimal("1.00"), available_at=date(2023, 9, 1), source_reference="SRC1")
        div_src2 = CorporateAction(stock_id=stock.id, action_type="dividend", ex_date=date(2023, 10, 1), cash_amount=Decimal("1.00"), available_at=date(2023, 9, 1), source_reference="SRC2")
        await repo.add(div_src1)
        await repo.add(div_src2)
        await session.commit()
        
        divs_oct = await repo.list_for_stock_as_of(stock.id, date(2023, 11, 1), ["dividend"])
        oct_divs = [d for d in divs_oct if d.ex_date == date(2023, 10, 1)]
        assert len(oct_divs) == 1
        assert oct_divs[0].source_reference == "SRC1"



# ---------------------------------------------------------------------------
# Section 6 — IndexMembership Model + Repository (DB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_membership_add_and_point_in_time() -> None:
    from app.db.session import get_session_factory
    from app.models.index_membership import IndexMembership
    from app.models.market_index import MarketIndex
    from app.models.stock import Stock
    from app.repositories.historical import IndexMembershipRepository

    factory = get_session_factory()
    async with factory() as session:
        idx = MarketIndex(code="NIFTY50", country="INDIA", name="Nifty 50")
        session.add(idx)
        stock = Stock(symbol="MEMBER1", company_name="Member 1", exchange="NSE",
                      lifecycle_status="active")
        session.add(stock)
        await session.flush()

        mem = IndexMembership(
            index_id=idx.id, stock_id=stock.id,
            valid_from=date(2020, 1, 1),
            available_at=date(2020, 1, 1),
        )
        repo = IndexMembershipRepository(session)
        await repo.add(mem)
        await session.commit()

        members = await repo.get_members_as_of(idx.id, date(2023, 1, 1))
        assert len(members) == 1
        assert members[0].stock_id == stock.id


@pytest.mark.asyncio
async def test_index_membership_survivorship_bias_exclusion() -> None:
    """Stocks removed from index (valid_to set) must not appear after valid_to."""
    from app.db.session import get_session_factory
    from app.models.index_membership import IndexMembership
    from app.models.market_index import MarketIndex
    from app.models.stock import Stock
    from app.repositories.historical import IndexMembershipRepository

    factory = get_session_factory()
    async with factory() as session:
        idx = MarketIndex(code="NIFTY100", country="INDIA", name="Nifty 100")
        session.add(idx)
        stock_removed = Stock(symbol="REMOVED", company_name="Removed Co", exchange="NSE",
                              lifecycle_status="delisted")
        stock_current = Stock(symbol="CURRENT", company_name="Current Co", exchange="NSE",
                              lifecycle_status="active")
        session.add(stock_removed)
        session.add(stock_current)
        await session.flush()

        mem_removed = IndexMembership(
            index_id=idx.id, stock_id=stock_removed.id,
            valid_from=date(2018, 1, 1), valid_to=date(2020, 12, 31),
            available_at=date(2018, 1, 1),
        )
        mem_current = IndexMembership(
            index_id=idx.id, stock_id=stock_current.id,
            valid_from=date(2018, 1, 1),
            available_at=date(2018, 1, 1),
        )
        repo = IndexMembershipRepository(session)
        await repo.add(mem_removed)
        await repo.add(mem_current)
        await session.commit()

        # Query after stock_removed's valid_to — should see only stock_current
        members_2023 = await repo.get_members_as_of(idx.id, date(2023, 1, 1))
        stock_ids_2023 = {m.stock_id for m in members_2023}
        assert stock_current.id in stock_ids_2023
        assert stock_removed.id not in stock_ids_2023

        # Query during stock_removed's validity — should see both
        members_2019 = await repo.get_members_as_of(idx.id, date(2019, 6, 1))
        stock_ids_2019 = {m.stock_id for m in members_2019}
        assert stock_removed.id in stock_ids_2019
        assert stock_current.id in stock_ids_2019


@pytest.mark.asyncio
async def test_index_membership_available_at_gate() -> None:
    """Membership not yet announced must not appear in point-in-time query."""
    from app.db.session import get_session_factory
    from app.models.index_membership import IndexMembership
    from app.models.market_index import MarketIndex
    from app.models.stock import Stock
    from app.repositories.historical import IndexMembershipRepository

    factory = get_session_factory()
    async with factory() as session:
        idx = MarketIndex(code="NIFTY500", country="INDIA", name="Nifty 500")
        session.add(idx)
        stock = Stock(symbol="LATEANN", company_name="Late Announce", exchange="NSE",
                      lifecycle_status="active")
        session.add(stock)
        await session.flush()

        mem = IndexMembership(
            index_id=idx.id, stock_id=stock.id,
            valid_from=date(2023, 1, 1),
            available_at=date(2023, 3, 1),  # announced 2 months after inclusion
        )
        repo = IndexMembershipRepository(session)
        await repo.add(mem)
        await session.commit()

        # Query before available_at — must not be found
        members = await repo.get_members_as_of(idx.id, date(2023, 2, 1))
        assert len(members) == 0

        # Query after available_at — must be found
        members2 = await repo.get_members_as_of(idx.id, date(2023, 4, 1))
        assert len(members2) == 1


# ---------------------------------------------------------------------------
# Section 7 — DataQualityIssue Model + Repository (DB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_data_quality_issue_add_and_resolve() -> None:
    from app.db.session import get_session_factory
    from app.models.data_quality_issue import DataQualityIssue
    from app.repositories.historical import DataQualityIssueRepository

    factory = get_session_factory()
    async with factory() as session:
        issue = DataQualityIssue(
            issue_code="OHLC_INVALID",
            severity="error",
            exchange="NSE",
            symbol="BADSTOCK",
            trade_date=date(2023, 1, 5),
            description="High < Low detected",
            detected_at=datetime.now(UTC),
        )
        repo = DataQualityIssueRepository(session)
        await repo.add(issue)
        await session.commit()

        open_issues = await repo.list_open(exchange="NSE", trade_date=date(2023, 1, 5))
        assert len(open_issues) == 1

        resolved = await repo.resolve(issue.id, resolution_notes="Confirmed bad data source")
        await session.commit()
        assert resolved is not None
        assert resolved.resolved is True

        open_after = await repo.list_open(exchange="NSE", trade_date=date(2023, 1, 5))
        assert len(open_after) == 0


@pytest.mark.asyncio
async def test_data_quality_issue_count_by_severity() -> None:
    from app.db.session import get_session_factory
    from app.models.data_quality_issue import DataQualityIssue
    from app.repositories.historical import DataQualityIssueRepository

    factory = get_session_factory()
    async with factory() as session:
        issues = [
            DataQualityIssue(
                issue_code="OHLC_INVALID", severity="error", exchange="NSE",
                description="error1", detected_at=datetime.now(UTC),
            ),
            DataQualityIssue(
                issue_code="ZERO_VOLUME", severity="warning", exchange="NSE",
                description="warn1", detected_at=datetime.now(UTC),
            ),
            DataQualityIssue(
                issue_code="MISSING_DELIVERY", severity="warning", exchange="NSE",
                description="warn2", detected_at=datetime.now(UTC),
            ),
        ]
        repo = DataQualityIssueRepository(session)
        for iss in issues:
            await repo.add(iss)
        await session.commit()

        counts = await repo.count_by_severity(exchange="NSE")
        assert counts["error"] == 1
        assert counts["warning"] == 2


# ---------------------------------------------------------------------------
# Section 8 — HistoricalStockRepository: lifecycle_status + universe (DB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stock_lifecycle_status_default() -> None:
    """New stocks get lifecycle_status='active' by default."""
    from app.db.session import get_session_factory
    from app.models.stock import Stock

    factory = get_session_factory()
    async with factory() as session:
        stock = Stock(symbol="LCTEST", company_name="Lifecycle Test", exchange="NSE")
        session.add(stock)
        await session.commit()
        await session.refresh(stock)
        assert stock.lifecycle_status == "active"


@pytest.mark.asyncio
async def test_get_universe_as_of_filters_delisted() -> None:
    from app.db.session import get_session_factory
    from app.models.stock import Stock
    from app.repositories.historical import HistoricalStockRepository

    factory = get_session_factory()
    async with factory() as session:
        active = Stock(symbol="ACTIVESTOCK", company_name="Active", exchange="NSE",
                       lifecycle_status="active")
        delisted = Stock(symbol="DELISTEDSTOCK", company_name="Delisted", exchange="NSE",
                         lifecycle_status="delisted")
        session.add(active)
        session.add(delisted)
        await session.commit()

        repo = HistoricalStockRepository(session)
        universe = await repo.get_universe_as_of(
            "NSE", as_of_date=date(2023, 1, 1)
        )
        symbols = {s.symbol for s in universe}
        assert "ACTIVESTOCK" in symbols
        assert "DELISTEDSTOCK" not in symbols


@pytest.mark.asyncio
async def test_get_universe_as_of_include_suspended() -> None:
    from app.db.session import get_session_factory
    from app.models.stock import Stock
    from app.repositories.historical import HistoricalStockRepository

    factory = get_session_factory()
    async with factory() as session:
        suspended = Stock(symbol="SUSPENDEDSTOCK", company_name="Suspended", exchange="NSE",
                          lifecycle_status="suspended")
        session.add(suspended)
        await session.commit()

        repo = HistoricalStockRepository(session)
        universe = await repo.get_universe_as_of(
            "NSE", as_of_date=date(2023, 1, 1),
            lifecycle_statuses=["active", "suspended"]
        )
        symbols = {s.symbol for s in universe}
        assert "SUSPENDEDSTOCK" in symbols


# ---------------------------------------------------------------------------
# Section 9 — HistoricalQueryService (DB, no network)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_symbol_as_of_via_alias() -> None:
    from app.db.session import get_session_factory
    from app.models.security_alias import SecurityAlias
    from app.models.stock import Stock
    from app.services.historical_query import HistoricalQueryService

    factory = get_session_factory()
    async with factory() as session:
        stock = Stock(symbol="NEWCO", company_name="New Co", exchange="NSE",
                      lifecycle_status="active")
        session.add(stock)
        await session.flush()

        # Record old symbol
        alias = SecurityAlias(
            stock_id=stock.id, exchange="NSE", symbol="OLDCO",
            valid_from=date(2018, 1, 1), valid_to=date(2021, 12, 31),
            available_at=date(2018, 1, 1),
        )
        session.add(alias)
        await session.commit()

        svc = HistoricalQueryService(session)
        resolved = await svc.resolve_symbol_as_of("NSE", "OLDCO", date(2020, 6, 1))
        assert resolved is not None
        assert resolved.symbol == "NEWCO"


@pytest.mark.asyncio
async def test_resolve_symbol_falls_back_to_direct_lookup() -> None:
    """Stock with no aliases resolves via direct exchange/symbol lookup."""
    from app.db.session import get_session_factory
    from app.models.stock import Stock
    from app.services.historical_query import HistoricalQueryService

    factory = get_session_factory()
    async with factory() as session:
        stock = Stock(symbol="DIRECTCO", company_name="Direct Co", exchange="NSE",
                      lifecycle_status="active")
        session.add(stock)
        await session.commit()

        svc = HistoricalQueryService(session)
        resolved = await svc.resolve_symbol_as_of("NSE", "DIRECTCO", date(2023, 1, 1))
        assert resolved is not None
        assert resolved.symbol == "DIRECTCO"


@pytest.mark.asyncio
async def test_get_price_history_returns_raw_prices() -> None:
    from app.db.session import get_session_factory
    from app.models.daily_price import DailyPrice
    from app.models.stock import Stock
    from app.services.historical_query import HistoricalQueryService

    factory = get_session_factory()
    async with factory() as session:
        stock = Stock(symbol="PRICETEST", company_name="Price Test", exchange="NSE",
                      lifecycle_status="active")
        session.add(stock)
        await session.flush()

        for i in range(3):
            dp = DailyPrice(
                stock_id=stock.id,
                trade_date=date(2023, 1, i + 1),
                open=Decimal("100"), high=Decimal("110"),
                low=Decimal("95"), close=Decimal("105"),
                volume=10000,
            )
            session.add(dp)
        await session.commit()

        svc = HistoricalQueryService(session)
        rows = await svc.get_price_history(
            stock.id, date(2023, 1, 1), date(2023, 1, 3)
        )
        assert len(rows) == 3
        # Raw prices unchanged
        for row in rows:
            assert row.close == Decimal("105")


@pytest.mark.asyncio
async def test_get_adjusted_price_history_applies_split() -> None:
    from app.db.session import get_session_factory
    from app.models.corporate_action import CorporateAction
    from app.models.daily_price import DailyPrice
    from app.models.stock import Stock
    from app.services.historical_query import HistoricalQueryService

    factory = get_session_factory()
    async with factory() as session:
        stock = Stock(symbol="ADJTEST", company_name="Adj Test", exchange="NSE",
                      lifecycle_status="active")
        session.add(stock)
        await session.flush()

        # Price row before ex_date
        dp = DailyPrice(
            stock_id=stock.id, trade_date=date(2023, 1, 5),
            open=Decimal("200"), high=Decimal("210"),
            low=Decimal("195"), close=Decimal("200"),
            volume=10000,
        )
        session.add(dp)

        # 1:2 split on 2023-01-15, available_at 2023-01-10
        ca = CorporateAction(
            stock_id=stock.id, action_type="split",
            ex_date=date(2023, 1, 15),
            ratio_numerator=Decimal("1"), ratio_denominator=Decimal("2"),
            available_at=date(2023, 1, 10),
        )
        session.add(ca)
        await session.commit()

        svc = HistoricalQueryService(session)
        adj_rows = await svc.get_adjusted_price_history(
            stock.id, date(2023, 1, 1), date(2023, 1, 10),
            as_of_date=date(2023, 12, 31)
        )
        assert len(adj_rows) == 1
        assert adj_rows[0].adj_close == Decimal("100.0000")
        # Raw price unchanged
        assert adj_rows[0].raw_close == Decimal("200")


@pytest.mark.asyncio
async def test_get_adjusted_no_look_ahead() -> None:
    """Adjustment with as_of_date before available_at must not apply the action."""
    from app.db.session import get_session_factory
    from app.models.corporate_action import CorporateAction
    from app.models.daily_price import DailyPrice
    from app.models.stock import Stock
    from app.services.historical_query import HistoricalQueryService

    factory = get_session_factory()
    async with factory() as session:
        stock = Stock(symbol="NOLOOKSTOCK", company_name="No Look", exchange="NSE",
                      lifecycle_status="active")
        session.add(stock)
        await session.flush()

        dp = DailyPrice(
            stock_id=stock.id, trade_date=date(2023, 1, 5),
            open=Decimal("200"), high=Decimal("210"),
            low=Decimal("195"), close=Decimal("200"), volume=10000,
        )
        session.add(dp)

        ca = CorporateAction(
            stock_id=stock.id, action_type="split",
            ex_date=date(2023, 1, 15),
            ratio_numerator=Decimal("1"), ratio_denominator=Decimal("2"),
            available_at=date(2024, 1, 1),  # future — not knowable yet
        )
        session.add(ca)
        await session.commit()

        svc = HistoricalQueryService(session)
        adj_rows = await svc.get_adjusted_price_history(
            stock.id, date(2023, 1, 1), date(2023, 1, 10),
            as_of_date=date(2023, 6, 30)  # before available_at
        )
        assert len(adj_rows) == 1
        # No adjustment applied — raw = adjusted
        assert adj_rows[0].adj_close == Decimal("200")
        assert adj_rows[0].actions_applied == 0


@pytest.mark.asyncio
async def test_get_index_members_point_in_time() -> None:
    from app.db.session import get_session_factory
    from app.models.index_membership import IndexMembership
    from app.models.market_index import MarketIndex
    from app.models.stock import Stock
    from app.services.historical_query import HistoricalQueryService

    factory = get_session_factory()
    async with factory() as session:
        idx = MarketIndex(code="NIFTY50SVC", country="INDIA", name="Nifty 50 SVC")
        session.add(idx)
        s1 = Stock(
            symbol="MEM1", company_name="Member 1", exchange="NSE", lifecycle_status="active"
        )
        s2 = Stock(
            symbol="MEM2", company_name="Member 2", exchange="NSE", lifecycle_status="delisted"
        )
        session.add(s1)
        session.add(s2)
        await session.flush()

        im1 = IndexMembership(index_id=idx.id, stock_id=s1.id,
                              valid_from=date(2020, 1, 1), available_at=date(2020, 1, 1))
        im2 = IndexMembership(index_id=idx.id, stock_id=s2.id,
                              valid_from=date(2020, 1, 1), valid_to=date(2021, 12, 31),
                              available_at=date(2020, 1, 1))
        session.add(im1)
        session.add(im2)
        await session.commit()

        svc = HistoricalQueryService(session)
        # Query 2023 → only s1 (s2 was removed end of 2021)
        members_2023 = await svc.get_index_members(idx.id, date(2023, 1, 1))
        ids_2023 = {m.stock_id for m in members_2023}
        assert s1.id in ids_2023
        assert s2.id not in ids_2023

        # Query 2021 → both
        members_2021 = await svc.get_index_members(idx.id, date(2021, 6, 1))
        ids_2021 = {m.stock_id for m in members_2021}
        assert s1.id in ids_2021
        assert s2.id in ids_2021


@pytest.mark.asyncio
async def test_get_universe_as_of_via_index_membership() -> None:
    from app.db.session import get_session_factory
    from app.models.index_membership import IndexMembership
    from app.models.market_index import MarketIndex
    from app.models.stock import Stock
    from app.services.historical_query import HistoricalQueryService

    factory = get_session_factory()
    async with factory() as session:
        idx = MarketIndex(code="SMALLCAP", country="INDIA", name="Nifty Smallcap 100")
        session.add(idx)
        s = Stock(
            symbol="SCMEMBER", company_name="Smallcap", exchange="NSE", lifecycle_status="active"
        )
        session.add(s)
        await session.flush()

        im = IndexMembership(
            index_id=idx.id, stock_id=s.id,
            valid_from=date(2020, 1, 1), available_at=date(2020, 1, 1)
        )
        session.add(im)
        await session.commit()

        svc = HistoricalQueryService(session)
        universe = await svc.get_universe_as_of(
            date(2023, 1, 1), index_id=idx.id
        )
        assert any(stk.symbol == "SCMEMBER" for stk in universe)


# ---------------------------------------------------------------------------
# Section 10 — HistoricalCoverageService (DB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_historical_coverage_service_corporate_actions() -> None:
    from app.db.session import get_session_factory
    from app.models.corporate_action import CorporateAction
    from app.models.stock import Stock
    from app.services.historical_query import HistoricalCoverageService

    factory = get_session_factory()
    async with factory() as session:
        stock = Stock(symbol="COVCATEST", company_name="Cov CA", exchange="NSE",
                      lifecycle_status="active")
        session.add(stock)
        await session.flush()

        for i in range(3):
            ca = CorporateAction(
                stock_id=stock.id, action_type="split" if i % 2 == 0 else "bonus",
                ex_date=date(2020 + i, 1, 1),
                ratio_numerator=Decimal("1"), ratio_denominator=Decimal("2"),
                available_at=date(2020 + i, 1, 1),
            )
            session.add(ca)
        await session.commit()

        svc = HistoricalCoverageService(session)
        cov = await svc.get_corporate_action_coverage(stock.id)
        assert cov["total_actions"] == 3
        assert cov["earliest_ex_date"] == "2020-01-01"
        assert cov["latest_ex_date"] == "2022-01-01"


@pytest.mark.asyncio
async def test_historical_coverage_data_quality_summary() -> None:
    from app.db.session import get_session_factory
    from app.models.data_quality_issue import DataQualityIssue
    from app.services.historical_query import HistoricalCoverageService

    factory = get_session_factory()
    async with factory() as session:
        for i in range(2):
            session.add(DataQualityIssue(
                issue_code="OHLC_INVALID", severity="error", exchange="NSE",
                description=f"error {i}", detected_at=datetime.now(UTC),
            ))
        session.add(DataQualityIssue(
            issue_code="ZERO_VOLUME", severity="warning", exchange="NSE",
            description="warn", detected_at=datetime.now(UTC),
        ))
        await session.commit()

        svc = HistoricalCoverageService(session)
        summary = await svc.get_data_quality_summary(exchange="NSE")
        assert summary["open_errors"] == 2
        assert summary["open_warnings"] == 1
        assert summary["total_open"] == 3


# ---------------------------------------------------------------------------
# Section 11 — Preservation Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stock_model_backward_compatible() -> None:
    """Existing code creating a Stock without lifecycle_status must still work."""
    from app.db.session import get_session_factory
    from app.models.stock import Stock

    factory = get_session_factory()
    async with factory() as session:
        stock = Stock(symbol="COMPAT", company_name="Compat Co", exchange="NSE")
        session.add(stock)
        await session.commit()
        await session.refresh(stock)
        # Old code path — lifecycle_status defaults to "active"
        assert stock.lifecycle_status == "active"
        # is_active still works
        assert stock.is_active is True


@pytest.mark.asyncio
async def test_daily_price_raw_values_never_modified() -> None:
    """DailyPrice rows must never be mutated by the new adjustment service."""
    from app.db.session import get_session_factory
    from app.models.corporate_action import CorporateAction
    from app.models.daily_price import DailyPrice
    from app.models.stock import Stock
    from app.services.historical_query import HistoricalQueryService

    factory = get_session_factory()
    async with factory() as session:
        stock = Stock(symbol="RAWPRICE", company_name="Raw", exchange="NSE",
                      lifecycle_status="active")
        session.add(stock)
        await session.flush()

        original_close = Decimal("400.0000")
        dp = DailyPrice(
            stock_id=stock.id, trade_date=date(2023, 1, 5),
            open=original_close, high=original_close,
            low=original_close, close=original_close, volume=5000,
        )
        session.add(dp)

        ca = CorporateAction(
            stock_id=stock.id, action_type="split",
            ex_date=date(2023, 1, 15),
            ratio_numerator=Decimal("1"), ratio_denominator=Decimal("4"),
            available_at=date(2023, 1, 10),
        )
        session.add(ca)
        await session.commit()

        svc = HistoricalQueryService(session)
        await svc.get_adjusted_price_history(
            stock.id, date(2023, 1, 1), date(2023, 1, 10),
            as_of_date=date(2023, 12, 31)
        )

        # Re-fetch raw row — must be unchanged
        await session.refresh(dp)
        assert dp.close == original_close


def test_stock_model_import() -> None:
    """Ensure the Stock model imports cleanly (no circular imports)."""
    from app.models.stock import Stock
    assert hasattr(Stock, "lifecycle_status")
    assert hasattr(Stock, "security_aliases")
    assert hasattr(Stock, "corporate_actions")
    assert hasattr(Stock, "index_memberships")


def test_all_new_models_in_init() -> None:
    """All 4 new models must be exported from app.models."""
    from app import models
    for name in ("SecurityAlias", "CorporateAction", "IndexMembership", "DataQualityIssue"):
        assert hasattr(models, name), f"{name} not in app.models"
