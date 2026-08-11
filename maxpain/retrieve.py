"""Retrieval and cross-source reconciliation for one ticker.

The rule this module enforces: a number is only reported alongside a status
that says how much to trust it. There is no path here that produces a price or
a max pain value without also producing the status explaining its provenance.
"""

from __future__ import annotations

import datetime as dt

from . import compute, http
from .models import Status, TickerResult
from .sources import cboe, optioncharts

# Strikes land on clean ladders (0.5, 1.0, 2.5 increments), so exact equality
# would nearly always work. A cent of tolerance absorbs float representation
# without being wide enough to hide a genuine one-strike disagreement.
AGREEMENT_TOLERANCE = 0.01


def reconcile(
    computed: float,
    published: float | None,
    *,
    tolerance: float = AGREEMENT_TOLERANCE,
) -> tuple[Status, float | None]:
    """Compare our computed max pain against the published one.

    Returns (status, published_value). We always report *our* computed value
    as the answer -- it is derived from exchange open interest and is
    reproducible from the fixtures. The published number's job is to catch us
    being wrong, not to overwrite us silently.
    """
    if published is None:
        return Status.UNVERIFIED, None
    if abs(computed - published) <= tolerance:
        return Status.OK, published
    return Status.MISMATCH, published


def retrieve(
    ticker: str,
    *,
    today: dt.date | None = None,
    verify: bool = True,
    timeout: float = http.DEFAULT_TIMEOUT,
    max_age_minutes: float | None = None,
    now: dt.datetime | None = None,
) -> TickerResult:
    """Fetch, compute, and verify one ticker. Never raises.

    Every failure is converted into a TickerResult carrying a non-OK status,
    so one bad ticker cannot take down a multi-ticker run.
    """
    ticker = ticker.strip().upper()
    today = today or dt.date.today()

    try:
        chain = cboe.fetch_chain(ticker, timeout=timeout)
    except http.NotFoundError:
        return TickerResult(ticker, Status.NOT_FOUND, detail="no such ticker")
    except http.HttpError as exc:
        return TickerResult(ticker, Status.FETCH_ERROR, detail=str(exc))
    except cboe.SourceError as exc:
        return TickerResult(ticker, Status.FETCH_ERROR, detail=str(exc))

    if not chain.contracts:
        return TickerResult(
            ticker, Status.NO_OPTIONS, price=chain.price, as_of=chain.as_of,
            detail="no listed options",
        )

    try:
        expiry = compute.select_expiry_with_open_interest(chain.contracts, today)
        computed = compute.max_pain_for_expiry(chain.contracts, expiry)
    except compute.ComputationError as exc:
        # The price is still good, so report it -- but max_pain stays None.
        return TickerResult(
            ticker, Status.NO_OPTIONS, price=chain.price, as_of=chain.as_of, detail=str(exc)
        )

    published = (
        optioncharts.fetch_max_pain(ticker, expiry, timeout=timeout) if verify else None
    )
    status, verified_against = reconcile(computed, published)

    detail = ""
    if status is Status.MISMATCH:
        detail = f"OptionCharts reports {published:.2f}"
    elif status is Status.UNVERIFIED and verify:
        detail = "OptionCharts unavailable"

    # Staleness is checked last so it overrides a clean verification: data can
    # be correctly computed and still too old to act on.
    if max_age_minutes is not None:
        reference = now or dt.datetime.now(dt.UTC).replace(tzinfo=None)
        age_minutes = (reference - chain.as_of).total_seconds() / 60
        if age_minutes > max_age_minutes:
            status = Status.STALE
            detail = f"snapshot is {age_minutes:.0f} min old"

    return TickerResult(
        ticker=ticker,
        status=status,
        price=chain.price,
        max_pain=computed,
        expiry=expiry,
        as_of=chain.as_of,
        verified_against=verified_against,
        detail=detail,
    )
