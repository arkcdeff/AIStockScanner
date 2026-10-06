"""Focused regression tests — Options workspace state propagation (G.11-OPT-13 Phase A).

Defect repaired here: ``refreshActiveTab()`` reloaded only the **active** tab, so
after an index or expiry change the other four tabs kept rendering the previous
underlying/expiry — and each of them kept doing so until the operator opened it
and waited out another request. Two data-integrity gaps were closed with it:

  * the five tab partials trusted the requested expiry even when it belonged to
    another underlying (a stale container or a stale bookmark), so they queried a
    contract the underlying does not list and rendered a blank tab;
  * ``get_multistrike_oi()`` and ``get_options_oi_vs_time()`` read every stored
    session, so a previous session's observations were read into the current
    intraday trajectory (and into the same clock-time bucket).

Authoritative rule under test: the three workspace controls (#opt-symbol,
#opt-expiry, #opt-strike-count) are the single source of truth; a tab whose own
inputs moved is invalidated — cleared to an explicit pending state, never left
presenting the previous selection as current; and only an expiry listed for the
selected underlying may drive a view (otherwise the nearest listed expiry is
used, never an invented or a foreign one).
"""
from __future__ import annotations

import re
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.db.session import get_session_factory
from app.models.derivative_contract import DerivativeContract
from app.models.market_index import MarketIndex
from app.models.option_chain import OptionChainQuote, OptionChainSnapshot
from app.services.derivatives_observation_service import ist_trade_date
from app.services.intraday_options_service import IST, IntradayOptionsService
from tests.auth_helpers import auth_client

WORKSPACE_TEMPLATE = "app/templates/options_workspace.html"
PAGE_ROUTE_MODULE = "app/api/routes/market_data_pages.py"

NIFTY_EXPIRIES = ["2026-10-06", "2026-10-13", "2026-10-20"]
BANKNIFTY_EXPIRIES = ["2026-10-27", "2026-11-23", "2026-12-29"]
FOREIGN_EXPIRY = "2026-10-06"  # a NIFTY weekly that BANKNIFTY does not list

TAB_PARTIALS = [
    "/market-data/options/chain-table",
    "/market-data/options/dashboard-cards",
    "/market-data/options/pcr-view",
    "/market-data/options/oi-chart",
    "/market-data/options/oi-change-view",
]

DERIVATIVES_OPTION_PAGES = [
    "/market-data/derivatives/oi-statistics",
    "/market-data/derivatives/oi-change",
    "/market-data/derivatives/multistrike-oi",
    "/market-data/derivatives/options-oi-time",
]


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _script(path: str = WORKSPACE_TEMPLATE) -> str:
    src = _read(path)
    return src[src.index("Options Workspace Auto-Sync Script") :]


def _function_body(script: str, name: str) -> str:
    """Body of a top-level ``function name(...) { ... }`` in the workspace script."""
    start = script.index(f"function {name}(")
    end = script.index("\n}\n", start)
    return script[start : end + 3]


def _expiry_rows(tokens: list[str]) -> list[dict]:
    return [
        {
            "expiry_date": token,
            "display_label": token,
            "days_to_expiry": 7 * (idx + 1),
            "expiry_type": "WEEKLY" if idx == 0 else "MONTHLY",
            "is_nearest": idx == 0,
            "is_next": idx == 1,
            "is_current_monthly": idx == 1,
            "contract_count": 10,
            "provider": "TEST",
            "provenance": "TEST",
            "is_provider_listed": True,
            "status": "ACTIVE",
        }
        for idx, token in enumerate(tokens)
    ]


