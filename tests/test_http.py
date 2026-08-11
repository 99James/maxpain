"""HTTP retry and error-classification behaviour, with urlopen stubbed out."""

from __future__ import annotations

import unittest
import urllib.error
from unittest import mock

from maxpain import http


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x", code, "err", {}, None)


class _Response:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class GetTest(unittest.TestCase):
    def test_returns_body(self):
        with mock.patch("urllib.request.urlopen", return_value=_Response(b"hello")):
            self.assertEqual(http.get("http://x"), b"hello")

    def test_403_raises_not_found_without_retrying(self):
        """CBOE answers 403 for unknown tickers; retrying is pointless and rude."""
        opener = mock.Mock(side_effect=http_error(403))
        with mock.patch("urllib.request.urlopen", opener):
            with self.assertRaises(http.NotFoundError):
                http.get("http://x", sleep=lambda _: None)
        self.assertEqual(opener.call_count, 1)

    def test_404_also_raises_not_found(self):
        with mock.patch("urllib.request.urlopen", mock.Mock(side_effect=http_error(404))):
            with self.assertRaises(http.NotFoundError):
                http.get("http://x", sleep=lambda _: None)

    def test_400_is_not_retried(self):
        opener = mock.Mock(side_effect=http_error(400))
        with mock.patch("urllib.request.urlopen", opener):
            with self.assertRaises(http.HttpError) as caught:
                http.get("http://x", sleep=lambda _: None)
        self.assertNotIsInstance(caught.exception, http.NotFoundError)
        self.assertEqual(opener.call_count, 1)

    def test_500_is_retried_then_fails(self):
        opener = mock.Mock(side_effect=http_error(500))
        with mock.patch("urllib.request.urlopen", opener):
            with self.assertRaises(http.HttpError):
                http.get("http://x", retries=2, sleep=lambda _: None)
        self.assertEqual(opener.call_count, 3)

    def test_429_is_retried(self):
        opener = mock.Mock(side_effect=http_error(429))
        with mock.patch("urllib.request.urlopen", opener):
            with self.assertRaises(http.HttpError):
                http.get("http://x", retries=1, sleep=lambda _: None)
        self.assertEqual(opener.call_count, 2)

    def test_transient_failure_then_success(self):
        opener = mock.Mock(side_effect=[urllib.error.URLError("boom"), _Response(b"ok")])
        with mock.patch("urllib.request.urlopen", opener):
            self.assertEqual(http.get("http://x", sleep=lambda _: None), b"ok")
        self.assertEqual(opener.call_count, 2)

    def test_backoff_grows(self):
        delays: list[float] = []
        with mock.patch("urllib.request.urlopen", mock.Mock(side_effect=http_error(500))):
            with self.assertRaises(http.HttpError):
                http.get("http://x", retries=3, sleep=delays.append)
        self.assertEqual(delays, [0.5, 1.0, 2.0])

    def test_sends_browser_user_agent(self):
        """Both upstreams reject the default Python agent string."""
        captured = {}

        def fake(request, timeout=None):
            captured["ua"] = request.get_header("User-agent")
            return _Response(b"")

        with mock.patch("urllib.request.urlopen", fake):
            http.get("http://x")
        self.assertIn("Mozilla", captured["ua"])


if __name__ == "__main__":
    unittest.main()
