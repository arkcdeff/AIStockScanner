"""G.11-OPT-3 Phase 1 & 2 tests: underlying-specific ATM, grids, lots, filter schema.

Root cause under test: the pre-OPT-3 workbench derived ATM from
``round(spot / hardcoded_step)`` where the provider parser hardcoded the spot to
``24500.00``. NIFTY and BANKNIFTY therefore received the *same* ATM, which was
often not a listed strike at all.

Grids and lot sizes used here are the real values captured from the authorized
provider contract master during discovery. No test contacts the network.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.derivatives_scope import (
    ANALYSIS_WINDOW_CHOICES_MINUTES,
    DEFAULT_TIE_BREAK,
    INTERVAL_CHOICES_MINUTES,
    OI_UNIT_CONTRACTS,
    OI_UNIT_LOTS,
    OI_UNIT_QUANTITY,
    PROV_PROVIDER_LISTED,
    REASON_NO_LISTED_STRIKES,
    REASON_NO_LOT_SIZE,
    REASON_NO_SPOT,
    STRIKE_COUNT_CHOICES,
    STRIKE_COUNT_SEMANTICS,
    TIE_BREAK_HIGHER,
    TIE_BREAK_LOWER,
    DerivativesFilterSpec,
    convert_oi_units,
    describe_strike_selection,
    get_underlying,
    infer_strike_grid,
    is_material_spot_change,
    normalize_underlying,
    parse_interval_token,
    resolve_atm,
    resolve_lot_size,
    select_atm_window,
    supported_underlying_rows,
)

# ---------------------------------------------------------------------------
# Real, provider-verified grids (captured live from /option/contract)
# ---------------------------------------------------------------------------

NIFTY_GRID = tuple(Decimal(str(20800 + 50 * i)) for i in range(89))  # 20,800 .. 25,200
BANKNIFTY_GRID = tuple(Decimal(str(43500 + 100 * i)) for i in range(261))  # 43,500 .. 69,600
FINNIFTY_GRID = tuple(Decimal(str(22000 + 50 * i)) for i in range(125))
MIDCPNIFTY_GRID = tuple(Decimal(str(12500 + 25 * i)) for i in range(145))
SENSEX_GRID = tuple(Decimal(str(68000 + 100 * i)) for i in range(148))

# Real observed spots at discovery time (NIFTY 23,140.50 / BANKNIFTY 55,580.40).
NIFTY_SPOT = Decimal("23140.50")
BANKNIFTY_SPOT = Decimal("55580.40")
FINNIFTY_SPOT = Decimal("25105.15")
MIDCPNIFTY_SPOT = Decimal("14027.70")
SENSEX_SPOT = Decimal("73804.26")


# ---------------------------------------------------------------------------
# 1. Underlying identity and catalogue
# ---------------------------------------------------------------------------

def test_catalog_covers_every_required_underlying() -> None:
    symbols = {row["symbol"] for row in supported_underlying_rows()}
    assert symbols == {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("nifty", "NIFTY"),
        ("NIFTY 50", "NIFTY"),
        ("NIFTY_50", "NIFTY"),
        ("BSE_SENSEX", "SENSEX"),
        ("NIFTY BANK", "BANKNIFTY"),
        ("NIFTY_FIN_SERVICE", "FINNIFTY"),
        ("NIFTY MID SELECT", "MIDCPNIFTY"),
        ("RELIANCE", "RELIANCE"),
        ("", ""),
    ],
)
def test_symbol_normalization(raw: str, expected: str) -> None:
    assert normalize_underlying(raw) == expected


def test_each_underlying_has_its_own_provider_instrument_key() -> None:
    keys = {
        sym: get_underlying(sym).index_instrument_key
        for sym in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX")
    }
    assert len(set(keys.values())) == 5, "one underlying's key must never be reused for another"
    assert keys["NIFTY"] == "NSE_INDEX|Nifty 50"
    assert keys["BANKNIFTY"] == "NSE_INDEX|Nifty Bank"
    assert keys["SENSEX"] == "BSE_INDEX|SENSEX"


def test_unsupported_underlying_has_no_definition() -> None:
    assert get_underlying("NOT_AN_INDEX") is None


# ---------------------------------------------------------------------------
# 2. Grid inference from actual listed strikes
# ---------------------------------------------------------------------------

def test_grid_step_is_observed_not_assumed() -> None:
    nifty = infer_strike_grid(NIFTY_GRID)
    bank = infer_strike_grid(BANKNIFTY_GRID)
    mid = infer_strike_grid(MIDCPNIFTY_GRID)
    assert nifty.step == Decimal("50")
    assert bank.step == Decimal("100")
    assert mid.step == Decimal("25")
    assert nifty.step != bank.step, "indices must not be assumed to share a strike interval"


def test_grid_reports_non_uniform_wings_honestly() -> None:
    """Real index grids widen in the deep wings; the step near the money must survive."""
    widened = list(NIFTY_GRID) + [Decimal("12000"), Decimal("13000"), Decimal("14000")]
    grid = infer_strike_grid(widened)
    assert grid.is_uniform is False
    assert grid.step == Decimal("50"), "the dominant near-money step must still be reported"
    assert Decimal("1000") in grid.observed_steps


def test_empty_grid_reports_a_reason_and_never_invents_strikes() -> None:
    grid = infer_strike_grid([])
    assert grid.strikes == ()
    assert grid.is_available is False
    assert grid.reason == REASON_NO_LISTED_STRIKES
    assert grid.step is None


def test_grid_ignores_unusable_values() -> None:
    grid = infer_strike_grid([Decimal("100"), None, "abc", Decimal("-5"), Decimal("0"), Decimal("150")])
    assert grid.strikes == (Decimal("100"), Decimal("150"))


# ---------------------------------------------------------------------------
# 3. The reported ATM defect
# ---------------------------------------------------------------------------

def test_nifty_and_banknifty_resolve_different_atms() -> None:
    nifty = resolve_atm(NIFTY_SPOT, infer_strike_grid(NIFTY_GRID))
    bank = resolve_atm(BANKNIFTY_SPOT, infer_strike_grid(BANKNIFTY_GRID))

    assert nifty.atm_strike == Decimal("23150")
    assert bank.atm_strike == Decimal("55600")
    assert nifty.atm_strike != bank.atm_strike
    assert nifty.is_listed is True
    assert bank.is_listed is True


def test_all_supported_underlyings_resolve_independently() -> None:
    cases = [
        ("NIFTY", NIFTY_SPOT, NIFTY_GRID, Decimal("23150"), Decimal("50")),
        ("BANKNIFTY", BANKNIFTY_SPOT, BANKNIFTY_GRID, Decimal("55600"), Decimal("100")),
        ("FINNIFTY", FINNIFTY_SPOT, FINNIFTY_GRID, Decimal("25100"), Decimal("50")),
        ("MIDCPNIFTY", MIDCPNIFTY_SPOT, MIDCPNIFTY_GRID, Decimal("14025"), Decimal("25")),
        ("SENSEX", SENSEX_SPOT, SENSEX_GRID, Decimal("73800"), Decimal("100")),
    ]
    atms: set[Decimal] = set()
    for symbol, spot, strikes, expected_atm, expected_step in cases:
        grid = infer_strike_grid(strikes)
        atm = resolve_atm(spot, grid)
        assert grid.step == expected_step, symbol
        assert atm.atm_strike == expected_atm, symbol
        assert atm.is_listed is True, symbol
        atms.add(atm.atm_strike)
    assert len(atms) == 5, "every underlying must produce its own ATM"


def test_old_hardcoded_spot_is_no_longer_reproduced() -> None:
    """24,500 was the hardcoded spot; it must not be BANKNIFTY's ATM any more."""
    bank = resolve_atm(BANKNIFTY_SPOT, infer_strike_grid(BANKNIFTY_GRID))
    assert bank.atm_strike != Decimal("24500")
    assert Decimal("24500") not in BANKNIFTY_GRID