def _install_route_stubs(
    monkeypatch: pytest.MonkeyPatch,
    expiries_by_symbol: dict[str, list[str]],
    *,
    lookup_failure: bool = False,
) -> dict:
    """Patch the route-facing service with recording stubs.

    Returns a recorder holding, per view, the ``(underlying, expiry_date,
    strike_count)`` each render call actually received.
    """
    from app.services import intraday_options_service as svc_module

    recorder: dict = {"calls": {}, "expiry_lookups": []}

    async def _supported(self):  # noqa: ANN001
        return [{"symbol": sym, "name": sym, "strike_step": 50} for sym in expiries_by_symbol]

    async def _available(self, underlying: str):  # noqa: ANN001
        symbol = underlying.strip().upper()
        recorder["expiry_lookups"].append(symbol)
        if lookup_failure:
            raise RuntimeError("provider unavailable")
        if symbol not in expiries_by_symbol:
            raise ValueError(f"Unsupported underlying symbol '{underlying}'")
        return _expiry_rows(expiries_by_symbol[symbol])

    async def _chain(self, underlying, expiry_date=None, strike_count=None, **kwargs):  # noqa: ANN001
        recorder["calls"]["chain"] = (underlying, expiry_date, strike_count)
        return {
            "ladder": [],
            "selected_expiry": expiry_date.isoformat() if expiry_date else None,
            "underlying": underlying,
            "underlying_name": underlying,
            "spot_price": 0.0,
            "atm_strike": None,
            "strike_step": 50.0,
            "summary": {"strike_coverage_label": "ATM ± 0 Strikes (0 strikes)"},
            "provider": "TEST",
            "freshness": "TEST",
            "as_of_ist": "—",
            "market_session": "CLOSED",
            "is_market_open": False,
            "available_expiries": list(expiries_by_symbol.get(underlying.strip().upper(), [])),
        }

    def _recorder(name: str):  # noqa: ANN202
        async def _call(self, underlying, expiry_date=None, *args, **kwargs):  # noqa: ANN001
            recorder["calls"][name] = (underlying, expiry_date, kwargs.get("strike_count"))
            return None

        return _call

    monkeypatch.setattr(svc_module.IntradayOptionsService, "get_supported_underlyings", _supported)
    monkeypatch.setattr(svc_module.IntradayOptionsService, "get_available_expiries", _available)
    monkeypatch.setattr(svc_module.IntradayOptionsService, "get_option_chain_ladder", _chain)
    monkeypatch.setattr(svc_module.IntradayOptionsService, "get_option_dashboard", _recorder("dashboard"))
    monkeypatch.setattr(svc_module.IntradayOptionsService, "get_live_pcr", _recorder("pcr"))
    monkeypatch.setattr(svc_module.IntradayOptionsService, "get_call_vs_put_oi", _recorder("oi"))
    monkeypatch.setattr(svc_module.IntradayOptionsService, "get_oi_change_analysis", _recorder("oi_change"))
    return recorder


# ---------------------------------------------------------------------------
# 1. Tab partials — the expiry of the selected underlying only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", TAB_PARTIALS)
@pytest.mark.asyncio
async def test_every_tab_partial_normalizes_a_foreign_expiry(monkeypatch, url):
    """A partial request carrying another underlying's expiry is normalized."""
    recorder = _install_route_stubs(monkeypatch, {"BANKNIFTY": BANKNIFTY_EXPIRIES})
    async with auth_client(role="viewer", browser=True) as client:
        r = await client.get(url, params={"symbol": "BANKNIFTY", "expiry": FOREIGN_EXPIRY})
    assert r.status_code == 200
    view = "chain" if "chain-table" in url else next(
        name for name in ("dashboard", "pcr", "oi", "oi_change") if name in recorder["calls"]
    )
    underlying, expiry_date, _strike = recorder["calls"][view]
    assert underlying == "BANKNIFTY"
    assert expiry_date == date(2026, 10, 27), "the nearest listed expiry must drive the view"
    assert FOREIGN_EXPIRY not in r.text


@pytest.mark.asyncio
async def test_listed_expiry_is_used_verbatim_by_every_tab_partial(monkeypatch):
    recorder = _install_route_stubs(monkeypatch, {"BANKNIFTY": BANKNIFTY_EXPIRIES})
    async with auth_client(role="viewer", browser=True) as client:
        for url in TAB_PARTIALS:
            r = await client.get(url, params={"symbol": "BANKNIFTY", "expiry": "2026-11-23"})
            assert r.status_code == 200
    for name in ("chain", "dashboard", "pcr", "oi", "oi_change"):
        assert recorder["calls"][name][1] == date(2026, 11, 23), name


