"""Custom exceptions for Herald scraper."""


class HeraldScraperError(Exception):
    """Base exception for Herald scraper errors."""

    pass


class AuthenticationError(HeraldScraperError):
    """Raised when authentication to Phabricator fails."""

    pass


class RuleParseError(HeraldScraperError):
    """Raised when a rule page cannot be parsed."""

    def __init__(self, rule_id: str, message: str) -> None:
        self.rule_id = rule_id
        self.message = message
        super().__init__(f"Failed to parse rule {rule_id}: {message}")
