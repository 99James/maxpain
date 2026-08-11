"""Minimal HTTP helper built on the standard library.

No third-party dependencies: this container has no pip, and the job (GET a
URL, retry a bit, hand back bytes) does not justify one.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request

# Both endpoints are ordinary public web URLs; CBOE's CDN and OptionCharts
# both reject the default Python agent string.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

DEFAULT_TIMEOUT = 20.0
DEFAULT_RETRIES = 2
_BACKOFF_BASE = 0.5


class HttpError(Exception):
    """Any failure to retrieve a URL."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class NotFoundError(HttpError):
    """The upstream says this symbol does not exist.

    CBOE answers 403 (not 404) for unknown tickers, so both are treated as a
    definitive "no such symbol" and are never retried.
    """


def _is_retryable(status: int | None) -> bool:
    # 4xx means the request itself was wrong -- repeating it verbatim cannot
    # help, and hammering a public endpoint on a bad symbol is rude.
    return status is None or status >= 500 or status == 429


def get(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    sleep=time.sleep,
) -> bytes:
    """GET `url`, retrying transient failures with exponential backoff.

    Raises NotFoundError for 403/404 and HttpError for everything else.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})

    last_error: HttpError | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404):
                raise NotFoundError(f"{url} returned {exc.code}", status=exc.code) from exc
            last_error = HttpError(f"{url} returned {exc.code}", status=exc.code)
            if not _is_retryable(exc.code):
                raise last_error from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = HttpError(f"{url} failed: {exc}")

        if attempt < retries:
            sleep(_BACKOFF_BASE * (2**attempt))

    assert last_error is not None
    raise last_error
