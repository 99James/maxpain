"""Reconciliation and the failure paths that must never fabricate a number."""

from __future__ import annotations

import datetime as dt
import unittest
from dataclasses import replace
from unittest import mock

from maxpain import http
from maxpain.models import Status
from maxpain.retrieve import reconcile, retrieve, session_staleness
from maxpain.sources import cboe, nasdaq
from tests.fixtures import CAPTURE_DATE, load_cboe_payload

CBOE_CHAIN = cboe.parse_chain(load_cboe_payload(), "NVDA")
# The same snapshot presented as the primary source, so every assertion below
# about prices and max pain holds whichever source path is under test.
CHAIN = replace(CBOE_CHAIN, source=nasdaq.SOURCE_NAME)


def _stub(outcome):
    """A fetch_chain stand-in that returns a chain or raises an exception."""
    if isinstance(outcome, Exception):
        return mock.Mock(side_effect=outcome)
    return mock.Mock(return_value=outcome)


def run(**kwargs):
    """Retrieve NVDA against fixture chains, with OptionCharts stubbed.

    `chain` is what Nasdaq returns and `fallback` what CBOE returns; either may
    be an exception. The clock defaults to the capture instant, so the fixture
    is always the latest session rather than going stale as time moves on.
    """
    published = kwargs.pop("published", 212.5)
    primary = _stub(kwargs.pop("chain", CHAIN))
    fallback = _stub(kwargs.pop("fallback", CBOE_CHAIN))
    kwargs.setdefault("now", CHAIN.as_of)
    kwargs.setdefault("today", CAPTURE_DATE)
    with mock.patch.object(nasdaq, "fetch_chain", primary), mock.patch.object(
        cboe, "fetch_chain", fallback
    ), mock.patch("maxpain.retrieve.optioncharts.fetch_max_pain", return_value=published):
        result = retrieve("NVDA", **kwargs)
    run.fallback_calls = fallback.call_count
    return result


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
        with mock.patch.object(nasdaq, "fetch_chain", return_value=CHAIN), mock.patch(
            "maxpain.retrieve.optioncharts.fetch_max_pain"
        ) as fetch:
            result = retrieve("NVDA", today=CAPTURE_DATE, verify=False, now=CHAIN.as_of)
        fetch.assert_not_called()
        self.assertIs(result.status, Status.UNVERIFIED)


class FailurePathTest(unittest.TestCase):
    """Every failure yields a status and no numbers -- never a zero."""

    def _fails_with(self, exc):
        return run(chain=exc, fallback=exc)

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
            session=CHAIN.session,
            source=CHAIN.source,
        )
        result = run(chain=empty)
        self.assertIs(result.status, Status.NO_OPTIONS)
        self.assertEqual(result.price, 10.0)
        self.assertIsNone(result.max_pain)


