"""HTTP client for fetching Phabricator pages."""

import logging
import os
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests


from herald_scraper.exceptions import AuthenticationError
from herald_scraper.rate_limit import raise_for_rate_limit, retry_on_rate_limit

logger = logging.getLogger(__name__)


class HeraldClient:
    """HTTP client for fetching Herald-related pages from Phabricator."""

    def __init__(
        self,
        base_url: str,
        session_cookie: Optional[str] = None,
        user_agent: str = "HeraldScraper/0.1",
        timeout: float = 30.0,
    ) -> None:
        """
        Initialize the Herald client.

        Requests are not paced. The client runs at full speed and backs off
        only when a response reports a rate limit (see
        ``herald_scraper.rate_limit``).

        Args:
            base_url: Base URL of the Phabricator instance
            session_cookie: Optional session cookie for authentication
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
        self.user_agent = user_agent
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers["User-Agent"] = user_agent
        if session_cookie:
            # Extract domain from base_url for cookie
            # e.g., "phabricator.services.mozilla.com" -> ".services.mozilla.com"
            netloc = parsed.netloc
            if "." in netloc:
                # Use parent domain to allow cookie to work with subdomains
                cookie_domain = "." + netloc.split(".", 1)[1]
            else:
                cookie_domain = netloc
            self._session.cookies.set(
                "phsid",
                session_cookie.replace("phsid=", ""),
                domain=cookie_domain,
            )

    def fetch_page(self, url: str) -> str:
        """
        Fetch a page and return its HTML content.

        A rate-limited response is waited out and retried; see
        ``herald_scraper.rate_limit``.

        Args:
            url: Full URL or path to fetch

        Returns:
            HTML content of the page

        Raises:
            AuthenticationError: If authentication fails
            RateLimitError: If the rate limit persists across every retry
            requests.RequestException: If the request fails
        """
        return retry_on_rate_limit(f"fetching {url}", lambda: self._fetch_page_once(url))

    def _fetch_page_once(self, url: str) -> str:
        """Make a single attempt at fetching a page."""
        if url.startswith("/"):
            full_url = f"{self.base_url}{url}"
        else:
            full_url = urljoin(self.base_url, url)

        response = self._session.get(full_url, allow_redirects=False, timeout=self.timeout)

        # Handle redirects
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location", "")
            if "/auth/" in location:
                raise AuthenticationError(f"Authentication required. Redirect to: {location}")
            # Follow non-auth redirects
            logger.debug(f"Following redirect from {full_url} to {location}")
            if location.startswith("/"):
                location = f"{self.base_url}{location}"
            response = self._session.get(location, timeout=self.timeout)

        raise_for_rate_limit(response, f"fetching {full_url}")
        response.raise_for_status()

        # Check for login page returned with 200 OK (Phabricator sometimes
        # serves login page directly instead of redirecting)
        content: str = response.text
        if "<title>Login</title>" in content:
            raise AuthenticationError(
                f"Authentication required. Login page returned for: {full_url}"
            )

        return content

    def fetch_listing(self) -> str:
        """
        Fetch the Herald rules listing page.

        Uses the explicit "all" query to avoid user's saved query preference
        (authenticated users may default to "Authored" or other filtered views).

        Returns:
            HTML content of the listing page
        """
        return self.fetch_page("/herald/query/all/")

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

    def fetch_project_members(self, project_id: str) -> str:
        """
        Fetch a project's members page.

        Args:
            project_id: Numeric project ID (e.g., '171')

        Returns:
            HTML content of the project members page
        """
        return self.fetch_page(f"/project/members/{project_id}/")

    @classmethod
    def from_environment(cls) -> "HeraldClient":
        """
        Create a HeraldClient from environment variables.

        Reads configuration from:
            - PHABRICATOR_URL: Base URL of the Phabricator instance (required)
            - PHABRICATOR_SESSION_COOKIE: Session cookie for authentication
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
            timeout = float(os.environ.get("HERALD_SCRAPER_TIMEOUT", "30.0"))
        except ValueError as e:
            raise ValueError(f"HERALD_SCRAPER_TIMEOUT must be a number: {e}") from e

        user_agent = os.environ.get("HERALD_SCRAPER_USER_AGENT", "HeraldScraper/0.1")

        return cls(
            base_url=base_url,
            session_cookie=session_cookie,
            user_agent=user_agent,
            timeout=timeout,
        )
