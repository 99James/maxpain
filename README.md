# maxpain

A personal command-line tool that takes US stock tickers and returns two numbers for each: the **current price** and the **options Max Pain** for the nearest expiration.

It is a data-retrieval tool, not a decision engine. See [SPEC.md](SPEC.md).

## Requirements

Python 3.12+. **No third-party dependencies** — standard library only, so there is nothing to install.

## Usage

```bash
python3 -m maxpain NVDA AMZN MSFT
```

```
Ticker    Price  Max Pain      Expiry  Status
------  -------  --------  ----------  ------
NVDA    $217.99   $212.50  2026-08-12  OK
AMZN    $277.00   $265.00  2026-08-12  OK
MSFT    $505.33   $485.00  2026-08-12  OK

  Prices from the 2026-08-10 trading session
  Source: Nasdaq, as of 2026-08-11 03:50:57 UTC
```

Tickers may be separated by commas, spaces, or both: `NVDA,AMZN MSFT`.

### Options

| Flag | Effect |
|---|---|
| `--json` | Structured output; missing values are `null`, never `0` |
| `--no-verify` | Skip the OptionCharts cross-check — roughly 3× faster, rows marked `UNVERIFIED` |
| `--timeout SECONDS` | Per-request timeout (default 20) |
| `--workers N` | Concurrent fetches (default 6) |

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Every row is `OK` or `UNVERIFIED` |
| `1` | At least one row is `MISMATCH`, `STALE`, `NOT_FOUND`, `FETCH_ERROR`, or `NO_OPTIONS` |
| `2` | Bad arguments (e.g. a malformed ticker) |

## How it works

Max Pain is the price at which option holders collectively receive the least money at expiration:

```
Pain(P) = Σ max(P − K, 0) · OI_call  +  Σ max(K − P, 0) · OI_put
```

Max Pain is the `P` minimising that sum. It is a **deterministic function of open interest** — not proprietary data — so the tool computes it directly rather than scraping a rendered number.

```
tickers ──► Nasdaq (CBOE fallback) ──► price + open interest ──► compute max pain ──┐
                                                                                    ├─► reconcile ──► table
                       OptionCharts ──► published max pain ─────────────────────────┘
```

**Sources**

- **[Nasdaq](https://www.nasdaq.com/market-activity/stocks/nvda/option-chain)** — primary. One request returns the underlying's last trade and every contract's open interest, from a single snapshot, so the price is never from a different moment than the Max Pain input. It carries the current session's price and the latest open interest from OCC. No API key.
- **[CBOE](https://cdn.cboe.com/api/global/delayed_quotes/options/NVDA.json)** — fallback, fetched only when Nasdaq fails or is behind; whichever source has the later session wins. Its CDN files are delayed ~15 minutes and have been seen a full session out of date (on 2026-09-23 after the close they still held 2026-09-22's price and open interest).
- **[OptionCharts](https://optioncharts.io/options/NVDA/max-pain)** — verification only. Its published value is compared against ours; it never overwrites our number.

Each row carries a status describing how much to trust it:

| Status | Meaning |
|---|---|
| `OK` | Computed value matches OptionCharts |
| `UNVERIFIED` | OptionCharts unavailable, or `--no-verify`. Our value stands |
| `MISMATCH` | The two sources disagree — **our value is shown, theirs is reported in the notes** |
| `STALE` | Price is from an earlier session than the latest NYSE session, from every source tried |
| `NO_OPTIONS` | No listed options, or no expiry with open interest |
| `NOT_FOUND` | No such ticker |
| `FETCH_ERROR` | Network failure or unparseable response |

A cell either holds a real retrieved number or `--`. Nothing renders missing data as `0.00`.

## Testing

```bash
python3 -m unittest discover -s tests -t .        # full suite, offline
python3 -m unittest tests.test_compute -v         # correctness tests only
python3 -m unittest tests.test_compute.GoldenCrossSourceTest   # a single class
```

The suite runs entirely against committed snapshots — no network, deterministic, under a second.

The central test asserts our computation reproduces OptionCharts' published Max Pain **exactly, across all 20+ shared expirations** in a real captured NVDA chain. The two numbers travel completely independent paths (our arithmetic over CBOE open interest vs. their computation over their own OPRA feed), so agreement to the cent is meaningful evidence rather than a tautology.

Also covered: a hand-computable micro-chain, scale-invariance and argmin properties, every parser rejection path, HTTP retry classification, and the negative assertions that missing data can never render as a number.

## Known limitations

- **Prices are regular-session last trades, not after-hours.** The session each price comes from is always displayed, and a price from before the latest NYSE session is flagged `STALE`. Dates follow the New York market calendar, not the local machine's. If the CBOE fallback is used, its quotes are delayed ~15 minutes.
- **Open interest updates once a day.** OCC publishes it each morning for the previous close; no source can make it fresher intraday.
- **The verification path reads unversioned markup.** OptionCharts can change its page at any time; if it does, rows degrade to `UNVERIFIED` rather than breaking or reporting a wrong value.
- **No endpoint carries an SLA.** Both are public and unauthenticated, and could change without notice.
- **Expiries with zero open interest are skipped.** Max Pain is undefined there. Newly listed expirations often have none — OptionCharts omits them for the same reason.