@pytest.mark.asyncio
async def test_tab_partial_invents_no_expiry_when_none_is_listed(monkeypatch):
    recorder = _install_route_stubs(monkeypatch, {"BANKNIFTY": []})
    async with auth_client(role="viewer", browser=True) as client:
        r = await client.get(
            "/market-data/options/oi-change-view",
            params={"symbol": "BANKNIFTY", "expiry": FOREIGN_EXPIRY},
        )
    assert r.status_code == 200
    assert recorder["calls"]["oi_change"][1] is None
    assert "No Open Interest Change Data Available" in r.text


@pytest.mark.asyncio
async def test_unknown_underlying_tab_partial_reports_no_scope_instead_of_a_500(monkeypatch):
    recorder = _install_route_stubs(monkeypatch, {"BANKNIFTY": BANKNIFTY_EXPIRIES})

    async def _chain_raises(self, underlying, expiry_date=None, strike_count=None, **kwargs):  # noqa: ANN001
        recorder["calls"]["chain"] = (underlying, expiry_date, strike_count)
        raise ValueError(f"Unsupported underlying symbol '{underlying}'")

    monkeypatch.setattr(
        IntradayOptionsService, "get_option_chain_ladder", _chain_raises
    )
    async with auth_client(role="viewer", browser=True) as client:
        r = await client.get(
            "/market-data/options/chain-table", params={"symbol": "NOT_A_REAL_SYMBOL"}
        )
    assert r.status_code == 200
    assert recorder["calls"]["chain"][1] is None, "no expiry may be invented for an unknown underlying"


# ---------------------------------------------------------------------------
# 2. The canonical expiry rule itself (shared by pages, partials and panels)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_canonical_expiry_returns_a_listed_expiry_unchanged():
    from app.api.routes import market_data_pages as pages

    class _Service:
        async def get_available_expiries(self, symbol: str):  # noqa: ANN201
            return _expiry_rows(BANKNIFTY_EXPIRIES)

    resolved = await pages._canonical_listed_expiry(
        _Service(), "BANKNIFTY", date(2026, 11, 23)
    )
    assert resolved == "2026-11-23"


@pytest.mark.asyncio
async def test_canonical_expiry_falls_back_to_the_nearest_listed_expiry():
    from app.api.routes import market_data_pages as pages

    class _Service:
        async def get_available_expiries(self, symbol: str):  # noqa: ANN201
            return _expiry_rows(BANKNIFTY_EXPIRIES)

    assert await pages._canonical_listed_expiry(
        _Service(), "BANKNIFTY", date(2026, 10, 6)
    ) == "2026-10-27"
    assert await pages._canonical_listed_expiry(_Service(), "BANKNIFTY", None) == "2026-10-27"


@pytest.mark.asyncio
async def test_canonical_expiry_reports_none_when_nothing_is_listed():
    from app.api.routes import market_data_pages as pages

    class _Empty:
        async def get_available_expiries(self, symbol: str):  # noqa: ANN201
            return []

    assert await pages._canonical_listed_expiry(_Empty(), "BANKNIFTY", date(2026, 10, 6)) is None
    assert await pages._canonical_listed_expiry(_Empty(), "BANKNIFTY", None) is None


@pytest.mark.asyncio
async def test_canonical_expiry_tolerates_unknown_underlying_and_failed_lookup():
    from app.api.routes import market_data_pages as pages

    class _Unsupported:
        async def get_available_expiries(self, symbol: str):  # noqa: ANN201
            raise ValueError("Unsupported underlying symbol 'X'")

    class _Failing:
        async def get_available_expiries(self, symbol: str):  # noqa: ANN201
            raise RuntimeError("provider unavailable")

    target = date(2026, 11, 23)
    assert await pages._canonical_listed_expiry(_Unsupported(), "X", target) is None
    assert await pages._canonical_listed_expiry(_Failing(), "NIFTY", target) is None


