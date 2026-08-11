"""Data types shared across the package.

Everything here is immutable. A result object is built once and never mutated,
so a row can't acquire a price or a max pain value after the fact without
also carrying the status that explains where it came from.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum


class Status(Enum):
    """Outcome for a single ticker.

    Order matters: `severity` uses the declaration order so the CLI can pick
    the worst status across all rows for its exit code.
    """

    OK = "OK"
    UNVERIFIED = "UNVERIFIED"
    STALE = "STALE"
    MISMATCH = "MISMATCH"
    NO_OPTIONS = "NO_OPTIONS"
    NOT_FOUND = "NOT_FOUND"
    FETCH_ERROR = "FETCH_ERROR"

    @property
    def severity(self) -> int:
        return list(Status).index(self)

    @property
    def is_ok(self) -> bool:
        return self is Status.OK


class OptionType(Enum):
    CALL = "C"
    PUT = "P"


@dataclass(frozen=True, slots=True)
class Contract:
    """One option contract's open interest at a strike/expiry."""

    expiry: dt.date
    option_type: OptionType
    strike: float
    open_interest: float


@dataclass(frozen=True, slots=True)
class Chain:
    """A full option chain plus the underlying price, from one snapshot.

    Price and contracts come from the same fetch, so they are always mutually
    consistent -- the price is never from a different moment than the open
    interest used to compute max pain.
    """

    ticker: str
    price: float
    as_of: dt.datetime
    contracts: tuple[Contract, ...]

    def expiries(self) -> tuple[dt.date, ...]:
        return tuple(sorted({c.expiry for c in self.contracts}))


@dataclass(frozen=True, slots=True)
class TickerResult:
    """What the CLI renders for one ticker.

    `price` and `max_pain` are Optional on purpose. When retrieval fails they
    are None and `status` explains why; there is no zero or placeholder that
    could be mistaken for real market data.
    """

    ticker: str
    status: Status
    price: float | None = None
    max_pain: float | None = None
    expiry: dt.date | None = None
    as_of: dt.datetime | None = None
    verified_against: float | None = None
    detail: str = ""
