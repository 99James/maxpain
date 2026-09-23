# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A **personal, single-user CLI** that takes US stock tickers and returns the current price and options Max Pain for the nearest expiration. Read `SPEC.md` for the product definition and `README.md` for usage.

## Commands

```bash
python3 -m maxpain NVDA AMZN MSFT              # run
python3 -m unittest discover -s tests -t .     # full suite (offline, <1s)
python3 -m unittest tests.test_compute -v      # correctness tests only
python3 -m unittest tests.test_compute.GoldenCrossSourceTest.test_matches_optioncharts_on_every_shared_expiry
```

Tests must be run from the project root (`-t .`) so the `maxpain` and `tests` packages resolve.

## Environment constraints

- **No `pip`, no `ensurepip`; Python is PEP-668 externally-managed.** Third-party packages cannot be installed.
- **The project is therefore standard-library only, and must stay that way.** `urllib.request`, `json`, `concurrent.futures`, `unittest` cover everything needed. Do not add a dependency — it cannot be installed here.
- Go is not installed (the sibling `stock-sentinel` project is unrelated to this one).

## Architecture

```
cli.py ──► retrieve.py ──► sources/nasdaq.py    (price + open interest, primary)
                      ├──► sources/cboe.py      (same, fallback only)
                      └──► sources/optioncharts.py  (verification only)
                           compute.py            (pure max pain, no I/O)
                           market.py             (pure NYSE calendar / Eastern time)
                           render.py             (table / JSON + exit codes)
```

**`compute.py` is pure** — no network, no clock, no globals; "today" is always passed in. This is what makes the correctness tests possible, so keep I/O out of it.

Max Pain is computed from exchange open interest, not scraped:
`Pain(P) = Σ max(P−K,0)·OI_call + Σ max(K−P,0)·OI_put`, minimised over strikes. It is deterministic, which is why an independent publisher can be expected to agree exactly.

## Invariants to preserve

These encode the spec's "correctness beats speed" requirement. Breaking one silently produces wrong financial data.

- **Never fabricate a number.** Missing data is `None` in the model and `--` on screen — never `0.00`, never a blank, never a last-known value. `tests/test_render.py::NoSilentFallbackTest` enforces this.
- **Every number ships with a `Status`.** There is no code path producing a price or Max Pain without one.
- **Getting the latest data is the main feature.** Nasdaq is primary because it is current; CBOE is fetched only when Nasdaq fails or is behind, and the later session wins. Every result is checked against the latest NYSE session (`retrieve.session_staleness`, calendar in `market.py`, which avoids `zoneinfo` since Windows Python has no tz database) and flagged `STALE` if behind.
- **Our computed value is the answer; OptionCharts only checks it.** On disagreement, report *our* computed value and flag `MISMATCH` — never silently adopt theirs.
- **Verification is best-effort.** Any OptionCharts failure returns "no opinion" (`UNVERIFIED`); it must never raise or break a run.
- **Price and open interest come from one snapshot.** One request to one source supplies both, so they can never be from different moments. Don't split them across requests.
- **Date a price by its trading session, never a publish stamp.** CBOE republishes overnight without new trades, so its `timestamp` can be today while `last_trade_time` is a session old; Nasdaq's `lastTrade` carries the session date.
- **Skip expiries with zero open interest.** Max Pain is undefined there; the argmin would be an artefact of iteration order.
- **Parse strikes as integer thousandths.** OCC encodes strike×1000; integer arithmetic keeps 212.5 exact.

## Fixtures

`tests/fixtures/*.gz` are real responses, stored gzipped: CBOE and OptionCharts captured 2026-08-11, and Nasdaq plus a matching OptionCharts fragment captured 2026-09-23. **Do not regenerate them** — their value is being a frozen, independently-sourced ground truth. `tests/fixtures/__init__.py` pins `CAPTURE_DATE` so expiry selection is deterministic regardless of when tests run.

The NVDA fixture deliberately contains two awkward real-world cases: an already-expired date (2026-08-10) and an expiry with contracts but zero open interest (2026-08-24).

## Out of scope

Per `SPEC.md`, do not add: buy/sell recommendations, portfolio management, broker integration, AI analysis, technical indicators, news/fundamentals, historical persistence, or auth.
