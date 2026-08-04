import time

from app.core.config import settings

_cache: "AIMemoryCache | None" = None


class AIMemoryCache:
    """Small thread-safe-ish TTL cache keyed by provider + prompt hash."""

    def __init__(self, ttl: int) -> None:
        self.ttl = ttl
        self._store: dict[str, tuple[float, object]] = {}

    def get(self, key: str) -> object | None:
        item = self._store.get(key)
        if item is None:
            return None
        ts, value = item
        if time.monotonic() - ts > self.ttl:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: object) -> None:
        self._store[key] = (time.monotonic(), value)

    def clear(self) -> None:
        self._store.clear()


def get_memory_cache() -> AIMemoryCache:
    global _cache
    if _cache is None or _cache.ttl != settings.ai_cache_ttl:
        _cache = AIMemoryCache(settings.ai_cache_ttl)
    return _cache


def clear_memory_cache() -> None:
    global _cache
    _cache = None