def test_every_page_and_partial_route_uses_the_shared_expiry_rule():
    src = _read(PAGE_ROUTE_MODULE)
    # Page route + 5 tab partials + 4 standalone derivatives pages.
    assert src.count("_canonical_listed_expiry(") >= 11, "every option view must share the rule"
    # The replay workbench is the only route that keeps its own expiry logic: its
    # expiries are session-derived and it never queries an option contract by them.
    assert src.count('expiry.isoformat() if expiry else') == 1


# ---------------------------------------------------------------------------
# 3. Workspace state — one authoritative scope, explicit invalidation
# ---------------------------------------------------------------------------


def test_workspace_state_is_read_from_the_controls_only():
    script = _script()
    assert "const TAB_CONTAINERS = {" in script
    assert "const TAB_SCOPE_INPUTS = {" in script
    assert "const renderedScope = {};" in script
    assert "const pendingScope = {};" in script
    # Every tab's scope is derived from the control read, never from a stored value.
    scope_fn = _function_body(script, "scopeForPane")
    assert "getFilterParams()" in scope_fn
    assert "TAB_SCOPE_INPUTS[pane]" in scope_fn
    # A tab that never reads the strike range is not invalidated by it.
    assert re.search(r"'tab-dashboard': \['symbol', 'expiry'\]", script)
    assert re.search(r"'tab-pcr': \['symbol', 'expiry'\]", script)
    assert re.search(r"'tab-chain': \['symbol', 'expiry', 'strikeCount'\]", script)
    assert re.search(r"'tab-oichange': \['symbol', 'expiry', 'strikeCount'\]", script)


def test_all_five_workspace_tabs_are_covered_by_the_state_map():
    script = _script()
    for pane in ("tab-chain", "tab-dashboard", "tab-pcr", "tab-oi", "tab-oichange"):
        assert f"'{pane}': '#" in script, f"{pane} must have a container mapping"
    assert script.count("data-workspace-load") >= 1


def test_index_change_invalidates_every_tab_immediately():
    script = _script()
    block = re.search(
        r"if \(id === 'opt-symbol'\) \{[\s\S]*?\n    \}", script
    )
    assert block is not None, "the underlying change handler must exist"
    body = block.group(0)
    hold_at = body.index("expiryOptionsRefreshing = true;")
    invalidate_at = body.index("invalidateAllTabs();")
    assert hold_at < invalidate_at, (
        "the outgoing underlying's tabs must be invalidated as soon as the symbol moves, "
        "not when the new expiries arrive"
    )
    invalidate_fn = _function_body(script, "invalidateAllTabs")
    assert "setTabPending(pane, 'invalidated')" in invalidate_fn


def test_invalidation_replaces_previous_content_with_an_explicit_state():
    script = _script()
    pending_fn = _function_body(script, "setTabPending")
    assert "renderedScope[pane] = null;" in pending_fn
    assert "container.removeAttribute('data-workspace-scope')" in pending_fn
    assert "container.innerHTML = pendingHtml(" in pending_fn
    state_fn = _function_body(script, "invalidateStaleTabs")
    assert "renderedScope[pane] !== current" in state_fn
    # The cleared state names the current selection, so it cannot be read as data.
    pending_html = _function_body(script, "pendingHtml")
    assert "Not loaded for the current selection" in pending_html
    assert "escapeHtml" in pending_html


def test_expiry_change_invalidates_dependents_then_reloads_the_active_tab():
    script = _script()
    refresh_fn = _function_body(script, "refreshActiveTab")
    assert refresh_fn.index("invalidateStaleTabs();") < refresh_fn.index("loadTab(activeTab)")
    assert "if (expiryOptionsRefreshing)" in refresh_fn
    assert "return;" in refresh_fn
    change_handler = script[script.index("When expiry or strike count changes"):]
    assert "e.target.id === 'opt-expiry' || e.target.id === 'opt-strike-count'" in change_handler
    assert "refreshActiveTab();" in change_handler


def test_opening_a_tab_loads_only_when_it_does_not_hold_the_current_selection():
    script = _script()
    tab_handler = script[script.index("const tabButtons = document.querySelectorAll"):]
    assert "shown.bs.tab" in tab_handler
    assert "if (renderedScope[pane] === current || pendingScope[pane] === current)" in tab_handler
    assert "loadTab(btn.id);" in tab_handler


