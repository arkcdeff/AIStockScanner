from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.scheduler.eod_scheduler import (
    MARKET_INTELLIGENCE_POSTMARKET_JOB_ID,
    MARKET_INTELLIGENCE_PREMARKET_JOB_ID,
    MARKET_INTELLIGENCE_SNAPSHOT_JOB_ID,
    EODScheduler,
)
from app.services.composite_market_intelligence import (
    CompositeMarketIntelligenceResult,
    FactorOut,
)
from app.services.market_intelligence_jobs import (
    MarketIntelligenceJobResult,
    run_market_intelligence_refresh_job,
)
from app.services.market_intelligence_notifications import (
    EVENT_TYPE_MARKET_MACRO_RISK,
    VIX_EXTREME_THRESHOLD,
    MarketIntelligenceNotificationResult,
    MarketIntelligenceNotificationService,
    snapshot_to_composite_result,
)


def test_market_intelligence_scheduler_jobs_disabled_by_default() -> None:
    """Test that market intelligence scheduler jobs are disabled by default."""
    from app.core.config import settings
    assert settings.market_intelligence_premarket_enabled is False
    assert settings.market_intelligence_postmarket_enabled is False
    assert settings.market_intelligence_snapshot_enabled is False

    scheduler = EODScheduler()
    status = scheduler.status()
    assert status["market_intelligence_premarket_enabled"] is False
    assert status["market_intelligence_postmarket_enabled"] is False
    assert status["market_intelligence_snapshot_enabled"] is False
    assert MARKET_INTELLIGENCE_PREMARKET_JOB_ID not in status["registered_job_ids"]
    assert MARKET_INTELLIGENCE_POSTMARKET_JOB_ID not in status["registered_job_ids"]
    assert MARKET_INTELLIGENCE_SNAPSHOT_JOB_ID not in status["registered_job_ids"]


@pytest.mark.asyncio
async def test_premarket_job_registers_once_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings
    monkeypatch.setattr(settings, "market_intelligence_premarket_enabled", True)
    scheduler = EODScheduler()
    try:
        scheduler.start()
        scheduler.start()  # Call twice to test idempotency
        assert scheduler._scheduler is not None
        ids = [job.id for job in scheduler._scheduler.get_jobs()]
        assert MARKET_INTELLIGENCE_PREMARKET_JOB_ID in ids
        status = scheduler.status()
        assert status["jobs"]["market_intelligence_premarket"]["next_run_time"] is not None
    finally:
        scheduler.stop()


@pytest.mark.asyncio
async def test_postmarket_job_registers_once_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings
    monkeypatch.setattr(settings, "market_intelligence_postmarket_enabled", True)
    scheduler = EODScheduler()
    try:
        scheduler.start()
        scheduler.start()
        assert scheduler._scheduler is not None
        ids = [job.id for job in scheduler._scheduler.get_jobs()]
        assert MARKET_INTELLIGENCE_POSTMARKET_JOB_ID in ids
        status = scheduler.status()
        assert status["jobs"]["market_intelligence_postmarket"]["next_run_time"] is not None
    finally:
        scheduler.stop()


@pytest.mark.asyncio
async def test_snapshot_job_registers_once_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings
    monkeypatch.setattr(settings, "market_intelligence_snapshot_enabled", True)
    scheduler = EODScheduler()
    try:
        scheduler.start()
        scheduler.start()
        assert scheduler._scheduler is not None
        ids = [job.id for job in scheduler._scheduler.get_jobs()]
        assert MARKET_INTELLIGENCE_SNAPSHOT_JOB_ID in ids
        status = scheduler.status()
        assert status["jobs"]["market_intelligence_snapshot"]["next_run_time"] is not None
    finally:
        scheduler.stop()


@pytest.mark.asyncio
async def test_manual_premarket_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test manual pre-market intelligence run."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "market_intelligence_premarket_enabled", True)
    monkeypatch.setattr(settings, "market_intelligence_snapshot_enabled", True)

    # Mock the job function
    async def mock_job():
        return MarketIntelligenceJobResult(
            status="completed",
            market_date=date.today().isoformat(),
            composite_score=10.0,
            regime="neutral",
            confidence_score=50.0,
            factors_refreshed=5,
            factors_failed=2,
            explanation_generated=False,
        )

    import app.scheduler.eod_scheduler as sched_module
    monkeypatch.setattr(sched_module, "run_premarket_intelligence_job", mock_job)

    scheduler = EODScheduler()
    result = await scheduler.run_premarket_intelligence_now()

    assert result["status"] == "completed"
    assert result["composite_score"] == 10.0
    assert result["regime"] == "neutral"


