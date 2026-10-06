"""G.11-OPT-9 — regression tests for the six defects closed in the acceptance audit.

Every test here pins one confirmed, browser-reproduced defect to its fix:

* **C-1**  ``/market-data/derivatives/oi-change`` returned HTTP 422 when the shared
  derivatives filter panel submitted its "All listed strikes" option.
* **D-02** OI Statistics rendered the shared panel's Strike Count control, whose
  ``strikes`` parameter the route never reads (a dead control).
* **D-03** the Options workspace underlying selector swapped content over AJAX but
  never updated the address bar, so URL and displayed data disagreed.
* **D-01** OI Replay labelled the stored records it replayed with the **live**
  adapter name (``Source: UPSTOX``) and showed today's live spot beside the
  replayed point-in-time spot with nothing distinguishing them.
* **D-05** the futures payload's ``summary_cards.underlying_spot`` was hardcoded to
  ``None``/``UNAVAILABLE`` while the top-level ``underlying_spot`` carried the real
  value — one payload making two contradictory claims about one field.
* **D-08-04** the futures interval ledger rendered a literal ``None`` badge for the
  ``LEVEL_BREAK_NONE`` state, reading as a missing value.

Fixtures are deterministic, clearly-labelled ``NSE_TEST`` rows written into the
isolated test database only (conftest enforces a ``*test*`` database name and
truncates per test). No value produced here is ever presented as live.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.db.session import get_session_factory
from app.models.derivative_contract import DerivativeContract
from app.models.market_index import MarketIndex
from app.models.option_chain import OptionChainQuote, OptionChainSnapshot
from app.services.derivatives_futures_ledger import (
    LEVEL_BREAK_BOTH,
    LEVEL_BREAK_HIGH,
    LEVEL_BREAK_LOW,
    LEVEL_BREAK_NONE,
    _summary_cards,
    compute_level_break,
)
from app.services.derivatives_observation_service import (
    FuturesObservation,
    ist_trade_date,
)

SESSION = date(2026, 9, 24)
EXPIRY = date(2026, 9, 24)
# 09:45 IST == 04:15 UTC on 2026-09-24; the stored observation became available
# two seconds later, which is what the point-in-time gate keys on.
MARKET_TS = datetime(2026, 9, 24, 4, 15, tzinfo=UTC)
AVAILABLE_AT = datetime(2026, 9, 24, 4, 15, 2, tzinfo=UTC)
SPOT = Decimal("24550.2500")
STRIKES = (Decimal("24000"), Decimal("24050"))


@pytest.fixture(autouse=True)
async def _clean_derivative_tables(_clean_test_db):
    """Isolate the derivative tables for every test in this module."""
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text("DELETE FROM option_chain_quotes"))
        await session.execute(text("DELETE FROM option_chain_snapshots"))
        await session.execute(text("DELETE FROM derivative_daily_prices"))
        await session.execute(text("DELETE FROM derivative_contracts"))
        await session.execute(text("DELETE FROM market_indices"))
        await session.commit()
    yield


@pytest.fixture
async def browser_client():
    from tests.auth_helpers import auth_client

    async with auth_client(is_superuser=True, browser=True) as client:
        yield client


# --------------------------------------------------------------------------- #
# NSE_TEST seeding (TEST_FIXTURE_ONLY)
# --------------------------------------------------------------------------- #
async def _seed_replay_fixture(source_name: str = "NSE_TEST") -> int:
    """One NIFTY snapshot plus two two-sided strikes, stored as a labelled fixture."""
    factory = get_session_factory()
    async with factory() as session:
        index = MarketIndex(code="NIFTY", name="NIFTY TEST INDEX", country="IN", exchange="NSE")
        session.add(index)
        await session.commit()
        index_id = index.id

        for strike in STRIKES:
            for option_type in ("CE", "PE"):
                session.add(
                    DerivativeContract(
                        exchange="NSE",
                        segment="NFO",
                        instrument_type="OPTION",
                        underlying_index_id=index_id,
                        trading_symbol=f"NIFTY{int(strike)}{option_type}",
                        expiry_date=EXPIRY,
                        strike_price=strike,
                        option_type=option_type,
                        lot_size=65,
                        idempotency_hash=f"hash-NIFTY{int(strike)}{option_type}",
                        available_at=SESSION,
                        source_name=source_name,
                    )
                )
        await session.commit()

        snapshot = OptionChainSnapshot(
            underlying_index_id=index_id,
            chain_scope="SINGLE_EXPIRY",
            expiry_scope=EXPIRY,
            spot_price=SPOT,
            market_timestamp=MARKET_TS,
            source_timestamp=MARKET_TS,
            ingestion_timestamp=AVAILABLE_AT,
            available_at=AVAILABLE_AT,
            source_name=source_name,
            raw_payload_hash="payload-opt9",
            normalization_version="TEST-1",
            batch_id="batch-opt9",
            revision=1,
            quality_status="VALID",
            quality_flags={},
        )
        session.add(snapshot)
        await session.flush()

        for strike in STRIKES:
            for option_type in ("CE", "PE"):
                row = await session.execute(
                    text("SELECT id FROM derivative_contracts WHERE trading_symbol = :s"),
                    {"s": f"NIFTY{int(strike)}{option_type}"},
                )
                contract_id = row.scalar()
                assert contract_id is not None
                session.add(
                    OptionChainQuote(
                        snapshot_id=snapshot.id,
                        contract_id=contract_id,
                        trade_date=ist_trade_date(MARKET_TS),
                        strike_price=strike,
                        option_type=option_type,
                        expiry_date=EXPIRY,
                        ltp=Decimal("155.00"),
                        volume=5000,
                        open_interest=50000,
                        quality_flags={},
                    )
                )
        await session.commit()
        return snapshot.id


# --------------------------------------------------------------------------- #
# C-1 — OI Change must accept the vocabulary the filter panel actually submits
# --------------------------------------------------------------------------- #
OI_CHANGE = "/market-data/derivatives/oi-change?symbol=NIFTY"


async def test_oi_change_accepts_all_listed_strikes_token(browser_client):
    """The select's "All listed strikes" option used to produce HTTP 422."""
    response = await browser_client.get(f"{OI_CHANGE}&strikes=all")
    assert response.status_code == 200, response.text[:400]


