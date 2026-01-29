"""Conduit API client for Phabricator."""

import logging
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)


class ConduitError(Exception):
    """Raised when a Conduit API call fails."""

    def __init__(self, message: str, error_code: Optional[str] = None) -> None:
        super().__init__(message)
        self.error_code = error_code


class ConduitClient:
    """Client for Phabricator's Conduit API.

    Provides access to Conduit API methods for fetching project and user data.
    Uses API token authentication.

    Example:
        client = ConduitClient(
            base_url="https://phabricator.example.com",
            api_token="api-xxxxx"
        )
        projects = client.project_search(slugs=["my-project"])
    """

    def __init__(
        self,
        base_url: str,
        api_token: str,
        delay: float = 1.0,
        timeout: float = 30.0,
        user_agent: str = "HeraldScraper/0.1",
    ) -> None:
        """
        Initialize the Conduit client.

        Args:
            base_url: Base URL of the Phabricator instance
            api_token: Conduit API token (from Settings -> Conduit API Tokens)
            delay: Delay between requests in seconds (rate limiting)
            timeout: Request timeout in seconds
            user_agent: User-Agent string for requests

        Raises:
            ValueError: If base_url is invalid or api_token is empty
        """
        parsed = urlparse(base_url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(
                f"Invalid base_url: '{base_url}'. "
                f"Must be a complete URL (e.g., https://phabricator.example.com)"
            )

        if not api_token:
            raise ValueError("api_token is required")

        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.delay = delay
        self.timeout = timeout
        self._last_request_time: Optional[float] = None
        self._session = requests.Session()
        self._session.headers["User-Agent"] = user_agent

    def _rate_limit(self) -> None:
        """Apply rate limiting between requests."""
        if self._last_request_time is not None:
            elapsed = time.time() - self._last_request_time
            if elapsed < self.delay:
                time.sleep(self.delay - elapsed)
        self._last_request_time = time.time()

    def call_method(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Call a Conduit API method.

        Args:
            method: API method name (e.g., 'project.search')
            params: Optional parameters for the API call

        Returns:
            The 'result' field from the API response

        Raises:
            ConduitError: If the API returns an error
            requests.RequestException: If the HTTP request fails
        """
        raise NotImplementedError("call_method not yet implemented")

    def project_search(
        self,
        slugs: Optional[List[str]] = None,
        phids: Optional[List[str]] = None,
        attachments: Optional[Dict[str, bool]] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Search for projects by slug or PHID.

        Handles pagination automatically to return all matching results.

        Args:
            slugs: List of project slugs to search for
            phids: List of project PHIDs to search for
            attachments: Attachments to include (e.g., {"members": True})
            limit: Maximum results per page (default 100, max 100)

        Returns:
            List of project data dictionaries, each containing:
                - phid: Project PHID
                - fields: {name, slug, ...}
                - attachments: {members: {members: [PHIDs]}} if requested

        Raises:
            ConduitError: If the API returns an error
            ValueError: If neither slugs nor phids is provided
        """
        raise NotImplementedError("project_search not yet implemented")

    def user_search(
        self,
        phids: Optional[List[str]] = None,
        usernames: Optional[List[str]] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Search for users by PHID or username.

        Handles pagination automatically to return all matching results.

        Args:
            phids: List of user PHIDs to search for
            usernames: List of usernames to search for
            limit: Maximum results per page (default 100, max 100)

        Returns:
            List of user data dictionaries, each containing:
                - phid: User PHID
                - fields: {username, realName, ...}

        Raises:
            ConduitError: If the API returns an error
            ValueError: If neither phids nor usernames is provided
        """
        raise NotImplementedError("user_search not yet implemented")