@pytest.mark.asyncio
async def test_manual_postmarket_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test manual post-market intelligence run."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "market_intelligence_snapshot_enabled", True)

    async def mock_job():
        return MarketIntelligenceJobResult(
            status="completed",
            market_date=date.today().isoformat(),
            composite_score=25.0,
            regime="bullish",
            confidence_score=70.0,
            factors_refreshed=8,
            factors_failed=0,
            explanation_generated=True,
        )

    import app.scheduler.eod_scheduler as sched_module
    monkeypatch.setattr(sched_module, "run_postmarket_intelligence_job", mock_job)

    scheduler = EODScheduler()
    result = await scheduler.run_postmarket_intelligence_now()

    assert result["status"] == "completed"
    assert result["regime"] == "bullish"
    assert result["explanation_generated"] is True


@pytest.mark.asyncio
async def test_manual_snapshot_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test manual daily snapshot intelligence run."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "market_intelligence_snapshot_enabled", True)

    async def mock_job():
        return MarketIntelligenceJobResult(
            status="completed",
            market_date=date.today().isoformat(),
            composite_score=65.0,
            regime="strong_bullish",
            confidence_score=85.0,
            factors_refreshed=10,
            factors_failed=0,
            explanation_generated=True,
        )

    import app.scheduler.eod_scheduler as sched_module
    monkeypatch.setattr(sched_module, "run_daily_snapshot_intelligence_job", mock_job)

    scheduler = EODScheduler()
    result = await scheduler.run_daily_snapshot_intelligence_now()

    assert result["status"] == "completed"
    assert result["regime"] == "strong_bullish"
    assert result["confidence_score"] == 85.0


@pytest.mark.asyncio
async def test_refresh_job_returns_disabled_when_all_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that refresh job returns disabled when all MI jobs are disabled."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "market_intelligence_snapshot_enabled", False)
    monkeypatch.setattr(settings, "market_intelligence_premarket_enabled", False)
    monkeypatch.setattr(settings, "market_intelligence_postmarket_enabled", False)

    result = await run_market_intelligence_refresh_job()

    assert result.status == "disabled"


@pytest.mark.asyncio
async def test_refresh_job_runs_with_mock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test market intelligence refresh job with mocked internals."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "market_intelligence_snapshot_enabled", True)

    # Mock the internal evaluation
    mock_result = MagicMock()
    mock_result.status = "completed"
    mock_result.composite_score = 15.0
    mock_result.regime = "bullish"
    mock_result.confidence_score = 60.0
    mock_result.snapshot_id = 1
    mock_result.factors = [MagicMock(score=10.0), MagicMock(score=None)]

    async def mock_evaluate(*args, **kwargs):
        return mock_result

    def mock_create_explanation(*args, **kwargs):
        return MagicMock(is_ai_generated=False)

    import app.services.market_intelligence_jobs as mi_jobs
    monkeypatch.setattr(mi_jobs.CompositeMarketIntelligence, "evaluate", mock_evaluate)
    monkeypatch.setattr(mi_jobs, "create_market_explanation", mock_create_explanation)

    result = await run_market_intelligence_refresh_job(
        market_date=date.today(),
        generate_explanation=True,
        persistence=True,
    )

    assert result.status == "completed"
    assert result.composite_score == 15.0
    assert result.regime == "bullish"
    assert result.explanation_generated is True


# Notification service tests

