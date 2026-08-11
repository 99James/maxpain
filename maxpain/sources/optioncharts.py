"""OptionCharts source, used only to check our own arithmetic.

The rendered page computes max pain in JavaScript, but the async fragment it
loads embeds the numbers as plain JSON:

    https://optioncharts.io/async/options_charts/max_pain?ticker=NVDA&...

Inside is `let chart_data = [ ... ]`, one entry per expiration, carrying
`date_yyyymmdd` and `y` (the max pain value). A single request covers every
expiration, so we never have to reconstruct their `:w` weekly-suffix
parameter -- we just look up the date we care about.

This whole module is best-effort. It reads someone else's private, unversioned
markup, which can change without notice. Every failure path returns "no
opinion" rather than raising, so a layout change downgrades results to
UNVERIFIED instead of breaking the tool or corrupting a value.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import urllib.parse

from .. import http

URL_TEMPLATE = (
    "https://optioncharts.io/async/options_charts/max_pain"
    "?ticker={ticker}&expiration_dates={expiry}&option_type=all"
)

_MARKER = "let chart_data = "

# Class-share tickers are punctuated differently by the two sources: CBOE
# serves "BRK.B" while OptionCharts expects "BRKB" (the dotted and dashed
# forms both return a near-empty 336-byte body). Without this, every dotted
# symbol would silently fall back to UNVERIFIED.
_SEPARATORS = str.maketrans("", "", ".-/")


def normalize_ticker(ticker: str) -> str:
    """Convert a CBOE-style symbol to the form OptionCharts expects."""
    return ticker.strip().upper().translate(_SEPARATORS)


def url_for(ticker: str, expiry: dt.date) -> str:
    return URL_TEMPLATE.format(
        ticker=urllib.parse.quote(normalize_ticker(ticker)),
        expiry=urllib.parse.quote(expiry.isoformat()),
    )


def parse_max_pain_by_expiry(document: str) -> dict[dt.date, float]:
    """Extract {expiry: max_pain} from an OptionCharts fragment.

    Returns {} if the marker is absent or the JSON will not decode -- callers
    treat an empty mapping as "no second opinion available".

    The array is located by marker offset and decoded with raw_decode. A
    regex is not usable here: the fragment contains further JSON after the
    array, so a greedy match overshoots and a lazy one truncates.
    """
    text = html.unescape(document)
    start = text.find(_MARKER)
    if start < 0:
        return {}

    try:
        decoded, _ = json.JSONDecoder().raw_decode(text[start + len(_MARKER) :])
    except json.JSONDecodeError:
        return {}

    if not isinstance(decoded, list):
        return {}

    result: dict[dt.date, float] = {}
    for entry in decoded:
        if not isinstance(entry, dict):
            continue
        raw_date, value = entry.get("date_yyyymmdd"), entry.get("y")
        if not isinstance(raw_date, str) or not isinstance(value, (int, float)):
            continue
        try:
            expiry = dt.date.fromisoformat(raw_date)
        except ValueError:
            continue
        result[expiry] = float(value)
    return result


def fetch_max_pain(
    ticker: str, expiry: dt.date, *, timeout: float = http.DEFAULT_TIMEOUT
) -> float | None:
    """Published max pain for one expiry, or None if unavailable.

    Never raises: verification failing is not a reason for the whole run to
    fail, and a missing second opinion is reported honestly as UNVERIFIED.
    """
    try:
        raw = http.get(url_for(ticker, expiry), timeout=timeout, retries=1)
    except http.HttpError:
        return None

    try:
        document = raw.decode("utf-8", errors="replace")
    except (UnicodeError, AttributeError):
        return None

    return parse_max_pain_by_expiry(document).get(expiry)
