from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.exceptions import AuthenticationError, TokenConfigurationError
from app.db.session import get_db_session
from app.services.auth_service import (
    AuthenticatedUser,
    AuthService,
    AuthServiceError,
)

bearer_scheme = HTTPBearer(auto_error=False)


def unauthorized_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def forbidden_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Administrator access required",
    )


async def get_current_authenticated_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AuthenticatedUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized_exception()
    try:
        return await AuthService(session).get_current_user(credentials.credentials)
    except TokenConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
        ) from exc
    except (AuthenticationError, AuthServiceError) as exc:
        raise unauthorized_exception() from exc


def require_admin_user(
    current: Annotated[AuthenticatedUser, Depends(get_current_authenticated_user)],
) -> AuthenticatedUser:
    if not current.user.is_superuser and "super_admin" not in current.roles:
        raise forbidden_exception()
    return current


CurrentAdminUser = Annotated[AuthenticatedUser, Depends(require_admin_user)]
