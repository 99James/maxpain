"""Command-line entry point."""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Sequence

from . import http, render
from .retrieve import retrieve

# Tickers may be separated by commas, whitespace, or both, so "NVDA, AMZN MSFT"
# works as typed.
_SPLIT = re.compile(r"[,\s]+")

# Must start with a letter, and any separator must sit between alphanumerics.
# Class shares (BRK.B, and the BRK-B spelling some people type) are valid;
# bare punctuation like "--" is not, so a stray flag never becomes a lookup.
_VALID_TICKER = re.compile(r"[A-Z][A-Z0-9]*(?:[.\-][A-Z0-9]+)*")

# Long enough for class shares and warrants, short enough to catch a fat-finger
# before it becomes an upstream request.
MAX_TICKER_LENGTH = 10

# Payloads are large (SPY alone is 6.4 MB) and both endpoints are unmetered
# public services. A small pool keeps the run fast without hammering them.
DEFAULT_WORKERS = 6


class TickerError(ValueError):
    pass


def parse_tickers(arguments: Sequence[str]) -> list[str]:
    """Split and normalise ticker arguments, preserving the order given."""
    seen: dict[str, None] = {}
    for chunk in arguments:
        for raw in _SPLIT.split(chunk):
            if not raw:
                continue
            ticker = raw.strip().upper()
            if len(ticker) > MAX_TICKER_LENGTH or not _VALID_TICKER.fullmatch(ticker):
                raise TickerError(f"not a valid ticker: {raw!r}")
            seen.setdefault(ticker, None)
    if not seen:
        raise TickerError("no tickers given")
    return list(seen)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="maxpain",
        description="Current price and options max pain for US stock tickers.",
        epilog=(
            "Max pain is computed from CBOE open interest and cross-checked "
            "against OptionCharts. Exit status is non-zero if any row is not "
            "trustworthy."
        ),
    )
    parser.add_argument("tickers", nargs="+", help="e.g. NVDA AMZN MSFT  or  'NVDA,AMZN'")
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="skip the OptionCharts cross-check (faster, results marked UNVERIFIED)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    parser.add_argument(
        "--max-age",
        type=float,
        metavar="MINUTES",
        help=(
            "flag data older than MINUTES as STALE. Off by default: quotes are "
            "legitimately hours old outside market hours, and the snapshot "
            "timestamp is always shown regardless."
        ),
    )
    parser.add_argument(
        "--timeout", type=float, default=http.DEFAULT_TIMEOUT, metavar="SECONDS"
    )
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, metavar="N")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        tickers = parse_tickers(args.tickers)
    except TickerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    today = dt.date.today()

    def work(ticker: str):
        return retrieve(
            ticker,
            today=today,
            verify=not args.no_verify,
            timeout=args.timeout,
            max_age_minutes=args.max_age,
        )

    workers = max(1, min(args.workers, len(tickers)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # map preserves input order, so output matches what the user typed.
        results = list(pool.map(work, tickers))

    output = render.render_json(results) if args.json else render.render_table(results)
    print(output)
    return render.exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
