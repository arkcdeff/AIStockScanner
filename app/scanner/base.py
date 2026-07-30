from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(slots=True)
class ScannerMatch:
    symbol: str
    scan_date: date
    score: float | None = None
    metrics: dict[str, Any] = field(default_factory=dict)


class Scanner(ABC):
    code: str
    name: str
    category: str

    @abstractmethod
    async def run(self, *, scan_date: date) -> list[ScannerMatch]:
        """Execute the scan and return matching stocks."""
