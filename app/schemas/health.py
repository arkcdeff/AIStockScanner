from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    application: str
    environment: str


class DatabaseHealthResponse(BaseModel):
    status: Literal["ok", "error"]
    database: str
    detail: str | None = None