@pytest.mark.parametrize("token", ["5", "10", "15", "20", "25"])
async def test_oi_change_accepts_every_numeric_strike_choice(browser_client, token):
    response = await browser_client.get(f"{OI_CHANGE}&strikes={token}")
    assert response.status_code == 200, response.text[:400]


async def test_oi_change_rejects_malformed_strikes_with_a_reason(browser_client):
    """Genuinely invalid input is rejected explicitly, never silently defaulted."""
    response = await browser_client.get(f"{OI_CHANGE}&strikes=not-a-count")
    assert response.status_code == 400
    assert "strike_count" in response.text


async def test_oi_change_echoes_all_as_the_selected_strike_token(browser_client):
    """The shared panel's select must reflect `all`, not a substituted default."""
    response = await browser_client.get(f"{OI_CHANGE}&strikes=all")
    assert response.status_code == 200
    html = response.text
    assert 'value="all" selected' in html


# --------------------------------------------------------------------------- #
# D-02 — OI Statistics must not render the strike control its route ignores
# --------------------------------------------------------------------------- #
OI_STATS = "/market-data/derivatives/oi-statistics?symbol=NIFTY"


def _select_block(html: str, name: str) -> str:
    """Return the first ``<select name=...>`` element's markup."""
    import re

    match = re.search(rf'<select[^>]*name="{name}".*?</select>', html, re.S)
    assert match, f'select[name="{name}"] must render'
    return match.group(0)


async def test_oi_statistics_shared_strike_control_is_no_longer_dead(browser_client):
    """Submitting `strikes` must be read back, not silently ignored."""
    response = await browser_client.get(f"{OI_STATS}&strikes=20")
    assert response.status_code == 200
    assert 'value="20" selected' in _select_block(response.text, "strikes")