def test_atm_is_always_a_listed_strike_even_when_off_grid_spot() -> None:
    grid = infer_strike_grid([Decimal("100"), Decimal("200"), Decimal("300")])
    atm = resolve_atm(Decimal("247.3"), grid)
    assert atm.atm_strike == Decimal("200")
    assert atm.is_listed is True


def test_equal_distance_ties_are_broken_deterministically_lower_by_default() -> None:
    grid = infer_strike_grid([Decimal("100"), Decimal("200")])
    atm = resolve_atm(Decimal("150"), grid, tie_break=TIE_BREAK_LOWER)
    assert atm.atm_strike == Decimal("100")
    assert atm.method == "NEAREST_LISTED_TIE_BROKEN_LOWER"
    assert atm.tie_break == DEFAULT_TIE_BREAK == TIE_BREAK_LOWER


def test_equal_distance_ties_can_be_broken_higher() -> None:
    grid = infer_strike_grid([Decimal("100"), Decimal("200")])
    atm = resolve_atm(Decimal("150"), grid, tie_break=TIE_BREAK_HIGHER)
    assert atm.atm_strike == Decimal("200")
    assert atm.method == "NEAREST_LISTED_TIE_BROKEN_HIGHER"


def test_tie_break_does_not_depend_on_bankers_rounding() -> None:
    """``round(23125/50)*50`` is a banker's-rounding coin flip; the resolver is exact."""
    grid = infer_strike_grid(NIFTY_GRID)
    lower = resolve_atm(Decimal("23125"), grid, tie_break=TIE_BREAK_LOWER).atm_strike
    higher = resolve_atm(Decimal("23125"), grid, tie_break=TIE_BREAK_HIGHER).atm_strike
    assert (lower, higher) == (Decimal("23100"), Decimal("23150"))


