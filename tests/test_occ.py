"""OCC symbol parsing, including every malformed shape we must reject."""

from __future__ import annotations

import datetime as dt
import unittest

from maxpain.models import OptionType
from maxpain.occ import SymbolError, parse_symbol
from tests.fixtures import load_cboe_payload


class ParseSymbolTest(unittest.TestCase):
    def test_call(self):
        root, expiry, kind, strike = parse_symbol("NVDA260810C00110000")
        self.assertEqual(root, "NVDA")
        self.assertEqual(expiry, dt.date(2026, 8, 10))
        self.assertIs(kind, OptionType.CALL)
        self.assertEqual(strike, 110.0)

    def test_put_with_fractional_strike(self):
        _, _, kind, strike = parse_symbol("NVDA260812P00212500")
        self.assertIs(kind, OptionType.PUT)
        self.assertEqual(strike, 212.5)

    def test_root_containing_digits(self):
        """The date/type/strike tail is fixed-width, so a numeric root is fine."""
        root, expiry, _, strike = parse_symbol("BRK1260814C00500000")
        self.assertEqual(root, "BRK1")
        self.assertEqual(expiry, dt.date(2026, 8, 14))
        self.assertEqual(strike, 500.0)

    def test_lowercase_is_accepted(self):
        self.assertEqual(parse_symbol("nvda260810c00110000")[0], "NVDA")

    def test_strike_precision_is_exact(self):
        """Integer thousandths avoid the float drift of parsing decimals."""
        for encoded, expected in [
            ("00007500", 7.5),
            ("00212500", 212.5),
            ("01000000", 1000.0),
            ("00000500", 0.5),
        ]:
            with self.subTest(encoded=encoded):
                self.assertEqual(parse_symbol(f"AAA260810C{encoded}")[3], expected)


class RejectionTest(unittest.TestCase):
    def test_rejects_malformed(self):
        for bad in [
            "",
            "NVDA",
            "NVDA260810X00110000",  # bad option type
            "NVDA26081C00110000",  # short date
            "NVDA260810C0011000",  # short strike
            "NVDA260810C001100000",  # long strike
            "NVDA-260810-C-110",
            "NVDA261332C00110000",  # month 13, day 32
            "NVDA260810C00000000",  # zero strike
        ]:
            with self.subTest(symbol=bad), self.assertRaises(SymbolError):
                parse_symbol(bad)


class RealPayloadTest(unittest.TestCase):
    def test_every_symbol_in_the_snapshot_parses(self):
        """All 3,822 live contracts parse -- no silent drops in real data."""
        symbols = [o["option"] for o in load_cboe_payload()["data"]["options"]]
        self.assertEqual(len(symbols), 3822)
        for symbol in symbols:
            parse_symbol(symbol)  # raises on failure


if __name__ == "__main__":
    unittest.main()