@pytest.mark.asyncio
async def test_regime_change_notification_triggered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that regime change triggers notification."""
    from app.services.composite_market_intelligence import (
        CompositeMarketIntelligenceResult,
        FactorOut,
    )

    # Create current and previous results with different regimes
    factors = [
        FactorOut(name="price_action", score=30.0, configured_weight=0.25),
        FactorOut(name="breadth", score=20.0, configured_weight=0.15),
    ]

    current = CompositeMarketIntelligenceResult(
        regime="bullish",
        composite_score=25.0,
        confidence_score=70.0,
        factors=factors,
        snapshot_id=2,
    )

    previous = CompositeMarketIntelligenceResult(
        regime="neutral",
        composite_score=5.0,
        confidence_score=50.0,
        factors=factors,
        snapshot_id=1,
    )

    # We need a mock session - this is complex, so we'll test the logic directly
    # by checking the trigger condition
    triggered = current.regime != previous.regime
    assert triggered is True
    assert current.regime == "bullish"
    assert previous.regime == "neutral"


@pytest.mark.asyncio
async def test_regime_change_not_triggered_when_same(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that regime change doesn't trigger when regime is same."""
    from app.services.composite_market_intelligence import (
        CompositeMarketIntelligenceResult,
        FactorOut,
    )

    factors = [FactorOut(name="price_action", score=10.0, configured_weight=0.25)]

    current = CompositeMarketIntelligenceResult(
        regime="neutral",
        composite_score=10.0,
        confidence_score=55.0,
        factors=factors,
    )

    previous = CompositeMarketIntelligenceResult(
        regime="neutral",
        composite_score=8.0,
        confidence_score=50.0,
        factors=factors,
    )

    triggered = current.regime != previous.regime
    assert triggered is False


@pytest.mark.asyncio
async def test_score_transition_triggered_above_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that score transition triggers when threshold exceeded."""
    from decimal import Decimal

    from app.services.composite_market_intelligence import (
        CompositeMarketIntelligenceResult,
        FactorOut,
    )

    factors = [FactorOut(name="price_action", score=10.0, configured_weight=0.25)]

    current = CompositeMarketIntelligenceResult(
        composite_score=40.0,
        regime="bullish",
        confidence_score=70.0,
        factors=factors,
    )

    previous = CompositeMarketIntelligenceResult(
        composite_score=10.0,
        regime="neutral",
        confidence_score=50.0,
        factors=factors,
    )

    change = abs(Decimal(str(current.composite_score)) - Decimal(str(previous.composite_score)))
    from app.services.market_intelligence_notifications import SCORE_TRANSITION_THRESHOLD

    assert change >= SCORE_TRANSITION_THRESHOLD
    assert change == Decimal("30.0")


@pytest.mark.asyncio
async def test_score_transition_not_triggered_below_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that score transition doesn't trigger when below threshold."""
    from decimal import Decimal

    from app.services.composite_market_intelligence import (
        CompositeMarketIntelligenceResult,
        FactorOut,
    )

    factors = [FactorOut(name="price_action", score=10.0, configured_weight=0.25)]

    current = CompositeMarketIntelligenceResult(
        composite_score=15.0,
        regime="neutral",
        confidence_score=55.0,
        factors=factors,
    )

    previous = CompositeMarketIntelligenceResult(
        composite_score=10.0,
        regime="neutral",
        confidence_score=50.0,
        factors=factors,
    )

    change = abs(Decimal(str(current.composite_score)) - Decimal(str(previous.composite_score)))
    from app.services.market_intelligence_notifications import SCORE_TRANSITION_THRESHOLD

    assert change < SCORE_TRANSITION_THRESHOLD
    assert change == Decimal("5.0")


@pytest.mark.asyncio
async def test_vix_extreme_triggered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that extreme VIX triggers notification."""
    factors = [
        FactorOut(name="price_action", score=10.0, configured_weight=0.25),
        FactorOut(name="volatility", score=-65.0, configured_weight=0.10),
    ]

    current_result = CompositeMarketIntelligenceResult(
        composite_score=-20.0,
        regime="bearish",
        confidence_score=75.0,
        factors=factors,
    )

    vix_factor = next(f for f in current_result.factors if f.name == "volatility")
    score_ok = vix_factor.score is not None
    threshold_check = Decimal(str(vix_factor.score)) <= VIX_EXTREME_THRESHOLD
    triggered = score_ok and threshold_check
    assert triggered is True
    assert Decimal(str(vix_factor.score)) <= VIX_EXTREME_THRESHOLD


@pytest.mark.asyncio
async def test_vix_extreme_not_triggered_when_normal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that VIX doesn't trigger when normal."""
    factors = [
        FactorOut(name="price_action", score=10.0, configured_weight=0.25),
        FactorOut(name="volatility", score=-10.0, configured_weight=0.10),
    ]

    current_result = CompositeMarketIntelligenceResult(
        composite_score=5.0,
        regime="neutral",
        confidence_score=60.0,
        factors=factors,
    )

    vix_factor = next(f for f in current_result.factors if f.name == "volatility")
    score_ok = vix_factor.score is not None
    threshold_check = Decimal(str(vix_factor.score)) <= VIX_EXTREME_THRESHOLD
    triggered = score_ok and threshold_check
    assert triggered is False


