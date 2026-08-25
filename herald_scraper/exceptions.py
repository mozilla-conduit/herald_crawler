"""Custom exceptions for Herald scraper."""

from typing import Optional


class HeraldScraperError(Exception):
    """Base exception for Herald scraper errors."""

    pass


class RateLimitError(HeraldScraperError):
    """Raised when an upstream API says we exceeded a rate limit.

    ``retry_after`` is how long the response told us to wait, in seconds,
    already resolved from whichever signal the response carried (the
    ``retry-after`` header, or ``x-ratelimit-reset`` when
    ``x-ratelimit-remaining`` is ``0``). It is None when the response gave
    no usable hint, leaving the wait up to the caller.
    """

    def __init__(self, message: str, retry_after: Optional[float] = None) -> None:
        self.retry_after = retry_after
        super().__init__(message)


class AuthenticationError(HeraldScraperError):
    """Raised when authentication to Phabricator fails."""

    pass


class RuleParseError(HeraldScraperError):
    """Raised when a rule page cannot be parsed."""

    def __init__(self, rule_id: str, message: str) -> None:
        self.rule_id = rule_id
        self.message = message
        super().__init__(f"Failed to parse rule {rule_id}: {message}")
