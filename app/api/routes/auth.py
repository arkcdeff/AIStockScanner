from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_authenticated_user, unauthorized_exception
from app.auth.exceptions import (
    AuthenticationError,
    PasswordHashError,
    PasswordPolicyError,
    TokenConfigurationError,
)
from app.db.session import get_db_session
from app.schemas.auth import (
    ChangePasswordRequest,
    ChangePasswordResponse,
    CurrentUserResponse,
    LoginRequest,
    LogoutAllResponse,
    LogoutRequest,
    LogoutResponse,
    RefreshRequest,
    RevokeSessionResponse,
    SessionRead,
    TokenPairResponse,
    UserPreferenceSummary,
    UserSummary,
)
from app.services.auth_service import (
    AuthenticatedUser,
    AuthService,
    InvalidCredentialsError,
    InvalidCurrentPasswordError,
    PasswordReuseError,
    SessionInfo,
    SessionNotFoundError,
    TokenPair,
    mask_ip_address,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])
# Authentication rate limiting is intentionally pending a later security task.

DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]
CurrentAuthentication = Annotated[
    AuthenticatedUser,
    Depends(get_current_authenticated_user),
]


def _client_context(request: Request) -> tuple[str | None, str | None]:
    user_agent = request.headers.get("user-agent")
    ip_address = request.client.host if request.client else None
    return user_agent, ip_address


def _user_summary(user) -> UserSummary:
    return UserSummary(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        is_verified=user.is_verified,
        is_superuser=user.is_superuser,
    )


def _token_pair_response(result: TokenPair) -> TokenPairResponse:
    return TokenPairResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        access_expires_at=result.access_expires_at,
        refresh_expires_at=result.refresh_expires_at,
        user=_user_summary(result.user),
        session_id=result.session_id,
    )


def _current_user_response(current: AuthenticatedUser) -> CurrentUserResponse:
    preference = current.user.preference
    return CurrentUserResponse(
        **_user_summary(current.user).model_dump(),
        roles=list(current.roles),
        permissions=list(current.permissions),
        preferences=(
            UserPreferenceSummary(
                theme=preference.theme,
                timezone=preference.timezone,
                language=preference.language,
            )
            if preference is not None
            else None
        ),
        current_session_id=current.principal.session_id,
    )


def _session_response(info: SessionInfo, current_session_id: int) -> SessionRead:
    record = info.session
    return SessionRead(
        id=record.id,
        created_at=info.created_at,
        expires_at=record.expires_at,
        last_seen_at=record.last_seen_at,
        user_agent=record.user_agent,
        ip_address=mask_ip_address(record.ip_address),
        revoked=record.revoked_at is not None,
        current_session=record.id == current_session_id,
    )


def _service_unavailable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Authentication service unavailable",
    )


@router.post("/login", response_model=TokenPairResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    session: DatabaseSession,
) -> TokenPairResponse:
    user_agent, ip_address = _client_context(request)
    try:
        result = await AuthService(session).login(
            payload.username_or_email,
            payload.password,
            user_agent,
            ip_address,
            remember_me=payload.remember_me,
        )
    except InvalidCredentialsError as exc:
        raise unauthorized_exception() from exc
    except TokenConfigurationError as exc:
        raise _service_unavailable(exc) from exc
    return _token_pair_response(result)


@router.post("/refresh", response_model=TokenPairResponse)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    session: DatabaseSession,
) -> TokenPairResponse:
    user_agent, ip_address = _client_context(request)
    try:
        result = await AuthService(session).refresh(
            payload.refresh_token,
            user_agent,
            ip_address,
        )
    except TokenConfigurationError as exc:
        raise _service_unavailable(exc) from exc
    except (AuthenticationError, InvalidCredentialsError) as exc:
        raise unauthorized_exception() from exc
    return _token_pair_response(result)


@router.post("/logout", response_model=LogoutResponse)
async def logout(payload: LogoutRequest, session: DatabaseSession) -> LogoutResponse:
    try:
        result = await AuthService(session).logout(payload.refresh_token)
    except TokenConfigurationError as exc:
        raise _service_unavailable(exc) from exc
    except AuthenticationError as exc:
        raise unauthorized_exception() from exc
    return LogoutResponse(
        session_id=result.session_id,
        revoked=True,
        message=("Session revoked" if result.newly_revoked else "Session was already revoked"),
    )


@router.post("/logout-all", response_model=LogoutAllResponse)
async def logout_all(
    current: CurrentAuthentication,
    session: DatabaseSession,
) -> LogoutAllResponse:
    revoked = await AuthService(session).logout_all(current.user.id)
    return LogoutAllResponse(
        revoked_sessions=revoked,
        message="All sessions were revoked; sign in again",
    )


@router.get("/me", response_model=CurrentUserResponse)
async def me(current: CurrentAuthentication) -> CurrentUserResponse:
    return _current_user_response(current)


@router.post("/change-password", response_model=ChangePasswordResponse)
async def change_password(
    payload: ChangePasswordRequest,
    current: CurrentAuthentication,
    session: DatabaseSession,
) -> ChangePasswordResponse:
    try:
        revoked = await AuthService(session).change_password(
            current.user.id,
            payload.current_password,
            payload.new_password,
        )
    except InvalidCurrentPasswordError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is invalid",
        ) from exc
    except PasswordReuseError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except PasswordPolicyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=list(exc.violations),
        ) from exc
    except PasswordHashError as exc:
        raise _service_unavailable(exc) from exc
    return ChangePasswordResponse(
        changed=True,
        revoked_sessions=revoked,
        message="Password changed and all sessions revoked; sign in again",
    )


@router.get("/sessions", response_model=list[SessionRead])
async def list_sessions(
    current: CurrentAuthentication,
    session: DatabaseSession,
) -> list[SessionRead]:
    records = await AuthService(session).list_sessions(current.user.id)
    return [_session_response(record, current.principal.session_id) for record in records]


@router.delete("/sessions/{session_id}", response_model=RevokeSessionResponse)
async def revoke_session(
    session_id: int,
    current: CurrentAuthentication,
    session: DatabaseSession,
) -> RevokeSessionResponse:
    try:
        result = await AuthService(session).revoke_session(current.user.id, session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        ) from exc
    return RevokeSessionResponse(
        session_id=result.session_id,
        revoked=True,
        message=("Session revoked" if result.newly_revoked else "Session was already revoked"),
    )
