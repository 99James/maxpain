"""Market calendar: Eastern time and NYSE sessions, checked against known dates."""

from __future__ import annotations

import datetime as dt
import unittest

from maxpain import market


class HolidayTest(unittest.TestCase):
    def test_2026_calendar(self):
        # NYSE's published 2026 closures.
        expected = {
            dt.date(2026, 1, 1), dt.date(2026, 1, 19), dt.date(2026, 2, 16),
            dt.date(2026, 4, 3), dt.date(2026, 5, 25), dt.date(2026, 6, 19),
            dt.date(2026, 7, 3), dt.date(2026, 9, 7), dt.date(2026, 11, 26),
            dt.date(2026, 12, 25),
        }
        self.assertEqual(market.nyse_holidays(2026), expected)

    def test_saturday_new_year_is_not_observed(self):
        # 2022-01-01 was a Saturday; NYSE stayed open on Friday 2021-12-31.
        self.assertTrue(market.is_trading_day(dt.date(2021, 12, 31)))

    def test_good_friday(self):
        self.assertFalse(market.is_trading_day(dt.date(2025, 4, 18)))
        self.assertFalse(market.is_trading_day(dt.date(2027, 3, 26)))

    def test_previous_trading_day_skips_weekends_and_holidays(self):
        # Tue after Labor Day 2026 -> Fri before it.
        self.assertEqual(market.previous_trading_day(dt.date(2026, 9, 8)), dt.date(2026, 9, 4))


class EasternTimeTest(unittest.TestCase):
    def test_summer_is_edt(self):
        self.assertEqual(
            market.to_eastern(dt.datetime(2026, 9, 23, 22, 0)), dt.datetime(2026, 9, 23, 18, 0)
        )

    def test_winter_is_est(self):
        self.assertEqual(
            market.to_eastern(dt.datetime(2026, 1, 15, 15, 0)), dt.datetime(2026, 1, 15, 10, 0)
        )

    def test_dst_boundaries_2026(self):
        # Starts Sun 2026-03-08 07:00 UTC, ends Sun 2026-11-01 06:00 UTC.
        self.assertEqual(market.to_eastern(dt.datetime(2026, 3, 8, 6, 59)).hour, 1)
        self.assertEqual(market.to_eastern(dt.datetime(2026, 3, 8, 7, 0)).hour, 3)
        self.assertEqual(market.to_eastern(dt.datetime(2026, 11, 1, 5, 59)).hour, 1)
        self.assertEqual(market.to_eastern(dt.datetime(2026, 11, 1, 6, 0)).hour, 1)


class SessionTest(unittest.TestCase):
    def test_before_the_open_yesterday_is_latest(self):
        # Wed 2026-09-23 08:00 EDT.
        self.assertEqual(market.latest_session(dt.datetime(2026, 9, 23, 12, 0)), dt.date(2026, 9, 22))

    def test_asian_morning_is_still_new_yorks_evening(self):
        # Thu 2026-09-24 07:00 in Tokyo is Wed 18:00 in New York.
        self.assertEqual(market.latest_session(dt.datetime(2026, 9, 23, 22, 0)), dt.date(2026, 9, 23))

    def test_weekend_points_to_friday(self):
        self.assertEqual(market.latest_session(dt.datetime(2026, 9, 27, 15, 0)), dt.date(2026, 9, 25))


class ExpiryReferenceTest(unittest.TestCase):
    def test_same_day_expiry_is_alive_during_the_session(self):
        self.assertEqual(
            market.expiry_reference_date(dt.datetime(2026, 9, 23, 18, 0)), dt.date(2026, 9, 23)
        )

    def test_rolls_forward_after_the_close(self):
        self.assertEqual(
            market.expiry_reference_date(dt.datetime(2026, 9, 23, 20, 0)), dt.date(2026, 9, 24)
        )


if __name__ == "__main__":
    unittest.main()