def test_missing_spot_yields_an_unresolved_atm_with_a_reason() -> None:
    atm = resolve_atm(None, infer_strike_grid(NIFTY_GRID))
    assert atm.atm_strike is None
    assert atm.is_listed is False
    assert atm.reason == REASON_NO_SPOT


@pytest.mark.parametrize("bad_spot", [Decimal("0"), Decimal("-5")])
def test_non_positive_spot_is_refused(bad_spot: Decimal) -> None:
    atm = resolve_atm(bad_spot, infer_strike_grid(NIFTY_GRID))
    assert atm.atm_strike is None
    assert atm.reason == REASON_NO_SPOT


def test_missing_strikes_yield_an_unresolved_atm_with_the_grid_reason() -> None:
    atm = resolve_atm(NIFTY_SPOT, infer_strike_grid([]))
    assert atm.atm_strike is None
    assert atm.reason == REASON_NO_LISTED_STRIKES


def test_spot_below_the_grid_floor_clamps_to_the_lowest_listed_strike() -> None:
    grid = infer_strike_grid([Decimal("100"), Decimal("200")])
    atm = resolve_atm(Decimal("10"), grid)
    assert atm.atm_strike == Decimal("100")
    assert atm.method == "NEAREST_LISTED_BELOW_GRID_FLOOR"
    assert atm.is_listed is True


def test_spot_above_the_grid_ceiling_clamps_to_the_highest_listed_strike() -> None:
    grid = infer_strike_grid([Decimal("100"), Decimal("200")])
    atm = resolve_atm(Decimal("9999"), grid)
    assert atm.atm_strike == Decimal("200")
    assert atm.method == "NEAREST_LISTED_ABOVE_GRID_CEILING"


def test_spot_exactly_on_a_listed_strike_is_marked_exact() -> None:
    atm = resolve_atm(Decimal("23150"), infer_strike_grid(NIFTY_GRID))
    assert atm.atm_strike == Decimal("23150")
    assert atm.method == "EXACT_LISTED_STRIKE"
    assert atm.distance == Decimal("0")


def test_atm_diagnostics_expose_provenance_and_bracket() -> None:
    atm = resolve_atm(BANKNIFTY_SPOT, infer_strike_grid(BANKNIFTY_GRID))
    payload = atm.to_dict()
    assert payload["grid_source"] == PROV_PROVIDER_LISTED
    assert payload["is_listed"] is True
    assert payload["lower_strike"] == "55500"
    assert payload["upper_strike"] == "55600"
    assert payload["tie_break"] == TIE_BREAK_LOWER


# ---------------------------------------------------------------------------
# 4. Material spot-change recomputation rule
# ---------------------------------------------------------------------------

