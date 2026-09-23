"""US equity market clock: Eastern time, NYSE holidays, and trading sessions.

Pure functions only -- the current instant is always passed in, as a naive UTC
datetime, the same convention `retrieve` uses for its clock.

This exists because a quote is only meaningful relative to the *market's*
calendar, not the machine's. A laptop in Tokyo is already on tomorrow's date
while New York is still trading today, and a feed that has not been refreshed
since yesterday's close looks perfectly current if you only check the wall
clock.

Eastern time is computed from the US DST rule rather than `zoneinfo`, because
Windows Python ships without the tz database and this project cannot install
`tzdata`.
"""

from __future__ import annotations

import datetime as dt

# Regular session hours, Eastern.
MARKET_OPEN = dt.time(9, 30)
MARKET_CLOSE = dt.time(16, 0)

# CBOE's feed is ~15 minutes delayed, so a session's first print cannot be
# expected before 09:45. Another 15 minutes of slack keeps the opening minutes
# from being flagged just because the CDN has not republished yet.
SESSION_EXPECTED_BY = dt.time(10, 0)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """The n-th `weekday` (Mon=0) of a month; n=-1 means the last one."""
    if n > 0:
        first = dt.date(year, month, 1)
        offset = (weekday - first.weekday()) % 7
        return first + dt.timedelta(days=offset + 7 * (n - 1))
    following = dt.date(year + (month == 12), month % 12 + 1, 1)
    last = following - dt.timedelta(days=1)
    return last - dt.timedelta(days=(last.weekday() - weekday) % 7)


def _easter(year: int) -> dt.date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


def _observed(day: dt.date) -> dt.date:
    """Saturday holidays close the Friday before; Sunday ones the Monday after."""
    if day.weekday() == 5:
        return day - dt.timedelta(days=1)
    if day.weekday() == 6:
        return day + dt.timedelta(days=1)
    return day


def nyse_holidays(year: int) -> frozenset[dt.date]:
    """Full-day NYSE closures under the exchange's standing rules.

    Unscheduled closures (national days of mourning, weather) cannot be
    predicted; on those days fresh data will simply be reported as STALE,
    which errs on the side of caution.
    """
    days = {
        _nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),  # Washington's Birthday
        _easter(year) - dt.timedelta(days=2),  # Good Friday
        _nth_weekday(year, 5, 0, -1),  # Memorial Day
        _observed(dt.date(year, 7, 4)),  # Independence Day
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed(dt.date(year, 12, 25)),  # Christmas
    }
    # NYSE does not move New Year's Day back into the previous year, so a
    # Saturday Jan 1 is simply not observed.
    new_year = dt.date(year, 1, 1)
    if new_year.weekday() != 5:
        days.add(_observed(new_year))
    if year >= 2022:
        days.add(_observed(dt.date(year, 6, 19)))  # Juneteenth
    return frozenset(days)


def is_trading_day(day: dt.date) -> bool:
    return day.weekday() < 5 and day not in nyse_holidays(day.year)


def previous_trading_day(day: dt.date) -> dt.date:
    """The last trading day strictly before `day`."""
    day -= dt.timedelta(days=1)
    while not is_trading_day(day):
        day -= dt.timedelta(days=1)
    return day


def _is_eastern_dst(utc: dt.datetime) -> bool:
    """US DST: 2nd Sunday of March 02:00 EST to 1st Sunday of November 02:00 EDT."""
    year = utc.year
    start = dt.datetime.combine(_nth_weekday(year, 3, 6, 2), dt.time(7))  # 02:00 EST
    end = dt.datetime.combine(_nth_weekday(year, 11, 6, 1), dt.time(6))  # 02:00 EDT
    return start <= utc < end


def to_eastern(utc: dt.datetime) -> dt.datetime:
    """Naive UTC -> naive US Eastern wall-clock time."""
    return utc + dt.timedelta(hours=-4 if _is_eastern_dst(utc) else -5)


def latest_session(utc_now: dt.datetime) -> dt.date:
    """The most recent trading session a delayed feed should already reflect.

    Today's session once it is underway (allowing for the feed delay),
    otherwise the previous trading day.
    """
    eastern = to_eastern(utc_now)
    today = eastern.date()
    if is_trading_day(today) and eastern.time() >= SESSION_EXPECTED_BY:
        return today
    return previous_trading_day(today)


def expiry_reference_date(utc_now: dt.datetime) -> dt.date:
    """The first date whose options are still alive, by New York's clock.

    Options expiring today stop trading at the 16:00 close, so after that the
    nearest live expiry starts tomorrow.
    """
    eastern = to_eastern(utc_now)
    if eastern.time() >= MARKET_CLOSE:
        return eastern.date() + dt.timedelta(days=1)
    return eastern.date()
