"""HTTP client for fetching Phabricator pages."""

from typing import Optional


class HeraldClient:
    """HTTP client for fetching Herald-related pages from Phabricator."""

    def __init__(
        self,
        base_url: str,
        session_cookie: Optional[str] = None,
        delay: float = 1.0,
        user_agent: str = "HeraldScraper/0.1",
    ) -> None:
        """
        Initialize the Herald client.

        Args:
            base_url: Base URL of the Phabricator instance
            session_cookie: Optional session cookie for authentication
            delay: Delay between requests in seconds (rate limiting)
            user_agent: User-Agent string for requests
        """
        raise NotImplementedError

    def fetch_page(self, url: str) -> str:
        """
        Fetch a page and return its HTML content.

        Args:
            url: Full URL or path to fetch

        Returns:
            HTML content of the page

        Raises:
            AuthenticationError: If authentication fails
            requests.RequestException: If the request fails
        """
        raise NotImplementedError

    def fetch_listing(self) -> str:
        """
        Fetch the Herald rules listing page.

        Returns:
            HTML content of the listing page
        """
        raise NotImplementedError

    def fetch_rule(self, rule_id: str) -> str:
        """
        Fetch a specific Herald rule page.

        Args:
            rule_id: Rule ID (e.g., 'H420')

        Returns:
            HTML content of the rule page
        """
        raise NotImplementedError

    def fetch_project(self, project_slug: str) -> str:
        """
        Fetch a project/group page.

        Args:
            project_slug: Project slug (e.g., 'myproject')

        Returns:
            HTML content of the project page
        """
        raise NotImplementedError

    @classmethod
    def from_environment(cls) -> "HeraldClient":
        """
        Create a HeraldClient from environment variables.

        Reads configuration from:
            - PHABRICATOR_URL: Base URL of the Phabricator instance
            - PHABRICATOR_SESSION_COOKIE: Session cookie for authentication
            - HERALD_SCRAPER_DELAY: Optional delay between requests (default: 1.0)
            - HERALD_SCRAPER_USER_AGENT: Optional custom user agent

        Returns:
            Configured HeraldClient instance
        """
        raise NotImplementedError