def test_spot_change_is_material_only_when_it_can_move_the_atm() -> None:
    step = Decimal("50")
    assert is_material_spot_change(Decimal("23140"), Decimal("23149"), step) is False
    assert is_material_spot_change(Decimal("23140"), Decimal("23165"), step) is True


def test_missing_or_unknown_grid_always_forces_recomputation() -> None:
    assert is_material_spot_change(None, Decimal("100"), Decimal("50")) is True
    assert is_material_spot_change(Decimal("100"), None, Decimal("50")) is True
    assert is_material_spot_change(Decimal("100"), Decimal("100.0001"), None) is True
    assert is_material_spot_change(Decimal("100"), Decimal("100"), None) is False


def test_expiry_change_recomputes_because_the_grid_changes() -> None:
    """A different expiry lists a different grid, so ATM must be recomputed."""
    near_expiry_grid = infer_strike_grid([Decimal("23000"), Decimal("23050"), Decimal("23100")])
    far_expiry_grid = infer_strike_grid([Decimal("12000"), Decimal("15000"), Decimal("18000")])

    near = resolve_atm(Decimal("23070"), near_expiry_grid)
    far = resolve_atm(Decimal("23070"), far_expiry_grid)

    assert near.atm_strike == Decimal("23050")
    assert far.atm_strike == Decimal("18000")
    assert near.atm_strike != far.atm_strike


# ---------------------------------------------------------------------------
# 5. Strike-count semantics and windowing
# ---------------------------------------------------------------------------

def test_strike_count_means_strikes_per_side_and_is_labelled_unambiguously() -> None:
    assert STRIKE_COUNT_SEMANTICS == "STRIKES_PER_SIDE"
    grid = infer_strike_grid(NIFTY_GRID)
    atm = Decimal("23150")
    window = select_atm_window(grid, atm, 5)
    assert len(window) == 11, "5 per side plus ATM itself"
    assert window[5] == atm
    label = describe_strike_selection(5, len(window), len(grid.strikes))
    assert "per side" in label
    assert "11 strikes total" in label
    assert f"of {len(NIFTY_GRID)} listed" in label


def test_strike_count_never_widens_beyond_the_listed_grid() -> None:
    grid = infer_strike_grid([Decimal("100"), Decimal("200"), Decimal("300")])
    assert select_atm_window(grid, Decimal("200"), 25) == (
        Decimal("100"),
        Decimal("200"),
        Decimal("300"),
    )


def test_strike_count_none_selects_every_listed_strike() -> None:
    grid = infer_strike_grid(NIFTY_GRID)
    total = len(NIFTY_GRID)
    assert len(select_atm_window(grid, Decimal("23150"), None)) == total
    assert "All listed strikes" in describe_strike_selection(None, total, total)


def test_window_at_the_grid_edge_does_not_fabricate_strikes() -> None:
    grid = infer_strike_grid(NIFTY_GRID)
    window = select_atm_window(grid, Decimal("20800"), 5)
    assert len(window) == 6, "the floor has no strikes below it"
    assert window[0] == Decimal("20800")


def test_window_requests_an_unlisted_atm_returns_empty() -> None:
    """An off-grid centre must yield nothing: strikes are never invented around it."""
    grid = infer_strike_grid(NIFTY_GRID)
    assert Decimal("24525") not in grid.strikes
    assert select_atm_window(grid, Decimal("24525"), 5) == ()


def test_hardcoded_24500_is_a_real_nifty_strike_but_wrong_for_banknifty() -> None:
    """24,500 exists on NIFTY's grid, which is exactly why the old bug hid so well."""
    assert Decimal("24500") in infer_strike_grid(NIFTY_GRID).strikes
    assert Decimal("24500") not in infer_strike_grid(BANKNIFTY_GRID).strikes


# ---------------------------------------------------------------------------
# 6. Lot and tick metadata must be verified or refused
# ---------------------------------------------------------------------------

def test_lot_size_resolves_from_provider_rows() -> None:
    resolved = resolve_lot_size([{"lot_size": 65, "tick_size": 5.0}])
    assert resolved.lot_size == 65
    assert resolved.tick_size == Decimal("5.0")
    assert resolved.is_verified is True
    assert resolved.source == PROV_PROVIDER_LISTED


