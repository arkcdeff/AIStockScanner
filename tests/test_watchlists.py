import pytest

from app.db.session import get_session_factory
from app.services.watchlist import WatchlistError, WatchlistService
from tests.auth_helpers import analyst_client
from tests.seed_helpers import seed_stock


async def _service() -> WatchlistService:
    factory = get_session_factory()
    session = factory()
    await session.__aenter__()
    return WatchlistService(session)


async def _cleanup(service: WatchlistService) -> None:
    await service.session.commit()
    await service.session.close()


# ---------------------------------------------------------------- service


@pytest.mark.asyncio
async def test_watchlist_create_and_list() -> None:
    service = await _service()
    try:
        await service.create({"name": "My Watchlist", "color": "#2563eb"})
        watchlists = await service.list()
        assert len(watchlists) == 1
        assert watchlists[0].name == "My Watchlist"
        assert watchlists[0].color == "#2563eb"
    finally:
        await _cleanup(service)


@pytest.mark.asyncio
async def test_watchlist_requires_name() -> None:
    service = await _service()
    try:
        with pytest.raises(WatchlistError):
            await service.create({"name": ""})
    finally:
        await _cleanup(service)


@pytest.mark.asyncio
async def test_default_watchlist_singleton() -> None:
    service = await _service()
    try:
        first = await service.create({"name": "A", "is_default": True})
        second = await service.create({"name": "B", "is_default": True})
        watchlists = await service.list()
        defaults = [wl for wl in watchlists if wl.is_default]
        assert len(defaults) == 1
        assert defaults[0].id == second.id
        assert first.id != second.id
    finally:
        await _cleanup(service)


@pytest.mark.asyncio
async def test_watchlist_update_and_delete() -> None:
    service = await _service()
    try:
        watchlist = await service.create({"name": "Old", "is_favorite": False})
        updated = await service.update(watchlist.id, {"name": "New", "is_favorite": True})
        assert updated.name == "New"
        assert updated.is_favorite is True
        await service.delete(watchlist.id)
        assert await service.get(watchlist.id) is None
    finally:
        await _cleanup(service)


@pytest.mark.asyncio
async def test_add_remove_update_stock() -> None:
    stock_id = await seed_stock(symbol="WLSTK")
    service = await _service()
    try:
        watchlist = await service.create({"name": "Watch"})
        entry = await service.add_stock(watchlist.id, stock_id, notes="watch closely", priority=2)
        assert entry.stock_id == stock_id
        assert entry.priority == 2

        with pytest.raises(WatchlistError):
            await service.add_stock(watchlist.id, stock_id)

        updated = await service.update_stock(watchlist.id, stock_id, priority=5, notes="top")
        assert updated.priority == 5
        assert updated.notes == "top"

        await service.remove_stock(watchlist.id, stock_id)
        assert await service.list_stocks(watchlist.id) == []
    finally:
        await _cleanup(service)


@pytest.mark.asyncio
async def test_list_stocks_includes_latest_price() -> None:
    stock_id = await seed_stock(symbol="WLPRC")
    service = await _service()
    try:
        watchlist = await service.create({"name": "Watch"})
        await service.add_stock(watchlist.id, stock_id)
        rows = await service.list_stocks(watchlist.id)
        assert len(rows) == 1
        assert rows[0]["stock"].symbol == "WLPRC"
        assert rows[0]["latest_price"] is not None
        assert float(rows[0]["latest_price"].close) > 0
    finally:
        await _cleanup(service)


@pytest.mark.asyncio
async def test_search_stocks() -> None:
    await seed_stock(symbol="WLSEARCH")
    service = await _service()
    try:
        results = await service.search_stocks("WLSEARCH")
        assert len(results) == 1
        assert results[0].symbol == "WLSEARCH"
    finally:
        await _cleanup(service)


# ------------------------------------------------------------------- API


@pytest.mark.asyncio
async def test_watchlist_api_crud() -> None:
    async with analyst_client() as client:
        created = await client.post(
            "/api/v1/watchlists/", json={"name": "API Watchlist", "color": "#16a34a"}
        )
        assert created.status_code == 200
        body = created.json()
        assert body["name"] == "API Watchlist"
        watchlist_id = body["id"]

        listed = await client.get("/api/v1/watchlists/")
        assert listed.status_code == 200
        assert len(listed.json()) == 1

        updated = await client.put(
            f"/api/v1/watchlists/{watchlist_id}", json={"name": "Renamed"}
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "Renamed"

        deleted = await client.delete(f"/api/v1/watchlists/{watchlist_id}")
        assert deleted.status_code == 204


@pytest.mark.asyncio
async def test_watchlist_api_add_and_list_stocks() -> None:
    stock_id = await seed_stock(symbol="WLAPI")
    async with analyst_client() as client:
        created = await client.post("/api/v1/watchlists/", json={"name": "API"})
        watchlist_id = created.json()["id"]

        added = await client.post(
            f"/api/v1/watchlists/{watchlist_id}/stocks",
            json={"stock_id": stock_id, "notes": "n", "priority": 3},
        )
        assert added.status_code == 200
        assert added.json()["stock_id"] == stock_id

        duplicate = await client.post(
            f"/api/v1/watchlists/{watchlist_id}/stocks", json={"stock_id": stock_id}
        )
        assert duplicate.status_code == 400

        stocks = await client.get(f"/api/v1/watchlists/{watchlist_id}/stocks")
        assert stocks.status_code == 200
        assert stocks.json()[0]["symbol"] == "WLAPI"
        assert stocks.json()[0]["latest_close"] is not None

        detail = await client.get(f"/api/v1/watchlists/{watchlist_id}")
        assert detail.status_code == 200
        assert detail.json()["stock_count"] == 1

        removed = await client.delete(f"/api/v1/watchlists/{watchlist_id}/stocks/{stock_id}")
        assert removed.status_code == 204


@pytest.mark.asyncio
async def test_watchlist_api_404() -> None:
    async with analyst_client() as client:
        response = await client.get("/api/v1/watchlists/999999")
        assert response.status_code == 404


# ------------------------------------------------------------------- UI


@pytest.mark.asyncio
async def test_watchlists_page_renders() -> None:
    service = await _service()
    try:
        await service.create({"name": "UI Watchlist"})
    finally:
        await _cleanup(service)
    async with analyst_client(browser=True) as client:
        response = await client.get("/watchlists")
        assert response.status_code == 200
        assert "Watchlists" in response.text
        assert "UI Watchlist" in response.text


@pytest.mark.asyncio
async def test_dashboard_shows_watchlist_cards() -> None:
    await seed_stock(symbol="WLDASH")
    service = await _service()
    try:
        await service.create({"name": "Dash Watchlist"})
    finally:
        await _cleanup(service)
    async with analyst_client(browser=True) as client:
        response = await client.get("/dashboard")
        assert response.status_code == 200
        assert "Watchlists" in response.text


@pytest.mark.asyncio
async def test_stock_detail_shows_watchlist_panel() -> None:
    await seed_stock(symbol="WLPANEL")
    async with analyst_client(browser=True) as client:
        response = await client.get("/stocks/WLPANEL")
        assert response.status_code == 200
        assert "Add to Watchlist" in response.text