def test_a_completed_swap_records_the_scope_it_rendered():
    script = _script()
    mark_fn = _function_body(script, "markTabRendered")
    assert "pendingScope[pane] || scopeForPane(pane)" in mark_fn
    assert "container.setAttribute('data-workspace-scope', scope);" in mark_fn
    assert "data-workspace-state', 'ready'" in mark_fn
    assert "TAB_CONTAINERS[pane] === '#' + target.id" in script


def test_a_failed_tab_request_cannot_leave_stale_content():
    script = _script()
    assert "'htmx:responseError'" in script
    assert "'htmx:sendError'" in script
    assert "'htmx:timeout'" in script
    error_block = script[script.index("['htmx:responseError'"):]
    assert "setTabPending(pane, 'error'" in error_block
    assert "setTabPending(active, 'error'" in error_block


def test_workspace_has_one_expiry_control_and_no_hardcoded_dates():
    script = _script()
    assert script.count("getFilterParams()") >= 2
    assert re.search(r"const \{ symbol, expiry, strikeCount \} = getFilterParams\(\);", script)
    assert "expiry_date =" not in script
    assert not re.search(r"\b20\d\d-\d\d-\d\d\b", script), "no hardcoded date in the workspace script"
    src = _read(WORKSPACE_TEMPLATE)
    assert src.count('id="opt-expiry"') == 1


def test_page_route_renders_the_strike_range_control_it_was_asked_for():
    src = _read(PAGE_ROUTE_MODULE)
    route = src[src.index("async def options_workspace_page"):]
    route = route[: route.index('@router.get("/derivatives/oi-statistics"')]
    assert 'strike_count: str = Query(' in route
    assert "parse_strike_count_token(strike_count)" in route
    assert "selected_strike_count" in route
    assert "strike_count=parsed_strike_count" in route
    template = _read(WORKSPACE_TEMPLATE)
    assert "_selected_strike_count" in template
    assert template.count("{{ 'selected' if _selected_strike_count ==") == 6


@pytest.mark.asyncio
async def test_workspace_page_renders_the_requested_strike_range_and_expiry(monkeypatch):
    """A reloaded workspace URL reproduces the controls it was mirrored from."""
    recorder = _install_route_stubs(monkeypatch, {"BANKNIFTY": BANKNIFTY_EXPIRIES})
    async with auth_client(role="viewer", browser=True) as client:
        r = await client.get(
            "/market-data/options",
            params={"symbol": "BANKNIFTY", "expiry": FOREIGN_EXPIRY, "strike_count": "20"},
        )
    assert r.status_code == 200
    calls = recorder["calls"]
    for name in ("chain", "dashboard", "pcr", "oi", "oi_change"):
        assert calls[name][1] == date(2026, 10, 27), name
    # The strike-range control and the tabs that consume it agree.
    assert calls["chain"][2] == 20
    assert calls["oi"][2] == 20
    assert calls["oi_change"][2] == 20
    assert calls["dashboard"][2] is None and calls["pcr"][2] is None
    assert 'value="20" selected' in r.text
    assert FOREIGN_EXPIRY not in r.text


@pytest.mark.asyncio
async def test_workspace_page_rejects_a_malformed_strike_range(monkeypatch):
    _install_route_stubs(monkeypatch, {"BANKNIFTY": BANKNIFTY_EXPIRIES})
    async with auth_client(role="viewer", browser=True) as client:
        r = await client.get("/market-data/options", params={"strike_count": "7"})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# 4. Session safety — real observations, two sessions, one canonical IST rule
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
async def _clean_derivative_tables(_clean_test_db):
    """Clear the derivative tables around each test.

    ``conftest`` refuses to run unless the database name contains "test", so this
    can never touch a real database.
    """
    factory = get_session_factory()
    for _ in range(2):
        async with factory() as session:
            await session.execute(text("DELETE FROM option_chain_quotes"))
            await session.execute(text("DELETE FROM option_chain_snapshots"))
            await session.execute(text("DELETE FROM derivative_daily_prices"))
            await session.execute(text("DELETE FROM derivative_contracts"))
            await session.execute(text("DELETE FROM market_indices"))
            await session.commit()
        yield
        break


