"""G.11-OPT-3 Phases 5/6/7/8 tests.

Covers the observation engine, the interval co-location rules that replace
pro-rata scaling, futures ledger semantics, no-lookahead replay and CSV export.

Behaviours under test are the ones the milestone forbids violating:

  * an interval OI change is only produced from two real comparable observations,
  * a replay at T can never read an observation recorded after T,
  * missing OI is never forward-filled and never treated as zero,
  * a stale observation is never classified as a genuine buildup,
  * no synthetic futures price or OI ever enters the ledger.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.services.derivatives_export import (
    FUTURES_LEDGER_EXPORT_COLUMNS,
    MAX_EXPORT_ROWS,
    DerivativeExportError,
    build_csv,
    export_filename,
    is_forbidden_column,
)
from app.services.derivatives_futures_ledger import (
    BASIS_DENOMINATOR_NOTE,
    DIRECTION_DOWN,
    DIRECTION_UNAVAILABLE,
    DIRECTION_UP,
    FUTURES_LEDGER_COLUMNS,
    INTERPRETATION_INSUFFICIENT,
    INTERPRETATION_LONG_BUILDUP,
    INTERPRETATION_LONG_UNWINDING,
    INTERPRETATION_NEUTRAL,
    INTERPRETATION_SHORT_BUILDUP,
    INTERPRETATION_SHORT_COVERING,
    LEVEL_BREAK_BOTH,
    LEVEL_BREAK_HIGH,
    LEVEL_BREAK_LOW,
    LEVEL_BREAK_NONE,
    POSITIONING_DISCLAIMER,
    build_futures_ledger,
    classify_direction,
    classify_futures_interval,
    compute_basis,
    compute_level_break,
    compute_oi_churn,
    compute_session_positioning,
)
from app.services.derivatives_observation_service import (
    AVAILABILITY_INSUFFICIENT,
    AVAILABILITY_OK,
    ISSUE_DUPLICATE_TIMESTAMP,
    ISSUE_MISSING_OI,
    ISSUE_OUT_OF_ORDER,
    ISSUE_STALE,
    ContractObservation,
    FuturesObservation,
    FuturesObservationStore,
    ObservationSeries,
    compute_interval_deltas,
    compute_range_delta,
    locate_baseline,
    order_and_audit,
    tolerance_seconds_for,
)
from app.services.derivatives_scope import (
    BASELINE_CUSTOM_RANGE,
    BASELINE_INTRADAY_INTERVAL,
    PROV_LIVE_OBSERVATION,
    PROV_PERSISTED_OBSERVATION,
)

SESSION = date(2026, 9, 24)
BASE = datetime(2026, 9, 24, 4, 0, tzinfo=UTC)  # 09:30 IST


def _obs(
    minutes: int,
    oi: int | None,
    ltp: str | None,
    *,
    volume: int | None = None,
    available_offset_minutes: int = 0,
    source: str = PROV_PERSISTED_OBSERVATION,
    key: str = "NIFTY:23150:CE",
) -> ContractObservation:
    """One real observation at BASE + ``minutes``."""
    observed = BASE + timedelta(minutes=minutes)
    return ContractObservation(
        contract_key=key,
        label="NIFTY 24SEP26 23,150 CE",
        observed_at=observed,
        available_at=observed + timedelta(minutes=available_offset_minutes),
        source=source,
        strike=Decimal("23150"),
        option_type="CE",
        expiry=SESSION,
        ltp=Decimal(ltp) if ltp is not None else None,
        open_interest=oi,
        volume=volume,
    )


def _series(points: list[ContractObservation]) -> ObservationSeries:
    series = ObservationSeries(contract_key=points[0].contract_key, label=points[0].label)
    series.observations, series.issues = order_and_audit(points)
    return series


def _rising_series(step_minutes: int, count: int) -> ObservationSeries:
    return _series(
        [_obs(i * step_minutes, 1000 + i * 100, str(100 + i)) for i in range(count)]
    )


# ===========================================================================
# 1. Interval vocabulary
# ===========================================================================

@pytest.mark.parametrize("minutes", [3, 5, 10, 15, 30, 45, 60])
def test_every_required_interval_measures_from_real_observations(minutes: int) -> None:
    series = _rising_series(step_minutes=minutes, count=6)
    deltas = compute_interval_deltas(series, minutes)

    assert deltas, f"expected measured deltas for {minutes}m"
    for delta in deltas:
        assert delta.availability == AVAILABILITY_OK
        assert delta.baseline_provenance == BASELINE_INTRADAY_INTERVAL
        assert delta.start_at is not None and delta.end_at is not None
        assert delta.effective_seconds == pytest.approx(minutes * 60)
        assert delta.oi_change == 100
        assert delta.ltp_change == Decimal("1")
        # The percentage denominator is the observation's own start LTP, so each
        # step is checked against the value the delta itself reports.
        expected_pct = (Decimal("1") / delta.ltp_start) * Decimal("100")
        assert delta.ltp_change_pct == expected_pct


def test_occluded_observation_is_not_interpolated() -> None:
    """A missing timestamp must simply produce no delta for that step."""
    series = _series(
        [
            _obs(0, 1000, "100"),
            _obs(5, 1100, "101"),
            # 10 minutes is absent on purpose
            _obs(15, 1300, "103"),
        ]
    )
    deltas = compute_interval_deltas(series, 5)
    # Only the 0→5 minute pair is measurable. The 15-minute observation has no
    # baseline within tolerance of 10 minutes, so it yields no step at all
    # rather than a 10-minute delta labelled as a 5-minute one.
    assert len(deltas) == 1
    assert deltas[0].effective_seconds == 300.0
    assert deltas[0].end_at == BASE + timedelta(minutes=5)


def test_tolerance_prevents_a_long_gap_from_serving_a_short_window() -> None:
    """A 3-minute request must never be answered from a 30-minute pair."""
    tolerance = tolerance_seconds_for(3)
    assert tolerance == pytest.approx(3 * 60 * 0.34, abs=0.01)

    series = _series([_obs(0, 1000, "100"), _obs(30, 5000, "150")])
    assert compute_interval_deltas(series, 3) == [], "out-of-tolerance baseline is absent"


def test_tolerance_is_configurable_and_documented() -> None:
    """A 6-minute gap cannot masquerade as a 3-minute measurement."""
    wide = _series([_obs(0, 1000, "100"), _obs(6, 1200, "110")])
    assert compute_interval_deltas(wide, 3) == []

    relaxed = compute_interval_deltas(wide, 3, tolerance_ratio=2.0)
    assert len(relaxed) == 1
    assert relaxed[0].oi_change == 200
    # Whatever the tolerance, the real elapsed duration is always reported.
    assert relaxed[0].effective_seconds == 360.0


def test_slightly_wide_pair_is_measured_and_its_duration_is_disclosed() -> None:
    """A modestly wide real pair is acceptable *because* the duration is shown."""
    near = _series([_obs(0, 1000, "100"), _obs(4, 1200, "110")])
    deltas = compute_interval_deltas(near, 3)
    assert len(deltas) == 1
    assert deltas[0].oi_change == 200
    assert deltas[0].effective_seconds == 240.0
    assert deltas[0].effective_seconds != 3 * 60, "the caller can see it was 4 minutes"
    assert deltas[0].to_dict()["effective_label"] == "4.0m"


def test_locate_baseline_never_returns_a_future_observation() -> None:
    series = _rising_series(step_minutes=5, count=4)
    target = BASE + timedelta(minutes=10)
    found = locate_baseline(series.observations, target, tolerance_seconds=200)
    assert found is not None
    assert found.observed_at <= target
    assert found.observed_at == BASE + timedelta(minutes=10)


def test_locate_baseline_refuses_a_far_away_observation() -> None:
    series = _rising_series(step_minutes=5, count=4)
    target = BASE + timedelta(minutes=30)
    assert locate_baseline(series.observations, target, tolerance_seconds=60) is None


# ===========================================================================
# 2. No pro-rata, ever
# ===========================================================================

def test_interval_change_is_never_scaled_from_a_session_change() -> None:
    """The removed behaviour: ``day_change * (minutes / 375)``."""
    series = _series([_obs(0, 1000, "100"), _obs(75, 4000, "200")])
    assert compute_interval_deltas(series, 3) == []

    full = compute_interval_deltas(series, 60, tolerance_ratio=2.0)
    assert full, "a genuine 75-minute pair is measurable for a 60-minute window"
    assert full[0].oi_change == 3000

    pro_rata_would_be = int(round(3000 * (3 / 375)))
    assert pro_rata_would_be != full[0].oi_change


def test_missing_oi_blocks_the_delta_instead_of_reporting_zero() -> None:
    series = _series([_obs(0, None, "100"), _obs(5, 1100, "101")])
    deltas = compute_interval_deltas(series, 5)
    assert len(deltas) == 1
    assert deltas[0].oi_change is None, "MISSING != ZERO"
    assert ISSUE_MISSING_OI in deltas[0].issues


def test_missing_ltp_still_reports_the_oi_change_without_inventing_a_price() -> None:
    series = _series([_obs(0, 1000, None), _obs(5, 1200, None)])
    delta = compute_interval_deltas(series, 5)[0]
    assert delta.oi_change == 200
    assert delta.ltp_change is None
    assert delta.ltp_change_pct is None


# ===========================================================================
# 3. Observation hygiene
# ===========================================================================

def test_out_of_order_observations_are_sorted_and_flagged() -> None:
    shuffled = [_obs(10, 1200, "102"), _obs(0, 1000, "100"), _obs(5, 1100, "101")]
    ordered, issues = order_and_audit(shuffled)
    assert [o.open_interest for o in ordered] == [1000, 1100, 1200]
    assert ISSUE_OUT_OF_ORDER in issues


def test_duplicate_timestamps_collapse_to_the_newer_available_copy() -> None:
    early = _obs(5, 1100, "101")
    late = ContractObservation(
        **{**early.__dict__, "open_interest": 1150, "available_at": early.available_at + timedelta(minutes=1)}
    )
    ordered, issues = order_and_audit([early, late])
    assert len(ordered) == 1
    assert ordered[0].open_interest == 1150
    assert ISSUE_DUPLICATE_TIMESTAMP in issues


def test_stale_observations_are_flagged_not_hidden() -> None:
    from app.services.derivatives_observation_service import mark_stale_issues

    old = _obs(0, 1000, "100")
    flags = mark_stale_issues([old], evaluation_at=BASE + timedelta(hours=2), staleness_seconds=300)
    assert flags[old.contract_key] == (ISSUE_STALE,)

    fresh = mark_stale_issues([old], evaluation_at=BASE + timedelta(seconds=30), staleness_seconds=300)
    assert fresh == {}


# ===========================================================================
# 4. No-lookahead guarantees
# ===========================================================================

def test_evaluation_cutoff_hides_later_observations() -> None:
    series = _rising_series(step_minutes=5, count=6)
    cutoff = BASE + timedelta(minutes=10)

    deltas = compute_interval_deltas(series, 5, evaluation_at=cutoff)
    assert deltas, "observations at or before the cutoff are usable"
    for delta in deltas:
        assert delta.end_at is not None
        assert delta.end_at <= cutoff


def test_end_of_day_oi_never_backfills_an_earlier_row() -> None:
    """A deliberately planted future observation must not appear at T."""
    early = _series([_obs(0, 1000, "100"), _obs(5, 1100, "101")])
    planted = _obs(60 * 6, 9_999_999, "500")  # end-of-session value
    early.observations, _ = order_and_audit([*early.observations, planted])

    cutoff = BASE + timedelta(minutes=5)
    deltas = compute_interval_deltas(early, 5, evaluation_at=cutoff)
    assert len(deltas) == 1
    assert deltas[0].oi_end == 1100
    assert deltas[0].oi_change == 100
    assert all(d.oi_end != 9_999_999 for d in deltas)


def test_series_without_history_reports_insufficient_rather_than_a_row() -> None:
    series = _series([_obs(0, 1000, "100")])
    deltas = compute_interval_deltas(series, 5)
    assert deltas == []

    empty = ObservationSeries(contract_key="x", label="x", reason="NO_PERSISTED_OBSERVATIONS")
    fallback = compute_interval_deltas(empty, 5)
    assert len(fallback) == 1
    assert fallback[0].availability == AVAILABILITY_INSUFFICIENT
    assert fallback[0].reason == "NO_PERSISTED_OBSERVATIONS"


# ===========================================================================
# 5. Custom range
# ===========================================================================

def test_custom_range_measures_between_real_observations() -> None:
    series = _rising_series(step_minutes=5, count=8)
    delta = compute_range_delta(
        series,
        start_at=BASE + timedelta(minutes=5),
        end_at=BASE + timedelta(minutes=30),
    )
    assert delta.availability == AVAILABILITY_OK
    assert delta.baseline_provenance == BASELINE_CUSTOM_RANGE
    assert delta.oi_change == 500
    assert delta.interval_minutes == 25


def test_custom_range_without_matching_observations_is_insufficient() -> None:
    series = _rising_series(step_minutes=5, count=3)
    delta = compute_range_delta(
        series,
        start_at=BASE + timedelta(hours=5),
        end_at=BASE + timedelta(hours=6),
    )
    assert delta.availability == AVAILABILITY_INSUFFICIENT
    assert delta.reason == "NO_COMPARABLE_OBSERVATIONS_IN_RANGE"


def test_custom_range_with_identical_endpoints_is_insufficient() -> None:
    series = _rising_series(step_minutes=5, count=3)
    same = BASE + timedelta(minutes=5)
    delta = compute_range_delta(series, start_at=same, end_at=same)
    assert delta.availability == AVAILABILITY_INSUFFICIENT


# ===========================================================================
# 6. Futures interpretation
# ===========================================================================

@pytest.mark.parametrize(
    ("price", "oi", "expected"),
    [
        (Decimal("10"), 500, INTERPRETATION_LONG_BUILDUP),
        (Decimal("-10"), 500, INTERPRETATION_SHORT_BUILDUP),
        (Decimal("10"), -500, INTERPRETATION_SHORT_COVERING),
        (Decimal("-10"), -500, INTERPRETATION_LONG_UNWINDING),
        (Decimal("0"), 500, INTERPRETATION_NEUTRAL),
        (Decimal("10"), 0, INTERPRETATION_NEUTRAL),
    ],
)
def test_four_quadrant_futures_classification(price, oi, expected) -> None:
    assert classify_futures_interval(price, oi)["classification"] == expected


@pytest.mark.parametrize(
    ("price", "oi"),
    [(None, 500), (Decimal("10"), None), (None, None)],
)
def test_missing_inputs_cannot_produce_a_buildup(price, oi) -> None:
    result = classify_futures_interval(price, oi)
    assert result["classification"] == INTERPRETATION_INSUFFICIENT
    assert result["reason"] == "MISSING_COMPARABLE_OBSERVATION"


def test_stale_observation_is_never_classified_as_a_buildup() -> None:
    result = classify_futures_interval(Decimal("25"), 9000, is_stale=True)
    assert result["classification"] == INTERPRETATION_INSUFFICIENT
    assert result["reason"] == ISSUE_STALE


def test_direction_labels() -> None:
    assert classify_direction(Decimal("1"))["code"] == DIRECTION_UP
    assert classify_direction(Decimal("-1"))["code"] == DIRECTION_DOWN
    assert classify_direction(None)["code"] == DIRECTION_UNAVAILABLE


# ===========================================================================
# 7. Level break and churn
# ===========================================================================

def test_level_break_variants() -> None:
    assert compute_level_break(Decimal("100"), Decimal("110"), Decimal("90"))["code"] == LEVEL_BREAK_NONE
    assert compute_level_break(Decimal("110"), Decimal("110"), Decimal("90"))["code"] == LEVEL_BREAK_HIGH
    assert compute_level_break(Decimal("90"), Decimal("110"), Decimal("90"))["code"] == LEVEL_BREAK_LOW
    high_and_low = compute_level_break(Decimal("110"), Decimal("110"), Decimal("110"))
    assert high_and_low["code"] == LEVEL_BREAK_BOTH


def test_level_break_without_extremes_is_unavailable_not_none_break() -> None:
    result = compute_level_break(Decimal("100"), None, None)
    assert result["reason"] == "MISSING_SESSION_EXTREMES"


def test_oi_churn_is_magnitude_only() -> None:
    assert compute_oi_churn(-450)["value"] == 450
    assert compute_oi_churn(450)["value"] == 450
    assert compute_oi_churn(None)["available"] is False


# ===========================================================================
# 8. Futures ledger assembly
# ===========================================================================

def _futures_observations(count: int, step_minutes: int = 5):
    return [
        ContractObservation(
            contract_key="NSE_FO|68407",
            label="NIFTY FUT 29 SEP 26",
            observed_at=BASE + timedelta(minutes=i * step_minutes),
            available_at=BASE + timedelta(minutes=i * step_minutes),
            source=PROV_LIVE_OBSERVATION,
            expiry=date(2026, 9, 29),
            instrument_key="NSE_FO|68407",
            ltp=Decimal(str(23100 + i * 10)),
            open_interest=13_000_000 + i * 50_000,
            volume=1_000_000 + i * 90_000,
        )
        for i in range(count)
    ]


def test_ledger_exposes_exactly_the_fifteen_required_columns() -> None:
    assert len(FUTURES_LEDGER_COLUMNS) == 15
    keys = [c["key"] for c in FUTURES_LEDGER_COLUMNS]
    assert keys == [
        "row_number",
        "interval_label",
        "total_oi",
        "oi_change",
        "day_high",
        "day_low",
        "level_break",
        "oi_churn",
        "interval_volume",
        "ltp",
        "ltp_change",
        "ltp_change_pct",
        "direction",
        "interpretation",
        "quality",
    ]


def test_ledger_builds_real_rows_with_all_columns_populated() -> None:
    ledger = build_futures_ledger(
        _futures_observations(7),
        interval_minutes=5,
        day_high=Decimal("23241.90"),
        day_low=Decimal("23058.00"),
    )
    assert ledger["availability"] == AVAILABILITY_OK
    assert len(ledger["rows"]) == 6
    first = ledger["rows"][0]
    assert first["total_oi"] == 13_050_000
    assert first["oi_change"] == 50_000
    assert first["ltp"] == 23110.0
    assert first["ltp_change"] == 10.0
    assert first["oi_churn"]["value"] == 50_000
    assert first["interval_volume"] == 90_000
    assert first["level_break"]["code"] == LEVEL_BREAK_NONE
    assert first["interpretation"]["classification"] == INTERPRETATION_LONG_BUILDUP
    assert first["source_timestamp_ist"] is not None
    assert first["baseline_provenance"] == BASELINE_INTRADAY_INTERVAL
    assert first["effective_minutes"] == pytest.approx(5.0)


def test_single_observation_yields_one_honest_unavailable_row() -> None:
    """The real state on this machine: real futures state, no real history."""
    ledger = build_futures_ledger(
        _futures_observations(1),
        interval_minutes=5,
        day_high=Decimal("23241.90"),
        day_low=Decimal("23058.00"),
    )
    assert ledger["availability"] == AVAILABILITY_INSUFFICIENT
    assert len(ledger["rows"]) == 1
    row = ledger["rows"][0]
    assert row["total_oi"] == 13_000_000, "the real current OI is still shown"
    assert row["oi_change"] is None
    assert row["direction"]["code"] == DIRECTION_UNAVAILABLE
    assert row["interpretation"]["classification"] == INTERPRETATION_INSUFFICIENT
    assert row["reason"]


def test_ledger_without_any_observation_is_unavailable_without_inventing_rows() -> None:
    ledger = build_futures_ledger([], interval_minutes=5)
    assert ledger["availability"] == "UNAVAILABLE"
    assert ledger["reason"] == "NO_REAL_FUTURES_OBSERVATIONS"
    assert ledger["rows"] == []


def test_ledger_without_session_extremes_leaves_them_unavailable() -> None:
    """A single price is not a day range, so extremes must not be derived from it."""
    ledger = build_futures_ledger(_futures_observations(3), interval_minutes=5)
    row = ledger["rows"][0]
    assert row["day_high"] is None
    assert row["day_low"] is None
    assert row["level_break"]["reason"] == "MISSING_SESSION_EXTREMES"


def test_ledger_session_totals_need_two_observations() -> None:
    single = build_futures_ledger(_futures_observations(1), interval_minutes=5)
    assert single["summary"]["session_oi_change"] is None
    assert single["summary"]["session_volume"] is None
    assert single["summary"]["oi_session_range"] is None

    multi = build_futures_ledger(_futures_observations(5), interval_minutes=5)
    assert multi["summary"]["session_oi_change"] == 200_000
    assert multi["summary"]["oi_session_range"]["high"] == 13_200_000


def test_ledger_never_reports_zero_for_an_unmeasured_interval() -> None:
    ledger = build_futures_ledger(_futures_observations(1), interval_minutes=5)
    row = ledger["rows"][0]
    assert row["oi_change"] is not None or row["availability"] != AVAILABILITY_OK
    assert row["oi_change"] is None
    assert row["oi_churn"]["value"] is None


# ===========================================================================
# 9. Session positioning
# ===========================================================================

def test_positioning_reports_counts_and_labelled_aggregation() -> None:
    inputs = [
        {"price_change": Decimal("10"), "oi_change": 100},
        {"price_change": Decimal("20"), "oi_change": 200},
        {"price_change": Decimal("-5"), "oi_change": 300},
        {"price_change": Decimal("7"), "oi_change": -400},
        {"price_change": Decimal("-9"), "oi_change": -500},
    ]
    positioning = compute_session_positioning(inputs)

    by_code = {card["classification"]: card for card in positioning["cards"]}
    assert by_code[INTERPRETATION_LONG_BUILDUP]["interval_count"] == 2
    assert by_code[INTERPRETATION_LONG_BUILDUP]["aggregated_oi_change"] == 300
    assert by_code[INTERPRETATION_SHORT_BUILDUP]["interval_count"] == 1
    assert by_code[INTERPRETATION_SHORT_COVERING]["interval_count"] == 1
    assert by_code[INTERPRETATION_LONG_UNWINDING]["interval_count"] == 1

    assert positioning["aggregation_method"] == "AGGREGATED_INTERVAL_OI_CHANGE"
    assert "sum of measured interval open-interest changes" in positioning["aggregation_label"]
    assert positioning["net_oi_change"] == -300
    assert positioning["available"] is True


def test_positioning_states_that_absolute_levels_are_never_summed() -> None:
    positioning = compute_session_positioning(
        [{"price_change": Decimal("1"), "oi_change": 100}] * 3
    )
    assert "never summed" in positioning["absolute_level_note"]
    assert positioning["net_oi_change"] == 300, "three 100-contract changes, not three levels"


def test_positioning_disclaims_trader_identity() -> None:
    positioning = compute_session_positioning([{"price_change": Decimal("1"), "oi_change": 1}])
    assert positioning["disclaimer"] == POSITIONING_DISCLAIMER
    assert "not verified trader identities" in positioning["disclaimer"]
    assert "not a guarantee of" in positioning["disclaimer"]


def test_positioning_excludes_unclassifiable_intervals_from_measurement() -> None:
    positioning = compute_session_positioning(
        [
            {"price_change": Decimal("5"), "oi_change": 100},
            {"price_change": None, "oi_change": None},
            {"price_change": Decimal("0"), "oi_change": 0},
        ]
    )
    # A measured-but-flat interval counts as measured AND as neutral; only the
    # interval with no comparable observations is insufficient.
    assert positioning["measured_intervals"] == 2
    assert positioning["total_intervals"] == 3
    assert positioning["insufficient_intervals"] == 1
    assert positioning["neutral_intervals"] == 1


def test_positioning_with_no_classifiable_interval_reports_unavailable() -> None:
    positioning = compute_session_positioning([{"price_change": None, "oi_change": None}])
    assert positioning["available"] is False
    assert positioning["reason"] == "NO_CLASSIFIABLE_INTERVALS"
    assert positioning["net_oi_change"] is None


# ===========================================================================
# 10. Basis and carry
# ===========================================================================

def test_basis_uses_documented_denominator_and_valid_carry() -> None:
    basis = compute_basis(
        Decimal("23190.00"), Decimal("23140.50"), days_to_expiry=3
    )
    assert basis["basis"] == pytest.approx(49.5)
    assert basis["basis_pct"] == pytest.approx(0.2139, abs=1e-3)
    assert basis["denominator_note"] == BASIS_DENOMINATOR_NOTE
    assert basis["annualised_carry_valid"] is True
    assert basis["annualised_carry_pct"] == pytest.approx(26.03, abs=0.05)
    assert basis["availability"] == AVAILABILITY_OK


def test_annualised_carry_is_refused_without_a_positive_day_count() -> None:
    basis = compute_basis(Decimal("100"), Decimal("99"), days_to_expiry=0)
    assert basis["annualised_carry_valid"] is False
    assert basis["annualised_carry_pct"] is None
    assert basis["annualised_carry_reason"] == "NON_POSITIVE_OR_UNKNOWN_DAYS_TO_EXPIRY"


def test_basis_without_a_real_spot_is_unavailable() -> None:
    basis = compute_basis(Decimal("100"), None, days_to_expiry=5)
    assert basis["availability"] == "UNAVAILABLE"
    assert basis["basis"] is None
    assert basis["reason"] == "MISSING_REAL_FUTURES_OR_SPOT_OBSERVATION"


# ===========================================================================
# 11. Capture store is off by default
# ===========================================================================

def test_futures_capture_is_disabled_and_refuses_to_write(tmp_path) -> None:
    store = FuturesObservationStore(base_dir=tmp_path)
    assert store.enabled is False, "no ingestion may be enabled by default"

    written = store.append(
        FuturesObservation(
            instrument_key="NSE_FO|68407",
            trading_symbol="NIFTY FUT 29 SEP 26",
            underlying="NIFTY",
            expiry=date(2026, 9, 29),
            observed_at=BASE,
            received_at=BASE,
            source=PROV_LIVE_OBSERVATION,
            last_price=Decimal("23190"),
            open_interest=13_331_370,
        )
    )
    assert written is False
    assert not any(tmp_path.iterdir()), "nothing may be written while capture is disabled"


def test_store_reads_nothing_from_an_empty_directory(tmp_path) -> None:
    store = FuturesObservationStore(base_dir=tmp_path)
    assert store.read("NIFTY", SESSION, "NSE_FO|68407") == []
    assert store.has_history("NIFTY", SESSION, "NSE_FO|68407") is False


# ===========================================================================
# 12. CSV export
# ===========================================================================

def test_export_carries_provenance_metadata() -> None:
    csv_text = build_csv(
        [{"ltp": 23190.0, "source": "LIVE_PROVIDER_OBSERVATION", "source_timestamp_ist": "17:57:36"}],
        metadata={
            "kind": "futures-ledger",
            "underlying": "NIFTY",
            "generated_from": "REAL_PROVIDER_OBSERVATIONS_ONLY",
        },
    )
    assert csv_text.startswith("# AIStockScanner derivatives export")
    assert "# underlying: NIFTY" in csv_text
    assert "source" in csv_text.splitlines()[-2]
    assert "17:57:36" in csv_text


def test_export_refuses_forbidden_columns() -> None:
    for column in ("token", "api_key", "snapshot_id", "contract_id", "raw_payload_hash", "user_id"):
        assert is_forbidden_column(column) is True

    with pytest.raises(DerivativeExportError):
        build_csv(
            [{"ltp": 1, "auth_token": "secret-value"}],
            columns=["ltp", "auth_token"],
        )


def test_export_never_emits_a_bearer_token_value() -> None:
    csv_text = build_csv(
        [{"note": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.signature", "ltp": 5}]
    )
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.signature" not in csv_text


def test_export_row_limit_is_enforced() -> None:
    with pytest.raises(DerivativeExportError):
        build_csv([{"a": 1}] * (MAX_EXPORT_ROWS + 1))


def test_export_flattens_nested_provenance_fields() -> None:
    csv_text = build_csv(
        [
            {
                "ltp": 100,
                "level_break": {"code": "DAY_HIGH_BREAK", "label": "Day-high break"},
                "interpretation": {"label": "Long Buildup"},
            }
        ],
        columns=FUTURES_LEDGER_EXPORT_COLUMNS,
    )
    assert "level_break.code" in csv_text
    assert "DAY_HIGH_BREAK" in csv_text
    assert "Long Buildup" in csv_text


def test_export_keeps_empty_cells_empty_rather_than_zero() -> None:
    csv_text = build_csv([{"ltp": 100, "oi_change": None}], columns=["ltp", "oi_change"])
    last_line = csv_text.strip().splitlines()[-1]
    assert last_line.endswith(","), "a missing value must remain an empty cell"


def test_export_filename_is_safe_and_deterministic() -> None:
    assert export_filename("futures-ledger", "NIFTY", "2026-09-24") == (
        "aistockscanner_futures-ledger_NIFTY_2026-09-24.csv"
    )
    assert "/" not in export_filename("a/b", "NIFTY", None)
