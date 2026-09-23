"""CBOE delayed-quotes source: fallback provider of price and option chain.

Used only when Nasdaq (sources/nasdaq.py) fails or is behind: these CDN files
are republished overnight and have been seen a full session out of date.

    https://cdn.cboe.com/api/global/delayed_quotes/options/{TICKER}.json

One request returns both numbers this tool reports, taken from a single
snapshot, so the price can never be from a different moment than the open
interest behind the max pain figure.

The feed is explicitly *delayed* (roughly 15 minutes). Two times are carried
through rather than hidden, because they can differ by a whole day:

- `timestamp` (UTC) -- when CBOE last republished the file.
- `last_trade_time` (US Eastern) -- when the underlying last traded.

CBOE republishes overnight without new trades, so a file stamped today can
still hold yesterday's close. Only `last_trade_time` says which session the
price belongs to.
"""

from __future__ import annotations

import datetime as dt
import json

from .. import http
from ..models import Chain, Contract
from ..occ import SymbolError, parse_symbol

SOURCE_NAME = "CBOE"

URL_TEMPLATE = "https://cdn.cboe.com/api/global/delayed_quotes/options/{ticker}.json"


class SourceError(Exception):
    """The payload did not look like what we expect."""


def url_for(ticker: str) -> str:
    return URL_TEMPLATE.format(ticker=ticker.strip().upper())


def _parse_timestamp(raw: str) -> dt.datetime:
    # CBOE sends "2026-08-11 03:44:39" with no zone marker.
    try:
        return dt.datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError) as exc:
        raise SourceError(f"unparseable timestamp {raw!r}") from exc


def _parse_session(raw: object) -> dt.date | None:
    # "2026-08-10T15:59:59", Eastern wall-clock time. Absent or malformed
    # means "unknown", which the caller treats as unable to prove freshness.
    if not isinstance(raw, str):
        return None
    try:
        return dt.datetime.fromisoformat(raw).date()
    except ValueError:
        return None


def parse_chain(payload: dict, ticker: str) -> Chain:
    """Turn a decoded CBOE payload into a Chain.

    Pure: no network. Raises SourceError rather than defaulting if a required
    field is missing, because a fabricated price is worse than no price.
    """
    if not isinstance(payload, dict):
        raise SourceError("payload is not a JSON object")

    data = payload.get("data")
    if not isinstance(data, dict):
        raise SourceError("payload has no 'data' object")

    price = data.get("current_price")
    if not isinstance(price, (int, float)) or price <= 0:
        raise SourceError(f"missing or invalid current_price: {price!r}")

    raw_options = data.get("options")
    if not isinstance(raw_options, list):
        raise SourceError("payload has no 'options' list")

    contracts: list[Contract] = []
    skipped = 0
    for raw in raw_options:
        symbol = raw.get("option") if isinstance(raw, dict) else None
        if not symbol:
            skipped += 1
            continue
        try:
            _, expiry, option_type, strike = parse_symbol(symbol)
        except SymbolError:
            # Unrecognised symbol shapes are dropped, never guessed at.
            skipped += 1
            continue

        open_interest = raw.get("open_interest", 0.0)
        if not isinstance(open_interest, (int, float)) or open_interest < 0:
            skipped += 1
            continue

        contracts.append(
            Contract(
                expiry=expiry,
                option_type=option_type,
                strike=strike,
                open_interest=float(open_interest),
            )
        )

    if raw_options and not contracts:
        raise SourceError(f"no parseable contracts out of {len(raw_options)}")

    return Chain(
        ticker=ticker.strip().upper(),
        price=float(price),
        as_of=_parse_timestamp(payload.get("timestamp")),
        session=_parse_session(data.get("last_trade_time")),
        source=SOURCE_NAME,
        contracts=tuple(contracts),
    )


def fetch_chain(ticker: str, *, timeout: float = http.DEFAULT_TIMEOUT) -> Chain:
    """Retrieve and parse the chain for `ticker`.

    Propagates http.NotFoundError for unknown symbols so the caller can
    distinguish "bad ticker" from "network trouble".
    """
    raw = http.get(url_for(ticker), timeout=timeout)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SourceError(f"{ticker}: response was not valid JSON") from exc
    return parse_chain(payload, ticker)
