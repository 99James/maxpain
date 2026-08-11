"""CLI argument handling and orchestration."""

from __future__ import annotations

import datetime as dt
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from maxpain.cli import TickerError, main, parse_tickers
from maxpain.models import Status, TickerResult

RESULT = TickerResult(
    ticker="NVDA",
    status=Status.OK,
    price=217.9915,
    max_pain=212.5,
    expiry=dt.date(2026, 8, 12),
    as_of=dt.datetime(2026, 8, 11, 3, 44, 39),
)


class ParseTickersTest(unittest.TestCase):
    def test_separate_arguments(self):
        self.assertEqual(parse_tickers(["NVDA", "AMZN"]), ["NVDA", "AMZN"])

    def test_comma_separated(self):
        self.assertEqual(parse_tickers(["NVDA,AMZN,MSFT"]), ["NVDA", "AMZN", "MSFT"])

    def test_mixed_separators_and_spacing(self):
        self.assertEqual(parse_tickers(["NVDA, AMZN", "MSFT"]), ["NVDA", "AMZN", "MSFT"])

    def test_uppercases(self):
        self.assertEqual(parse_tickers(["nvda"]), ["NVDA"])

    def test_allows_dotted_class_shares(self):
        self.assertEqual(parse_tickers(["BRK.B"]), ["BRK.B"])

    def test_deduplicates_preserving_order(self):
        self.assertEqual(parse_tickers(["NVDA,AMZN,nvda"]), ["NVDA", "AMZN"])

    def test_rejects_junk(self):
        for bad in [["NV$DA"], ["--"], ["A" * 11], [""], [","]]:
            with self.subTest(bad=bad), self.assertRaises(TickerError):
                parse_tickers(bad)


class MainTest(unittest.TestCase):
    def _run(self, argv, results=(RESULT,)):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("maxpain.cli.retrieve", side_effect=list(results)):
            with redirect_stdout(out), redirect_stderr(err):
                code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_table_output(self):
        code, out, _ = self._run(["NVDA"])
        self.assertEqual(code, 0)
        self.assertIn("$212.50", out)

    def test_json_output(self):
        code, out, _ = self._run(["NVDA", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)[0]["max_pain"], 212.5)

    def test_bad_ticker_exits_two(self):
        code, _, err = self._run(["NV$DA"])
        self.assertEqual(code, 2)
        self.assertIn("not a valid ticker", err)

    def test_failed_row_exits_one(self):
        failed = TickerResult("ZZZZZ", Status.NOT_FOUND, detail="no such ticker")
        code, out, _ = self._run(["ZZZZZ"], results=(failed,))
        self.assertEqual(code, 1)
        self.assertIn("NOT_FOUND", out)

    def test_preserves_input_order(self):
        rows = [
            TickerResult(t, Status.OK, price=1.0, max_pain=1.0)
            for t in ("NVDA", "AMZN", "MSFT")
        ]
        _, out, _ = self._run(["NVDA,AMZN,MSFT"], results=rows)
        positions = [out.index(t) for t in ("NVDA", "AMZN", "MSFT")]
        self.assertEqual(positions, sorted(positions))

    def test_no_verify_is_passed_through(self):
        with mock.patch("maxpain.cli.retrieve", return_value=RESULT) as retrieve:
            with redirect_stdout(io.StringIO()):
                main(["NVDA", "--no-verify"])
        self.assertFalse(retrieve.call_args.kwargs["verify"])

    def test_max_age_is_passed_through(self):
        with mock.patch("maxpain.cli.retrieve", return_value=RESULT) as retrieve:
            with redirect_stdout(io.StringIO()):
                main(["NVDA", "--max-age", "45"])
        self.assertEqual(retrieve.call_args.kwargs["max_age_minutes"], 45.0)


if __name__ == "__main__":
    unittest.main()
