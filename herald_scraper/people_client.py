"""Client for Mozilla People Directory API."""

import logging
import time
from typing import NamedTuple, Optional

import requests


class GitHubResolution(NamedTuple):
    """Result of resolving a Phabricator username to GitHub."""

    username: Optional[str]
    user_id: Optional[int]


logger = logging.getLogger(__name__)

PMO_GRAPHQL_URL = "https://people.mozilla.org/api/v4/graphql"
PMO_GITHUB_USERNAME_URL = "https://people.mozilla.org/whoami/github/username/{github_id}"
PMO_SEARCH_SIMPLE_URL = "https://people.mozilla.org/api/v4/search/simple/"

GITHUB_ID_QUERY = """
query GetGitHubId($username: String) {
  profile(username: $username) {
    identities {
      githubIdV3 { value }
    }
  }
}
"""


class PeopleDirectoryClient:
    """Client for Mozilla People Directory API.

    Resolves Phabricator usernames to GitHub usernames via a two-step process:
    1. GraphQL API to get GitHub ID from Phabricator username
    2. REST API to get GitHub username from GitHub ID
    """

    def __init__(self, cookie: str, delay: float = 0.5) -> None:
        """Initialize the People Directory client.

        Args:
            cookie: pmo-access cookie value for authentication
            delay: Delay between requests in seconds (rate limiting)
        """
        self.delay = delay
        self._session = requests.Session()
        self._session.cookies.set("pmo-access", cookie, domain=".mozilla.org")
        self._session.headers["User-Agent"] = "HeraldScraper/0.1"

    def get_github_id(self, username: str) -> dict:
        """Get GitHub ID from Phabricator username via GraphQL.

        Args:
            username: Phabricator username

        Returns:
            Raw JSON response from GraphQL API
        """
        headers = {"Content-Type": "application/json"}
        payload = {
            "operationName": "GetGitHubId",
            "variables": {"username": username},
            "query": GITHUB_ID_QUERY,
        }

        logger.debug(f"Querying GitHub ID for: {username}")
        response = self._session.post(PMO_GRAPHQL_URL, headers=headers, json=payload)
        response.raise_for_status()
        result: dict = response.json()
        return result

    def get_github_username_by_id(self, github_id: str) -> dict:
        """Get GitHub username from GitHub ID via REST API.

        Args:
            github_id: GitHub numeric ID

        Returns:
            Raw JSON response from REST API
        """
        url = PMO_GITHUB_USERNAME_URL.format(github_id=github_id)

        logger.debug(f"Querying GitHub username for ID: {github_id}")
        response = self._session.get(url)
        response.raise_for_status()
        result: dict = response.json()
        return result

    def search_simple(self, query: str) -> dict:
        """Perform a broad profile search via the simple search endpoint.

        The GraphQL ``profile(username:)`` lookup is case-sensitive, so users
        whose PMO primary_username differs in case from their Phabricator
        username cannot be resolved directly. This endpoint performs a fuzzy,
        case-insensitive match that we can use to recover the correct case.

        Args:
            query: Search query (typically a Phabricator username)

        Returns:
            Raw JSON response with shape ``{"total", "next", "dinos": [...]}``
        """
        logger.debug(f"Searching profiles for: {query}")
        response = self._session.get(
            PMO_SEARCH_SIMPLE_URL, params={"q": query, "w": "all"}
        )
        response.raise_for_status()
        result: dict = response.json()
        return result

    def resolve_github(self, username: str) -> GitHubResolution:
        """Resolve Phabricator username to GitHub username and user ID.

        This is the main method that performs the full two-step resolution.

        Args:
            username: Phabricator username

        Returns:
            GitHubResolution with username and user_id (either may be None)
        """
        # Step 1: Get GitHub ID
        graphql_response = self.get_github_id(username)
        github_id = extract_github_id(graphql_response)

        # The GraphQL profile lookup is case-sensitive. If the user wasn't
        # found, fall back to the simple search endpoint to recover the
        # correct case of their PMO primary_username, then retry.
        if not github_id and _profile_not_found(graphql_response):
            time.sleep(self.delay)
            search_response = self.search_simple(username)
            resolved = find_username_case_insensitive(search_response, username)
            if resolved and resolved != username:
                logger.info(f"Case-insensitive match: {username} -> {resolved}")
                time.sleep(self.delay)
                graphql_response = self.get_github_id(resolved)
                github_id = extract_github_id(graphql_response)

        if not github_id:
            logger.debug(f"No GitHub ID found for: {username}")
            return GitHubResolution(username=None, user_id=None)

        # Convert ID string to int
        try:
            github_user_id = int(github_id)
        except ValueError:
            logger.warning(f"Invalid GitHub ID format: {github_id}")
            return GitHubResolution(username=None, user_id=None)

        # Rate limit between API calls
        time.sleep(self.delay)

        # Step 2: Get GitHub username from ID
        rest_response = self.get_github_username_by_id(github_id)
        github_username = extract_github_username(rest_response)

        if github_username:
            logger.info(f"Resolved {username} -> {github_username} (ID: {github_user_id})")
        else:
            logger.warning(f"Could not resolve GitHub username from ID {github_id}")

        return GitHubResolution(username=github_username, user_id=github_user_id)

    def resolve_github_username(self, username: str) -> Optional[str]:
        """Resolve Phabricator username to GitHub username.

        Convenience method that only returns the username.

        Args:
            username: Phabricator username

        Returns:
            GitHub username if found, None otherwise
        """
        return self.resolve_github(username).username


