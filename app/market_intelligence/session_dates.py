from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.market_intelligence.index_catalog import INDEX_CATALOG_BY_CODE

INDIA_TIMEZONE = "Asia/Kolkata"
INDIAN_CASH_OPEN = time(9, 15)
INDIAN_CASH_CLOSE = time(15, 30)

GIFT_NIFTY_SESSION_ONE_OPEN = time(6, 30)
GIFT_NIFTY_SESSION_ONE_CLOSE = time(15, 40)
GIFT_NIFTY_SESSION_TWO_OPEN = time(16, 35)
GIFT_NIFTY_SESSION_TWO_CLOSE = time(2, 45)


@dataclass(frozen=True, slots=True)
class MarketSessionPolicy:
    timezone: str
    close_time: time


SESSION_POLICIES: dict[str, MarketSessionPolicy] = {
    "SP500": MarketSessionPolicy("America/New_York", time(16, 0)),
    "NASDAQ_100": MarketSessionPolicy("America/New_York", time(16, 0)),
    "DOW_JONES": MarketSessionPolicy("America/New_York", time(16, 0)),
    "NIKKEI_225": MarketSessionPolicy("Asia/Tokyo", time(15, 0)),
    "HANG_SENG": MarketSessionPolicy("Asia/Hong_Kong", time(16, 0)),
    "SHANGHAI_COMPOSITE": MarketSessionPolicy("Asia/Shanghai", time(15, 0)),
    "KOSPI": MarketSessionPolicy("Asia/Seoul", time(15, 30)),
}


def session_cutoff_date(index_code: str, evaluation_at: datetime) -> date:
    """Return the latest eligible native session date at an aware timestamp.

    Weekends and exchange holidays are intentionally not guessed here. The
    bounded history lookup naturally selects the latest stored session on or
    before this cutoff.
    """
    if evaluation_at.tzinfo is None or evaluation_at.utcoffset() is None:
        raise ValueError("evaluation_at must be timezone-aware")
    policy = SESSION_POLICIES[index_code]
    local = evaluation_at.astimezone(ZoneInfo(policy.timezone))
    if local.timetz().replace(tzinfo=None) < policy.close_time:
        return local.date() - timedelta(days=1)
    return local.date()


def indian_cash_reference_cutoff_date(evaluation_at: datetime) -> date:
    """Latest Indian cash-market date whose close has completed by ``evaluation_at``.

    Exchange holidays are not guessed. A repository lookup for the latest stored
    NIFTY session on or before this cutoff resolves weekends and holidays.
    """
    local = _india_local(evaluation_at)
    if local.timetz().replace(tzinfo=None) < INDIAN_CASH_CLOSE:
        return local.date() - timedelta(days=1)
    return local.date()


def date_only_publication_cutoff_date(timezone: str, evaluation_at: datetime) -> date:
    """Return the last safely completed date for a date-only published series.

    USD/INR spot and Europe Brent spot imports retain publication provenance as
    dates in ``MarketIndexPrice``. Without a timestamp, a same-local-date row
    cannot be proven knowable at an intraday replay instant, so the conservative
    cutoff is always the preceding local calendar date.
    """
    if evaluation_at.tzinfo is None or evaluation_at.utcoffset() is None:
        raise ValueError("evaluation_at must be timezone-aware")
    return evaluation_at.astimezone(ZoneInfo(timezone)).date() - timedelta(days=1)


def institutional_flow_cutoff_date(evaluation_at: datetime) -> date:
    """Conservative cutoff for date-only provisional FII/FPI and DII flows."""
    return date_only_publication_cutoff_date(INDIA_TIMEZONE, evaluation_at)


def indian_cash_context(
    evaluation_at: datetime,
    *,
    holiday_dates: frozenset[date] = frozenset(),
) -> str:
    """Classify the Indian cash-market context without inventing holidays."""
    local = _india_local(evaluation_at)
    if local.weekday() >= 5:
        return "weekend"
    if local.date() in holiday_dates:
        return "holiday"
    local_time = local.timetz().replace(tzinfo=None)
    if local_time < INDIAN_CASH_OPEN:
        return "pre_open"
    if local_time < INDIAN_CASH_CLOSE:
        return "cash_market_open"
    return "post_close"


def gift_nifty_session_context(evaluation_at: datetime) -> str:
    """Return the current NSE IX GIFT Nifty session context.

    Current official NSE IX hours audited for Task 18.4 are 06:30–15:40
    and 16:35–02:45 next day, in IST. Historical timing changes are not
    inferred by this function.
    """
    local = _india_local(evaluation_at)
    local_time = local.timetz().replace(tzinfo=None)

    # The after-midnight portion belongs to the preceding trading evening.
    if local_time <= GIFT_NIFTY_SESSION_TWO_CLOSE:
        return "session_two" if local.weekday() in {1, 2, 3, 4, 5} else "closed_weekend"
    if local.weekday() >= 5:
        return "closed_weekend"
    if GIFT_NIFTY_SESSION_ONE_OPEN <= local_time <= GIFT_NIFTY_SESSION_ONE_CLOSE:
        return "session_one"
    if GIFT_NIFTY_SESSION_ONE_CLOSE < local_time < GIFT_NIFTY_SESSION_TWO_OPEN:
        return "inter_session_break"
    if local_time >= GIFT_NIFTY_SESSION_TWO_OPEN:
        return "session_two"
    return "closed"


def gift_nifty_completed_daily_cutoff_date(evaluation_at: datetime) -> date:
    """Conservative cutoff for date-only completed GIFT Nifty session rows.

    ``MarketIndexPrice`` cannot represent intraday timestamps and stores only
    one row per trade date. Consequently, a row for the local calendar date is
    never treated as an intraday observation; only dates strictly before the
    evaluation's local date are eligible for DB fallback.
    """
    return _india_local(evaluation_at).date() - timedelta(days=1)


def _india_local(evaluation_at: datetime) -> datetime:
    if evaluation_at.tzinfo is None or evaluation_at.utcoffset() is None:
        raise ValueError("evaluation_at must be timezone-aware")
    return evaluation_at.astimezone(ZoneInfo(INDIA_TIMEZONE))


def validate_session_catalog() -> None:
    """Fail fast if catalog timezone metadata diverges from score policy."""
    for code, policy in SESSION_POLICIES.items():
        if INDEX_CATALOG_BY_CODE[code].timezone != policy.timezone:
            raise ValueError(f"Session timezone mismatch for {code}")


validate_session_catalog()