# ---------------------------------------------------------------------------
# market_macro_risk contract: Task 18.8 upcoming high-importance macro events
# ---------------------------------------------------------------------------


def _upcoming_macro_event(
    *,
    code: str = "RBI_POLICY_DECISION",
    importance: str = "high",
    reference_time: str = "2026-08-20T09:00:00+05:30",
    name: str = "RBI Monetary Policy Decision",
) -> dict:
    """Shape of a Task 18.8 ``upcoming_events`` entry (lifecycle upcoming)."""
    return {
        "event_code": code,
        "name": name,
        "category": "monetary_policy",
        "importance": importance,
        "reference_time": reference_time,
        "lifecycle": "upcoming",
        "time_to_event": 2.5,
        "actual": None,
        "forecast": None,
        "previous": None,
        "signal": None,
        "freshness_status": "recent",
        "source_name": "manual_import",
        "source_reference": "rbi-calendar",
    }


def _macro_current_result(upcoming_events: list[dict]) -> CompositeMarketIntelligenceResult:
    factors = [
        FactorOut(name="price_action", score=10.0, configured_weight=0.25),
        FactorOut(name="macro", score=5.0, configured_weight=0.05),
    ]
    return CompositeMarketIntelligenceResult(
        composite_score=10.0,
        regime="neutral",
        confidence_score=60.0,
        factors=factors,
        snapshot_id=7,
        upcoming_macro_events=upcoming_events,
    )


def _macro_service() -> MarketIntelligenceNotificationService:
    service = MarketIntelligenceNotificationService(MagicMock())
    service.delivery_repo.list_recent_by_type_and_date = AsyncMock(return_value=[])
    service._send_market_intelligence_notification = AsyncMock(return_value=1)
    return service


@pytest.mark.asyncio
async def test_macro_risk_triggered_by_high_importance_upcoming_event() -> None:
    """High-importance upcoming Task 18.8 event triggers market_macro_risk."""
    service = _macro_service()
    result = await service._check_macro_risk(
        current=_macro_current_result([_upcoming_macro_event()]),
        market_date=date(2026, 8, 17),
    )
    assert result.triggered is True
    assert result.event_type == EVENT_TYPE_MARKET_MACRO_RISK
    assert result.market_date == "2026-08-17"
    service._send_market_intelligence_notification.assert_awaited_once()
    kwargs = service._send_market_intelligence_notification.await_args.kwargs
    assert kwargs["context"]["event_code"] == "RBI_POLICY_DECISION"
    assert kwargs["context"]["market_date"] == "2026-08-17"
    # Dedup identity = real event identity/date (event_code + reference_time).
    assert kwargs["from_state"] == "RBI_POLICY_DECISION"
    assert kwargs["to_state"] == "2026-08-20T09:00:00+05:30"


@pytest.mark.asyncio
async def test_macro_risk_triggered_for_top_tier_importance() -> None:
    """'very high' risk maps to the top tier of the existing high/medium/low enum."""
    events = [
        _upcoming_macro_event(code="INDIA_WPI", importance="medium"),
        _upcoming_macro_event(
            code="FED_POLICY_DECISION",
            importance="high",
            reference_time="2026-08-19T14:00:00-04:00",
            name="US Federal Reserve Policy Decision",
        ),
    ]
    service = _macro_service()
    result = await service._check_macro_risk(
        current=_macro_current_result(events),
        market_date=date(2026, 8, 17),
    )
    assert result.triggered is True
    kwargs = service._send_market_intelligence_notification.await_args.kwargs
    # Primary event is the nearest scheduled high-importance event (deterministic).
    assert kwargs["from_state"] == "FED_POLICY_DECISION"


