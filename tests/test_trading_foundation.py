from __future__ import annotations

import inspect
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db.session import get_session_factory
from app.models.auth import User
from app.models.backtest import BacktestMetric, BacktestRun, BacktestTrade
from app.models.stock import Stock
from app.models.trading_account import (
    TRADING_ACCOUNT_BASE_CURRENCIES,
    TRADING_ACCOUNT_STATUSES,
    TRADING_EXECUTION_MODES,
    TradingAccount,
)
from app.models.trading_execution import EXECUTION_SOURCES, TradingExecution
from app.models.trading_order import (
    ORDER_ORIGINS,
    ORDER_SIDES,
    ORDER_STATUSES,
    ORDER_TIME_IN_FORCE,
    ORDER_TYPES,
    TradingOrder,
)
from app.models.trading_position import POSITION_SIDES, TradingPosition
from app.repositories.trading import (
    TradingExecutionRepository,
    TradingOrderRepository,
    TradingPositionRepository,
)
from app.schemas.trading import OrderCreate, TradingAccountCreate
from app.services import trading_foundation as tf_module
from app.services.trading_foundation import (
    ORDER_STATUS_TRANSITIONS,
    TERMINAL_ORDER_STATUSES,
    DuplicateClientOrderIdError,
    InvalidOrderTransitionError,
    TradingAccountInactiveError,
    TradingAccountService,
    TradingOrderService,
    UnsupportedExecutionModeError,
    is_terminal_order_status,
    validate_order_transition,
)


async def _user(session, username: str = "trader19") -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        full_name="Trader 19",
        password_hash="x",
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _stock(session, symbol: str = "INFY19") -> Stock:
    stock = Stock(
        symbol=symbol,
        company_name=f"{symbol} Ltd",
        exchange="NSE",
        series="EQ",
        isin=f"INE{symbol}19",
        is_active=True,
    )
    session.add(stock)
    await session.flush()
    return stock


async def _account(session, owner_user_id: int, **kwargs) -> TradingAccount:
    svc = TradingAccountService(session)
    data = TradingAccountCreate(
        owner_user_id=owner_user_id,
        name=kwargs.get("name", "Paper Acct 19"),
        execution_mode=kwargs.get("execution_mode", "paper"),
        initial_cash=kwargs.get("initial_cash", Decimal("100000.00")),
        status=kwargs.get("status", "active"),
    )
    return await svc.create_account(data)


def _order_create(account_id: int, stock_id: int, **kwargs) -> OrderCreate:
    return OrderCreate(
        account_id=account_id,
        stock_id=stock_id,
        symbol=kwargs.get("symbol", "INFY19"),
        exchange=kwargs.get("exchange", "NSE"),
        instrument_type=kwargs.get("instrument_type", "equity"),
        client_order_id=kwargs.get("client_order_id", "COID19"),
        side=kwargs.get("side", "buy"),
        order_type=kwargs.get("order_type", "market"),
        quantity=kwargs.get("quantity", 10),
        limit_price=kwargs.get("limit_price"),
        stop_price=kwargs.get("stop_price"),
        time_in_force=kwargs.get("time_in_force"),
        origin=kwargs.get("origin"),
        execution_mode=kwargs.get("execution_mode", "paper"),
    )


# ---------------------------------------------------------------------------
# ENUMS
# ---------------------------------------------------------------------------


def test_execution_mode_enum() -> None:
    assert "paper" in TRADING_EXECUTION_MODES
    assert "live" in TRADING_EXECUTION_MODES
    assert tuple(sorted(TRADING_EXECUTION_MODES)) == ("live", "paper")


def test_order_side_enum() -> None:
    assert ORDER_SIDES == ("buy", "sell")


def test_order_type_enum() -> None:
    assert "market" in ORDER_TYPES
    assert "limit" in ORDER_TYPES


def test_order_status_enum() -> None:
    for expected in (
        "created",
        "pending",
        "accepted",
        "partially_filled",
        "filled",
        "cancelled",
        "rejected",
        "expired",
    ):
        assert expected in ORDER_STATUSES


def test_order_origin_enum() -> None:
    for expected in ("manual", "strategy", "signal", "api", "system"):
        assert expected in ORDER_ORIGINS
    assert "day" in ORDER_TIME_IN_FORCE
    assert "ioc" in ORDER_TIME_IN_FORCE
    assert "long" in POSITION_SIDES and "short" in POSITION_SIDES
    assert "system" in EXECUTION_SOURCES
    assert "INR" in TRADING_ACCOUNT_BASE_CURRENCIES
    assert "active" in TRADING_ACCOUNT_STATUSES