def test_missing_lot_size_is_refused_rather_than_guessed() -> None:
    resolved = resolve_lot_size([{"tick_size": 5.0}])
    assert resolved.lot_size is None
    assert resolved.is_verified is False
    assert resolved.reason == REASON_NO_LOT_SIZE


def test_ambiguous_lot_size_is_flagged() -> None:
    resolved = resolve_lot_size([{"lot_size": 65, "tick_size": 5.0}, {"lot_size": 30, "tick_size": 5.0}])
    assert resolved.reason == "AMBIGUOUS_LOT_SIZE"
    assert resolved.lot_size == 30


@pytest.mark.parametrize(
    ("symbol", "lot", "tick"),
    [
        ("NIFTY", 65, Decimal("5")),
        ("BANKNIFTY", 30, Decimal("5")),
        ("FINNIFTY", 60, Decimal("5")),
        ("MIDCPNIFTY", 120, Decimal("5")),
        ("SENSEX", 20, Decimal("5")),
    ],
)
def test_real_provider_lot_sizes_differ_per_underlying(
    symbol: str, lot: int, tick: Decimal
) -> None:
    """The pre-OPT-3 code hardcoded 25/15/25/50 and a 0.05 tick for all of these."""
    resolved = resolve_lot_size([{"lot_size": lot, "tick_size": tick}])
    assert resolved.lot_size == lot, symbol
    assert resolved.tick_size == tick, symbol


# ---------------------------------------------------------------------------
# 7. OI unit conversion
# ---------------------------------------------------------------------------

def test_contracts_need_no_lot_size() -> None:
    result = convert_oi_units(6588, None, OI_UNIT_CONTRACTS)
    assert result["available"] is True
    assert result["value"] == 6588


def test_lots_and_quantity_use_the_verified_lot_size() -> None:
    lots = convert_oi_units(65, 65, OI_UNIT_LOTS)
    assert lots["available"] is True
    assert lots["value"] == pytest.approx(1.0)

    quantity = convert_oi_units(65, 65, OI_UNIT_QUANTITY)
    assert quantity["available"] is True
    assert quantity["value"] == 65 * 65


def test_conversion_is_refused_without_a_verified_lot_size() -> None:
    lots = convert_oi_units(1000, None, OI_UNIT_LOTS)
    assert lots["available"] is False
    assert lots["value"] is None
    assert lots["reason"] == REASON_NO_LOT_SIZE


def test_missing_open_interest_is_never_treated_as_zero() -> None:
    result = convert_oi_units(None, 65, OI_UNIT_CONTRACTS)
    assert result["available"] is False
    assert result["value"] is None


def test_unknown_unit_is_refused() -> None:
    assert convert_oi_units(10, 65, "BUSHELS")["reason"] == "UNSUPPORTED_OI_UNIT"


def test_wrong_preopt2_lot_size_would_have_corrupted_conversion() -> None:
    """NIFTY lots were reported as 25 instead of 65 — a 2.6x error."""
    correct = convert_oi_units(650, 65, OI_UNIT_LOTS)["value"]
    wrong = convert_oi_units(650, 25, OI_UNIT_LOTS)["value"]
    assert correct == pytest.approx(10.0)
    assert wrong == pytest.approx(26.0)


# ---------------------------------------------------------------------------
# 8. Shared filter schema
# ---------------------------------------------------------------------------

def test_defaults_match_the_required_defaults() -> None:
    spec = DerivativesFilterSpec()
    assert spec.strike_count == 5, "default is ATM ±5 strikes"
    assert spec.interval_minutes == 5
    assert spec.oi_unit == OI_UNIT_CONTRACTS
    assert spec.is_valid() is True


def test_interval_and_window_vocabularies_are_the_required_sets() -> None:
    assert set(ANALYSIS_WINDOW_CHOICES_MINUTES) == {3, 5, 10, 15, 30, 45, 60}
    assert set(INTERVAL_CHOICES_MINUTES) == {1, 2, 3, 5, 10, 15, 30, 45, 60}
    assert set(STRIKE_COUNT_CHOICES) == {5, 10, 15, 20, 25}


