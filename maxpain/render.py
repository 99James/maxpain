"""Output formatting.

The guiding rule: a cell either holds a real retrieved number or a visible
placeholder. Nothing here can turn missing data into something that reads like
a quote -- no zeros, no last-known values, no blanks.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Sequence

from .models import Status, TickerResult

# Deliberately not "0.00" or an empty cell: it must be impossible to mistake
# for a price at a glance.
MISSING = "--"

_HEADERS = ("Ticker", "Price", "Max Pain", "Expiry", "Status")


def _money(value: float | None) -> str:
    return MISSING if value is None else f"${value:,.2f}"


def _row(result: TickerResult) -> tuple[str, ...]:
    return (
        result.ticker,
        _money(result.price),
        _money(result.max_pain),
        result.expiry.isoformat() if result.expiry else MISSING,
        result.status.value,
    )


def render_table(results: Sequence[TickerResult]) -> str:
    """A plain-text table, with any explanatory detail listed underneath."""
    if not results:
        return "no tickers requested"

    rows = [_HEADERS, *(_row(r) for r in results)]
    widths = [max(len(row[i]) for row in rows) for i in range(len(_HEADERS))]

    def format_row(row: Sequence[str]) -> str:
        # Ticker left-aligned, numbers right-aligned, status left-aligned.
        cells = [
            row[0].ljust(widths[0]),
            row[1].rjust(widths[1]),
            row[2].rjust(widths[2]),
            row[3].rjust(widths[3]),
            row[4].ljust(widths[4]),
        ]
        return "  ".join(cells).rstrip()

    lines = [format_row(_HEADERS), "  ".join("-" * w for w in widths)]
    lines.extend(format_row(row) for row in rows[1:])

    notes = [f"  {r.ticker}: {r.detail}" for r in results if r.detail]
    if notes:
        lines.append("")
        lines.extend(notes)

    # The session is what dates a price. A snapshot stamp is not enough: CBOE
    # republishes overnight without new trades, so a fresh-looking stamp can
    # sit on top of the previous session's close.
    sessions = sorted({r.session for r in results if r.session})
    sources = sorted({r.source for r in results if r.source})
    stamps = [r.as_of for r in results if r.as_of]
    if sessions or sources:
        lines.append("")
    if sessions:
        span = sessions[0].isoformat()
        if len(sessions) > 1:
            span += f" .. {sessions[-1].isoformat()}"
        lines.append(f"  Prices from the {span} trading session")
    if sources:
        line = f"  Source: {', '.join(sources)}"
        if stamps:
            line += f", as of {max(stamps):%Y-%m-%d %H:%M:%S} UTC"
        if "CBOE" in sources:
            line += " (CBOE quotes are delayed ~15 min)"
        lines.append(line)

    return "\n".join(lines)


def render_json(results: Sequence[TickerResult]) -> str:
    """Machine-readable output. Missing values stay null rather than becoming 0."""

    def encode(result: TickerResult) -> dict:
        return {
            "ticker": result.ticker,
            "status": result.status.value,
            "price": result.price,
            "max_pain": result.max_pain,
            "expiry": result.expiry.isoformat() if result.expiry else None,
            "as_of": result.as_of.isoformat(sep=" ") if result.as_of else None,
            "session": result.session.isoformat() if result.session else None,
            "source": result.source,
            "verified_against": result.verified_against,
            "detail": result.detail or None,
        }

    return json.dumps([encode(r) for r in results], indent=2)


def exit_code(results: Sequence[TickerResult]) -> int:
    """0 when every row is trustworthy, non-zero otherwise.

    UNVERIFIED counts as success: verification is best-effort by design, and
    OptionCharts being unreachable says nothing about our own arithmetic.
    Anything else -- a mismatch, stale data, a failed fetch -- is a real
    problem the caller should notice.
    """
    acceptable = {Status.OK, Status.UNVERIFIED}
    return 0 if all(r.status in acceptable for r in results) else 1