# ---------------------------------------------------------------------------
# ACCOUNT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_paper_account() -> None:
    factory = get_session_factory()
    async with factory() as session:
        user = await _user(session)
        account = await _account(session, user.id)
        assert account.id is not None
        assert account.execution_mode == "paper"
        assert account.status == "active"
        assert account.initial_cash == Decimal("100000.00")
        assert account.cash_balance == Decimal("100000.00")
        assert account.owner_user_id == user.id
        await session.rollback()


@pytest.mark.asyncio
async def test_account_execution_mode_defaults_paper() -> None:
    factory = get_session_factory()
    async with factory() as session:
        user = await _user(session, "trader_def")
        data = TradingAccountCreate(owner_user_id=user.id, name="Default Mode")
        account = await TradingAccountService(session).create_account(data)
        assert account.execution_mode == "paper"
        await session.rollback()


@pytest.mark.asyncio
async def test_active_account_accepts_new_order() -> None:
    factory = get_session_factory()
    async with factory() as session:
        user = await _user(session, "trader_act")
        stock = await _stock(session, "ACC1")
        account = await _account(session, user.id)
        order = await TradingOrderService(session).create_order(
            _order_create(account.id, stock.id)
        )
        assert order.id is not None
        assert order.status == "created"
        await session.rollback()


@pytest.mark.asyncio
async def test_inactive_account_rejects_new_order() -> None:
    factory = get_session_factory()
    async with factory() as session:
        user = await _user(session, "trader_inact")
        stock = await _stock(session, "ACC2")
        account = await _account(session, user.id, status="suspended")
        with pytest.raises(TradingAccountInactiveError):
            await TradingOrderService(session).create_order(
                _order_create(account.id, stock.id)
            )
        await session.rollback()


# ---------------------------------------------------------------------------
# ORDER VALIDATION
# ---------------------------------------------------------------------------


def test_quantity_must_be_positive_schema() -> None:
    with pytest.raises(ValidationError):
        OrderCreate(
            account_id=1, stock_id=1, symbol="X", client_order_id="X", side="buy",
            order_type="market", quantity=0,
        )
    with pytest.raises(ValidationError):
        OrderCreate(
            account_id=1, stock_id=1, symbol="X", client_order_id="X", side="buy",
            order_type="market", quantity=-5,
        )


def test_limit_price_required_for_limit_orders() -> None:
    with pytest.raises(ValidationError):
        OrderCreate(
            account_id=1, stock_id=1, symbol="X", client_order_id="X", side="buy",
            order_type="limit", quantity=10,
        )
    ok = OrderCreate(
        account_id=1, stock_id=1, symbol="X", client_order_id="X", side="buy",
        order_type="limit", quantity=10, limit_price=Decimal("100.50"),
    )
    assert ok.limit_price == Decimal("100.50")


def test_negative_limit_price_rejected() -> None:
    with pytest.raises(ValidationError):
        OrderCreate(
            account_id=1, stock_id=1, symbol="X", client_order_id="X", side="buy",
            order_type="limit", quantity=10, limit_price=Decimal("-1"),
        )


def test_market_order_does_not_require_limit_price() -> None:
    ok = OrderCreate(
        account_id=1, stock_id=1, symbol="X", client_order_id="X", side="buy",
        order_type="market", quantity=10,
    )
    assert ok.limit_price is None


@pytest.mark.asyncio
async def test_live_execution_rejected_not_operational() -> None:
    factory = get_session_factory()
    async with factory() as session:
        user = await _user(session, "trader_live")
        stock = await _stock(session, "ACC3")
        with pytest.raises(UnsupportedExecutionModeError):
            await TradingAccountService(session).create_account(
                TradingAccountCreate(
                    owner_user_id=user.id, name="Live", execution_mode="live"
                )
            )
        account = await _account(session, user.id)
        with pytest.raises(UnsupportedExecutionModeError):
            await TradingOrderService(session).create_order(
                _order_create(account.id, stock.id, execution_mode="live")
            )
        await session.rollback()


# ---------------------------------------------------------------------------
# IDEMPOTENCY
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unique_client_order_id_per_account() -> None:
    factory = get_session_factory()
    async with factory() as session:
        user = await _user(session, "trader_idem")
        stock = await _stock(session, "ACC4")
        account = await _account(session, user.id)
        svc = TradingOrderService(session)
        o1 = await svc.create_order(_order_create(account.id, stock.id, client_order_id="C1"))
        o2 = await svc.create_order(_order_create(account.id, stock.id, client_order_id="C2"))
        assert o1.client_order_id == "C1"
        assert o2.client_order_id == "C2"
        await session.rollback()