@pytest.mark.parametrize("minutes", [3, 5, 10, 15, 30, 45, 60])
def test_every_required_analysis_interval_is_accepted(minutes: int) -> None:
    assert DerivativesFilterSpec(interval_minutes=minutes).is_valid() is True


def test_unsupported_interval_is_rejected_not_coerced() -> None:
    spec = DerivativesFilterSpec(interval_minutes=17)
    assert spec.is_valid() is False
    assert any("interval" in problem for problem in spec.errors())


def test_unsupported_strike_count_is_rejected() -> None:
    assert DerivativesFilterSpec(strike_count=7).is_valid() is False
    assert DerivativesFilterSpec(strike_count=None).is_valid() is True


def test_min_strike_above_max_strike_is_rejected() -> None:
    spec = DerivativesFilterSpec(min_strike=Decimal("200"), max_strike=Decimal("100"))
    assert any("min_strike" in problem for problem in spec.errors())


def test_custom_window_requires_a_bound() -> None:
    assert DerivativesFilterSpec(window="CUSTOM").is_valid() is False
    assert DerivativesFilterSpec(window="CUSTOM", start_time="09:30").is_valid() is True


def test_sort_direction_validation() -> None:
    assert DerivativesFilterSpec(sort_dir="sideways").is_valid() is False
    assert DerivativesFilterSpec(sort_dir="ASC").normalized().sort_dir == "asc"


def test_from_query_parses_the_documented_parameter_names() -> None:
    spec = DerivativesFilterSpec.from_query(
        {
            "symbol": "banknifty",
            "strikes": "10",
            "interval": "3m",
            "call_strikes": "55100,55200",
            "put_strikes": "55700",
            "oi_unit": "lots",
            "expiry": "2026-09-29",
            "window": "INTRADAY_INTERVAL",
        }
    ).normalized()
    assert spec.underlying == "BANKNIFTY"
    assert spec.strike_count == 10
    assert spec.interval_minutes == 3
    assert spec.call_strikes == (Decimal("55100"), Decimal("55200"))
    assert spec.put_strikes == (Decimal("55700"),)
    assert spec.oi_unit == "LOTS"
    assert spec.expiry.isoformat() == "2026-09-29"
    assert spec.is_valid() is True


def test_from_query_treats_all_as_full_strike_coverage() -> None:
    assert DerivativesFilterSpec.from_query({"strikes": "all"}).strike_count is None


@pytest.mark.parametrize(
    ("token", "expected"),
    [("3m", 3), ("3", 3), ("15m", 15), ("60", 60), ("session", None), ("bogus", None), (None, None)],
)
def test_interval_token_parsing(token, expected) -> None:
    assert parse_interval_token(token) == expected


def test_fingerprint_is_stable_and_scope_sensitive() -> None:
    nifty = DerivativesFilterSpec(underlying="NIFTY", expiry=None)
    bank = DerivativesFilterSpec(underlying="BANKNIFTY", expiry=None)

    assert nifty.fingerprint() == DerivativesFilterSpec(underlying="NIFTY").fingerprint()
    assert nifty.fingerprint() != bank.fingerprint(), "a stale response can be told apart"

    changed_interval = DerivativesFilterSpec(underlying="NIFTY", interval_minutes=15)
    assert nifty.fingerprint() != changed_interval.fingerprint()


def test_reset_clears_the_selection_but_keeps_the_underlying() -> None:
    spec = DerivativesFilterSpec(
        underlying="BANKNIFTY",
        strike_count=25,
        interval_minutes=45,
        call_strikes=(Decimal("55000"),),
        min_strike=Decimal("54000"),
    )
    reset = spec.reset()
    assert reset.underlying == "BANKNIFTY"
    assert reset.strike_count == 5
    assert reset.interval_minutes == 5
    assert reset.call_strikes == ()
    assert reset.min_strike is None
    assert reset.fingerprint() != spec.fingerprint()


