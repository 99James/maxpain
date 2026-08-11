# Product Definition — MVP v0.1

## Core Problem

You want a fast, reliable way to retrieve the two pieces of market information you currently use when making stock-operation decisions:

- Current stock price
- Options Max Pain

The eventual purpose is to support your buy / hold / reduce / sell decisions, but this MVP is deliberately only a **data retrieval tool**, not a decision engine.

## Who Is It For?

You only.

It can therefore be optimized for your personal workflow rather than requiring:

- User accounts
- Multi-user support
- Permissions
- Portfolio sharing
- A polished consumer UI

## Input

You enter one or more US stock tickers, for example:

```
NVDA, AMZN, MSFT
```

The application should handle multiple tickers in one request.

## Output

For every ticker, return something like:

| Ticker | Current Price | Max Pain |
| ------ | ------------- | -------- |
| NVDA   | $XXX.XX       | $XXX     |
| AMZN   | $XXX.XX       | $XXX     |
| MSFT   | $XXX.XX       | $XXX     |

- **Max Pain** should come from OptionCharts.
- **Current stock price** should come from a reliable market-data source.

## Success Criteria

The MVP succeeds if it is:

1. **Fast** — You can submit a list of tickers and receive the results quickly.
2. **Highly reliable** — The application should minimize incorrect/stale data and clearly handle cases where data cannot be retrieved.

For this project, I would treat data correctness as more important than speed when there is a conflict.

## Explicitly Out of Scope

The MVP will **NOT**:

- ❌ Make automatic buy/sell recommendations
- ❌ Manage your portfolio
- ❌ Connect to a broker or execute trades
- ❌ Use AI to analyze or predict stocks
- ❌ Calculate technical indicators
- ❌ Analyze news or fundamentals
- ❌ Store historical data/database records

This is important because it keeps our first Claude Code project small and testable.

## The Product in One Sentence

> A personal command-line/web tool that accepts a list of US stock tickers and quickly retrieves their current prices and Max Pain values from reliable sources, with a strong emphasis on data accuracy.

## One Architectural Decision I'd Make Now

I would not build the future "stock advisor" yet.

Instead, we should build this as a clean data-retrieval foundation:

```
Ticker Input
     ↓
┌─────────────────────┐
│ Data Retrieval      │
│                     │
│ Current Price       │
│ Max Pain            │
└──────────┬──────────┘
           ↓
      Structured Data
           ↓
      Simple Output
```

Later we can add modules without rewriting the foundation:

```
                 ┌── Price
                 ├── Max Pain
Ticker → Data ───┼── Options
                 ├── Fundamentals
                 ├── News
                 └── ...
                       ↓
                Analysis Layer
                       ↓
              Buy / Hold / Reduce
                       ↓
                  Your decision
```

That separation will be particularly useful if you eventually want Claude Code to build the larger personal stock-operation assistant you originally described.
