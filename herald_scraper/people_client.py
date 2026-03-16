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
        return response.json()

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
        return response.json()

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

        return github_id_obj.get("value")
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
    return response.get("username")