async def _seed_index(code: str = "NIFTY") -> int:
    factory = get_session_factory()
    async with factory() as session:
        index = MarketIndex(code=code, name=f"{code} TEST INDEX", country="IN", exchange="NSE")
        session.add(index)
        await session.commit()
        return index.id


async def _seed_contracts(index_id: int, expiry: date, strikes: tuple[Decimal, ...]) -> None:
    factory = get_session_factory()
    async with factory() as session:
        for strike in strikes:
            for option_type in ("CE", "PE"):
                symbol = f"NIFTY{int(strike)}{option_type}"
                session.add(
                    DerivativeContract(
                        exchange="NSE",
                        segment="NFO",
                        instrument_type="OPTION",
                        underlying_index_id=index_id,
                        trading_symbol=symbol,
                        expiry_date=expiry,
                        strike_price=strike,
                        option_type=option_type,
                        lot_size=65,
                        idempotency_hash=f"hash-{symbol}-{expiry.isoformat()}",
                        available_at=expiry,
                        source_name="NSE_TEST",
                    )
                )
        await session.commit()


async def _seed_observation(
    index_id: int,
    *,
    expiry: date,
    market_ts: datetime,
    oi_by_strike: dict[tuple[Decimal, str], int | None],
) -> None:
    """Persist one real-format snapshot plus its quotes for one session instant."""
    factory = get_session_factory()
    async with factory() as session:
        snapshot = OptionChainSnapshot(
            underlying_index_id=index_id,
            chain_scope="SINGLE_EXPIRY",
            expiry_scope=expiry,
            spot_price=Decimal("24500.00"),
            market_timestamp=market_ts,
            source_timestamp=market_ts,
            ingestion_timestamp=market_ts + timedelta(seconds=1),
            available_at=market_ts + timedelta(seconds=1),
            source_name="NSE_TEST",
            raw_payload_hash=f"payload-{market_ts.isoformat()}",
            normalization_version="TEST-1",
            batch_id=f"batch-{market_ts.isoformat()}",
            revision=1,
            quality_status="VALID",
            quality_flags={},
        )
        session.add(snapshot)
        await session.flush()

        contracts = {}
        for strike, option_type in oi_by_strike:
            row = await session.execute(
                text("SELECT id FROM derivative_contracts WHERE trading_symbol = :s"),
                {"s": f"NIFTY{int(strike)}{option_type}"},
            )
            contracts[(strike, option_type)] = row.scalar()

        for (strike, option_type), oi in oi_by_strike.items():
            session.add(
                OptionChainQuote(
                    snapshot_id=snapshot.id,
                    contract_id=contracts[(strike, option_type)],
                    trade_date=ist_trade_date(market_ts),
                    strike_price=strike,
                    option_type=option_type,
                    expiry_date=expiry,
                    ltp=Decimal("120.00"),
                    volume=1000,
                    open_interest=oi,
                    quality_flags={},
                )
            )
        await session.commit()


def _session_instant(target: date, at=time(9, 30)) -> datetime:
    """A real IST session clock time on ``target``, expressed in UTC."""
    return datetime.combine(target, at, tzinfo=IST).astimezone(UTC)


async def _resolved_scope() -> tuple[date, tuple[Decimal, ...]]:
    """The expiry and strikes this service actually resolves under mock mode.

    The seeded observations must belong to the scope the service resolves, so the
    scope is read from the service instead of being assumed.
    """
    service = IntradayOptionsService(force_mock=True)
    ladder = await service.get_option_chain_ladder("NIFTY", strike_count=5)
    expiry = date.fromisoformat(ladder["selected_expiry"])
    strikes = tuple(
        Decimal(str(row["strike_price"])) for row in ladder["ladder"][4:6]
    )
    return expiry, strikes


