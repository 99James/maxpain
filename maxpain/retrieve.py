"""Retrieval and cross-source reconciliation for one ticker.

The rule this module enforces: a number is only reported alongside a status
that says how much to trust it. There is no path here that produces a price or
a max pain value without also producing the status explaining its provenance.
"""

from __future__ import annotations

import datetime as dt

from . import compute, http, market
from .models import Chain, Status, TickerResult
from .sources import cboe, nasdaq, optioncharts

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


def session_staleness(chain: Chain, now: dt.datetime) -> str | None:
    """Why the price is not from the latest trading session, or None if it is.

    Checked on every result, whichever source supplied it: a snapshot stamp
    alone cannot reveal a price that is a full session behind (CBOE republishes
    overnight without new trades), but the session the price traded in can.
    """
    expected = market.latest_session(now)
    if chain.session is None:
        return f"{chain.source} gave no last-trade date, so freshness is unknown"
    if chain.session < expected:
        return (
            f"price is from the {chain.session.isoformat()} session; "
            f"{chain.source} has not published {expected.isoformat()} yet"
        )
    return None


class _NoChain(Exception):
    def __init__(self, status: Status, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def fetch_latest_chain(
    ticker: str, *, today: dt.date, now: dt.datetime, timeout: float
) -> tuple[Chain, str]:
    """The freshest chain available, plus a note if the primary source lost.

    Nasdaq is asked first because it is current. CBOE is only fetched when
    Nasdaq fails or is behind the latest session, and then whichever is from
    the later session wins -- a fallback never replaces fresher data.
    Raises _NoChain when neither source produces anything.
    """
    expected = market.latest_session(now)
    fetchers = (
        (nasdaq.SOURCE_NAME, lambda: nasdaq.fetch_chain(ticker, today=today, now=now, timeout=timeout)),
        (cboe.SOURCE_NAME, lambda: cboe.fetch_chain(ticker, timeout=timeout)),
    )

    best: Chain | None = None
    failures: list[tuple[str, str, bool]] = []  # (source, reason, not_found)
    for name, fetch in fetchers:
        try:
            chain = fetch()
        except http.NotFoundError:
            failures.append((name, "no such ticker", True))
            continue
        except (http.HttpError, cboe.SourceError) as exc:
            failures.append((name, str(exc), False))
            continue
        if best is None or (chain.session or dt.date.min) > (best.session or dt.date.min):
            best = chain
        if best.session is not None and best.session >= expected:
            break

    if best is None:
        if all(not_found for _, _, not_found in failures):
            raise _NoChain(Status.NOT_FOUND, "no such ticker")
        reasons = "; ".join(f"{name}: {reason}" for name, reason, _ in failures)
        raise _NoChain(Status.FETCH_ERROR, reasons)

    note = ""
    if best.source != nasdaq.SOURCE_NAME:
        primary = next((r for n, r, _ in failures if n == nasdaq.SOURCE_NAME), None)
        note = f"via {best.source} fallback ({nasdaq.SOURCE_NAME}: {primary or 'older session'})"
    return best, note


def _join(*parts: str) -> str:
    return "; ".join(p for p in parts if p)


def retrieve(
    ticker: str,
    *,
    today: dt.date | None = None,
    verify: bool = True,
    timeout: float = http.DEFAULT_TIMEOUT,
    now: dt.datetime | None = None,
) -> TickerResult:
    """Fetch, compute, and verify one ticker. Never raises.

    Every failure is converted into a TickerResult carrying a non-OK status,
    so one bad ticker cannot take down a multi-ticker run.

    `now` is naive UTC. `today` is the first date whose options are still
    alive; it defaults to New York's calendar, never the local machine's.
    """
    ticker = ticker.strip().upper()
    now = now or dt.datetime.now(dt.UTC).replace(tzinfo=None)
    today = today or market.expiry_reference_date(now)

    try:
        chain, source_note = fetch_latest_chain(ticker, today=today, now=now, timeout=timeout)
    except _NoChain as exc:
        return TickerResult(ticker, exc.status, detail=exc.detail)

    stale = session_staleness(chain, now)
    price_only = dict(
        price=chain.price, as_of=chain.as_of, session=chain.session, source=chain.source
    )

    if not chain.contracts:
        return TickerResult(
            ticker, Status.NO_OPTIONS, **price_only,
            detail=_join(stale, "no listed options", source_note),
        )

    try:
        expiry = compute.select_expiry_with_open_interest(chain.contracts, today)
        computed = compute.max_pain_for_expiry(chain.contracts, expiry)
    except compute.ComputationError as exc:
        # The price is still good, so report it -- but max_pain stays None.
        return TickerResult(
            ticker, Status.NO_OPTIONS, **price_only,
            detail=_join(stale, str(exc), source_note),
        )

    published = (
        optioncharts.fetch_max_pain(ticker, expiry, timeout=timeout) if verify else None
    )
    status, verified_against = reconcile(computed, published)

    check = ""
    if status is Status.MISMATCH:
        check = f"OptionCharts reports {published:.2f}"
    elif status is Status.UNVERIFIED and verify:
        check = "OptionCharts unavailable"

    # Staleness overrides a clean verification: data can be correctly computed
    # and still too old to act on. Any mismatch note is kept, since stale open
    # interest is the usual reason for one.
    if stale is not None:
        status = Status.STALE

    return TickerResult(
        ticker=ticker,
        status=status,
        max_pain=computed,
        expiry=expiry,
        verified_against=verified_against,
        detail=_join(stale, check, source_note),
        **price_only,
    )
