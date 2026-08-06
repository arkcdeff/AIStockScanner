from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import require_browser_permission
from app.core.config import settings
from app.db.session import get_db_session
from app.notifications.exceptions import NotificationError
from app.schemas.notification import (
    SUPPORTED_DELIVERY_STATUSES,
    SUPPORTED_NOTIFICATION_PROVIDERS,
)
from app.services.daily_summary import DailySummaryService
from app.services.notification_service import NotificationConflictError, NotificationService
from app.services.notification_ui import NotificationUIError, NotificationUIService

router = APIRouter(
    dependencies=[Depends(require_browser_permission("notifications.read"))]
)
WRITE_DEPENDENCIES = [Depends(require_browser_permission("notifications.write"))]
SUMMARY_RUN_DEPENDENCIES = [Depends(require_browser_permission("daily_summary.run"))]
env = Environment(
    loader=FileSystemLoader(str(settings.base_dir / "app" / "templates")),
    autoescape=select_autoescape(("html", "htm", "xml")),
)


def _render(template_name: str, context: dict[str, Any]) -> str:
    return env.get_template(template_name).render(**context)


def _checked(form: Mapping[str, Any], name: str) -> bool:
    return str(form.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def _optional(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _history_filters(
    *,
    status: str | None = None,
    provider: str | None = None,
    notification_type: str | None = None,
    channel_id: int | str | None = None,
    stock_symbol: str | None = None,
    date_from: date | str | None = None,
    date_to: date | str | None = None,
    unread_only: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized_status = _optional(status)
    if normalized_status:
        normalized_status = normalized_status.lower()
        if normalized_status not in SUPPORTED_DELIVERY_STATUSES:
            raise NotificationUIError("Unsupported delivery status.", status_code=422)
    normalized_provider = _optional(provider)
    if normalized_provider:
        normalized_provider = normalized_provider.lower()
        if normalized_provider not in SUPPORTED_NOTIFICATION_PROVIDERS:
            raise NotificationUIError("Unsupported notification provider.", status_code=422)

    def parse_date(value: date | str | None) -> date | None:
        if isinstance(value, date):
            return value
        text = _optional(value)
        if not text:
            return None
        try:
            return date.fromisoformat(text)
        except ValueError as exc:
            raise NotificationUIError("Dates must use YYYY-MM-DD format.", status_code=422) from exc

    start = parse_date(date_from)
    end = parse_date(date_to)
    if start and end and start > end:
        raise NotificationUIError("Date from must be on or before date to.", status_code=422)
    selected_channel = int(channel_id) if _optional(channel_id) else None
    filter_values = {
        "status": normalized_status or "",
        "provider": normalized_provider or "",
        "notification_type": _optional(notification_type) or "",
        "channel_id": selected_channel or "",
        "stock_symbol": (_optional(stock_symbol) or "").upper(),
        "date_from": start.isoformat() if start else "",
        "date_to": end.isoformat() if end else "",
        "unread_only": unread_only,
    }
    filters = {
        "status": normalized_status,
        "provider": normalized_provider,
        "notification_type": (_optional(notification_type) or "").lower() or None,
        "channel_id": selected_channel,
        "stock_symbol": (_optional(stock_symbol) or "").upper() or None,
        "date_from": datetime.combine(start, datetime.min.time(), tzinfo=UTC) if start else None,
        "date_to": datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
        if end
        else None,
        "unread_only": unread_only,
    }
    return filters, filter_values


async def _history_context(
    service: NotificationUIService,
    *,
    filters: dict[str, Any],
    filter_values: dict[str, Any],
    page: int,
    page_size: int,
    flash: dict[str, str] | None = None,
) -> dict[str, Any]:
    history = await service.get_history(filters=filters, page=page, page_size=page_size)
    history.update(
        {
            "filter_values": filter_values,
            "flash": flash,
        }
    )
    return history


async def _history_context_from_form(
    request: Request,
    service: NotificationUIService,
    *,
    flash: dict[str, str] | None = None,
) -> tuple[dict[str, Any], str]:
    form = await request.form()
    filters, values = _history_filters(
        status=_optional(form.get("status")),
        provider=_optional(form.get("provider")),
        notification_type=_optional(form.get("notification_type")),
        channel_id=_optional(form.get("channel_id")),
        stock_symbol=_optional(form.get("stock_symbol")),
        date_from=_optional(form.get("date_from")),
        date_to=_optional(form.get("date_to")),
        unread_only=_checked(form, "unread_only"),
    )
    page = max(1, int(form.get("page", 1) or 1))
    page_size = min(100, max(1, int(form.get("page_size", 25) or 25)))
    context = await _history_context(
        service,
        filters=filters,
        filter_values=values,
        page=page,
        page_size=page_size,
        flash=flash,
    )
    return context, str(form.get("view", "history"))


async def _settings_context(
    service: NotificationUIService, *, flash: dict[str, str] | None = None
) -> dict[str, Any]:
    channels = await service.get_channels()
    return {
        "channels": channels,
        "preferences": await service.get_preferences(channels=channels),
        "summary_schedule": await service.get_summary_schedule(),
        "flash": flash,
    }


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def notifications_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = NotificationUIService(session)
    channels = await service.channel_repo.list_filtered(limit=500)
    html = _render(
        "notifications/index.html",
        {
            "request": request,
            "status": await service.get_status(channels=channels),
            "providers": await service.get_provider_cards(channels=channels),
            "recent": await service.get_recent_history(limit=10),
            "summary_schedule": await service.get_summary_schedule(),
        },
    )
    return HTMLResponse(content=html)


@router.get("/settings", response_class=HTMLResponse)
async def notification_settings_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    context = await _settings_context(NotificationUIService(session))
    context["request"] = request
    return HTMLResponse(content=_render("notifications/settings.html", context))


@router.get("/history", response_class=HTMLResponse)
async def notification_history_page(
    request: Request,
    status: str | None = Query(default=None),
    provider: str | None = Query(default=None),
    notification_type: str | None = Query(default=None),
    channel_id: int | None = Query(default=None, gt=0),
    stock_symbol: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    unread_only: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = NotificationUIService(session)
    filters, values = _history_filters(
        status=status,
        provider=provider,
        notification_type=notification_type,
        channel_id=channel_id,
        stock_symbol=stock_symbol,
        date_from=date_from,
        date_to=date_to,
        unread_only=unread_only,
    )
    context = await _history_context(
        service,
        filters=filters,
        filter_values=values,
        page=page,
        page_size=page_size,
    )
    context.update({"request": request, "channels": await service.get_channels()})
    return HTMLResponse(content=_render("notifications/history.html", context))


@router.get("/status", response_class=HTMLResponse)
async def notification_status_partial(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    status = await NotificationUIService(session).get_status()
    return HTMLResponse(
        content=_render(
            "notifications/components/status_cards.html",
            {"request": request, "status": status},
        )
    )


@router.get("/providers", response_class=HTMLResponse)
async def notification_provider_partial(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    providers = await NotificationUIService(session).get_provider_cards()
    return HTMLResponse(
        content=_render(
            "notifications/components/provider_cards.html",
            {"request": request, "providers": providers},
        )
    )


@router.get("/recent", response_class=HTMLResponse)
async def recent_notification_partial(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    recent = await NotificationUIService(session).get_recent_history(limit=10)
    return HTMLResponse(
        content=_render(
            "notifications/components/recent_history.html",
            {"request": request, "recent": recent},
        )
    )


@router.get("/summary-scheduler", response_class=HTMLResponse)
async def notification_summary_scheduler_partial(
    request: Request,
    show_run_action: bool = Query(default=False),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    schedule = await NotificationUIService(session).get_summary_schedule()
    return HTMLResponse(
        content=_render(
            "notifications/components/summary_scheduler.html",
            {
                "request": request,
                "summary_schedule": schedule,
                "show_run_action": show_run_action,
                "flash": None,
            },
        )
    )


@router.post(
        "/daily-summary/run",
        response_class=HTMLResponse,
        dependencies=SUMMARY_RUN_DEPENDENCIES,
    )
async def run_notification_daily_summary(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    form = await request.form()
    requested = _optional(form.get("summary_date"))
    try:
        summary_date = date.fromisoformat(requested) if requested else None
    except ValueError:
        schedule = await NotificationUIService(session).get_summary_schedule()
        return HTMLResponse(
            content=_render(
                "notifications/components/summary_scheduler.html",
                {
                    "request": request,
                    "summary_schedule": schedule,
                    "show_run_action": True,
                    "flash": {"level": "danger", "message": "Invalid summary date."},
                },
            )
        )

    execution = await DailySummaryService(session).run(summary_date)
    if execution.run.status == "failed":
        flash = {"level": "danger", "message": "Daily summary failed safely."}
    elif execution.idempotent:
        flash = {
            "level": "info",
            "message": "Daily summary already exists; no duplicate was sent.",
        }
    else:
        flash = {"level": "success", "message": "Daily summary completed."}
    schedule = await NotificationUIService(session).get_summary_schedule()
    return HTMLResponse(
        content=_render(
            "notifications/components/summary_scheduler.html",
            {
                "request": request,
                "summary_schedule": schedule,
                "show_run_action": True,
                "flash": flash,
            },
        )
    )


@router.post(
        "/providers/{provider}/test",
        response_class=HTMLResponse,
        dependencies=WRITE_DEPENDENCIES,
    )
async def test_notification_provider_card(
    provider: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    ui_service = NotificationUIService(session)
    provider = provider.strip().lower()
    if provider not in SUPPORTED_NOTIFICATION_PROVIDERS:
        flash = {"level": "danger", "message": "Unsupported notification provider."}
    else:
        channels = await ui_service.channel_repo.list_filtered(
            enabled=True, provider=provider, limit=1
        )
        channel = channels[0] if channels else None
        config = channel.config_json if channel else {}
        recipient = str((config or {}).get("recipient") or (config or {}).get("chat_id") or "")
        try:
            await NotificationService(session).test_provider(
                provider=provider,
                config=config,
                recipient=recipient,
            )
            flash = {"level": "success", "message": f"{provider.title()} test succeeded."}
        except NotificationError:
            flash = {
                "level": "danger",
                "message": "Provider test failed. Check the masked channel settings.",
            }
    return HTMLResponse(
        content=_render(
            "notifications/components/provider_cards.html",
            {
                "request": request,
                "providers": await ui_service.get_provider_cards(),
                "flash": flash,
            },
        )
    )


@router.get("/history/table", response_class=HTMLResponse)
async def notification_history_table(
    request: Request,
    status: str | None = Query(default=None),
    provider: str | None = Query(default=None),
    notification_type: str | None = Query(default=None),
    channel_id: int | None = Query(default=None, gt=0),
    stock_symbol: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    unread_only: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = NotificationUIService(session)
    filters, values = _history_filters(
        status=status,
        provider=provider,
        notification_type=notification_type,
        channel_id=channel_id,
        stock_symbol=stock_symbol,
        date_from=date_from,
        date_to=date_to,
        unread_only=unread_only,
    )
    context = await _history_context(
        service,
        filters=filters,
        filter_values=values,
        page=page,
        page_size=page_size,
    )
    context["request"] = request
    return HTMLResponse(content=_render("notifications/components/history_table.html", context))


@router.get("/channels", response_class=HTMLResponse)
async def notification_channels_partial(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    channels = await NotificationUIService(session).get_channels()
    return HTMLResponse(
        content=_render(
            "notifications/components/channel_list.html",
            {"request": request, "channels": channels, "flash": None},
        )
    )


@router.get("/preferences", response_class=HTMLResponse)
async def notification_preferences_partial(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = NotificationUIService(session)
    channels = await service.get_channels()
    return HTMLResponse(
        content=_render(
            "notifications/components/preference_list.html",
            {
                "request": request,
                "channels": channels,
                "preferences": await service.get_preferences(channels=channels),
                "flash": None,
            },
        )
    )


async def _channel_values(
    form: Mapping[str, Any], service: NotificationUIService, *, channel_id: int | None = None
) -> dict[str, Any]:
    provider = str(form.get("provider", "")).strip().lower()
    current = await service.get_channel(channel_id) if channel_id is not None else None
    config = service.channel_config_from_form(
        provider,
        form,
        current_provider=current.provider if current else None,
        current_config=current.config_json if current else None,
    )
    return {
        "name": str(form.get("name", "")).strip(),
        "provider": provider,
        "enabled": _checked(form, "enabled"),
        "config_json": config,
    }


async def _channel_action_response(
    request: Request,
    service: NotificationUIService,
    *,
    message: str,
    level: str = "success",
) -> HTMLResponse:
    return HTMLResponse(
        content=_render(
            "notifications/components/channel_list.html",
            {
                "request": request,
                "channels": await service.get_channels(),
                "flash": {"level": level, "message": message},
            },
        )
    )


@router.post("/channels/create", response_class=HTMLResponse, dependencies=WRITE_DEPENDENCIES)
async def create_notification_channel(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = NotificationUIService(session)
    form = await request.form()
    try:
        await service.create_channel(await _channel_values(form, service))
    except NotificationUIError as exc:
        return await _channel_action_response(request, service, message=str(exc), level="danger")
    return await _channel_action_response(request, service, message="Notification channel created.")


@router.post(
        "/channels/{channel_id}/update",
        response_class=HTMLResponse,
        dependencies=WRITE_DEPENDENCIES,
    )
async def update_notification_channel(
    channel_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = NotificationUIService(session)
    form = await request.form()
    try:
        await service.update_channel(
            channel_id, await _channel_values(form, service, channel_id=channel_id)
        )
    except NotificationUIError as exc:
        return await _channel_action_response(request, service, message=str(exc), level="danger")
    return await _channel_action_response(request, service, message="Notification channel updated.")


@router.post(
        "/channels/{channel_id}/delete",
        response_class=HTMLResponse,
        dependencies=WRITE_DEPENDENCIES,
    )
async def delete_notification_channel(
    channel_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = NotificationUIService(session)
    try:
        await service.delete_channel(channel_id)
    except NotificationUIError as exc:
        return await _channel_action_response(request, service, message=str(exc), level="danger")
    return await _channel_action_response(request, service, message="Notification channel deleted.")


@router.post(
        "/channels/{channel_id}/test",
        response_class=HTMLResponse,
        dependencies=WRITE_DEPENDENCIES,
    )
async def test_notification_channel(
    channel_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    ui_service = NotificationUIService(session)
    channel = await ui_service.get_channel(channel_id)
    if channel is None:
        return await _channel_action_response(
            request, ui_service, message="Notification channel not found.", level="danger"
        )
    config = channel.config_json or {}
    recipient = str(config.get("recipient") or config.get("chat_id") or "")
    try:
        await NotificationService(session).test_provider(
            provider=channel.provider,
            config=config,
            recipient=recipient,
        )
    except NotificationError:
        return await _channel_action_response(
            request,
            ui_service,
            message="Provider test failed. Check the masked channel settings.",
            level="danger",
        )
    return await _channel_action_response(
        request, ui_service, message=f"{channel.name} test succeeded."
    )


async def _preference_values(form: Mapping[str, Any]) -> dict[str, Any]:
    priority = _optional(form.get("minimum_priority"))
    return {
        "channel_id": int(form.get("channel_id", 0) or 0),
        "event_type": str(form.get("event_type", "")).strip(),
        "enabled": _checked(form, "enabled"),
        "minimum_priority": int(priority) if priority is not None else None,
        "quiet_hours_start": _optional(form.get("quiet_hours_start")),
        "quiet_hours_end": _optional(form.get("quiet_hours_end")),
    }


async def _preference_action_response(
    request: Request,
    service: NotificationUIService,
    *,
    message: str,
    level: str = "success",
) -> HTMLResponse:
    channels = await service.get_channels()
    return HTMLResponse(
        content=_render(
            "notifications/components/preference_list.html",
            {
                "request": request,
                "channels": channels,
                "preferences": await service.get_preferences(channels=channels),
                "flash": {"level": level, "message": message},
            },
        )
    )


@router.post("/preferences/create", response_class=HTMLResponse, dependencies=WRITE_DEPENDENCIES)
async def create_notification_preference(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = NotificationUIService(session)
    try:
        await service.create_preference(await _preference_values(await request.form()))
    except (NotificationUIError, TypeError, ValueError) as exc:
        return await _preference_action_response(request, service, message=str(exc), level="danger")
    return await _preference_action_response(
        request, service, message="Notification preference created."
    )


@router.post(
        "/preferences/{preference_id}/update",
        response_class=HTMLResponse,
        dependencies=WRITE_DEPENDENCIES,
    )
async def update_notification_preference(
    preference_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = NotificationUIService(session)
    try:
        await service.update_preference(
            preference_id, await _preference_values(await request.form())
        )
    except (NotificationUIError, TypeError, ValueError) as exc:
        return await _preference_action_response(request, service, message=str(exc), level="danger")
    return await _preference_action_response(
        request, service, message="Notification preference updated."
    )


@router.post(
        "/preferences/{preference_id}/delete",
        response_class=HTMLResponse,
        dependencies=WRITE_DEPENDENCIES,
    )
async def delete_notification_preference(
    preference_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = NotificationUIService(session)
    try:
        await service.delete_preference(preference_id)
    except NotificationUIError as exc:
        return await _preference_action_response(request, service, message=str(exc), level="danger")
    return await _preference_action_response(
        request, service, message="Notification preference deleted."
    )


@router.get("/history/{delivery_id}/detail", response_class=HTMLResponse)
async def notification_delivery_detail(
    delivery_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    detail = await NotificationUIService(session).get_delivery_detail(delivery_id)
    if detail is None:
        return HTMLResponse(
            content=_render(
                "notifications/components/flash_message.html",
                {
                    "request": request,
                    "flash": {"level": "danger", "message": "Delivery not found."},
                },
            ),
            status_code=404,
        )
    return HTMLResponse(
        content=_render(
            "notifications/components/delivery_detail.html",
            {"request": request, "delivery": detail},
        )
    )


@router.post(
        "/history/{delivery_id}/retry",
        response_class=HTMLResponse,
        dependencies=WRITE_DEPENDENCIES,
    )
async def retry_notification_delivery(
    delivery_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = NotificationUIService(session)
    try:
        delivery = await NotificationService(session).retry_delivery(
            delivery_id, reject_completed=True
        )
        if delivery is None:
            flash = {"level": "danger", "message": "Delivery not found."}
        else:
            flash = {
                "level": "success" if delivery.status == "sent" else "warning",
                "message": f"Retry completed with status: {delivery.status}.",
            }
    except NotificationConflictError as exc:
        flash = {"level": "warning", "message": str(exc)}
    context, view = await _history_context_from_form(request, service, flash=flash)
    if view == "recent":
        return HTMLResponse(
            content=_render(
                "notifications/components/recent_history.html",
                {
                    "request": request,
                    "recent": await service.get_recent_history(limit=10),
                    "flash": flash,
                },
            )
        )
    context["request"] = request
    return HTMLResponse(content=_render("notifications/components/history_table.html", context))


@router.post(
        "/history/{delivery_id}/mark-read",
        response_class=HTMLResponse,
        dependencies=WRITE_DEPENDENCIES,
    )
async def mark_notification_read(
    delivery_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = NotificationUIService(session)
    delivery = await NotificationService(session).mark_read(delivery_id)
    if delivery is None:
        flash = {"level": "danger", "message": "Delivery not found."}
    elif delivery.status == "read":
        flash = {"level": "success", "message": "Notification marked as read."}
    else:
        flash = {"level": "warning", "message": "Only sent notifications can be read."}
    context, view = await _history_context_from_form(request, service, flash=flash)
    if view == "recent":
        return HTMLResponse(
            content=_render(
                "notifications/components/recent_history.html",
                {
                    "request": request,
                    "recent": await service.get_recent_history(limit=10),
                    "flash": flash,
                },
            )
        )
    context["request"] = request
    return HTMLResponse(content=_render("notifications/components/history_table.html", context))
