"""NSE Exchange Trading Calendar Service — Sprint 19.1J-R1.

Provides deterministic exchange session logic for Indian Cash & Derivatives markets (NSE):
  - Weekend exclusion (Saturday, Sunday)
  - Known NSE official trading holidays (2023–2026)
  - Next trading session resolution: next_exchange_session(date)
  - Nth trading session resolution: nth_exchange_session_after(date, n)
  - Session counting: count_expected_exchange_sessions(start, end)
  - Session list: expected_exchange_sessions_between(start, end)

Strict Rules:
  - Friday signal date -> Monday is next exchange trading day.
  - Friday signal date -> Monday is holiday -> Tuesday is next exchange trading day.
  - If expected next exchange session has no observation in DB, session continuity fails (INSUFFICIENT_SESSION_COVERAGE).
"""
from collections.abc import Iterable
from datetime import date, timedelta

# Official & standard NSE Cash Market Trading Holidays (2023 - 2026)
from app.data.exchange_constants import OFFICIAL_EXCEPTION_SESSIONS

DEFAULT_NSE_HOLIDAYS: set[date] = {
    # 2023
    date(2023, 1, 26),   # Republic Day
    date(2023, 3, 7),    # Holi
    date(2023, 3, 30),   # Ram Navami
    date(2023, 4, 4),    # Mahavir Jayanti
    date(2023, 4, 7),    # Good Friday
    date(2023, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
    date(2023, 4, 21),   # Eid-Ul-Fitr
    date(2023, 5, 1),    # Maharashtra Day
    date(2023, 6, 29),   # Bakri Id
    date(2023, 8, 15),   # Independence Day
    date(2023, 9, 19),   # Ganesh Chaturthi
    date(2023, 10, 2),   # Mahatma Gandhi Jayanti
    date(2023, 10, 24),  # Dussehra
    date(2023, 11, 14),  # Diwali Balipratipada
    date(2023, 11, 27),  # Guru Nanak Jayanti
    date(2023, 12, 25),  # Christmas

    # 2024
    date(2024, 1, 22),   # Ram Mandir Pran Pratishtha
    date(2024, 1, 26),   # Republic Day
    date(2024, 3, 8),    # Mahashivratri
    date(2024, 3, 25),   # Holi
    date(2024, 3, 29),   # Good Friday
    date(2024, 4, 11),   # Id-Ul-Fitr
    date(2024, 4, 17),   # Ram Navami
    date(2024, 5, 1),    # Maharashtra Day
    date(2024, 5, 20),   # General Elections
    date(2024, 6, 17),   # Bakri Id
    date(2024, 7, 17),   # Moharram
    date(2024, 8, 15),   # Independence Day
    date(2024, 10, 2),   # Mahatma Gandhi Jayanti
    date(2024, 11, 1),   # Diwali Laxmi Pujan
    date(2024, 11, 15),  # Guru Nanak Jayanti
    date(2024, 11, 20),  # Maharashtra Assembly Elections
    date(2024, 12, 25),  # Christmas

    # 2025
    date(2025, 1, 26),   # Republic Day (Sun - structural holiday)
    date(2025, 2, 26),   # Mahashivratri
    date(2025, 3, 14),   # Holi
    date(2025, 3, 31),   # Id-Ul-Fitr
    date(2025, 4, 10),   # Mahavir Jayanti
    date(2025, 4, 14),   # Ambedkar Jayanti
    date(2025, 4, 18),   # Good Friday
    date(2025, 5, 1),    # Maharashtra Day
    date(2025, 6, 7),    # Bakri Id
    date(2025, 8, 15),   # Independence Day
    date(2025, 8, 27),   # Ganesh Chaturthi
    date(2025, 10, 2),   # Mahatma Gandhi Jayanti / Dussehra
    date(2025, 10, 21),  # Diwali Balipratipada
    date(2025, 11, 5),   # Guru Nanak Jayanti
    date(2025, 12, 25),  # Christmas

    # 2026
    date(2026, 1, 26),   # Republic Day
    date(2026, 3, 3),    # Holi
    date(2026, 3, 20),   # Id-Ul-Fitr
    date(2026, 4, 3),    # Good Friday
    date(2026, 4, 14),   # Ambedkar Jayanti
    date(2026, 5, 1),    # Maharashtra Day
    date(2026, 6, 26),   # Muharram
    date(2026, 8, 15),   # Independence Day
    date(2026, 10, 2),   # Gandhi Jayanti
    date(2026, 11, 10),  # Diwali
    date(2026, 12, 25),  # Christmas
}


class ExchangeCalendarService:
    """Service to resolve official exchange trading sessions for NSE."""

    def __init__(self, custom_holidays: Iterable[date] | None = None) -> None:
        self.holidays: set[date] = set(custom_holidays) if custom_holidays is not None else set(DEFAULT_NSE_HOLIDAYS)

    def is_exchange_trading_day(self, d: date, allow_exceptions: bool = True) -> bool:
        """Check if date d is an official exchange trading session (non-weekend, non-holiday, or official exception)."""
        if allow_exceptions and d in OFFICIAL_EXCEPTION_SESSIONS:
            return True
        if d.weekday() >= 5:  # 5=Saturday, 6=Sunday
            return False
        if d in self.holidays:
            return False
        return True

    def next_exchange_session(self, current_date: date) -> date:
        """Return the immediately following valid exchange trading session after current_date."""
        cur = current_date + timedelta(days=1)
        while not self.is_exchange_trading_day(cur):
            cur += timedelta(days=1)
        return cur

    def nth_exchange_session_after(self, current_date: date, n: int) -> date:
        """Return the nth valid exchange trading session strictly after current_date."""
        if n <= 0:
            return current_date
        cur = current_date
        count = 0
        while count < n:
            cur += timedelta(days=1)
            if self.is_exchange_trading_day(cur):
                count += 1
        return cur

    def expected_exchange_sessions_between(self, start_date: date, end_date: date) -> list[date]:
        """Return all expected valid exchange trading session dates in [start_date, end_date] inclusive."""
        if start_date > end_date:
            return []
        sessions: list[date] = []
        cur = start_date
        while cur <= end_date:
            if self.is_exchange_trading_day(cur):
                sessions.append(cur)
            cur += timedelta(days=1)
        return sessions

    def count_expected_exchange_sessions(self, start_date: date, end_date: date) -> int:
        """Count expected exchange trading sessions between start_date and end_date inclusive."""
        return len(self.expected_exchange_sessions_between(start_date, end_date))

    def previous_exchange_session(self, current_date: date) -> date:
        """Return the immediately preceding valid exchange trading session strictly before current_date."""
        cur = current_date - timedelta(days=1)
        while not self.is_exchange_trading_day(cur):
            cur -= timedelta(days=1)
        return cur

    def nth_exchange_session_before(self, current_date: date, n: int) -> date:
        """Return the nth valid exchange trading session strictly before current_date."""
        if n <= 0:
            return current_date
        cur = current_date
        count = 0
        while count < n:
            cur -= timedelta(days=1)
            if self.is_exchange_trading_day(cur):
                count += 1
        return cur


_default_calendar = ExchangeCalendarService()


def previous_exchange_session(current_date: date) -> date:
    """Convenience module function for previous exchange session."""
    return _default_calendar.previous_exchange_session(current_date)


def nth_exchange_session_before(current_date: date, n: int) -> date:
    """Convenience module function for nth exchange session before current_date."""
    return _default_calendar.nth_exchange_session_before(current_date, n)