@pytest.mark.asyncio
async def test_duplicate_client_order_id_rejected() -> None:
    factory = get_session_factory()
    async with factory() as session:
        user = await _user(session, "trader_dup")
        stock = await _stock(session, "ACC5")
        account = await _account(session, user.id)
        svc = TradingOrderService(session)
        await svc.create_order(_order_create(account.id, stock.id, client_order_id="DUP"))
        with pytest.raises(DuplicateClientOrderIdError):
            await svc.create_order(_order_create(account.id, stock.id, client_order_id="DUP"))
        found = await svc.orders.get_by_client_order_id(account.id, "DUP")
        assert found is not None
        assert found.client_order_id == "DUP"
        await session.rollback()


@pytest.mark.asyncio
async def test_same_client_order_id_allowed_on_different_account() -> None:
    factory = get_session_factory()
    async with factory() as session:
        user = await _user(session, "trader_a")
        stock = await _stock(session, "ACC6")
        a1 = await _account(session, user.id, name="Acct A")
        a2 = await _account(session, user.id, name="Acct B")
        svc = TradingOrderService(session)
        o1 = await svc.create_order(_order_create(a1.id, stock.id, client_order_id="SHARED"))
        o2 = await svc.create_order(_order_create(a2.id, stock.id, client_order_id="SHARED"))
        assert o1.account_id != o2.account_id
        assert o1.client_order_id == o2.client_order_id
        await session.rollback()


@pytest.mark.asyncio
async def test_db_uniqueness_protects_race_duplicates() -> None:
    factory = get_session_factory()
    async with factory() as session:
        user = await _user(session, "trader_race")
        stock = await _stock(session, "ACC7")
        account = await _account(session, user.id)
        base = dict(
            account_id=account.id,
            stock_id=stock.id,
            symbol="ACC7",
            exchange="NSE",
            instrument_type="equity",
            client_order_id="RACE",
            side="buy",
            order_type="market",
            quantity=10,
            status="created",
        )
        session.add(TradingOrder(**base))
        await session.flush()
        session.add(TradingOrder(**base))
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


# ---------------------------------------------------------------------------
# STATUS / LIFECYCLE
# ---------------------------------------------------------------------------


def test_valid_state_transition() -> None:
    assert validate_order_transition("created", "pending") is True
    assert validate_order_transition("pending", "accepted") is True
    assert validate_order_transition("accepted", "partially_filled") is True
    assert validate_order_transition("partially_filled", "filled") is True


def test_invalid_state_transition() -> None:
    assert validate_order_transition("filled", "pending") is False
    assert validate_order_transition("cancelled", "filled") is False
    assert validate_order_transition("rejected", "accepted") is False


def test_terminal_state_cannot_regress() -> None:
    for terminal in TERMINAL_ORDER_STATUSES:
        assert is_terminal_order_status(terminal) is True
        for target in ORDER_STATUS_TRANSITIONS:
            if target != terminal:
                assert validate_order_transition(terminal, target) is False


@pytest.mark.asyncio
async def test_service_rejects_invalid_transition() -> None:
    factory = get_session_factory()
    async with factory() as session:
        user = await _user(session, "trader_tx")
        stock = await _stock(session, "ACC8")
        account = await _account(session, user.id)
        svc = TradingOrderService(session)
        order = await svc.create_order(_order_create(account.id, stock.id))
        await svc.transition_order(order, "pending")
        await svc.transition_order(order, "accepted")
        await svc.transition_order(order, "filled")
        with pytest.raises(InvalidOrderTransitionError):
            await svc.transition_order(order, "cancelled")
        same = await svc.transition_order(order, "filled")
        assert same.status == "filled"
        await session.rollback()


@pytest.mark.asyncio
async def test_cancelled_and_rejected_terminal() -> None:
    factory = get_session_factory()
    async with factory() as session:
        user = await _user(session, "trader_term")
        stock = await _stock(session, "ACC9")
        account = await _account(session, user.id)
        svc = TradingOrderService(session)
        cancelled = await svc.create_order(
            _order_create(account.id, stock.id, client_order_id="K1")
        )
        await svc.transition_order(cancelled, "cancelled")
        with pytest.raises(InvalidOrderTransitionError):
            await svc.transition_order(cancelled, "filled")

        rejected = await svc.create_order(_order_create(account.id, stock.id, client_order_id="K2"))
        await svc.reject_order(rejected, "RISK", "not allowed")
        assert rejected.status == "rejected"
        assert rejected.rejection_code == "RISK"
        with pytest.raises(InvalidOrderTransitionError):
            await svc.transition_order(rejected, "pending")
        await session.rollback()


