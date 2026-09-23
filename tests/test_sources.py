"""Source parsing: Nasdaq and CBOE chains, and OptionCharts fragments.

Both are exercised against the committed snapshots plus deliberately broken
payloads. The recurring theme is that a malformed response must raise or
return nothing -- never a plausible-looking number.
"""

from __future__ import annotations

import datetime as dt
import unittest

from maxpain import http
from maxpain.models import OptionType
from maxpain.sources import cboe, nasdaq, optioncharts
from tests.fixtures import (
    NASDAQ_RETRIEVED_AT,
    NASDAQ_SESSION,
    OPTIONCHARTS_EXPECTED,
    load_cboe_payload,
    load_nasdaq_payload,
    load_optioncharts_document,
)


class NasdaqParseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chain = nasdaq.parse_chain(
            load_nasdaq_payload(), "nvda", retrieved_at=NASDAQ_RETRIEVED_AT
        )

    def test_extracts_price_session_and_source(self):
        self.assertEqual(self.chain.ticker, "NVDA")
        self.assertEqual(self.chain.price, 225.51)
        self.assertEqual(self.chain.session, NASDAQ_SESSION)
        self.assertEqual(self.chain.source, "Nasdaq")
        self.assertEqual(self.chain.as_of, NASDAQ_RETRIEVED_AT)

    def test_each_row_yields_a_call_and_a_put(self):
        calls = [c for c in self.chain.contracts if c.option_type is OptionType.CALL]
        puts = [c for c in self.chain.contracts if c.option_type is OptionType.PUT]
        self.assertEqual(len(calls), len(puts))
        self.assertGreater(len(calls), 500)

    def test_strike_and_expiry_come_from_the_occ_symbol(self):
        contract = next(
            c for c in self.chain.contracts
            if c.expiry == dt.date(2026, 9, 25) and c.strike == 215.0
            and c.option_type is OptionType.PUT
        )
        self.assertEqual(contract.open_interest, 12453.0)

    def test_window_starts_at_the_requested_date(self):
        self.assertEqual(min(self.chain.expiries()), dt.date(2026, 9, 25))

    def test_url_requests_a_date_window(self):
        url = nasdaq.url_for(" nvda ", dt.date(2026, 9, 24))
        self.assertIn("/quote/NVDA/option-chain", url)
        self.assertIn("fromdate=2026-09-24", url)
        self.assertIn("todate=2026-11-23", url)


class NasdaqRejectionTest(unittest.TestCase):
    """A missing field must raise, not default to zero."""

    GOOD = {
        "status": {"rCode": 200},
        "data": {
            "lastTrade": "LAST TRADE: $1,225.51 (AS OF SEP 23, 2026)",
            "table": {"rows": [
                {"expirygroup": "September 25, 2026", "drillDownURL": None},
                {
                    "drillDownURL": "/market-activity/stocks/x/option-chain/call-put-options/x--260925c00212500",
                    "c_Openinterest": "1,000", "p_Openinterest": "--",
                },
            ]},
        },
    }

    def parse(self, payload):
        return nasdaq.parse_chain(payload, "X", retrieved_at=NASDAQ_RETRIEVED_AT)

    def test_parses_minimal_payload(self):
        chain = self.parse(self.GOOD)
        self.assertEqual(chain.price, 1225.51)
        call, put = chain.contracts
        self.assertEqual((call.strike, call.open_interest), (212.5, 1000.0))
        self.assertEqual(put.open_interest, 0.0)

    def test_accepts_a_time_after_the_date(self):
        """Seen live for DELL on 2026-09-23; rejecting it forced a stale fallback."""
        for stamp in ("SEP 23, 2026 7:30 PM ET", "SEP 23, 2026 10:05 AM ET", "SEP 23, 2026 4:00 PM"):
            with self.subTest(stamp):
                data = {**self.GOOD["data"], "lastTrade": f"LAST TRADE: $549.73 (AS OF {stamp})"}
                chain = self.parse({"status": {"rCode": 200}, "data": data})
                self.assertEqual(chain.price, 549.73)
                self.assertEqual(chain.session, dt.date(2026, 9, 23))

    def test_unknown_symbol_is_not_found(self):
        payload = {"status": {"rCode": 400, "bCodeMessage": [
            {"code": 1001, "errorMessage": "Symbol not exists."}]}, "data": None}
        with self.assertRaises(http.NotFoundError):
            self.parse(payload)

    def test_rejects_bad_payloads(self):
        data = self.GOOD["data"]
        cases = [
            ("not a dict", []),
            ("error code", {"status": {"rCode": 500}, "data": data}),
            ("no data", {"status": {"rCode": 200}}),
            ("no last trade", {"status": {"rCode": 200}, "data": {**data, "lastTrade": None}}),
            ("undated price", {"status": {"rCode": 200},
                               "data": {**data, "lastTrade": "LAST TRADE: $10.00"}}),
            ("zero price", {"status": {"rCode": 200},
                            "data": {**data, "lastTrade": "LAST TRADE: $0 (AS OF SEP 23, 2026)"}}),
            ("no rows", {"status": {"rCode": 200}, "data": {**data, "table": {}}}),
        ]
        for label, payload in cases:
            with self.subTest(label), self.assertRaises(cboe.SourceError):
                self.parse(payload)

    def test_unreadable_open_interest_drops_the_row_not_zeroes_it(self):
        rows = [dict(self.GOOD["data"]["table"]["rows"][1], c_Openinterest="n/a")]
        payload = {"status": {"rCode": 200}, "data": {**self.GOOD["data"], "table": {"rows": rows}}}
        with self.assertRaises(cboe.SourceError):
            self.parse(payload)


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

    def test_dates_price_by_last_trade_not_publish_time(self):
        """Published on the 11th, but the price is the 10th's close."""
        self.assertEqual(self.chain.session, dt.date(2026, 8, 10))
        self.assertEqual(self.chain.source, "CBOE")

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