def extract_github_id(response: dict) -> Optional[str]:
    """Extract GitHub ID from GraphQL response.

    Args:
        response: JSON response from GraphQL API

    Returns:
        GitHub ID if found, None otherwise

    Examples:
        >>> extract_github_id({"data": {"profile": {"identities": {"githubIdV3": {"value": "123"}}}}})
        '123'
        >>> extract_github_id({"data": None, "errors": [...]})
        None
        >>> extract_github_id({"data": {"profile": {"identities": {"githubIdV3": None}}}})
        None
    """
    try:
        profile = response.get("data", {})
        if profile is None:
            return None
        profile = profile.get("profile")
        if not profile:
            return None

        identities = profile.get("identities", {})
        if not identities:
            return None

        github_id_obj = identities.get("githubIdV3", {})
        if not github_id_obj:
            return None

        value: Optional[str] = github_id_obj.get("value")
        return value
    except (KeyError, TypeError, AttributeError):
        return None


def extract_github_username(response: dict) -> Optional[str]:
    """Extract GitHub username from REST response.

    Args:
        response: JSON response from REST API

    Returns:
        GitHub username if found, None otherwise

    Examples:
        >>> extract_github_username({"username": "octocat"})
        'octocat'
        >>> extract_github_username({})
        None
    """
    username: Optional[str] = response.get("username")
    return username


def _profile_not_found(graphql_response: dict) -> bool:
    """True when the GraphQL response indicates no profile was found.

    Distinguishes "user does not exist" (where a case-insensitive retry makes
    sense) from "user exists but has no GitHub identity linked" (where a
    retry would be wasted).
    """
    data = graphql_response.get("data")
    if data is None:
        return True
    return data.get("profile") is None


def find_username_case_insensitive(response: dict, query: str) -> Optional[str]:
    """Find the primary_username from a search response that matches ``query``
    case-insensitively.

    The simple search endpoint performs fuzzy matching across many fields, so
    we filter the results to a dino whose ``username`` equals ``query``
    ignoring case.

    Args:
        response: JSON response from the ``/api/v4/search/simple/`` endpoint
        query: Username being looked up (case-sensitive)

    Returns:
        The matching primary_username in its canonical case, or None.

    Examples:
        >>> find_username_case_insensitive(
        ...     {"dinos": [{"username": "Octocat"}]}, "octocat"
        ... )
        'Octocat'
        >>> find_username_case_insensitive({"dinos": []}, "octocat")
    """
    query_lower = query.lower()
    dinos = response.get("dinos") or []
    for dino in dinos:
        candidate = dino.get("username")
        if candidate and candidate.lower() == query_lower:
            return str(candidate)
    return None