# ---------------------------------------------------------------------------
# DECIMAL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monetary_values_preserve_decimal() -> None:
    factory = get_session_factory()
    async with factory() as session:
        user = await _user(session, "trader_dec")
        account = await _account(session, user.id, initial_cash=Decimal("123456.7890"))
        assert isinstance(account.initial_cash, Decimal)
        assert account.initial_cash == Decimal("123456.7890")
        stock = await _stock(session, "DEC1")
        order = await TradingOrderService(session).create_order(
            _order_create(
                account.id, stock.id, order_type="limit", limit_price=Decimal("250.125"),
            )
        )
        assert isinstance(order.limit_price, Decimal)
        assert order.limit_price == Decimal("250.125")
        await session.rollback()


def test_no_float_precision_leak_in_storage() -> None:
    from sqlalchemy import Numeric

    assert isinstance(TradingAccount.__table__.c["initial_cash"].type, Numeric)
    assert isinstance(TradingAccount.__table__.c["cash_balance"].type, Numeric)
    assert isinstance(TradingOrder.__table__.c["limit_price"].type, Numeric)
    assert isinstance(TradingExecution.__table__.c["price"].type, Numeric)


# ---------------------------------------------------------------------------
# EXECUTION / FILL FOUNDATION
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_fills_structurally_allowed() -> None:
    factory = get_session_factory()
    async with factory() as session:
        user = await _user(session, "trader_fill")
        stock = await _stock(session, "FIL1")
        account = await _account(session, user.id)
        svc = TradingOrderService(session)
        order = await svc.create_order(_order_create(account.id, stock.id))
        repo = TradingExecutionRepository(session)
        now = datetime.now(UTC)
        session.add(
            TradingExecution(
                order_id=order.id, account_id=account.id, execution_id="E1",
                quantity=5, price=Decimal("100"), fees=Decimal("1"), executed_at=now,
            )
        )
        session.add(
            TradingExecution(
                order_id=order.id, account_id=account.id, execution_id="E2",
                quantity=5, price=Decimal("101"), fees=Decimal("1"), executed_at=now,
            )
        )
        await session.flush()
        fills = await repo.list_for_order(order.id)
        assert len(fills) == 2
        assert await repo.count_for_order(order.id) == 2
        await session.rollback()


@pytest.mark.asyncio
async def test_fill_belongs_to_order_and_validates() -> None:
    factory = get_session_factory()
    async with factory() as session:
        user = await _user(session, "trader_fillv")
        stock = await _stock(session, "FIL2")
        account = await _account(session, user.id)
        order = await TradingOrderService(session).create_order(
            _order_create(account.id, stock.id)
        )
        now = datetime.now(UTC)
        session.add(
            TradingExecution(
                order_id=order.id, account_id=account.id, execution_id="E1",
                quantity=10, price=Decimal("50"), fees=Decimal("0"), executed_at=now,
            )
        )
        await session.flush()
        bad_qty = TradingExecution(
            order_id=order.id, account_id=account.id, execution_id="E2",
            quantity=0, price=Decimal("50"), fees=Decimal("0"), executed_at=now,
        )
        session.add(bad_qty)
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

        factory2 = get_session_factory()
        async with factory2() as session2:
            user2 = await _user(session2, "trader_fillp")
            stock2 = await _stock(session2, "FIL3")
            account2 = await _account(session2, user2.id)
            order2 = await TradingOrderService(session2).create_order(
                _order_create(account2.id, stock2.id)
            )
            bad_price = TradingExecution(
                order_id=order2.id, account_id=account2.id, execution_id="P1",
                quantity=10, price=Decimal("0"), fees=Decimal("0"), executed_at=now,
            )
            session2.add(bad_price)
            with pytest.raises(IntegrityError):
                await session2.flush()
            await session2.rollback()


