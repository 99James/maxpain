"""Correctness tests for the max pain computation.

This is the file that matters. The tool's whole purpose is producing a number
a person will risk money on, so the priority is proving the arithmetic right
against evidence we did not generate ourselves.
"""

from __future__ import annotations

import datetime as dt
import unittest

from maxpain.compute import (
    CONTRACT_MULTIPLIER,
    ComputationError,
    max_pain,
    max_pain_for_expiry,
    pain_at,
    select_expiry,
    select_expiry_with_open_interest,
)
from maxpain.models import Contract, OptionType
from maxpain.sources import cboe, optioncharts
from tests.fixtures import (
    CAPTURE_DATE,
    OPTIONCHARTS_EXPECTED,
    load_cboe_payload,
    load_optioncharts_document,
)

CALL, PUT = OptionType.CALL, OptionType.PUT
EXPIRY = dt.date(2026, 8, 12)


def contract(kind: OptionType, strike: float, oi: float, expiry: dt.date = EXPIRY) -> Contract:
    return Contract(expiry=expiry, option_type=kind, strike=strike, open_interest=oi)


class GoldenCrossSourceTest(unittest.TestCase):
    """Our computation vs. OptionCharts' published numbers, on the same snapshot.

    These two values travel completely separate paths: ours is derived from
    CBOE open interest, theirs is computed by a third-party vendor from their
    own OPRA feed. Agreement to the cent is strong evidence the formula,
    the symbol parsing, and the per-expiry grouping are all correct.
    """

    @classmethod
    def setUpClass(cls):
        cls.chain = cboe.parse_chain(load_cboe_payload(), "NVDA")
        cls.published = optioncharts.parse_max_pain_by_expiry(load_optioncharts_document())

    def test_fixture_is_intact(self):
        self.assertEqual(len(self.chain.contracts), 3822)
        self.assertAlmostEqual(self.chain.price, 217.9915)
        self.assertEqual(len(self.published), 23)

    def test_matches_optioncharts_on_every_shared_expiry(self):
        """Every expiry both sources cover must agree exactly."""
        shared = sorted(set(self.published) & set(self.chain.expiries()))
        self.assertGreaterEqual(len(shared), 20, "expected substantial overlap")

        mismatches = []
        for expiry in shared:
            computed = max_pain_for_expiry(self.chain.contracts, expiry)
            if computed != self.published[expiry]:
                mismatches.append((expiry, computed, self.published[expiry]))

        self.assertEqual(mismatches, [], f"{len(mismatches)}/{len(shared)} expiries disagree")

    def test_matches_hardcoded_published_values(self):
        """Pin the four values verified by hand, independent of the OC fixture parser.

        If the OptionCharts parser silently returned {}, the test above would
        still pass vacuously. These literals cannot.
        """
        for expiry, expected in OPTIONCHARTS_EXPECTED.items():
            with self.subTest(expiry=expiry):
                self.assertEqual(max_pain_for_expiry(self.chain.contracts, expiry), expected)


class HandComputedTest(unittest.TestCase):
    """A chain small enough to verify with pen and paper.

    Calls: 100 @ OI 10, 110 @ OI 5.  Puts: 100 @ OI 5, 110 @ OI 20.

    At P=100: calls 0 + 0;            puts 0 + (110-100)*20 = 200  -> 200
    At P=110: calls (110-100)*10 = 100; puts 0 + 0               -> 100
    So max pain is 110.
    """

    def setUp(self):
        self.contracts = [
            contract(CALL, 100.0, 10),
            contract(CALL, 110.0, 5),
            contract(PUT, 100.0, 5),
            contract(PUT, 110.0, 20),
        ]

    def test_pain_totals(self):
        self.assertEqual(pain_at(self.contracts, 100.0), 200 * CONTRACT_MULTIPLIER)
        self.assertEqual(pain_at(self.contracts, 110.0), 100 * CONTRACT_MULTIPLIER)

    def test_max_pain(self):
        self.assertEqual(max_pain(self.contracts), 110.0)


class PropertyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chain = cboe.parse_chain(load_cboe_payload(), "NVDA")

    def test_result_is_always_an_existing_strike(self):
        for expiry in self.chain.expiries():
            selected = [c for c in self.chain.contracts if c.expiry == expiry]
            if sum(c.open_interest for c in selected) <= 0:
                continue  # covered by ZeroOpenInterestExpiryTest
            with self.subTest(expiry=expiry):
                strikes = {c.strike for c in selected}
                self.assertIn(max_pain_for_expiry(self.chain.contracts, expiry), strikes)

    def test_scaling_open_interest_does_not_move_the_result(self):
        """Pain scales linearly in OI, so the argmin must be scale-invariant."""
        selected = [c for c in self.chain.contracts if c.expiry == dt.date(2026, 8, 12)]
        scaled = [
            Contract(c.expiry, c.option_type, c.strike, c.open_interest * 7.5) for c in selected
        ]
        self.assertEqual(max_pain(scaled), max_pain(selected))

    def test_minimum_is_genuinely_minimal(self):
        """No strike may score lower pain than the reported max pain."""
        selected = [c for c in self.chain.contracts if c.expiry == dt.date(2026, 8, 12)]
        best = max_pain(selected)
        best_pain = pain_at(selected, best)
        for c in selected:
            self.assertGreaterEqual(pain_at(selected, c.strike), best_pain)

    def test_ties_resolve_to_lowest_strike(self):
        symmetric = [contract(CALL, 50.0, 0.0), contract(PUT, 60.0, 0.0)]
        symmetric.append(contract(CALL, 55.0, 1.0))
        # Only the 55 call has OI, so pain is 0 at both 50 and 55; lowest wins.
        self.assertEqual(max_pain(symmetric), 50.0)


class DegenerateInputTest(unittest.TestCase):
    """Bad input must raise, never return a plausible-looking number."""

    def test_empty_chain_raises(self):
        with self.assertRaises(ComputationError):
            max_pain([])

    def test_zero_open_interest_raises(self):
        with self.assertRaises(ComputationError):
            max_pain([contract(CALL, 100.0, 0.0), contract(PUT, 110.0, 0.0)])

    def test_unknown_expiry_raises(self):
        with self.assertRaises(ComputationError):
            max_pain_for_expiry([contract(CALL, 100.0, 5)], dt.date(2030, 1, 1))


class ZeroOpenInterestExpiryTest(unittest.TestCase):
    """A real, newly listed expiry with contracts but no open interest.

    NVDA's 2026-08-24 chain had 60 contracts and zero OI on the capture date.
    Max pain is undefined there -- any answer would be an artefact of
    iteration order. OptionCharts drops the expiry entirely (23 expiries to
    CBOE's 24), which is independent confirmation of the same judgement.
    """

    @classmethod
    def setUpClass(cls):
        cls.chain = cboe.parse_chain(load_cboe_payload(), "NVDA")
        cls.empty = dt.date(2026, 8, 24)

    def test_the_expiry_really_is_empty(self):
        selected = [c for c in self.chain.contracts if c.expiry == self.empty]
        self.assertEqual(len(selected), 60)
        self.assertEqual(sum(c.open_interest for c in selected), 0.0)

    def test_optioncharts_also_omits_it(self):
        published = optioncharts.parse_max_pain_by_expiry(load_optioncharts_document())
        self.assertNotIn(self.empty, published)
        self.assertEqual(len(self.chain.expiries()) - len(published), 1)

    def test_computation_refuses_rather_than_guessing(self):
        with self.assertRaises(ComputationError):
            max_pain_for_expiry(self.chain.contracts, self.empty)

    def test_selection_skips_it(self):
        """With 08-10 expired and 08-24 empty, selection must still be sensible."""
        chosen = select_expiry_with_open_interest(self.chain.contracts, CAPTURE_DATE)
        self.assertEqual(chosen, dt.date(2026, 8, 12))

    def test_selection_skips_empty_even_when_it_is_nearest(self):
        contracts = [
            contract(CALL, 100.0, 0.0, expiry=dt.date(2026, 8, 12)),
            contract(PUT, 100.0, 0.0, expiry=dt.date(2026, 8, 12)),
            contract(CALL, 100.0, 25.0, expiry=dt.date(2026, 8, 14)),
        ]
        chosen = select_expiry_with_open_interest(contracts, CAPTURE_DATE)
        self.assertEqual(chosen, dt.date(2026, 8, 14))


class SelectExpiryTest(unittest.TestCase):
    def test_picks_nearest_future(self):
        expiries = [dt.date(2026, 8, 14), dt.date(2026, 8, 12), dt.date(2026, 9, 18)]
        self.assertEqual(select_expiry(expiries, CAPTURE_DATE), dt.date(2026, 8, 12))

    def test_skips_expired(self):
        """The real fixture contains 2026-08-10, already past on the capture date."""
        chain = cboe.parse_chain(load_cboe_payload(), "NVDA")
        self.assertIn(dt.date(2026, 8, 10), chain.expiries())
        self.assertEqual(select_expiry(chain.expiries(), CAPTURE_DATE), dt.date(2026, 8, 12))

    def test_today_counts_as_unexpired(self):
        """A 0DTE contract still trades today, so today is eligible."""
        expiries = [dt.date(2026, 8, 11), dt.date(2026, 8, 12)]
        self.assertEqual(select_expiry(expiries, CAPTURE_DATE), dt.date(2026, 8, 11))

    def test_all_expired_raises(self):
        with self.assertRaises(ComputationError):
            select_expiry([dt.date(2026, 8, 10)], CAPTURE_DATE)


if __name__ == "__main__":
    unittest.main()
