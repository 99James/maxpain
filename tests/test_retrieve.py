"""Reconciliation and the failure paths that must never fabricate a number."""

from __future__ import annotations

import datetime as dt
import unittest
from unittest import mock

from maxpain import http
from maxpain.models import Status
from maxpain.retrieve import reconcile, retrieve
from maxpain.sources import cboe
from tests.fixtures import CAPTURE_DATE, load_cboe_payload

CHAIN = cboe.parse_chain(load_cboe_payload(), "NVDA")


def run(**kwargs):
    """Retrieve NVDA against the fixture chain, with OptionCharts stubbed."""
    published = kwargs.pop("published", 212.5)
    chain = kwargs.pop("chain", CHAIN)
    with mock.patch.object(cboe, "fetch_chain", return_value=chain), mock.patch(
        "maxpain.retrieve.optioncharts.fetch_max_pain", return_value=published
    ):
        return retrieve("NVDA", today=CAPTURE_DATE, **kwargs)


class ReconcileTest(unittest.TestCase):
    def test_agreement(self):
        self.assertEqual(reconcile(212.5, 212.5), (Status.OK, 212.5))

    def test_within_tolerance(self):
        self.assertEqual(reconcile(212.5, 212.505)[0], Status.OK)

    def test_disagreement_is_flagged(self):
        status, published = reconcile(212.5, 215.0)
        self.assertIs(status, Status.MISMATCH)
        self.assertEqual(published, 215.0)

    def test_one_strike_apart_is_never_swallowed(self):
        """The tolerance must be far tighter than the strike ladder."""
        self.assertIs(reconcile(212.5, 213.0)[0], Status.MISMATCH)

    def test_missing_second_opinion(self):
        self.assertEqual(reconcile(212.5, None), (Status.UNVERIFIED, None))


class RetrieveTest(unittest.TestCase):
    def test_verified_result(self):
        result = run()
        self.assertIs(result.status, Status.OK)
        self.assertEqual(result.max_pain, 212.5)
        self.assertEqual(result.expiry, dt.date(2026, 8, 12))
        self.assertAlmostEqual(result.price, 217.9915)
        self.assertEqual(result.verified_against, 212.5)

    def test_mismatch_keeps_our_value_and_reports_theirs(self):
        """We never silently adopt the other source's number."""
        result = run(published=999.0)
        self.assertIs(result.status, Status.MISMATCH)
        self.assertEqual(result.max_pain, 212.5)
        self.assertEqual(result.verified_against, 999.0)
        self.assertIn("999", result.detail)

    def test_unverified_when_optioncharts_is_down(self):
        result = run(published=None)
        self.assertIs(result.status, Status.UNVERIFIED)
        self.assertEqual(result.max_pain, 212.5)
        self.assertIn("unavailable", result.detail)

    def test_no_verify_skips_the_check(self):
        with mock.patch.object(cboe, "fetch_chain", return_value=CHAIN), mock.patch(
            "maxpain.retrieve.optioncharts.fetch_max_pain"
        ) as fetch:
            result = retrieve("NVDA", today=CAPTURE_DATE, verify=False)
        fetch.assert_not_called()
        self.assertIs(result.status, Status.UNVERIFIED)


class FailurePathTest(unittest.TestCase):
    """Every failure yields a status and no numbers -- never a zero."""

    def _fails_with(self, exc):
        with mock.patch.object(cboe, "fetch_chain", side_effect=exc):
            return retrieve("NVDA", today=CAPTURE_DATE)

    def test_unknown_ticker(self):
        result = self._fails_with(http.NotFoundError("403", status=403))
        self.assertIs(result.status, Status.NOT_FOUND)
        self.assertIsNone(result.price)
        self.assertIsNone(result.max_pain)

    def test_network_failure(self):
        result = self._fails_with(http.HttpError("timeout"))
        self.assertIs(result.status, Status.FETCH_ERROR)
        self.assertIsNone(result.price)
        self.assertIsNone(result.max_pain)

    def test_malformed_payload(self):
        result = self._fails_with(cboe.SourceError("bad json"))
        self.assertIs(result.status, Status.FETCH_ERROR)
        self.assertIsNone(result.max_pain)

    def test_retrieve_never_raises(self):
        for exc in [
            http.NotFoundError("x"),
            http.HttpError("x"),
            cboe.SourceError("x"),
        ]:
            with self.subTest(exc=type(exc).__name__):
                self._fails_with(exc)  # must not propagate

    def test_chain_without_open_interest_reports_price_only(self):
        """Price is still real, so report it; max pain stays None."""
        empty = CHAIN.__class__(
            ticker="AAA",
            price=10.0,
            as_of=CHAIN.as_of,
            contracts=tuple(c for c in CHAIN.contracts if c.expiry == dt.date(2026, 8, 24)),
        )
        result = run(chain=empty)
        self.assertIs(result.status, Status.NO_OPTIONS)
        self.assertEqual(result.price, 10.0)
        self.assertIsNone(result.max_pain)


class StalenessTest(unittest.TestCase):
    def test_old_snapshot_is_flagged(self):
        later = CHAIN.as_of + dt.timedelta(hours=3)
        result = run(max_age_minutes=30, now=later)
        self.assertIs(result.status, Status.STALE)
        self.assertIn("min old", result.detail)

    def test_fresh_snapshot_is_not_flagged(self):
        soon = CHAIN.as_of + dt.timedelta(minutes=5)
        self.assertIs(run(max_age_minutes=30, now=soon).status, Status.OK)

    def test_disabled_by_default(self):
        ancient = CHAIN.as_of + dt.timedelta(days=30)
        self.assertIs(run(now=ancient).status, Status.OK)

    def test_staleness_overrides_verification(self):
        """Correctly computed but too old to act on is still not OK."""
        later = CHAIN.as_of + dt.timedelta(hours=3)
        result = run(published=212.5, max_age_minutes=30, now=later)
        self.assertIs(result.status, Status.STALE)


if __name__ == "__main__":
    unittest.main()