# ---------------------------------------------------------------------------
# REPOSITORY
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bounded_order_listing_and_filters() -> None:
    factory = get_session_factory()
    async with factory() as session:
        user = await _user(session, "trader_repo")
        stock = await _stock(session, "REP1")
        account = await _account(session, user.id)
        svc = TradingOrderService(session)
        for i in range(5):
            await svc.create_order(
                _order_create(account.id, stock.id, client_order_id=f"R{i}", side="buy")
            )
        repo = TradingOrderRepository(session)
        listed = await repo.list_for_account(account.id, limit=2)
        assert len(listed) == 2
        assert await repo.count_for_account(account.id) == 5
        by_status = await repo.list_for_account(account.id, status="created")
        assert len(by_status) == 5
        await session.rollback()


@pytest.mark.asyncio
async def test_account_scoped_query() -> None:
    factory = get_session_factory()
    async with factory() as session:
        user = await _user(session, "trader_scope")
        stock = await _stock(session, "REP2")
        a1 = await _account(session, user.id, name="S1")
        a2 = await _account(session, user.id, name="S2")
        svc = TradingOrderService(session)
        await svc.create_order(_order_create(a1.id, stock.id, client_order_id="S1O"))
        await svc.create_order(_order_create(a2.id, stock.id, client_order_id="S2O"))
        repo = TradingOrderRepository(session)
        assert await repo.get_for_account(1, a2.id) is None
        found = await repo.get_by_client_order_id(a1.id, "S1O")
        assert found is not None
        assert found.account_id == a1.id
        await session.rollback()


# ---------------------------------------------------------------------------
# TIME
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timestamps_are_timezone_aware() -> None:
    factory = get_session_factory()
    async with factory() as session:
        user = await _user(session, "trader_tz")
        account = await _account(session, user.id)
        assert account.created_at.tzinfo is not None
        stock = await _stock(session, "TZ1")
        order = await TradingOrderService(session).create_order(
            _order_create(account.id, stock.id)
        )
        assert order.created_at.tzinfo is not None
        now = datetime.now(UTC)
        session.add(
            TradingExecution(
                order_id=order.id, account_id=account.id, execution_id="T1",
                quantity=1, price=Decimal("10"), fees=Decimal("0"), executed_at=now,
            )
        )
        await session.flush()
        ex = (await TradingExecutionRepository(session).list_for_order(order.id))[0]
        assert ex.executed_at.tzinfo is not None
        await session.rollback()


# ---------------------------------------------------------------------------
# POSITION (foundation only)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_position_foundation_persists_without_calculation() -> None:
    factory = get_session_factory()
    async with factory() as session:
        user = await _user(session, "trader_pos")
        stock = await _stock(session, "POS1")
        account = await _account(session, user.id)
        session.add(
            TradingPosition(
                account_id=account.id, stock_id=stock.id, symbol="POS1",
                exchange="NSE", instrument_type="equity", position_side="long",
                net_quantity=10, average_price=Decimal("100"),
            )
        )
        await session.flush()
        repo = TradingPositionRepository(session)
        pos = await repo.get_for_account_instrument(account.id, stock.id, "equity")
        assert pos is not None
        assert pos.net_quantity == 10
        assert pos.average_price == Decimal("100")
        await session.rollback()


# ---------------------------------------------------------------------------
# BACKTESTING SEPARATION
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trading_order_does_not_create_backtest_records() -> None:
    factory = get_session_factory()
    async with factory() as session:
        user = await _user(session, "trader_sep")
        stock = await _stock(session, "SEP1")
        account = await _account(session, user.id)
        await TradingOrderService(session).create_order(
            _order_create(account.id, stock.id)
        )
        await session.flush()
        for table in (BacktestRun, BacktestTrade, BacktestMetric):
            count = await session.scalar(select(func.count()).select_from(table))
            assert count == 0, f"{table.__name__} should not be created by trading order"
        await session.rollback()


# ---------------------------------------------------------------------------
# SECURITY / SCOPE (static)
# ---------------------------------------------------------------------------


def test_no_broker_integration_or_external_http() -> None:
    forbidden_modules = ("OpenAlgo", "Flattrade", "Zerodha", "Shoonya")
    forbidden_libs = ("httpx", "requests")
    from app.repositories import trading as repo_mod
    from app.schemas import trading as schema_mod

    for module in (tf_module, repo_mod, schema_mod):
        src = inspect.getsource(module)
        for token in forbidden_modules:
            assert token not in src, f"forbidden broker reference {token}"
        for token in forbidden_libs:
            assert token not in src, f"forbidden network library {token}"


def test_trading_service_has_no_live_execution_capability() -> None:
    attrs = {name for name in dir(tf_module) if not name.startswith("_")}
    for banned in ("execute_order", "place_order", "fill_order"):
        assert banned not in attrs, f"unexpected live capability: {banned}"