def test_normalization_deduplicates_and_sorts_manual_strikes() -> None:
    spec = DerivativesFilterSpec(
        call_strikes=(Decimal("200"), Decimal("100"), Decimal("200")),
        put_strikes=(Decimal("300"), Decimal("100")),
        underlying="nifty",
    ).normalized()
    assert spec.call_strikes == (Decimal("100"), Decimal("200"))
    assert spec.put_strikes == (Decimal("100"), Decimal("300"))
    assert spec.underlying == "NIFTY"


def test_page_size_bounds_are_enforced() -> None:
    assert DerivativesFilterSpec(page_size=0).is_valid() is False
    assert DerivativesFilterSpec(page_size=1).is_valid() is True
    assert DerivativesFilterSpec(page_size=5001).is_valid() is False


def test_empty_underlying_is_rejected() -> None:
    assert any("underlying" in problem for problem in DerivativesFilterSpec(underlying="  ").errors())


# ---------------------------------------------------------------------------
# Raw query tokens — rejected, never silently coerced
# ---------------------------------------------------------------------------

def test_from_query_rejects_an_interval_it_cannot_parse() -> None:
    # ``interval=17m`` used to become 5 minutes and return HTTP 200 with a window
    # the caller never asked for.
    spec = DerivativesFilterSpec.from_query({"symbol": "NIFTY", "interval": "17m"})
    assert spec.interval_minutes == 5  # the fallback is visible...
    assert spec.is_valid() is False, "...but it must never be accepted silently"
    assert any("17m" in problem and "interval" in problem for problem in spec.errors())


def test_from_query_rejects_an_unparseable_strike_count() -> None:
    spec = DerivativesFilterSpec.from_query({"symbol": "NIFTY", "strikes": "abc"})
    assert spec.is_valid() is False
    assert any("strike_count" in problem for problem in spec.errors())


def test_from_query_rejects_a_strike_count_outside_the_vocabulary() -> None:
    spec = DerivativesFilterSpec.from_query({"symbol": "NIFTY", "strikes": "7"})
    assert spec.is_valid() is False
    assert any("strike_count" in problem for problem in spec.errors())


@pytest.mark.parametrize("token", ["5", "10", "15", "20", "25", "all"])
def test_from_query_accepts_every_documented_strike_count_token(token: str) -> None:
    assert DerivativesFilterSpec.from_query(
        {"symbol": "NIFTY", "strikes": token}
    ).is_valid() is True


def test_from_query_accepts_the_session_interval_token() -> None:
    spec = DerivativesFilterSpec.from_query({"symbol": "NIFTY", "interval": "session"})
    assert spec.is_valid() is True
    assert spec.interval_minutes == 60


def test_strike_count_tokens_report_their_own_problem() -> None:
    from app.services.derivatives_scope import parse_strike_count_token

    assert parse_strike_count_token("5") == (5, None)
    assert parse_strike_count_token("all") == (None, None)
    assert parse_strike_count_token(None) == (5, None)
    _count, problem = parse_strike_count_token("nonsense")
    assert problem is not None and "not a number" in problem


# ---------------------------------------------------------------------------
# Display formatting — provider numbers must render whatever their type
# ---------------------------------------------------------------------------

def test_format_display_number_accepts_every_provider_type() -> None:
    from app.services.derivatives_scope import format_display_number

    # Exact decimal strings are what the scope payload carries; they must render
    # the same as Decimals and floats rather than raising a format error.
    assert format_display_number("23150.00") == "23,150"
    assert format_display_number(Decimal("23150")) == "23,150"
    assert format_display_number(23150.0) == "23,150"
    assert format_display_number("24550.25", 2) == "24,550.25"
    assert format_display_number(Decimal("0.05"), 2) == "0.05"
    assert format_display_number("1234567.5", 1) == "1,234,567.5"


def test_format_display_number_never_invents_a_value() -> None:
    from app.services.derivatives_scope import format_display_number

    assert format_display_number(None) == "\u2014"
    assert format_display_number("NaN") == "\u2014"
    assert format_display_number("unavailable") == "unavailable"


def test_format_display_number_rounds_half_up_not_to_even() -> None:
    from app.services.derivatives_scope import format_display_number

    # Banker's rounding would render 23,150.5 as "23,150"; a displayed strike must
    # follow the conventional rule instead.
    assert format_display_number("23150.5") == "23,151"
    assert format_display_number("23151.5") == "23,152"
