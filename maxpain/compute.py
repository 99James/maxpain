"""Max pain computation and expiry selection.

Pure functions only -- no network, no clock, no globals. Every input is passed
in explicitly (including "today"), which is what lets the tests pin real
market snapshots to known-correct answers.

Max pain is the price at which option holders collectively receive the least
money at expiration:

    Pain(P) = sum over calls  max(P - K, 0) * OI
            + sum over puts   max(K - P, 0) * OI

and max pain is the P minimising that. It is a deterministic function of open
interest -- not an opinion, and not proprietary data. That is why this module
can be checked against an independent publisher and expected to agree exactly.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence

from .models import Contract, OptionType

# One option contract covers 100 shares. The multiplier scales every term in
# the sum identically, so it cannot move the argmin -- it is applied only so
# that a reported pain total is in real dollars.
CONTRACT_MULTIPLIER = 100


class ComputationError(ValueError):
    """Raised when a chain cannot yield a meaningful max pain."""


def select_expiry(expiries: Iterable[dt.date], today: dt.date) -> dt.date:
    """Return the nearest expiry that has not already passed.

    An option expiring *today* has not expired yet, so today counts as
    eligible. Feeds routinely include recently-passed dates -- CBOE served
    2026-08-10 on 2026-08-11 -- and silently reporting a dead expiry's max
    pain would be exactly the kind of quiet wrongness this tool must avoid.
    """
    future = sorted(e for e in expiries if e >= today)
    if not future:
        raise ComputationError("no unexpired option expirations available")
    return future[0]


def select_expiry_with_open_interest(
    contracts: Iterable[Contract], today: dt.date
) -> dt.date:
    """Nearest unexpired expiry that actually has open interest.

    Newly listed expirations exist before anyone has traded them. NVDA's
    2026-08-24 chain, for instance, carried 60 contracts and zero open
    interest -- max pain is undefined there, and OptionCharts omits that
    expiry from its own results for the same reason.

    Skipping such expiries is what makes the tool return the expiry a person
    actually cares about instead of erroring on an empty one.
    """
    open_interest_by_expiry: dict[dt.date, float] = {}
    for c in contracts:
        open_interest_by_expiry[c.expiry] = (
            open_interest_by_expiry.get(c.expiry, 0.0) + c.open_interest
        )

    tradeable = [e for e, oi in open_interest_by_expiry.items() if oi > 0]
    if not tradeable:
        raise ComputationError("no expiration has any open interest")
    return select_expiry(tradeable, today)


def pain_at(contracts: Sequence[Contract], price: float) -> float:
    """Total dollars paid out to option holders if the underlying settles at `price`."""
    total = 0.0
    for c in contracts:
        if c.option_type is OptionType.CALL:
            intrinsic = price - c.strike
        else:
            intrinsic = c.strike - price
        if intrinsic > 0:
            total += intrinsic * c.open_interest
    return total * CONTRACT_MULTIPLIER


def max_pain(contracts: Sequence[Contract]) -> float:
    """Return the strike minimising total payout across `contracts`.

    Candidate prices are the strikes themselves: the pain curve is piecewise
    linear with breakpoints only at strikes, so its minimum is always attained
    at one of them.

    Ties resolve to the lowest strike, purely so the result is deterministic.
    """
    if not contracts:
        raise ComputationError("no contracts")

    strikes = sorted({c.strike for c in contracts})

    total_oi = sum(c.open_interest for c in contracts)
    if total_oi <= 0:
        # Every candidate price scores zero pain, so the argmin would be an
        # artefact of iteration order rather than a fact about the market.
        raise ComputationError("chain has no open interest")

    best_strike = strikes[0]
    best_pain = pain_at(contracts, best_strike)
    for strike in strikes[1:]:
        pain = pain_at(contracts, strike)
        if pain < best_pain:
            best_strike, best_pain = strike, pain
    return best_strike


def contracts_for(contracts: Iterable[Contract], expiry: dt.date) -> tuple[Contract, ...]:
    """All contracts for a single expiry."""
    return tuple(c for c in contracts if c.expiry == expiry)


def max_pain_for_expiry(contracts: Iterable[Contract], expiry: dt.date) -> float:
    """Max pain restricted to one expiry.

    Max pain is only meaningful per-expiry; mixing expirations would sum
    payouts that can never occur simultaneously.
    """
    selected = contracts_for(contracts, expiry)
    if not selected:
        raise ComputationError(f"no contracts for expiry {expiry.isoformat()}")
    return max_pain(selected)
