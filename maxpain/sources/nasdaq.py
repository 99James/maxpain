"""Nasdaq option-chain source: the primary provider of price and open interest.

    https://api.nasdaq.com/api/quote/{TICKER}/option-chain?...&fromdate=..&todate=..

One request returns both numbers this tool reports, from a single snapshot:
the underlying's last trade (`data.lastTrade`) and open interest for every
contract in the date window.

This is preferred over CBOE because it is current. CBOE's CDN files have been
seen a full session behind -- on 2026-09-23 after the close they still held
the 2026-09-22 price *and* the open interest OCC had published a day earlier,
so every max pain disagreed with OptionCharts. Nasdaq had the new session's
close and the new open interest, and matched OptionCharts on all seven
tickers checked.

`lastTrade` reads "LAST TRADE: $225.51 (AS OF SEP 23, 2026)": it dates the
price to a session but gives no time of day, so none is invented.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import urllib.parse

from .. import http
from ..models import Chain, Contract, OptionType
from ..occ import SymbolError, parse_symbol
from .cboe import SourceError

SOURCE_NAME = "Nasdaq"

URL_TEMPLATE = (
    "https://api.nasdaq.com/api/quote/{ticker}/option-chain"
    "?assetclass=stocks&limit=10000&excode=oprac&callput=callput&money=all&type=all"
    "&fromdate={start}&todate={end}"
)

# Wide enough that a monthly-only chain (next expiry up to ~5 weeks out) still
# has its nearest expiry in range, narrow enough to keep the payload small.
WINDOW_DAYS = 60

_LAST_TRADE = re.compile(
    r"\$\s*(?P<price>[\d,]+(?:\.\d+)?)\s*\(AS OF (?P<date>[A-Z]{3} \d{1,2}, \d{4})\)",
    re.IGNORECASE,
)

# Contract identity comes from the OCC tail of each row's link
# ("/.../nvda--260925c00050000"), so strikes stay integer thousandths exactly
# as with CBOE, rather than being re-parsed from the display "50.00".
_OCC_TAIL = re.compile(r"(\d{6}[CP]\d{8})$", re.IGNORECASE)

# Nasdaq shows "--" (or nothing) where a contract has no open interest.
_NO_VALUE = {"", "--", None}


def url_for(ticker: str, start: dt.date) -> str:
    return URL_TEMPLATE.format(
        ticker=urllib.parse.quote(ticker.strip().upper()),
        start=start.isoformat(),
        end=(start + dt.timedelta(days=WINDOW_DAYS)).isoformat(),
    )


def _parse_last_trade(raw: object) -> tuple[float, dt.date]:
    match = _LAST_TRADE.search(raw) if isinstance(raw, str) else None
    if match is None:
        raise SourceError(f"unparseable lastTrade {raw!r}")
    price = float(match["price"].replace(",", ""))
    if price <= 0:
        raise SourceError(f"invalid last trade price {price!r}")
    try:
        session = dt.datetime.strptime(match["date"].title(), "%b %d, %Y").date()
    except ValueError as exc:
        raise SourceError(f"unparseable lastTrade date {match['date']!r}") from exc
    return price, session


def _parse_open_interest(raw: object) -> float | None:
    """Open interest, 0.0 for Nasdaq's explicit blank, None if unreadable."""
    if raw in _NO_VALUE:
        return 0.0
    if not isinstance(raw, str):
        return None
    try:
        value = float(raw.replace(",", ""))
    except ValueError:
        return None
    return value if value >= 0 else None


def parse_chain(payload: dict, ticker: str, *, retrieved_at: dt.datetime) -> Chain:
    """Turn a decoded Nasdaq payload into a Chain.

    Pure: no network. Raises SourceError rather than defaulting if a required
    field is missing, and http.NotFoundError for Nasdaq's "Symbol not exists".
    """
    if not isinstance(payload, dict):
        raise SourceError("payload is not a JSON object")

    status = payload.get("status") or {}
    if status.get("rCode") != 200:
        messages = [m.get("errorMessage", "") for m in status.get("bCodeMessage") or []]
        if any("not exist" in m.lower() for m in messages):
            raise http.NotFoundError(f"Nasdaq: {'; '.join(messages)}")
        raise SourceError(f"Nasdaq error {status.get('rCode')!r}: {'; '.join(messages)}")

    data = payload.get("data")
    if not isinstance(data, dict):
        raise SourceError("payload has no 'data' object")

    price, session = _parse_last_trade(data.get("lastTrade"))

    table = data.get("table")
    rows = table.get("rows") if isinstance(table, dict) else None
    if not isinstance(rows, list):
        raise SourceError("payload has no option rows")

    contracts: list[Contract] = []
    contract_rows = 0
    for row in rows:
        link = row.get("drillDownURL") if isinstance(row, dict) else None
        if not link:
            continue  # expiry group header row
        contract_rows += 1
        match = _OCC_TAIL.search(link.strip())
        if match is None:
            continue
        try:
            # The root is irrelevant here; any placeholder satisfies the parser.
            _, expiry, _, strike = parse_symbol("X" + match[1])
        except SymbolError:
            continue

        call_oi = _parse_open_interest(row.get("c_Openinterest"))
        put_oi = _parse_open_interest(row.get("p_Openinterest"))
        if call_oi is None or put_oi is None:
            # Unreadable open interest is dropped, never guessed at.
            continue
        # Each row is one strike: the call and put share expiry and strike.
        for option_type, open_interest in ((OptionType.CALL, call_oi), (OptionType.PUT, put_oi)):
            contracts.append(
                Contract(
                    expiry=expiry,
                    option_type=option_type,
                    strike=strike,
                    open_interest=open_interest,
                )
            )

    if contract_rows and not contracts:
        raise SourceError(f"no parseable contracts out of {contract_rows} rows")

    return Chain(
        ticker=ticker.strip().upper(),
        price=price,
        as_of=retrieved_at,
        contracts=tuple(contracts),
        session=session,
        source=SOURCE_NAME,
    )


def fetch_chain(
    ticker: str,
    *,
    today: dt.date,
    now: dt.datetime,
    timeout: float = http.DEFAULT_TIMEOUT,
) -> Chain:
    """Retrieve and parse the chain for `ticker`, expiries from `today` on."""
    raw = http.get(url_for(ticker, today), timeout=timeout)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SourceError(f"{ticker}: Nasdaq response was not valid JSON") from exc
    return parse_chain(payload, ticker, retrieved_at=now)
