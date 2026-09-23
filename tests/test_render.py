"""Rendering and exit codes.

The important assertions here are negative: missing data must never reach the
screen looking like a quote.
"""

from __future__ import annotations

import datetime as dt
import json
import unittest
from dataclasses import replace

from maxpain.models import Status, TickerResult
from maxpain.render import MISSING, exit_code, render_json, render_table

AS_OF = dt.datetime(2026, 8, 11, 3, 44, 39)

OK_ROW = TickerResult(
    ticker="NVDA",
    status=Status.OK,
    price=217.9915,
    max_pain=212.5,
    expiry=dt.date(2026, 8, 12),
    as_of=AS_OF,
    session=dt.date(2026, 8, 10),
    source="Nasdaq",
    verified_against=212.5,
)

FAILED_ROW = TickerResult(ticker="ZZZZZ", status=Status.NOT_FOUND, detail="no such ticker")


class TableTest(unittest.TestCase):
    def test_renders_values(self):
        output = render_table([OK_ROW])
        self.assertIn("NVDA", output)
        self.assertIn("$217.99", output)
        self.assertIn("$212.50", output)
        self.assertIn("2026-08-12", output)
        self.assertIn("OK", output)

    def test_shows_session_source_and_timestamp(self):
        output = render_table([OK_ROW])
        self.assertIn("Prices from the 2026-08-10 trading session", output)
        self.assertIn("Source: Nasdaq, as of 2026-08-11 03:44:39 UTC", output)
        self.assertNotIn("delayed", output.lower())

    def test_warns_about_delay_when_cboe_fallback_is_used(self):
        fallback = replace(OK_ROW, ticker="AMD", source="CBOE", session=dt.date(2026, 8, 7))
        output = render_table([OK_ROW, fallback])
        self.assertIn("Source: CBOE, Nasdaq", output)
        self.assertIn("delayed", output.lower())
        self.assertIn("2026-08-07 .. 2026-08-10", output)

    def test_json_carries_session_and_source(self):
        row = json.loads(render_json([OK_ROW]))[0]
        self.assertEqual(row["session"], "2026-08-10")
        self.assertEqual(row["source"], "Nasdaq")

    def test_detail_is_surfaced(self):
        self.assertIn("no such ticker", render_table([FAILED_ROW]))

    def test_empty_input(self):
        self.assertIn("no tickers", render_table([]))

    def test_thousands_separator(self):
        row = TickerResult("SPY", Status.OK, price=6421.5, max_pain=6400.0)
        self.assertIn("$6,421.50", render_table([row]))


class NoSilentFallbackTest(unittest.TestCase):
    """A failed lookup must be unmistakable, never a number."""

    def test_missing_values_never_render_as_zero(self):
        output = render_table([FAILED_ROW])
        self.assertNotIn("$0.00", output)
        self.assertNotIn("0.00", output)
        self.assertIn(MISSING, output)

    def test_every_failure_status_renders_placeholders(self):
        for status in (
            Status.NOT_FOUND,
            Status.FETCH_ERROR,
            Status.NO_OPTIONS,
        ):
            with self.subTest(status=status):
                output = render_table([TickerResult("X", status)])
                self.assertNotIn("0.00", output)
                self.assertIn(status.value, output)

    def test_failed_row_alongside_good_row_stays_distinct(self):
        output = render_table([OK_ROW, FAILED_ROW])
        self.assertIn("$212.50", output)
        self.assertIn(MISSING, output)
        self.assertIn("NOT_FOUND", output)

    def test_json_uses_null_not_zero(self):
        payload = json.loads(render_json([FAILED_ROW]))[0]
        self.assertIsNone(payload["price"])
        self.assertIsNone(payload["max_pain"])
        self.assertIsNone(payload["expiry"])
        self.assertEqual(payload["status"], "NOT_FOUND")


class JsonTest(unittest.TestCase):
    def test_round_trips(self):
        payload = json.loads(render_json([OK_ROW]))[0]
        self.assertEqual(payload["ticker"], "NVDA")
        self.assertEqual(payload["max_pain"], 212.5)
        self.assertEqual(payload["expiry"], "2026-08-12")
        self.assertEqual(payload["verified_against"], 212.5)

    def test_is_a_list(self):
        self.assertEqual(len(json.loads(render_json([OK_ROW, FAILED_ROW]))), 2)


class ExitCodeTest(unittest.TestCase):
    def test_all_ok(self):
        self.assertEqual(exit_code([OK_ROW]), 0)

    def test_unverified_is_acceptable(self):
        """Verification is best-effort; its absence is not our error."""
        row = TickerResult("NVDA", Status.UNVERIFIED, price=1.0, max_pain=1.0)
        self.assertEqual(exit_code([row]), 0)

    def test_problems_are_reported(self):
        for status in (
            Status.MISMATCH,
            Status.STALE,
            Status.NOT_FOUND,
            Status.FETCH_ERROR,
            Status.NO_OPTIONS,
        ):
            with self.subTest(status=status):
                self.assertEqual(exit_code([TickerResult("X", status)]), 1)

    def test_one_bad_row_fails_the_run(self):
        self.assertEqual(exit_code([OK_ROW, FAILED_ROW]), 1)


if __name__ == "__main__":
    unittest.main()
