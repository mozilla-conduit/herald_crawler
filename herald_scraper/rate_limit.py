"""Reactive rate-limit handling shared by every HTTP client.

None of the clients pace themselves with a fixed sleep between requests. They
run at full speed and back off only when a response says they must, following
GitHub's documented rules for its REST API:
https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api

Those rules are the strictest of the services we talk to (and GitHub's limits
reach us directly through PMO's ``/whoami/github/`` proxy), so we apply the
same handling everywhere rather than one policy per host.
"""

import logging
import time
from typing import Callable, Optional, TypeVar

import requests

from herald_scraper.exceptions import RateLimitError

logger = logging.getLogger(__name__)

T = TypeVar("T")

# "If you exceed your primary rate limit, you will receive a 403 or 429
# response" — and the same pair for secondary limits.
RATE_LIMIT_STATUS_CODES = (403, 429)

# The documented floor for a rate limit that carries no explicit hint:
# "Otherwise, wait for at least one minute before retrying."
DEFAULT_RATE_LIMIT_WAIT = 60.0

# "throw an error after a specific number of retries" — with the exponential
# growth in rate_limit_backoff_seconds this bounds one call at
# 60+120+240+480s of waiting before it gives up.
MAX_RATE_LIMIT_RETRIES = 4


def rate_limit_wait_seconds(response: requests.Response) -> Optional[float]:
    """How long to wait before retrying a rate-limited response.

    Implements the documented order of precedence:

    1. ``retry-after`` present -> wait that many seconds.
    2. ``x-ratelimit-remaining`` is ``0`` -> wait until ``x-ratelimit-reset``
       (UTC epoch seconds).
    3. Otherwise -> wait at least one minute.

    Returns None when the response is not a rate limit, so the caller fails
    fast instead of retrying. A bare 403 carrying none of the signals above
    is far more likely a rejected credential than a rate limit, and retrying
    that would only spin; a 429 always means rate limited.
    """
    if response.status_code not in RATE_LIMIT_STATUS_CODES:
        return None

    retry_after = _parse_seconds(response.headers.get("retry-after"))
    if retry_after is not None:
        return max(0.0, retry_after)

    if (response.headers.get("x-ratelimit-remaining") or "").strip() == "0":
        reset = _parse_seconds(response.headers.get("x-ratelimit-reset"))
        if reset is None:
            return DEFAULT_RATE_LIMIT_WAIT
        return max(0.0, reset - time.time())

    if response.status_code == 429 or _mentions_rate_limit(response):
        return DEFAULT_RATE_LIMIT_WAIT

    return None


def rate_limit_backoff_seconds(error: RateLimitError, attempt: int) -> float:
    """How long to wait before retry ``attempt`` (0-based) of a rate-limited call.

    An explicit hint from the response is authoritative: we are told not to
    retry before it elapses, and inflating it would only idle longer than
    needed. The "wait at least a minute" fallback is the one that grows, per
    "if your request continues to fail due to a secondary rate limit, wait
    for an exponentially increasing amount of time between retries".
    """
    if error.retry_after is not None:
        return error.retry_after
    return DEFAULT_RATE_LIMIT_WAIT * (2.0**attempt)


def raise_for_rate_limit(response: requests.Response, what: str) -> None:
    """Raise RateLimitError if ``response`` reports a rate limit.

    Callers keep their own handling for every other kind of error response;
    this only splits rate limits out so they can be waited out and retried
    rather than surfacing as a permanent failure.
    """
    wait = rate_limit_wait_seconds(response)
    if wait is not None:
        raise RateLimitError(
            f"Rate limited (HTTP {response.status_code}) while {what}",
            retry_after=wait,
        )


def retry_on_rate_limit(what: str, call: Callable[[], T]) -> T:
    """Run ``call``, waiting out any rate limit it reports.

    Re-raises the RateLimitError once the retries are exhausted, so a
    persistent limit surfaces as an error instead of looking like a
    legitimately empty result. "Continuing to make requests while you are
    rate limited may result in the banning of your integration", so every
    retry is preceded by the full wait the response asked for.
    """
    for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
        try:
            return call()
        except RateLimitError as e:
            if attempt >= MAX_RATE_LIMIT_RETRIES:
                logger.error(f"Giving up {what} after {attempt + 1} rate-limited attempts: {e}")
                raise
            wait = rate_limit_backoff_seconds(e, attempt)
            logger.warning(
                f"Rate limited {what} (attempt {attempt + 1}/{MAX_RATE_LIMIT_RETRIES + 1}); "
                f"waiting {wait:.0f}s before retrying: {e}"
            )
            time.sleep(wait)

    raise AssertionError("unreachable")  # pragma: no cover


def _parse_seconds(value: Optional[str]) -> Optional[float]:
    """Parse a numeric header value, or None if absent or not a number.

    GitHub sends ``retry-after`` as an integer number of seconds rather than
    the HTTP-date the RFC also permits, so anything non-numeric counts as
    "no hint" and falls through to the next signal.
    """
    if value is None:
        return None
    try:
        return float(value.strip())
    except (AttributeError, ValueError):
        return None


def _mentions_rate_limit(response: requests.Response) -> bool:
    """Whether the response body names a rate limit.

    A secondary rate limit is only distinguishable from an ordinary 403 by
    "an error message that indicates that you exceeded a secondary rate
    limit", so fall back to sniffing the body.
    """
    try:
        return "rate limit" in response.text.lower()
    except Exception:  # pragma: no cover - defensive, body may not decode
        return False