class SourceSelectionTest(unittest.TestCase):
    """Nasdaq is current; CBOE is only a fallback and never beats fresher data."""

    # Fixture last traded Mon 2026-08-10. Tue 2026-08-11 11:00 EDT = 15:00 UTC.
    NEXT_SESSION_UNDERWAY = dt.datetime(2026, 8, 11, 15, 0)

    def test_fresh_primary_is_used_without_touching_the_fallback(self):
        result = run()
        self.assertEqual(result.source, "Nasdaq")
        self.assertEqual(run.fallback_calls, 0)
        self.assertNotIn("fallback", result.detail)

    def test_falls_back_to_cboe_when_nasdaq_fails(self):
        result = run(chain=http.HttpError("timeout"))
        self.assertIs(result.status, Status.OK)
        self.assertEqual(result.source, "CBOE")
        self.assertIn("via CBOE fallback", result.detail)
        self.assertIn("timeout", result.detail)

    def test_falls_back_when_nasdaq_does_not_know_the_ticker(self):
        result = run(chain=http.NotFoundError("Symbol not exists"))
        self.assertEqual(result.source, "CBOE")

    def test_fresher_fallback_beats_stale_primary(self):
        old = replace(CHAIN, session=dt.date(2026, 8, 7))
        result = run(chain=old, now=self.NEXT_SESSION_UNDERWAY - dt.timedelta(hours=3))
        self.assertEqual(result.source, "CBOE")
        self.assertIn("older session", result.detail)

    def test_stale_fallback_never_replaces_fresher_primary(self):
        behind = replace(CBOE_CHAIN, session=dt.date(2026, 8, 7), price=1.0)
        today = replace(CHAIN, session=dt.date(2026, 8, 11))
        result = run(chain=today, fallback=behind, now=self.NEXT_SESSION_UNDERWAY)
        self.assertEqual(result.source, "Nasdaq")
        self.assertAlmostEqual(result.price, 217.9915)

    def test_both_behind_is_stale_with_the_fresher_one_reported(self):
        result = run(fallback=http.HttpError("down"), now=self.NEXT_SESSION_UNDERWAY)
        self.assertIs(result.status, Status.STALE)
        self.assertEqual(result.source, "Nasdaq")

    def test_errors_from_both_sources_are_reported(self):
        result = run(chain=http.HttpError("nasdaq down"), fallback=cboe.SourceError("bad json"))
        self.assertIs(result.status, Status.FETCH_ERROR)
        self.assertIn("nasdaq down", result.detail)
        self.assertIn("bad json", result.detail)

    def test_unknown_to_one_source_but_failing_on_other_is_a_fetch_error(self):
        """Only call a ticker nonexistent when every source says so."""
        result = run(chain=http.NotFoundError("x"), fallback=http.HttpError("timeout"))
        self.assertIs(result.status, Status.FETCH_ERROR)


class SessionStalenessTest(unittest.TestCase):
    """The failure that motivated this check: CBOE republished overnight, so
    the snapshot stamp looked current, while the price was a session behind."""

    NEXT_SESSION_UNDERWAY = SourceSelectionTest.NEXT_SESSION_UNDERWAY

    def test_previous_session_price_is_stale_once_next_session_is_underway(self):
        result = run(fallback=http.HttpError("down"), now=self.NEXT_SESSION_UNDERWAY)
        self.assertIs(result.status, Status.STALE)
        self.assertIn("2026-08-10 session", result.detail)
        self.assertIn("2026-08-11", result.detail)
        # The number is still reported, flagged, never withheld or altered.
        self.assertAlmostEqual(result.price, 217.9915)

    def test_before_the_open_yesterdays_close_is_current(self):
        before_open = dt.datetime(2026, 8, 11, 13, 0)  # 09:00 EDT
        self.assertIs(run(now=before_open).status, Status.OK)

    def test_friday_close_is_current_over_the_weekend(self):
        friday = replace(CHAIN, session=dt.date(2026, 8, 7))
        sunday = dt.datetime(2026, 8, 9, 18, 0)
        self.assertIsNone(session_staleness(friday, sunday))

    def test_holiday_does_not_make_prior_session_stale(self):
        # Labor Day, Mon 2026-09-07, midday: Friday's close is the latest.
        labor_day = dt.datetime(2026, 9, 7, 16, 0)
        friday = replace(CHAIN, session=dt.date(2026, 9, 4))
        self.assertIsNone(session_staleness(friday, labor_day))

    def test_missing_session_cannot_be_proven_fresh(self):
        undated = replace(CHAIN, session=None)
        self.assertIn("unknown", session_staleness(undated, CHAIN.as_of))

    def test_mismatch_note_survives_staleness(self):
        result = run(
            published=999.0, fallback=http.HttpError("down"), now=self.NEXT_SESSION_UNDERWAY
        )
        self.assertIs(result.status, Status.STALE)
        self.assertIn("999", result.detail)

    def test_default_expiry_date_uses_new_york_calendar(self):
        # 2026-08-11 03:44 UTC is still Monday evening in New York, after the
        # close, so Monday's expiry is dead and Wednesday's is next.
        result = run(today=None)
        self.assertEqual(result.expiry, dt.date(2026, 8, 12))


if __name__ == "__main__":
    unittest.main()
