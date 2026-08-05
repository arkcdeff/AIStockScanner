from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db_session
from app.models.alert import AlertEvent
from app.models.notification import NotificationChannel, NotificationPreference
from app.notifications.base import NotificationMessage
from app.notifications.exceptions import (
    NotificationConfigError,
    NotificationError,
    NotificationProviderError,
)
from app.notifications.factory import get_provider
from app.repositories.notification import (
    DailySummaryRunRepository,
    NotificationChannelRepository,
    NotificationDeliveryRepository,
    NotificationPreferenceRepository,
)
from app.scheduler.eod_scheduler import get_scheduler
from app.schemas.notification import (
    SUPPORTED_DELIVERY_STATUSES,
    DailySummaryExecutionResponse,
    DailySummaryHistoryResponse,
    DailySummaryRunRead,
    DailySummaryRunRequest,
    DeliverAlertResponse,
    DeliverPendingRequest,
    DeliverPendingResponse,
    DeliverySchedulerRunResponse,
    NotificationAlertSummary,
    NotificationChannelCreate,
    NotificationChannelRead,
    NotificationChannelUpdate,
    NotificationDeliveryDetail,
    NotificationDeliveryHistoryItem,
    NotificationMarkReadResponse,
    NotificationPreferenceCreate,
    NotificationPreferenceRead,
    NotificationPreferenceUpdate,
    NotificationRetryResponse,
    NotificationSchedulerStatusRead,
    NotificationStatusRead,
    NotificationStockSummary,
    ProviderTestRequest,
    ProviderTestResponse,
    normalize_notification_provider,
    sanitize_notification_config,
    sanitize_notification_error,
    validate_provider_config,
    validate_quiet_hours,
)
from app.services.daily_summary import DailySummaryService, supported_summary_statuses
from app.services.notification_service import NotificationConflictError, NotificationService

router = APIRouter(prefix="/notifications", tags=["Notifications"])


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


def _normalized_provider(provider: str) -> str:
    try:
        return normalize_notification_provider(provider)
    except ValueError as exc:
        raise _unprocessable(str(exc)) from exc


def _normalized_event_type(event_type: str) -> str:
    value = event_type.strip().lower()
    if not value:
        raise _unprocessable("event_type must not be blank")
    return value


def _channel_read(channel: NotificationChannel) -> NotificationChannelRead:
    return NotificationChannelRead(
        id=channel.id,
        name=channel.name,
        provider=channel.provider,
        enabled=channel.enabled,
        config_json=sanitize_notification_config(channel.config_json),
        created_at=channel.created_at,
        updated_at=channel.updated_at,
    )


def _history_item(delivery) -> NotificationDeliveryHistoryItem:
    return NotificationDeliveryHistoryItem(
        id=delivery.id,
        alert_event_id=delivery.alert_event_id,
        channel_id=delivery.channel_id,
        channel_name=delivery.channel.name,
        provider=delivery.channel.provider,
        notification_type=delivery.notification_type,
        subject=delivery.subject,
        status=delivery.status,
        attempts=delivery.attempt_count,
        sent_at=delivery.sent_at,
        created_at=delivery.created_at,
        unread=delivery.status == "sent",
    )


def _config_secrets(config: dict[str, Any] | None) -> list[str]:
    if not config:
        return []
    sensitive = {"api_key", "bot_token", "chat_id", "password", "recipient", "token"}
    return [str(value) for key, value in config.items() if key.lower() in sensitive and value]