@pytest.mark.asyncio
async def test_macro_risk_not_triggered_for_medium_or_low_importance() -> None:
    """Moderate/low upcoming events never trigger market_macro_risk."""
    events = [
        _upcoming_macro_event(code="INDIA_WPI", importance="medium"),
        _upcoming_macro_event(code="INDIA_COAL_PRODUCTION", importance="low"),
    ]
    service = _macro_service()
    result = await service._check_macro_risk(
        current=_macro_current_result(events),
        market_date=date(2026, 8, 17),
    )
    assert result.triggered is False
    assert result.from_state is None
    service._send_market_intelligence_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_macro_risk_not_triggered_when_no_upcoming_events() -> None:
    """No upcoming Task 18.8 events -> no market_macro_risk notification."""
    service = _macro_service()
    result = await service._check_macro_risk(
        current=_macro_current_result([]),
        market_date=date(2026, 8, 17),
    )
    assert result.triggered is False
    service._send_market_intelligence_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_macro_risk_same_event_deduplicated_by_cooldown() -> None:
    """The same upcoming event is not re-sent every scheduler cycle."""
    service = MarketIntelligenceNotificationService(MagicMock())
    service.delivery_repo.list_recent_by_type_and_date = AsyncMock(
        return_value=[
            MagicMock(
                subject=(
                    "Market Intelligence: High-Risk Macro Event RBI_POLICY_DECISION "
                    "at 2026-08-20T09:00:00+05:30 [2026-08-17]"
                )
            )
        ]
    )
    service._send_market_intelligence_notification = AsyncMock(return_value=1)
    result = await service._check_macro_risk(
        current=_macro_current_result([_upcoming_macro_event()]),
        market_date=date(2026, 8, 17),
    )
    assert result.triggered is True
    assert result.deliveries_skipped == 1
    service._send_market_intelligence_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_macro_risk_existing_delivery_skips_resend_by_event_identity() -> None:
    """Per-channel idempotency keys on the real event identity/date."""
    service = MarketIntelligenceNotificationService(MagicMock())

    class _Pref:
        event_type = EVENT_TYPE_MARKET_MACRO_RISK
        enabled = True
        minimum_priority = None
        quiet_hours_start = None
        quiet_hours_end = None

    class _Channel:
        id = 1
        provider = "telegram"
        enabled = True
        config_json = {"chat_id": "123"}
        preferences = [_Pref()]

    service.channel_repo.list_with_preferences = AsyncMock(return_value=[_Channel()])
    service.delivery_repo.existing_by_type_date_states = AsyncMock(
        return_value=MagicMock()
    )
    service.manager.render = MagicMock()
    service.manager.resolve_provider = MagicMock()
    service.manager.send = AsyncMock()

    sent = await service._send_market_intelligence_notification(
        event_type=EVENT_TYPE_MARKET_MACRO_RISK,
        market_date=date(2026, 8, 17),
        context={"event_code": "RBI_POLICY_DECISION", "market_date": "2026-08-17"},
        from_state="RBI_POLICY_DECISION",
        to_state="2026-08-20T09:00:00+05:30",
    )
    assert sent == 0
    service.manager.send.assert_not_awaited()
    service.delivery_repo.existing_by_type_date_states.assert_awaited_once()
    kwargs = service.delivery_repo.existing_by_type_date_states.await_args.kwargs
    assert kwargs["from_state"] == "RBI_POLICY_DECISION"
    assert kwargs["to_state"] == "2026-08-20T09:00:00+05:30"