@asynccontextmanager
async def _open_service():
    from app.services.option_chain_adapter import MockOptionChainAdapter

    factory = get_session_factory()
    session = factory()
    try:
        yield IntradayOptionsService(adapter=MockOptionChainAdapter(), session=session)
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_multistrike_oi_reads_only_the_current_ist_session():
    """A previous session at the same clock time is not part of today's trajectory."""
    today = ist_trade_date(datetime.now(UTC))
    previous = today - timedelta(days=1)
    expiry, strikes = await _resolved_scope()
    index_id = await _seed_index()
    await _seed_contracts(index_id, expiry, strikes)
    today_oi = {strikes[0]: 1200, strikes[1]: 800}
    previous_oi = {strikes[0]: 5000, strikes[1]: 4000}
    await _seed_observation(
        index_id,
        expiry=expiry,
        market_ts=_session_instant(previous),
        oi_by_strike={
            (strike, side): oi for strike, oi in previous_oi.items() for side in ("CE", "PE")
        },
    )
    await _seed_observation(
        index_id,
        expiry=expiry,
        market_ts=_session_instant(today),
        oi_by_strike={
            (strike, side): oi for strike, oi in today_oi.items() for side in ("CE", "PE")
        },
    )

    async with _open_service() as service:
        payload = await service.get_multistrike_oi(
            "NIFTY", expiry_date=expiry, strike_count=5, interval_minutes=5
        )

    assert payload["observation_session"] == today.isoformat()
    entry = next(
        row for row in payload["strike_series"] if Decimal(str(row["strike_price"])) == strikes[0]
    )
    assert entry["call"]["available"] is True
    assert entry["call"]["oi"] == [today_oi[strikes[0]]], (
        "the previous session's observation must never be read as this session's value"
    )
    assert entry["call"]["observation_count"] == 1


@pytest.mark.asyncio
async def test_multistrike_oi_previous_session_is_not_the_current_session():
    """Without current-session data the trajectory is unavailable, not historical."""
    today = ist_trade_date(datetime.now(UTC))
    previous = today - timedelta(days=5)
    expiry, strikes = await _resolved_scope()
    index_id = await _seed_index()
    await _seed_contracts(index_id, expiry, strikes)
    await _seed_observation(
        index_id,
        expiry=expiry,
        market_ts=_session_instant(previous),
        oi_by_strike={(strikes[0], "CE"): 3300, (strikes[0], "PE"): 2200},
    )

    async with _open_service() as service:
        current = await service.get_multistrike_oi("NIFTY", expiry_date=expiry, strike_count=5)
        historical = await service.get_multistrike_oi(
            "NIFTY", expiry_date=expiry, strike_count=5, session_date=previous
        )

    assert current["series_available"] is False
    assert current["coverage"]["available"] is False
    assert current["observation_session"] == today.isoformat()
    # The same read, explicitly pointed at the session the observation belongs to,
    # proves the data exists and that the session bound is what excluded it.
    assert historical["observation_session"] == previous.isoformat()
    assert historical["series_available"] is True
    entry = next(
        row for row in historical["strike_series"] if Decimal(str(row["strike_price"])) == strikes[0]
    )
    assert entry["call"]["oi"] == [3300]


@pytest.mark.asyncio
async def test_options_oi_vs_time_reads_only_the_current_ist_session():
    """Two sessions must never collapse into one clock-time bucket."""
    today = ist_trade_date(datetime.now(UTC))
    previous = today - timedelta(days=1)
    expiry, strikes = await _resolved_scope()
    index_id = await _seed_index()
    await _seed_contracts(index_id, expiry, strikes)
    await _seed_observation(
        index_id,
        expiry=expiry,
        market_ts=_session_instant(previous),
        oi_by_strike={(strikes[0], "CE"): 7000, (strikes[1], "PE"): 6000},
    )
    await _seed_observation(
        index_id,
        expiry=expiry,
        market_ts=_session_instant(today),
        oi_by_strike={(strikes[0], "CE"): 900, (strikes[1], "PE"): 0},
    )

    async with _open_service() as service:
        payload = await service.get_options_oi_vs_time(
            "NIFTY", expiry_date=expiry, strike_count=5, interval_minutes=5
        )

    assert payload["observation_session"] == today.isoformat()
    assert len(payload["time_labels"]) == 1, "one session instant, not one per stored session"
    assert payload["call_oi_series"] == [900]
    assert payload["put_oi_series"] == [0], "a genuine zero Open Interest stays zero"
    assert payload["aggregate_available"] is True


