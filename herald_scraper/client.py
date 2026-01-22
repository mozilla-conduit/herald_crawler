"""HTTP client for fetching Phabricator pages."""

import os
import time
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests

from herald_scraper.exceptions import AuthenticationError


class HeraldClient:
    """HTTP client for fetching Herald-related pages from Phabricator."""

    def __init__(
        self,
        base_url: str,
        session_cookie: Optional[str] = None,
        delay: float = 1.0,
        user_agent: str = "HeraldScraper/0.1",
        timeout: float = 30.0,
    ) -> None:
        """
        Initialize the Herald client.

        Args:
            base_url: Base URL of the Phabricator instance
            session_cookie: Optional session cookie for authentication
            delay: Delay between requests in seconds (rate limiting)
            user_agent: User-Agent string for requests
            timeout: Request timeout in seconds (default: 30.0)

        Raises:
            ValueError: If base_url is invalid
        """
        parsed = urlparse(base_url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(
                f"Invalid base_url: '{base_url}'. "
                f"Must be a complete URL (e.g., https://phabricator.example.com)"
            )

        self.base_url = base_url.rstrip("/")
        self.session_cookie = session_cookie
        self.delay = delay
        self.user_agent = user_agent
        self.timeout = timeout
        self._last_request_time: Optional[float] = None
        self._session = requests.Session()
        self._session.headers["User-Agent"] = user_agent
        if session_cookie:
            self._session.cookies.set("phsid", session_cookie.replace("phsid=", ""))

    def _rate_limit(self) -> None:
        """Apply rate limiting between requests."""
        if self._last_request_time is not None:
            elapsed = time.time() - self._last_request_time
            if elapsed < self.delay:
                time.sleep(self.delay - elapsed)
        self._last_request_time = time.time()

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
        self._rate_limit()

        if url.startswith("/"):
            full_url = f"{self.base_url}{url}"
        else:
            full_url = urljoin(self.base_url, url)

        response = self._session.get(full_url, allow_redirects=False, timeout=self.timeout)

        if response.status_code == 302:
            location = response.headers.get("Location", "")
            if "/auth/" in location:
                raise AuthenticationError(
                    f"Authentication required. Redirect to: {location}"
                )

        response.raise_for_status()
        return response.text

    def fetch_listing(self) -> str:
        """
        Fetch the Herald rules listing page.

        Returns:
            HTML content of the listing page
        """
        return self.fetch_page("/herald/")

    def fetch_rule(self, rule_id: str) -> str:
        """
        Fetch a specific Herald rule page.

        Args:
            rule_id: Rule ID (e.g., 'H420')

        Returns:
            HTML content of the rule page
        """
        return self.fetch_page(f"/{rule_id}")

    def fetch_project(self, project_slug: str) -> str:
        """
        Fetch a project/group page.

        Args:
            project_slug: Project slug (e.g., 'myproject')

        Returns:
            HTML content of the project page
        """
        return self.fetch_page(f"/tag/{project_slug}/")

    @classmethod
    def from_environment(cls) -> "HeraldClient":
        """
        Create a HeraldClient from environment variables.

        Reads configuration from:
            - PHABRICATOR_URL: Base URL of the Phabricator instance (required)
            - PHABRICATOR_SESSION_COOKIE: Session cookie for authentication
            - HERALD_SCRAPER_DELAY: Optional delay between requests (default: 1.0)
            - HERALD_SCRAPER_USER_AGENT: Optional custom user agent
            - HERALD_SCRAPER_TIMEOUT: Optional request timeout (default: 30.0)

        Returns:
            Configured HeraldClient instance

        Raises:
            ValueError: If required environment variables are missing or invalid
        """
        base_url = os.environ.get("PHABRICATOR_URL")
        if not base_url:
            raise ValueError(
                "PHABRICATOR_URL environment variable is required. "
                "Set it to your Phabricator instance URL "
                "(e.g., https://phabricator.services.mozilla.com)"
            )

        session_cookie = os.environ.get("PHABRICATOR_SESSION_COOKIE")

        try:
            delay = float(os.environ.get("HERALD_SCRAPER_DELAY", "1.0"))
        except ValueError as e:
            raise ValueError(f"HERALD_SCRAPER_DELAY must be a number: {e}") from e

        try:
            timeout = float(os.environ.get("HERALD_SCRAPER_TIMEOUT", "30.0"))
        except ValueError as e:
            raise ValueError(f"HERALD_SCRAPER_TIMEOUT must be a number: {e}") from e

        user_agent = os.environ.get("HERALD_SCRAPER_USER_AGENT", "HeraldScraper/0.1")

        return cls(
            base_url=base_url,
            session_cookie=session_cookie,
            delay=delay,
            user_agent=user_agent,
            timeout=timeout,
        )
