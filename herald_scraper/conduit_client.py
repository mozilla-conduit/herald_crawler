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

    def _flatten_params(self, params: Any, data: Dict[str, str], prefix: str) -> None:
        """
        Flatten nested parameters into Phabricator's expected format.

        Phabricator expects: constraints[slugs][0]=value

        Args:
            params: The parameters to flatten (dict, list, or scalar)
            data: The output dictionary to add flattened params to
            prefix: The current key prefix
        """
        if isinstance(params, dict):
            for key, value in params.items():
                new_prefix = f"{prefix}[{key}]" if prefix else key
                self._flatten_params(value, data, new_prefix)
        elif isinstance(params, list):
            for i, value in enumerate(params):
                new_prefix = f"{prefix}[{i}]"
                self._flatten_params(value, data, new_prefix)
        else:
            # Convert booleans to strings Phabricator expects
            if isinstance(params, bool):
                data[prefix] = "true" if params else "false"
            else:
                data[prefix] = str(params) if params is not None else ""

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
        self._rate_limit()

        url = f"{self.base_url}/api/{method}"

        # Build form data with API token and flattened params
        data: Dict[str, str] = {"api.token": self.api_token}
        if params:
            self._flatten_params(params, data, "")

        logger.debug(f"Calling Conduit method: {method}")
        response = self._session.post(url, data=data, timeout=self.timeout)
        response.raise_for_status()

        result: Dict[str, Any] = response.json()

        # Check for API errors
        if result.get("error_code"):
            raise ConduitError(
                message=result.get("error_info", "Unknown error"),
                error_code=result.get("error_code"),
            )

        api_result: Dict[str, Any] = result.get("result", {})
        return api_result

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
        if not slugs and not phids:
            raise ValueError("Either slugs or phids must be provided")

        params: Dict[str, Any] = {"limit": limit}

        constraints: Dict[str, Any] = {}
        if slugs:
            constraints["slugs"] = slugs
        if phids:
            constraints["phids"] = phids

        params["constraints"] = constraints

        if attachments:
            params["attachments"] = attachments

        all_results: List[Dict[str, Any]] = []
        after_cursor: Optional[str] = None

        while True:
            if after_cursor:
                params["after"] = after_cursor

            result = self.call_method("project.search", params)
            data = result.get("data", [])
            all_results.extend(data)

            # Check for pagination
            cursor = result.get("cursor", {})
            after_cursor = cursor.get("after")

            if not after_cursor:
                break

        logger.debug(f"project_search returned {len(all_results)} projects")
        return all_results

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
        if not phids and not usernames:
            raise ValueError("Either phids or usernames must be provided")

        params: Dict[str, Any] = {"limit": limit}

        constraints: Dict[str, Any] = {}
        if phids:
            constraints["phids"] = phids
        if usernames:
            constraints["usernames"] = usernames

        params["constraints"] = constraints

        all_results: List[Dict[str, Any]] = []
        after_cursor: Optional[str] = None

        while True:
            if after_cursor:
                params["after"] = after_cursor

            result = self.call_method("user.search", params)
            data = result.get("data", [])
            all_results.extend(data)

            # Check for pagination
            cursor = result.get("cursor", {})
            after_cursor = cursor.get("after")

            if not after_cursor:
                break

        logger.debug(f"user_search returned {len(all_results)} users")
        return all_results

    def bugzilla_account_search(
        self,
        ids: Optional[List[str]] = None,
        phids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Look up Bugzilla accounts linked to Phabricator users.

        Unlike most Conduit methods, this one accepts ``ids`` and ``phids``
        as top-level list parameters (not inside a ``constraints`` object)
        and returns a flat list rather than a paginated ``data`` envelope.

        Args:
            ids: Bugzilla account ids (numeric strings)
            phids: Phabricator user PHIDs

        Returns:
            List of ``{"id": <bugzilla-id>, "phid": <phab-phid>}`` dicts.
            Empty if the user has no Bugzilla account linked.

        Raises:
            ConduitError: If the API returns an error
            ValueError: If neither ids nor phids is provided
        """
        if not ids and not phids:
            raise ValueError("Either ids or phids must be provided")

        params: Dict[str, Any] = {}
        if ids:
            params["ids"] = ids
        if phids:
            params["phids"] = phids

        # Endpoint returns `{"result": [...]}` (a flat list), not the
        # standard `{"result": {"data": [...]}}` envelope that call_method
        # is typed for. Cast through Any so mypy permits the runtime shape.
        raw: Any = self.call_method("bugzilla.account.search", params)
        if isinstance(raw, list):
            return list(raw)
        return []