@pytest.mark.asyncio
async def test_options_oi_vs_time_uses_the_evaluation_instants_session():
    """Point-in-time reads use the evaluation instant's session, not today's."""
    today = ist_trade_date(datetime.now(UTC))
    previous = today - timedelta(days=2)
    expiry, strikes = await _resolved_scope()
    index_id = await _seed_index()
    await _seed_contracts(index_id, expiry, strikes)
    await _seed_observation(
        index_id,
        expiry=expiry,
        market_ts=_session_instant(previous),
        oi_by_strike={(strikes[0], "CE"): 4321, (strikes[0], "PE"): 1234},
    )

    async with _open_service() as service:
        current = await service.get_options_oi_vs_time("NIFTY", expiry_date=expiry, strike_count=5)
        replayed = await service.get_options_oi_vs_time(
            "NIFTY",
            expiry_date=expiry,
            strike_count=5,
            evaluation_at=_session_instant(previous, at=time(15, 0)),
        )

    assert current["series_available"] is False
    assert current["observation_session"] == today.isoformat()
    # Intended historical semantics are preserved: an explicit session still
    # returns every observation of that session.
    assert replayed["series_available"] is True
    assert replayed["observation_session"] == previous.isoformat()
    assert replayed["call_oi_series"] == [4321]
    assert replayed["put_oi_series"] == [1234]


@pytest.mark.asyncio
async def test_missing_open_interest_stays_unavailable_and_does_not_become_zero():
    today = ist_trade_date(datetime.now(UTC))
    expiry, strikes = await _resolved_scope()
    index_id = await _seed_index()
    await _seed_contracts(index_id, expiry, strikes)
    await _seed_observation(
        index_id,
        expiry=expiry,
        market_ts=_session_instant(today),
        oi_by_strike={(strikes[0], "CE"): None, (strikes[0], "PE"): None},
    )

    async with _open_service() as service:
        multistrike = await service.get_multistrike_oi(
            "NIFTY", expiry_date=expiry, strike_count=5
        )
        oi_time = await service.get_options_oi_vs_time(
            "NIFTY", expiry_date=expiry, strike_count=5
        )

    entry = next(
        row for row in multistrike["strike_series"] if Decimal(str(row["strike_price"])) == strikes[0]
    )
    assert entry["call"]["available"] is True
    assert entry["call"]["oi"] == [None], "an unavailable observation is not a zero"
    assert oi_time["call_oi_series"] == [None]
    assert oi_time["aggregate_available"] is False


@pytest.mark.asyncio
async def test_rest_endpoints_thread_session_date_into_the_session_bound_reads():
    """The JSON twins expose ``session_date`` and pass it through."""
    from app.api.routes import options as options_module
    from app.services import intraday_options_service as svc_module

    recorded: dict[str, object] = {}

    async def _multistrike(self, underlying, **kwargs):  # noqa: ANN001
        recorded["multistrike"] = kwargs.get("session_date")
        return {"underlying": underlying}

    async def _oi_time(self, underlying, **kwargs):  # noqa: ANN001
        recorded["oi_time"] = kwargs.get("session_date")
        return {"underlying": underlying}

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(svc_module.IntradayOptionsService, "get_multistrike_oi", _multistrike)
    monkeypatch.setattr(svc_module.IntradayOptionsService, "get_options_oi_vs_time", _oi_time)
    try:
        async with auth_client(role="viewer") as client:
            r1 = await client.get(
                "/api/v1/market-data/options/multistrike-oi",
                params={"underlying": "NIFTY", "session_date": "2026-09-24"},
            )
            r2 = await client.get(
                "/api/v1/market-data/options/options-oi-time",
                params={"underlying": "NIFTY", "session_date": "2026-09-24"},
            )
    finally:
        monkeypatch.undo()
    assert r1.status_code == 200 and r2.status_code == 200
    assert recorded["multistrike"] == date(2026, 9, 24)
    assert recorded["oi_time"] == date(2026, 9, 24)
    assert hasattr(options_module, "router")
