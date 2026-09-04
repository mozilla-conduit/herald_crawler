"""Tests for the shared rate-limit handling.

The rules under test come from GitHub's REST API documentation:
https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api
"""

from typing import Dict, Optional
from unittest.mock import patch

import pytest
import requests

from herald_scraper.exceptions import RateLimitError
from herald_scraper.rate_limit import (
    DEFAULT_RATE_LIMIT_WAIT,
    MAX_RATE_LIMIT_RETRIES,
    raise_for_rate_limit,
    rate_limit_backoff_seconds,
    rate_limit_wait_seconds,
    retry_on_rate_limit,
)


def response(
    status_code: int, headers: Optional[Dict[str, str]] = None, body: str = ""
) -> requests.Response:
    """Build a real Response so header lookups are case-insensitive."""
    r = requests.Response()
    r.status_code = status_code
    r.headers.update(headers or {})
    r._content = body.encode()
    return r


class TestRateLimitWaitSeconds:
    """Which responses count as rate limits, and for how long."""

    @pytest.mark.parametrize("status_code", [200, 301, 404, 418, 500, 503])
    def test_non_rate_limit_statuses_are_ignored(self, status_code: int) -> None:
        assert rate_limit_wait_seconds(response(status_code)) is None

    @pytest.mark.parametrize("status_code", [403, 429])
    def test_retry_after_wins(self, status_code: int) -> None:
        """"If the retry-after response header is present" it takes precedence."""
        r = response(
            status_code,
            {"retry-after": "42", "x-ratelimit-remaining": "0", "x-ratelimit-reset": "1"},
        )
        assert rate_limit_wait_seconds(r) == 42.0

    def test_retry_after_is_case_insensitive(self) -> None:
        assert rate_limit_wait_seconds(response(429, {"Retry-After": "12"})) == 12.0

    def test_negative_retry_after_clamps_to_zero(self) -> None:
        assert rate_limit_wait_seconds(response(429, {"retry-after": "-5"})) == 0.0

    def test_non_numeric_retry_after_falls_through(self) -> None:
        """An HTTP-date retry-after is not a number, so the next signal applies."""
        r = response(
            403,
            {
                "retry-after": "Wed, 21 Oct 2015 07:28:00 GMT",
                "x-ratelimit-remaining": "0",
                "x-ratelimit-reset": "2000000060",
            },
        )
        with patch("herald_scraper.rate_limit.time.time", return_value=2000000000.0):
            assert rate_limit_wait_seconds(r) == 60.0

    @pytest.mark.parametrize("status_code", [403, 429])
    def test_exhausted_limit_waits_until_reset(self, status_code: int) -> None:
        """remaining==0 -> wait until x-ratelimit-reset, in UTC epoch seconds."""
        r = response(
            status_code,
            {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "2000000030"},
        )
        with patch("herald_scraper.rate_limit.time.time", return_value=2000000000.0):
            assert rate_limit_wait_seconds(r) == 30.0

    def test_past_reset_clamps_to_zero(self) -> None:
        r = response(429, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1"})
        with patch("herald_scraper.rate_limit.time.time", return_value=2000000000.0):
            assert rate_limit_wait_seconds(r) == 0.0

    def test_exhausted_limit_without_reset_uses_default(self) -> None:
        r = response(429, {"x-ratelimit-remaining": "0"})
        assert rate_limit_wait_seconds(r) == DEFAULT_RATE_LIMIT_WAIT

    def test_remaining_above_zero_is_not_a_primary_limit(self) -> None:
        """A 403 with quota left is a permission problem, not a rate limit."""
        r = response(403, {"x-ratelimit-remaining": "57", "x-ratelimit-limit": "60"})
        assert rate_limit_wait_seconds(r) is None

    def test_bare_429_uses_default_wait(self) -> None:
        """429 is unambiguous even with no headers at all."""
        assert rate_limit_wait_seconds(response(429)) == DEFAULT_RATE_LIMIT_WAIT

    def test_bare_403_is_not_a_rate_limit(self) -> None:
        """Retrying a rejected credential would spin forever, so fail fast."""
        assert rate_limit_wait_seconds(response(403, body="Forbidden")) is None

    def test_403_naming_a_secondary_limit_is_a_rate_limit(self) -> None:
        """Secondary limits are only identifiable from the error message."""
        r = response(403, body="You have exceeded a secondary rate limit.")
        assert rate_limit_wait_seconds(r) == DEFAULT_RATE_LIMIT_WAIT


class TestRateLimitBackoffSeconds:
    """How long each successive retry waits."""

    def test_explicit_hint_is_used_verbatim(self) -> None:
        """An explicit hint is authoritative and must not be inflated."""
        error = RateLimitError("limited", retry_after=45.0)
        assert [rate_limit_backoff_seconds(error, n) for n in range(3)] == [45.0, 45.0, 45.0]

    def test_zero_hint_is_honoured_not_replaced(self) -> None:
        """A reset already in the past means retry now, not in a minute."""
        assert rate_limit_backoff_seconds(RateLimitError("limited", retry_after=0.0), 0) == 0.0

    def test_hintless_limit_backs_off_exponentially(self) -> None:
        error = RateLimitError("limited")
        waits = [rate_limit_backoff_seconds(error, n) for n in range(4)]
        assert waits == [60.0, 120.0, 240.0, 480.0]


class TestRaiseForRateLimit:
    """Splitting rate limits out from other error responses."""

    def test_passes_through_non_rate_limits(self) -> None:
        raise_for_rate_limit(response(404), "fetching a page")

    def test_raises_with_the_computed_wait(self) -> None:
        with pytest.raises(RateLimitError) as excinfo:
            raise_for_rate_limit(response(429, {"retry-after": "9"}), "fetching a page")

        assert excinfo.value.retry_after == 9.0
        assert "fetching a page" in str(excinfo.value)
        assert "429" in str(excinfo.value)


class TestRetryOnRateLimit:
    """The retry loop shared by the Phabricator, Conduit and STMO clients."""

    def test_returns_immediately_when_not_limited(self) -> None:
        with patch("herald_scraper.rate_limit.time.sleep") as sleep:
            assert retry_on_rate_limit("working", lambda: "done") == "done"

        sleep.assert_not_called()

    def test_waits_the_requested_time_then_succeeds(self) -> None:
        calls = []

        def call() -> str:
            calls.append(1)
            if len(calls) == 1:
                raise RateLimitError("limited", retry_after=15.0)
            return "done"

        with patch("herald_scraper.rate_limit.time.sleep") as sleep:
            assert retry_on_rate_limit("working", call) == "done"

        sleep.assert_called_once_with(15.0)
        assert len(calls) == 2

    def test_reraises_after_exhausting_retries(self) -> None:
        """A persistent limit must surface, not masquerade as an empty result."""
        calls = []

        def call() -> str:
            calls.append(1)
            raise RateLimitError("limited", retry_after=1.0)

        with patch("herald_scraper.rate_limit.time.sleep") as sleep:
            with pytest.raises(RateLimitError):
                retry_on_rate_limit("working", call)

        assert len(calls) == MAX_RATE_LIMIT_RETRIES + 1
        assert sleep.call_count == MAX_RATE_LIMIT_RETRIES

    def test_other_errors_are_not_retried(self) -> None:
        calls = []

        def call() -> str:
            calls.append(1)
            raise requests.HTTPError("boom")

        with pytest.raises(requests.HTTPError):
            retry_on_rate_limit("working", call)

        assert len(calls) == 1