async def test_oi_statistics_accepts_the_all_listed_strikes_token(browser_client):
    response = await browser_client.get(f"{OI_STATS}&strikes=all")
    assert response.status_code == 200
    assert 'value="all" selected' in _select_block(response.text, "strikes")


async def test_oi_statistics_range_keeps_precedence_when_both_are_sent(browser_client):
    """Both controls drive the same display filter; the page's own wins."""
    response = await browser_client.get(f"{OI_STATS}&range=10&strikes=20")
    assert response.status_code == 200
    assert 'value="10" selected' in _select_block(response.text, "strikes")


async def test_oi_statistics_reechoes_default_range_when_no_strike_filter_is_sent(browser_client):
    response = await browser_client.get(OI_STATS)
    assert response.status_code == 200
    assert 'value="15" selected' in _select_block(response.text, "strikes")


async def test_oi_statistics_still_renders_its_own_range_control(browser_client):
    """The page's own control remains available and is not disabled."""
    response = await browser_client.get(OI_STATS)
    assert response.status_code == 200
    assert 'name="range"' in response.text
    assert 'name="symbol"' in response.text
    assert 'name="expiry"' in response.text


# --------------------------------------------------------------------------- #
# D-03 — the Options workspace must mirror its controls into the URL
# --------------------------------------------------------------------------- #
async def test_options_workspace_syncs_url_with_its_controls(browser_client):
    response = await browser_client.get("/market-data/options?symbol=NIFTY")
    assert response.status_code == 200
    html = response.text
    assert "syncWorkspaceUrl" in html
    assert "history.replaceState" in html
    # Delegated, so it survives htmx replacing the expiry select.
    assert "addEventListener('change'" in html
    for control_id in ("opt-symbol", "opt-expiry", "opt-strike-count"):
        assert control_id in html


# --------------------------------------------------------------------------- #
# D-01 — replay provenance must name the replayed record, not the live adapter
# --------------------------------------------------------------------------- #
async def test_filter_panel_labels_spot_and_source_as_current(browser_client):
    response = await browser_client.get(OI_STATS)
    assert response.status_code == 200
    html = response.text
    assert "Current spot:" in html
    assert "Source (current feed):" in html
    # The old ambiguous labels are gone.
    assert ">Source: <" not in html
    assert ">\n                Spot:" not in html


async def test_replay_page_discloses_record_source_and_scope_note(browser_client):
    response = await browser_client.get("/market-data/derivatives/oi-replay?symbol=NIFTY")
    assert response.status_code == 200
    html = response.text
    assert "Replayed spot: " in html
    assert "replayed from " in html
    assert "Scope note:" in html
    assert "not the replayed records" in html


async def test_replay_ladder_exposes_the_stored_record_source():
    """The payload must name the source of the records being replayed."""
    await _seed_replay_fixture(source_name="NSE_TEST")

    from app.services.intraday_options_service import IntradayOptionsService

    payload = await IntradayOptionsService().get_replay_ladder(
        "NIFTY",
        session_date=SESSION,
        evaluation_at=AVAILABLE_AT + timedelta(minutes=5),
        strike_count=5,
    )
    assert payload["snapshot_available"] is True
    assert payload["record_source_names"] == ["NSE_TEST"]
    # The replayed spot is a stored observation, never the live quote.
    assert payload["spot_provenance"] == "PERSISTED_SNAPSHOT"
    assert Decimal(str(payload["spot_price"])) == SPOT


async def test_replay_ladder_hides_records_that_were_not_yet_available():
    """No lookahead: before available_at the record must not be replayable."""
    await _seed_replay_fixture()

    from app.services.intraday_options_service import IntradayOptionsService

    payload = await IntradayOptionsService().get_replay_ladder(
        "NIFTY",
        session_date=SESSION,
        evaluation_at=MARKET_TS,  # exactly the observation instant, not yet available
        strike_count=5,
    )
    assert payload["snapshot_available"] is False
    assert payload["record_source_names"] == []
    # The empty path returns early, so the spot keys are simply absent rather
    # than populated with a substituted value.
    assert payload.get("spot_price") is None
    assert payload.get("spot_provenance") is None
    assert payload["reason"] == "NO_PERSISTED_OBSERVATIONS"


