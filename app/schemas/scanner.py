from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class ScanDefinitionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: str | None
    category: str
    parameters: dict
    is_active: bool


class ScanResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    scan_definition_id: int
    stock_id: int
    symbol: str
    scan_date: date
    score: Decimal | None
    metrics: dict


class ScanRunResponse(BaseModel):
    scanner: str
    matched_stocks: int
    score: float
    execution_time_ms: float
    results: list[ScanResultRead]


class ScanAllResponse(BaseModel):
    total_scanners: int
    total_matched: int
    execution_time_ms: float
    results: dict[str, list[ScanResultRead]]
