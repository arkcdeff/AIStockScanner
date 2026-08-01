from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db_session
from app.schemas.health import DatabaseHealthResponse, HealthResponse

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(application=settings.app_name, environment=settings.app_env)


@router.get("/database", response_model=DatabaseHealthResponse)
async def database_health(
    session: AsyncSession = Depends(get_db_session),
) -> DatabaseHealthResponse:
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        return DatabaseHealthResponse(status="error", database="postgresql", detail=str(exc))
    return DatabaseHealthResponse(status="ok", database="postgresql")