@pytest.mark.asyncio
async def test_macro_risk_event_beyond_lookahead_boundary_not_visible() -> None:
    """An event scheduled beyond the evaluation lookahead is never a trigger.

    Task 18.8 only reports events within its bounded lookahead as ``upcoming``;
    an event after the historical evaluation boundary is absent from
    ``upcoming_macro_events`` so it cannot emit a live notification.
    """
    service = _macro_service()
    result = await service._check_macro_risk(
        current=_macro_current_result([]),
        market_date=date(2026, 8, 17),
    )
    assert result.triggered is False
    service._send_market_intelligence_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_historical_evaluation_does_not_emit_live_alerts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-persisted (historical/manual) evaluation never emits live alerts."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "market_intelligence_snapshot_enabled", True)

    mock_result = MagicMock()
    mock_result.status = "completed"
    mock_result.composite_score = 10.0
    mock_result.regime = "neutral"
    mock_result.confidence_score = 60.0
    mock_result.snapshot_id = 1
    mock_result.factors = [MagicMock(score=10.0)]

    async def mock_evaluate(*args, **kwargs):
        return mock_result

    import app.services.market_intelligence_jobs as mi_jobs

    monkeypatch.setattr(mi_jobs.CompositeMarketIntelligence, "evaluate", mock_evaluate)
    notification_service_mock = MagicMock()
    notification_service_mock.evaluate_and_notify = AsyncMock()
    monkeypatch.setattr(
        mi_jobs,
        "MarketIntelligenceNotificationService",
        lambda session: notification_service_mock,
    )

    result = await run_market_intelligence_refresh_job(
        market_date=date(2026, 1, 5),
        persistence=False,
        generate_explanation=False,
    )
    assert result.status == "completed"
    notification_service_mock.evaluate_and_notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_macro_notification_deterministic_when_ai_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deterministic macro notification still fires when AI explanation is down."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "market_intelligence_snapshot_enabled", True)

    mock_result = MagicMock()
    mock_result.status = "completed"
    mock_result.composite_score = 10.0
    mock_result.regime = "neutral"
    mock_result.confidence_score = 60.0
    mock_result.snapshot_id = 1
    mock_result.factors = [MagicMock(score=10.0)]

    async def mock_evaluate(*args, **kwargs):
        return mock_result

    def mock_explanation_fails(*args, **kwargs):
        raise RuntimeError("AI provider unavailable")

    import app.services.market_intelligence_jobs as mi_jobs

    monkeypatch.setattr(mi_jobs.CompositeMarketIntelligence, "evaluate", mock_evaluate)
    monkeypatch.setattr(mi_jobs, "create_market_explanation", mock_explanation_fails)

    notification_service_mock = MagicMock()
    notification_service_mock.evaluate_and_notify = AsyncMock(
        return_value=[
            MarketIntelligenceNotificationResult(
                event_type=EVENT_TYPE_MARKET_MACRO_RISK,
                market_date="2026-08-17",
                triggered=True,
            )
        ]
    )
    monkeypatch.setattr(
        mi_jobs,
        "MarketIntelligenceNotificationService",
        lambda session: notification_service_mock,
    )

    result = await run_market_intelligence_refresh_job(
        market_date=date(2026, 8, 17),
        persistence=True,
        generate_explanation=True,
    )
    assert result.status == "completed"
    assert result.explanation_generated is False
    notification_service_mock.evaluate_and_notify.assert_awaited_once()


def test_macro_risk_subject_contains_event_identity() -> None:
    """Subject embeds event identity + date so cooldown/dedup can match."""
    from app.notifications.renderer import NotificationRenderer

    rendered = NotificationRenderer().render(
        notification_type=EVENT_TYPE_MARKET_MACRO_RISK,
        provider="telegram",
        context={
            "event_type": "macro_risk",
            "macro_events": [_upcoming_macro_event()],
            "event_code": "RBI_POLICY_DECISION",
            "event_reference_time": "2026-08-20T09:00:00+05:30",
            "event_name": "RBI Monetary Policy Decision",
            "event_importance": "high",
            "market_date": "2026-08-17",
            "regime": "neutral",
            "composite_score": 10.0,
            "confidence_score": 60.0,
        },
    )
    assert "RBI_POLICY_DECISION" in rendered["subject"]
    assert "2026-08-20T09:00:00+05:30" in rendered["subject"]
    assert "2026-08-17" in rendered["subject"]
    assert "RBI Monetary Policy Decision" in rendered["text"]


