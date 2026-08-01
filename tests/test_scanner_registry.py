from datetime import date

import pytest

from app.scanner.base import Scanner, ScannerMatch
from app.scanner.registry import ScannerRegistry


class ExampleScanner(Scanner):
    code = "example"
    name = "Example"
    category = "test"

    async def run(self, *, scan_date: date) -> list[ScannerMatch]:
        return []


def test_registry_registers_and_returns_scanner() -> None:
    registry = ScannerRegistry()
    scanner = ExampleScanner()
    registry.register(scanner)
    assert registry.get("example") is scanner


def test_registry_rejects_duplicate_code() -> None:
    registry = ScannerRegistry()
    registry.register(ExampleScanner())
    with pytest.raises(ValueError):
        registry.register(ExampleScanner())