# --------------------------------------------------------------------------- #
# D-05 — the futures spot card must never contradict the top-level field
# --------------------------------------------------------------------------- #
def _live_observation() -> FuturesObservation:
    return FuturesObservation(
        instrument_key="NSE_FO|68407",
        trading_symbol="NIFTY FUT 29 SEP 26",
        underlying="NIFTY",
        expiry=EXPIRY,
        observed_at=MARKET_TS,
        received_at=AVAILABLE_AT,
        source="LIVE_PROVIDER_OBSERVATION",
        last_price=Decimal("22715.20"),
        day_high=Decimal("22764.90"),
        day_low=Decimal("22579.00"),
        volume=5117450,
        open_interest=7120295,
        oi_day_high=8855860,
        oi_day_low=7120295,
    )


def _card(cards: list[dict], key: str) -> dict:
    return next(c for c in cards if c["key"] == key)


def test_underlying_spot_card_reports_the_real_value_and_provenance():
    cards = _summary_cards(
        _live_observation(),
        {"summary": {}},
        days_to_expiry=0,
        contract=SimpleNamespace(
            trading_symbol="NIFTY FUT 29 SEP 26", expiry_date=EXPIRY
        ),
        spot=Decimal("22716.20"),
        spot_provenance="UPSTOX_INDEX_LTP",
    )
    card = _card(cards, "underlying_spot")
    assert card["value"] == pytest.approx(22716.20)
    assert card["provenance"] == "UPSTOX_INDEX_LTP"


def test_underlying_spot_card_is_unavailable_only_when_there_is_no_spot():
    cards = _summary_cards(
        _live_observation(),
        {"summary": {}},
        days_to_expiry=0,
        contract=SimpleNamespace(
            trading_symbol="NIFTY FUT 29 SEP 26", expiry_date=EXPIRY
        ),
        spot=None,
        spot_provenance="UPSTOX_INDEX_LTP",
    )
    card = _card(cards, "underlying_spot")
    assert card["value"] is None
    assert card["provenance"] == "UNAVAILABLE"


async def test_futures_payload_never_contradicts_itself_on_underlying_spot():
    """Card and top-level field must make the same claim about the same value."""
    from app.services.intraday_options_service import IntradayOptionsService

    payload = await IntradayOptionsService().get_futures_oi_vs_time("NIFTY")
    top = payload.get("underlying_spot")
    cards = {c["key"]: c for c in payload.get("summary_cards") or []}
    if "underlying_spot" not in cards:
        assert top is None
        return
    card = cards["underlying_spot"]
    assert (card["value"] is None) == (top is None)
    if top is not None:
        assert Decimal(str(card["value"])) == Decimal(str(top))
        assert card["provenance"] != "UNAVAILABLE"


# --------------------------------------------------------------------------- #
# D-08-04 — the level-break label must not read as a missing value
# --------------------------------------------------------------------------- #
def test_level_break_none_label_is_readable_and_code_is_unchanged():
    result = compute_level_break(Decimal("100"), Decimal("110"), Decimal("90"))
    assert result["code"] == LEVEL_BREAK_NONE
    assert result["label"] == "No break"
    assert result["label"] != "None"
    assert result["broke_high"] is False and result["broke_low"] is False


@pytest.mark.parametrize(
    ("ltp", "high", "low", "code", "label"),
    [
        ("115", "110", "90", LEVEL_BREAK_HIGH, "Day-high break"),
        ("85", "110", "90", LEVEL_BREAK_LOW, "Day-low break"),
        ("115", "110", "120", LEVEL_BREAK_BOTH, "Both levels"),
    ],
)
def test_other_level_break_labels_are_unchanged(ltp, high, low, code, label):
    result = compute_level_break(Decimal(ltp), Decimal(high), Decimal(low))
    assert result["code"] == code
    assert result["label"] == label


def test_level_break_unavailable_state_is_still_explicit():
    result = compute_level_break(None, None, None)
    assert result["code"] == LEVEL_BREAK_NONE
    assert result["label"] == "Unavailable"
    assert result["reason"] == "MISSING_SESSION_EXTREMES"