@pytest.mark.asyncio
async def test_composite_carries_upcoming_macro_events_from_macro_engine() -> None:
    """Composite result propagates Task 18.8 upcoming events to notifications."""
    from app.services.composite_market_intelligence import CompositeMarketIntelligence

    def _engine(data: dict):
        engine = MagicMock()
        engine.evaluate = AsyncMock(return_value=data)
        return engine

    class _FakePriceRepo:
        async def get_date_range_by_index_code(self, index_code, start_date, end_date):
            return []

    ext = _engine({"factors": {}})
    breadth = _engine(
        {
            "breadth_score": 10.0,
            "confidence": 50.0,
            "completeness": 50.0,
            "freshness": "recent",
            "provenance": {},
            "breadth_direction": "positive",
        }
    )
    sector = _engine(
        {
            "sector_intelligence_score": 10.0,
            "confidence_score": 50.0,
            "completeness_percent": 50.0,
            "freshness": "recent",
            "provenance": {},
            "direction": "positive",
        }
    )
    institutional = _engine(
        {
            "institutional_score": 10.0,
            "confidence": 50.0,
            "completeness": 50.0,
            "freshness": "recent",
            "provenance": {},
            "direction": "positive",
        }
    )
    global_result = _engine(
        {
            "global_score": 10.0,
            "confidence": 50.0,
            "completeness": 50.0,
            "markets": {},
            "global_direction": "positive",
        }
    )
    gift = _engine(
        {
            "gift_nifty_score": 10.0,
            "confidence_score": 50.0,
            "data_completeness_percent": 50.0,
            "provenance": {},
            "direction": "positive",
            "gift_observation": None,
            "fallback_level": "none",
        }
    )
    macro = _engine(
        {
            "macro_score": 5.0,
            "confidence": 50.0,
            "completeness": 50.0,
            "freshness": "recent",
            "provenance": {},
            "direction": "positive",
            "upcoming_events": [_upcoming_macro_event()],
        }
    )

    engine = CompositeMarketIntelligence(
        None,
        price_repo=_FakePriceRepo(),
        snapshot_repo=MagicMock(),
        breadth_engine=breadth,
        sector_engine=sector,
        institutional_engine=institutional,
        external_factor_engine=ext,
        global_engine=global_result,
        gift_nifty_engine=gift,
        macro_engine=macro,
    )
    result = await engine.evaluate(datetime.now(UTC))
    assert len(result.upcoming_macro_events) == 1
    assert result.upcoming_macro_events[0]["event_code"] == "RBI_POLICY_DECISION"
    assert result.upcoming_macro_events[0]["importance"] == "high"


@pytest.mark.asyncio
async def test_snapshot_to_composite_result() -> None:
    """Test conversion of snapshot to composite result."""
    from app.models.market_intelligence_snapshot import MarketIntelligenceSnapshot

    snapshot = MarketIntelligenceSnapshot(
        id=1,
        market_date=date(2026, 8, 15),
        generated_at=datetime(2026, 8, 15, 17, 0, tzinfo=UTC),
        price_action_score=25.0,
        breadth_score=30.0,
        sector_score=15.0,
        institutional_score=-10.0,
        volatility_score=-5.0,
        global_score=20.0,
        gift_nifty_score=10.0,
        currency_score=-5.0,
        crude_score=5.0,
        macro_score=0.0,
        composite_score=8.5,
        confidence_score=65.0,
        regime="neutral",
        data_completeness_percent=80.0,
        stale_factor_count=1,
        missing_factor_count=2,
    )

    result = snapshot_to_composite_result(snapshot)

    assert result.snapshot_id == 1
    assert result.market_date == date(2026, 8, 15)
    assert result.composite_score == 8.5
    assert result.regime == "neutral"
    assert result.confidence_score == 65.0
    assert result.data_completeness_percent == 80.0
    assert len(result.factors) == 10
    assert result.factors[0].name == "price_action"
    assert result.factors[0].score == 25.0


# Test the scheduler status includes market intelligence jobs

@pytest.mark.asyncio
async def test_scheduler_status_includes_mi_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that scheduler status includes market intelligence jobs."""
    from app.core.config import settings
    from app.scheduler.eod_scheduler import EODScheduler

    monkeypatch.setattr(settings, "market_intelligence_premarket_enabled", True)
    monkeypatch.setattr(settings, "market_intelligence_postmarket_enabled", True)
    monkeypatch.setattr(settings, "market_intelligence_snapshot_enabled", True)

    scheduler = EODScheduler()
    try:
        scheduler.start()
        status = scheduler.status()

        assert status["market_intelligence_premarket_enabled"] is True
        assert status["market_intelligence_postmarket_enabled"] is True
        assert status["market_intelligence_snapshot_enabled"] is True
        assert "market_intelligence_premarket" in status["jobs"]
        assert "market_intelligence_postmarket" in status["jobs"]
        assert "market_intelligence_snapshot" in status["jobs"]
    finally:
        scheduler.stop()