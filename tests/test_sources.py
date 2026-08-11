"""Source parsing: CBOE chains and OptionCharts fragments.

Both are exercised against the committed snapshots plus deliberately broken
payloads. The recurring theme is that a malformed response must raise or
return nothing -- never a plausible-looking number.
"""

from __future__ import annotations

import datetime as dt
import unittest

from maxpain.sources import cboe, optioncharts
from tests.fixtures import (
    OPTIONCHARTS_EXPECTED,
    load_cboe_payload,
    load_optioncharts_document,
)


class CboeParseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chain = cboe.parse_chain(load_cboe_payload(), "nvda")

    def test_extracts_price_and_contracts(self):
        self.assertEqual(self.chain.ticker, "NVDA")
        self.assertAlmostEqual(self.chain.price, 217.9915)
        self.assertEqual(len(self.chain.contracts), 3822)

    def test_parses_timestamp(self):
        self.assertEqual(self.chain.as_of, dt.datetime(2026, 8, 11, 3, 44, 39))

    def test_expiries_are_sorted_and_unique(self):
        expiries = self.chain.expiries()
        self.assertEqual(len(expiries), 24)
        self.assertEqual(list(expiries), sorted(set(expiries)))

    def test_url_normalises_ticker(self):
        self.assertEqual(
            cboe.url_for(" nvda "),
            "https://cdn.cboe.com/api/global/delayed_quotes/options/NVDA.json",
        )


class CboeRejectionTest(unittest.TestCase):
    """A missing field must raise, not default to zero."""

    def test_rejects_bad_payloads(self):
        base = {"timestamp": "2026-08-11 03:44:39", "data": {"current_price": 10.0, "options": []}}
        for label, payload in [
            ("not a dict", ["nope"]),
            ("no data", {"timestamp": "2026-08-11 03:44:39"}),
            ("no price", {**base, "data": {"options": []}}),
            ("zero price", {**base, "data": {"current_price": 0, "options": []}}),
            ("negative price", {**base, "data": {"current_price": -5, "options": []}}),
            ("price is a string", {**base, "data": {"current_price": "217.99", "options": []}}),
            ("no options list", {**base, "data": {"current_price": 10.0}}),
            ("bad timestamp", {"timestamp": "yesterday", "data": base["data"]}),
        ]:
            with self.subTest(label=label), self.assertRaises(cboe.SourceError):
                cboe.parse_chain(payload, "NVDA")

    def test_rejects_when_no_symbol_parses(self):
        payload = {
            "timestamp": "2026-08-11 03:44:39",
            "data": {"current_price": 10.0, "options": [{"option": "GARBAGE", "open_interest": 1}]},
        }
        with self.assertRaises(cboe.SourceError):
            cboe.parse_chain(payload, "NVDA")

    def test_skips_individual_bad_contracts(self):
        """One unparseable contract is dropped; the good ones survive."""
        payload = {
            "timestamp": "2026-08-11 03:44:39",
            "data": {
                "current_price": 10.0,
                "options": [
                    {"option": "AAA260812C00010000", "open_interest": 5},
                    {"option": "GARBAGE", "open_interest": 99},
                    {"option": "AAA260812P00010000", "open_interest": -1},  # negative OI
                ],
            },
        }
        chain = cboe.parse_chain(payload, "AAA")
        self.assertEqual(len(chain.contracts), 1)
        self.assertEqual(chain.contracts[0].open_interest, 5.0)


class OptionChartsParseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.published = optioncharts.parse_max_pain_by_expiry(load_optioncharts_document())

    def test_extracts_every_expiration(self):
        self.assertEqual(len(self.published), 23)

    def test_values_match_the_verified_set(self):
        for expiry, expected in OPTIONCHARTS_EXPECTED.items():
            with self.subTest(expiry=expiry):
                self.assertEqual(self.published[expiry], expected)

    def test_returns_empty_on_unusable_input(self):
        """Every failure mode yields "no opinion" rather than an exception."""
        for label, document in [
            ("empty", ""),
            ("no marker", "<html><body>nothing here</body></html>"),
            ("marker but bad json", "let chart_data = [{oops"),
            ("marker but not a list", 'let chart_data = {"a": 1};'),
        ]:
            with self.subTest(label=label):
                self.assertEqual(optioncharts.parse_max_pain_by_expiry(document), {})

    def test_skips_malformed_entries_but_keeps_good_ones(self):
        document = (
            'let chart_data = [{"date_yyyymmdd": "2026-08-12", "y": 212.5}, '
            '{"date_yyyymmdd": "not-a-date", "y": 1}, '
            '{"date_yyyymmdd": "2026-08-14", "y": "oops"}, '
            '{"y": 5}];'
        )
        self.assertEqual(
            optioncharts.parse_max_pain_by_expiry(document), {dt.date(2026, 8, 12): 212.5}
        )

    def test_normalises_class_share_tickers(self):
        """CBOE says BRK.B; OptionCharts only answers to BRKB."""
        self.assertEqual(optioncharts.normalize_ticker("BRK.B"), "BRKB")
        self.assertEqual(optioncharts.normalize_ticker("brk-b"), "BRKB")
        self.assertIn("ticker=BRKB&", optioncharts.url_for("BRK.B", dt.date(2026, 8, 14)))


if __name__ == "__main__":
    unittest.main()