def _daily_summary_read(run) -> DailySummaryRunRead:
    return DailySummaryRunRead(
        id=run.id,
        summary_date=run.summary_date,
        status=run.status,
        alerts_count=run.alerts_count,
        scanner_matches=run.scanner_matches,
        top_rated_count=run.top_rated_count,
        watchlist_changes=run.watchlist_changes,
        portfolio_value=run.portfolio_value,
        started_at=run.started_at,
        finished_at=run.finished_at,
        error_message=sanitize_notification_error(run.error_message),
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


@router.get("/status", response_model=NotificationStatusRead)
async def notification_status(
    session: AsyncSession = Depends(get_db_session),
) -> NotificationStatusRead:
    channel_repo = NotificationChannelRepository(session)
    delivery_repo = NotificationDeliveryRepository(session)
    channels = await channel_repo.list_filtered(limit=500)
    counts = await delivery_repo.status_summary(stale_minutes=settings.notification_stale_minutes)

    enabled_providers = {channel.provider for channel in channels if channel.enabled}
    telegram_configured = bool(settings.telegram_bot_token and settings.telegram_default_chat_id)
    email_configured = bool(settings.email_host and settings.email_from)
    for channel in channels:
        config = channel.config_json or {}
        if channel.provider == "telegram":
            telegram_configured = telegram_configured or bool(
                (config.get("bot_token") or settings.telegram_bot_token)
                and (
                    config.get("recipient")
                    or config.get("chat_id")
                    or settings.telegram_default_chat_id
                )
            )
        elif channel.provider == "email":
            email_configured = email_configured or bool(
                (config.get("host") or settings.email_host)
                and (config.get("from") or settings.email_from)
            )

    return NotificationStatusRead(
        notifications_enabled=settings.notifications_enabled,
        provider_availability={
            "mock": True,
            "telegram": telegram_configured,
            "email": email_configured,
            "in_app": True,
        },
        telegram_enabled="telegram" in enabled_providers,
        email_enabled="email" in enabled_providers,
        in_app_enabled="in_app" in enabled_providers,
        **counts,
    )


@router.post("/daily-summary/run", response_model=DailySummaryExecutionResponse)
async def run_daily_summary(
    payload: DailySummaryRunRequest | None = Body(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> DailySummaryExecutionResponse:
    execution = await DailySummaryService(session).run(
        payload.summary_date if payload is not None else None
    )
    return DailySummaryExecutionResponse(
        run=_daily_summary_read(execution.run),
        idempotent=execution.idempotent,
        delivery_ids=execution.delivery_ids,
    )


@router.get("/daily-summary/history", response_model=DailySummaryHistoryResponse)
async def daily_summary_history(
    status: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> DailySummaryHistoryResponse:
    if status is not None and status not in supported_summary_statuses():
        raise _unprocessable("Unsupported daily summary status")
    if date_from is not None and date_to is not None and date_from > date_to:
        raise _unprocessable("date_from must be on or before date_to")
    repo = DailySummaryRunRepository(session)
    runs = await repo.list_filtered(
        status=status,
        date_from=date_from,
        date_to=date_to,
        offset=offset,
        limit=limit,
    )
    total = await repo.count_filtered(status=status, date_from=date_from, date_to=date_to)
    return DailySummaryHistoryResponse(
        items=[_daily_summary_read(run) for run in runs],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/daily-summary/{summary_id}", response_model=DailySummaryRunRead)
async def daily_summary_detail(
    summary_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> DailySummaryRunRead:
    run = await DailySummaryRunRepository(session).get(summary_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Daily summary run not found")
    return _daily_summary_read(run)


@router.post("/scheduler/run-delivery", response_model=DeliverySchedulerRunResponse)
async def run_notification_delivery_scheduler() -> DeliverySchedulerRunResponse:
    result = await get_scheduler().run_delivery_now()
    return DeliverySchedulerRunResponse.model_validate(result)


@router.get("/scheduler/status", response_model=NotificationSchedulerStatusRead)
async def notification_scheduler_status() -> NotificationSchedulerStatusRead:
    status = get_scheduler().status()
    return NotificationSchedulerStatusRead(
        running=bool(status["running"]),
        alert_delivery_job_enabled=bool(status["alert_delivery_job_enabled"]),
        daily_summary_job_enabled=bool(status["daily_summary_job_enabled"]),
        registered_job_ids=list(status["registered_job_ids"]),
        jobs=status["jobs"],
    )


@router.get("/channels", response_model=list[NotificationChannelRead])
async def list_channels(
    enabled: bool | None = Query(default=None),
    provider: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> list[NotificationChannelRead]:
    normalized_provider = _normalized_provider(provider) if provider is not None else None
    channels = await NotificationChannelRepository(session).list_filtered(
        enabled=enabled,
        provider=normalized_provider,
        offset=offset,
        limit=limit,
    )
    return [_channel_read(channel) for channel in channels]


@router.post(
    "/channels",
    response_model=NotificationChannelRead,
    status_code=http_status.HTTP_201_CREATED,
)
async def create_channel(
    payload: NotificationChannelCreate,
    session: AsyncSession = Depends(get_db_session),
) -> NotificationChannelRead:
    repo = NotificationChannelRepository(session)
    if await repo.get_by_name(payload.name) is not None:
        raise HTTPException(status_code=409, detail="Notification channel name already exists")
    values = payload.model_dump()
    try:
        values["config_json"] = validate_provider_config(payload.provider, payload.config_json)
    except (TypeError, ValueError) as exc:
        raise _unprocessable(str(exc)) from exc
    channel = NotificationChannel(**values)
    try:
        await repo.add(channel)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="Notification channel name already exists"
        ) from exc
    return _channel_read(channel)


@router.put("/channels/{channel_id}", response_model=NotificationChannelRead)
async def update_channel(
    channel_id: int,
    payload: NotificationChannelUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> NotificationChannelRead:
    repo = NotificationChannelRepository(session)
    channel = await repo.get(channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Notification channel not found")

    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes:
        duplicate = await repo.get_by_name(changes["name"])
        if duplicate is not None and duplicate.id != channel_id:
            raise HTTPException(status_code=409, detail="Notification channel name already exists")

    provider = changes.get("provider", channel.provider)
    provider_changed = provider != channel.provider
    if "config_json" in changes:
        supplied_config = changes["config_json"]
        if supplied_config is None:
            config: dict[str, Any] = {}
        else:
            config = {} if provider_changed else dict(channel.config_json or {})
            for key, value in supplied_config.items():
                if value == "********" and key in config:
                    continue
                config[key] = value
        try:
            changes["config_json"] = validate_provider_config(provider, config)
        except (TypeError, ValueError) as exc:
            raise _unprocessable(str(exc)) from exc
    elif provider_changed:
        changes["config_json"] = {}

    for key, value in changes.items():
        setattr(channel, key, value)
    try:
        await session.commit()
        await session.refresh(channel)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="Notification channel name already exists"
        ) from exc
    return _channel_read(channel)


@router.delete("/channels/{channel_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_channel(
    channel_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    repo = NotificationChannelRepository(session)
    channel = await repo.get(channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Notification channel not found")
    try:
        await session.delete(channel)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Notification channel is still referenced and cannot be deleted",
        ) from exc


@router.get("/preferences", response_model=list[NotificationPreferenceRead])
async def list_preferences(
    channel_id: int | None = Query(default=None, gt=0),
    event_type: str | None = Query(default=None, max_length=40),
    enabled: bool | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> list[NotificationPreferenceRead]:
    normalized_event = _normalized_event_type(event_type) if event_type is not None else None
    preferences = await NotificationPreferenceRepository(session).list_filtered(
        channel_id=channel_id,
        event_type=normalized_event,
        enabled=enabled,
        offset=offset,
        limit=limit,
    )
    return [NotificationPreferenceRead.model_validate(item) for item in preferences]


@router.post(
    "/preferences",
    response_model=NotificationPreferenceRead,
    status_code=http_status.HTTP_201_CREATED,
)
async def create_preference(
    payload: NotificationPreferenceCreate,
    session: AsyncSession = Depends(get_db_session),
) -> NotificationPreferenceRead:
    if await NotificationChannelRepository(session).get(payload.channel_id) is None:
        raise HTTPException(status_code=404, detail="Notification channel not found")
    repo = NotificationPreferenceRepository(session)
    if await repo.get_for_channel(payload.channel_id, payload.event_type) is not None:
        raise HTTPException(status_code=409, detail="Notification preference already exists")
    preference = NotificationPreference(**payload.model_dump())
    try:
        await repo.add(preference)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="Notification preference already exists"
        ) from exc
    return NotificationPreferenceRead.model_validate(preference)


@router.put("/preferences/{preference_id}", response_model=NotificationPreferenceRead)
async def update_preference(
    preference_id: int,
    payload: NotificationPreferenceUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> NotificationPreferenceRead:
    repo = NotificationPreferenceRepository(session)
    preference = await repo.get(preference_id)
    if preference is None:
        raise HTTPException(status_code=404, detail="Notification preference not found")
    changes = payload.model_dump(exclude_unset=True)

    channel_id = changes.get("channel_id", preference.channel_id)
    if channel_id != preference.channel_id:
        if await NotificationChannelRepository(session).get(channel_id) is None:
            raise HTTPException(status_code=404, detail="Notification channel not found")
    event_type = changes.get("event_type", preference.event_type)
    duplicate = await repo.get_for_channel(channel_id, event_type)
    if duplicate is not None and duplicate.id != preference_id:
        raise HTTPException(status_code=409, detail="Notification preference already exists")

    quiet_start = changes.get("quiet_hours_start", preference.quiet_hours_start)
    quiet_end = changes.get("quiet_hours_end", preference.quiet_hours_end)
    try:
        validate_quiet_hours(quiet_start, quiet_end)
    except ValueError as exc:
        raise _unprocessable(str(exc)) from exc

    for key, value in changes.items():
        setattr(preference, key, value)
    try:
        await session.commit()
        await session.refresh(preference)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="Notification preference already exists"
        ) from exc
    return NotificationPreferenceRead.model_validate(preference)


@router.delete("/preferences/{preference_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_preference(
    preference_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    preference = await NotificationPreferenceRepository(session).get(preference_id)
    if preference is None:
        raise HTTPException(status_code=404, detail="Notification preference not found")
    await session.delete(preference)
    await session.commit()


@router.post("/deliver-alert/{alert_event_id}", response_model=DeliverAlertResponse)
async def deliver_alert(
    alert_event_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> DeliverAlertResponse:
    if await session.get(AlertEvent, alert_event_id) is None:
        raise HTTPException(status_code=404, detail="Alert event not found")
    summary = await NotificationService(session).deliver_alert(alert_event_id)
    return DeliverAlertResponse(
        alert_event_id=alert_event_id,
        channels_evaluated=int(summary["total"]),
        sent=int(summary["sent"]),
        failed=int(summary["failed"]),
        retry_scheduled=int(summary["retry_scheduled"]),
        skipped=int(summary["skipped"]),
        delivery_ids=[int(value) for value in summary["delivery_ids"]],
    )


@router.post("/deliver-pending", response_model=DeliverPendingResponse)
async def deliver_pending(
    payload: DeliverPendingRequest | None = Body(default=None),
    batch_size: int | None = Query(default=None, ge=1),
    session: AsyncSession = Depends(get_db_session),
) -> DeliverPendingResponse:
    requested = batch_size if batch_size is not None else (payload.batch_size if payload else None)
    effective = requested or settings.notification_batch_size
    if effective > settings.notification_batch_size:
        raise _unprocessable(
            f"batch_size must be less than or equal to {settings.notification_batch_size}"
        )
    dispatched = await NotificationService(session).deliver_pending(batch_size=effective)
    return DeliverPendingResponse(batch_size=effective, dispatched=dispatched)


@router.post("/history/{delivery_id}/retry", response_model=NotificationRetryResponse)
async def retry_delivery(
    delivery_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> NotificationRetryResponse:
    try:
        delivery = await NotificationService(session).retry_delivery(
            delivery_id, reject_completed=True
        )
    except NotificationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if delivery is None:
        raise HTTPException(status_code=404, detail="Notification delivery not found")
    return NotificationRetryResponse(
        delivery_id=delivery.id,
        status=delivery.status,
        attempt_count=delivery.attempt_count,
        sent_at=delivery.sent_at,
        next_retry_at=delivery.next_retry_at,
    )


@router.post("/history/{delivery_id}/mark-read", response_model=NotificationMarkReadResponse)
async def mark_delivery_read(
    delivery_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> NotificationMarkReadResponse:
    delivery = await NotificationService(session).mark_read(delivery_id)
    if delivery is None:
        raise HTTPException(status_code=404, detail="Notification delivery not found")
    return NotificationMarkReadResponse(
        delivery_id=delivery.id,
        status=delivery.status,
        read=delivery.status == "read",
    )


@router.get("/history", response_model=list[NotificationDeliveryHistoryItem])
async def delivery_history(
    status: str | None = Query(default=None),
    provider: str | None = Query(default=None),
    notification_type: str | None = Query(default=None, min_length=1, max_length=40),
    channel_id: int | None = Query(default=None, gt=0),
    stock_symbol: str | None = Query(default=None, min_length=1, max_length=32),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    unread_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> list[NotificationDeliveryHistoryItem]:
    if status is not None and status not in SUPPORTED_DELIVERY_STATUSES:
        allowed = ", ".join(sorted(SUPPORTED_DELIVERY_STATUSES))
        raise _unprocessable(f"Unsupported delivery status. Allowed: {allowed}")
    normalized_provider = _normalized_provider(provider) if provider is not None else None
    if date_from is not None and date_to is not None and date_from > date_to:
        raise _unprocessable("date_from must be on or before date_to")
    from_datetime = (
        datetime.combine(date_from, datetime.min.time(), tzinfo=UTC) if date_from else None
    )
    to_datetime = (
        datetime.combine(date_to + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
        if date_to
        else None
    )
    deliveries = await NotificationDeliveryRepository(session).list_history(
        status=status,
        provider=normalized_provider,
        notification_type=notification_type.strip().lower() if notification_type else None,
        channel_id=channel_id,
        stock_symbol=stock_symbol.strip().upper() if stock_symbol else None,
        date_from=from_datetime,
        date_to=to_datetime,
        unread_only=unread_only,
        offset=offset,
        limit=limit,
    )
    return [_history_item(delivery) for delivery in deliveries]


@router.get("/history/{delivery_id}", response_model=NotificationDeliveryDetail)
async def delivery_detail(
    delivery_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> NotificationDeliveryDetail:
    delivery = await NotificationDeliveryRepository(session).get_detail(delivery_id)
    if delivery is None:
        raise HTTPException(status_code=404, detail="Notification delivery not found")
    item = _history_item(delivery)
    alert = delivery.alert_event
    alert_summary = None
    stock_summary = None
    if alert is not None:
        alert_summary = NotificationAlertSummary(
            id=alert.id,
            rule_id=alert.alert_rule_id,
            rule_name=alert.alert_rule.name if alert.alert_rule else None,
            trade_date=alert.trade_date,
            trigger_value=alert.trigger_value,
            message=alert.message,
        )
        if alert.stock is not None:
            stock_summary = NotificationStockSummary(
                id=alert.stock.id,
                symbol=alert.stock.symbol,
                company_name=alert.stock.company_name,
                exchange=alert.stock.exchange,
            )
    return NotificationDeliveryDetail(
        **item.model_dump(),
        rendered_body=delivery.rendered_body,
        next_retry_at=delivery.next_retry_at,
        sanitized_error=sanitize_notification_error(
            delivery.error_message,
            secrets=_config_secrets(delivery.channel.config_json),
        ),
        alert=alert_summary,
        stock=stock_summary,
    )


@router.post("/test/{provider}", response_model=ProviderTestResponse)
async def test_provider(
    provider: str,
    payload: ProviderTestRequest | None = Body(default=None),
) -> ProviderTestResponse:
    provider_name = _normalized_provider(provider)
    payload = payload or ProviderTestRequest()
    try:
        config = validate_provider_config(provider_name, payload.config_json)
        notification_provider = get_provider(provider_name, config=config)
        recipient = payload.recipient or config.get("recipient") or config.get("chat_id")
        if not recipient and provider_name in {"mock", "in_app"}:
            recipient = "api-test"
        result = await notification_provider.send(
            NotificationMessage(
                recipient=recipient or "",
                subject="AI Stock Scanner notification test",
                text="This is a safe test notification from AI Stock Scanner.",
                html="<p>This is a safe test notification from AI Stock Scanner.</p>",
            )
        )
    except (NotificationConfigError, NotificationProviderError, TypeError, ValueError) as exc:
        detail = sanitize_notification_error(
            str(exc),
            secrets=[*_config_secrets(payload.config_json), payload.recipient or ""],
        )
        raise _unprocessable(detail or "Invalid provider configuration") from exc
    except NotificationError as exc:
        detail = sanitize_notification_error(
            str(exc),
            secrets=[*_config_secrets(payload.config_json), payload.recipient or ""],
        )
        raise HTTPException(
            status_code=502, detail=detail or "Notification provider failed"
        ) from exc

    external_message_id = sanitize_notification_error(
        result.external_message_id,
        secrets=[payload.recipient] if payload.recipient else [],
    )
    return ProviderTestResponse(
        provider=result.provider,
        success=result.success,
        external_message_id=external_message_id,
        tried_at=result.tried_at,
        retry_after=result.retry_after,
    )
