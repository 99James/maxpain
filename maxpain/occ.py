"""Parsing of OCC option symbols.

CBOE identifies each contract with an OCC-format symbol, e.g.

    NVDA260810C00110000
    ^^^^ ~~~~~~ ^ ~~~~~~~~
    root  YYMMDD  |  strike x 1000
                 C/P

The strike is an integer number of thousandths of a dollar. Parsing it with
integer arithmetic (rather than float division on the whole symbol) keeps
strikes like 212.5 exact.
"""

from __future__ import annotations

import datetime as dt
import re

from .models import OptionType

# Root may contain digits (e.g. index or adjusted symbols), so the date/type/
# strike portion is pinned to the end by its fixed width rather than by a
# greedy root match.
_SYMBOL_RE = re.compile(r"^(?P<root>[A-Z0-9]+)(?P<date>\d{6})(?P<kind>[CP])(?P<strike>\d{8})$")

_STRIKE_SCALE = 1000


class SymbolError(ValueError):
    """Raised when a symbol is not valid OCC format."""


def parse_symbol(symbol: str) -> tuple[str, dt.date, OptionType, float]:
    """Parse an OCC symbol into (root, expiry, option_type, strike).

    Raises SymbolError on anything that does not match exactly. We never
    guess: a symbol we can't parse is dropped loudly rather than silently
    contributing a wrong strike to the max pain sum.
    """
    match = _SYMBOL_RE.match(symbol.strip().upper())
    if match is None:
        raise SymbolError(f"not an OCC option symbol: {symbol!r}")

    raw_date = match["date"]
    try:
        expiry = dt.datetime.strptime(raw_date, "%y%m%d").date()
    except ValueError as exc:
        raise SymbolError(f"invalid expiry {raw_date!r} in {symbol!r}") from exc

    strike_thousandths = int(match["strike"])
    if strike_thousandths <= 0:
        raise SymbolError(f"non-positive strike in {symbol!r}")

    # Exact for the standard half- and quarter-dollar strike ladders.
    strike = strike_thousandths / _STRIKE_SCALE

    return match["root"], expiry, OptionType(match["kind"]), strike
