from app.scanner.base import Scanner


class ScannerRegistry:
    def __init__(self) -> None:
        self._scanners: dict[str, Scanner] = {}

    def register(self, scanner: Scanner) -> None:
        if scanner.code in self._scanners:
            raise ValueError(f"Scanner '{scanner.code}' is already registered")
        self._scanners[scanner.code] = scanner

    def get(self, code: str) -> Scanner:
        try:
            return self._scanners[code]
        except KeyError as exc:
            raise KeyError(f"Unknown scanner '{code}'") from exc

    def list(self) -> list[Scanner]:
        return list(self._scanners.values())
