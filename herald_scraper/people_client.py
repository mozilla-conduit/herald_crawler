"""Client for Mozilla People Directory API."""

import logging
import time
import unicodedata
from typing import NamedTuple, Optional

import requests

from herald_scraper.exceptions import RateLimitError


class GitHubResolution(NamedTuple):
    """Result of resolving a Phabricator username to GitHub.

    On failure, ``reason`` carries a machine-readable code so callers can
    distinguish between different failure modes:

    - ``pmo_profile_not_found``: no PMO profile matched the Phab username,
      and none of the fallbacks (case-insensitive, BMO-id, real-name) picked
      a canonical PMO profile either.
    - ``no_github_linked``: a PMO profile was found, but its
      ``identities.githubIdV3`` field is null — the user hasn't linked a
      GitHub identity to their Mozilla People profile.
    - ``bmo_id_mismatch``: a PMO profile was found with a linked GitHub id,
      but its ``bugzillaMozillaOrgId`` disagrees with the one Phabricator
      reports for the same user.
    - ``github_id_invalid``: the ``githubIdV3`` value could not be coerced
      to an int (should not happen in practice).

    On success, ``reason`` is None.
    """

    username: Optional[str]
    user_id: Optional[int]
    reason: Optional[str] = None


logger = logging.getLogger(__name__)

PMO_GRAPHQL_URL = "https://people.mozilla.org/api/v4/graphql"
PMO_GITHUB_USERNAME_URL = "https://people.mozilla.org/whoami/github/username/{github_id}"
PMO_SEARCH_SIMPLE_URL = "https://people.mozilla.org/api/v4/search/simple/"

# PMO's /whoami/github/ endpoint proxies GitHub, so GitHub's rate limits can
# surface here. GitHub signals both its primary and secondary rate limits with
# either status code:
# https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api
RATE_LIMIT_STATUS_CODES = (403, 429)

# GitHub's documented floor for a secondary rate limit that carries no explicit
# retry hint: "Otherwise, wait for at least one minute before retrying."
DEFAULT_RATE_LIMIT_WAIT = 60.0

# "throw an error after a specific number of retries" — with the exponential
# growth below this bounds a single lookup at 60+120+240+480s of waiting.
MAX_RATE_LIMIT_RETRIES = 4

GITHUB_ID_QUERY = """
query GetGitHubId($username: String) {
  profile(username: $username) {
    identities {
      githubIdV3 { value }
    }
  }
}
"""

# PMO's GraphQL 500s when a single query selects multiple identity fields
# at once, so we issue BMO lookups as their own request.
BUGZILLA_ID_QUERY = """
query GetBugzillaId($username: String) {
  profile(username: $username) {
    identities {
      bugzillaMozillaOrgId { value }
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

    def __init__(self, cookie: str) -> None:
        """Initialize the People Directory client.

        Requests are not paced: the client runs at full speed and only backs
        off when a response reports a rate limit (see
        ``rate_limit_wait_seconds``).

        Args:
            cookie: pmo-access cookie value for authentication
        """
        self._session = requests.Session()
        self._session.cookies.set("pmo-access", cookie, domain=".mozilla.org")
        self._session.headers["User-Agent"] = "HeraldScraper/0.1"

    @staticmethod
    def _check_response(response: requests.Response, what: str) -> None:
        """Raise on an error response, telling rate limits apart from failures.

        Rate limits become ``RateLimitError`` so the caller can wait and retry
        instead of recording the user as permanently unresolved.
        """
        wait = rate_limit_wait_seconds(response)
        if wait is not None:
            raise RateLimitError(
                f"Rate limited (HTTP {response.status_code}) while {what}",
                retry_after=wait,
            )
        response.raise_for_status()

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
        self._check_response(response, f"querying GitHub ID for {username}")
        result: dict = response.json()
        return result

    def get_bugzilla_id(self, username: str) -> dict:
        """Get Bugzilla account ID for a PMO profile via GraphQL.

        Args:
            username: PMO primary username (canonical case)

        Returns:
            Raw JSON response from GraphQL API
        """
        headers = {"Content-Type": "application/json"}
        payload = {
            "operationName": "GetBugzillaId",
            "variables": {"username": username},
            "query": BUGZILLA_ID_QUERY,
        }

        logger.debug(f"Querying Bugzilla ID for: {username}")
        response = self._session.post(PMO_GRAPHQL_URL, headers=headers, json=payload)
        self._check_response(response, f"querying Bugzilla ID for {username}")
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
        self._check_response(response, f"querying GitHub username for ID {github_id}")
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
        self._check_response(response, f"searching profiles for {query}")
        result: dict = response.json()
        return result

    def resolve_github(
        self,
        username: str,
        expected_bmo_id: Optional[str] = None,
        expected_real_name: Optional[str] = None,
    ) -> GitHubResolution:
        """Resolve Phabricator username to GitHub username and user ID.

        This is the main method that performs the full two-step resolution.

        Args:
            username: Phabricator username
            expected_bmo_id: Optional Bugzilla account id, sourced from
                Phabricator's bugzilla.account.search, that the PMO profile
                must also expose via bugzillaMozillaOrgId. When provided and
                it doesn't match, the resolution is dropped.
            expected_real_name: Optional real name from Phabricator
                ``user.search``. Used as a tertiary fallback to pick a PMO
                profile when the username is divergent and ``bugzillaMozillaOrgId``
                is absent (so BMO-id matching can't run).

        Returns:
            GitHubResolution with username and user_id (either may be None)
        """
        logger.debug(
            f"resolve_github: username={username!r} "
            f"expected_bmo_id={expected_bmo_id!r} "
            f"expected_real_name={expected_real_name!r}"
        )

        # Step 1: Get GitHub ID
        graphql_response = self.get_github_id(username)
        github_id = extract_github_id(graphql_response)
        canonical_name = username
        profile_found = not _profile_not_found(graphql_response)

        # The GraphQL profile lookup is case-sensitive *and* people can keep
        # entirely different usernames between Phab and PMO (e.g. `phab_alias`
        # in Phab vs. `pmo_canonical` in PMO). Fall back to the simple search
        # endpoint and try four matching strategies in order:
        #   1. case-insensitive equality on the Phab username,
        #   2. BMO-id equality against the expected id from Phab,
        #   3. real-name equality against the Phab realName, and
        #   4. email local-part equality with the Phab username (e.g.
        #      Phab `alias_loc` -> PMO dino with primaryEmail alias_loc@...).
        # If a search by Phab username surfaces nothing useful (e.g. the
        # nickname `007` returns one unrelated dino), retry the search using
        # the Phab realName so the same fallbacks can match against a more
        # relevant result set.
        if not github_id and not profile_found:
            search_response = self.search_simple(username)
            resolved = self._match_search_fallbacks(
                search_response, username, expected_bmo_id, expected_real_name
            )
            if not resolved and expected_real_name:
                search_response = self.search_simple(expected_real_name)
                resolved = self._match_search_fallbacks(
                    search_response, username, expected_bmo_id, expected_real_name
                )
            if resolved and resolved != username:
                logger.info(f"PMO username fallback: {username} -> {resolved}")
                graphql_response = self.get_github_id(resolved)
                github_id = extract_github_id(graphql_response)
                if not _profile_not_found(graphql_response):
                    profile_found = True
                    canonical_name = resolved

        if not github_id:
            if profile_found:
                logger.debug(
                    f"PMO profile for {username} (PMO={canonical_name}) "
                    f"has no linked GitHub identity"
                )
                return GitHubResolution(
                    username=None, user_id=None, reason="no_github_linked"
                )
            logger.debug(f"No PMO profile found for: {username}")
            return GitHubResolution(
                username=None, user_id=None, reason="pmo_profile_not_found"
            )

        # Convert ID string to int
        try:
            github_user_id = int(github_id)
        except ValueError:
            logger.warning(f"Invalid GitHub ID format: {github_id}")
            return GitHubResolution(
                username=None, user_id=None, reason="github_id_invalid"
            )

        # Optional BMO id cross-check. Ensures the PMO profile we resolved
        # actually belongs to the Phabricator user we started from — catches
        # case collisions between different people that the username-based
        # fallback cannot distinguish. Only an *active* disagreement rejects:
        # if PMO doesn't expose a BMO id for this profile (privacy settings,
        # unlinked, etc.), there's nothing to contradict, so we keep the
        # resolution.
        if expected_bmo_id is not None:
            bmo_response = self.get_bugzilla_id(canonical_name)
            actual_bmo_id = extract_bugzilla_id(bmo_response)
            if actual_bmo_id is None:
                logger.debug(
                    f"BMO id unknown in PMO for {username} (PMO={canonical_name}); "
                    f"accepting resolution without verification"
                )
            elif actual_bmo_id != expected_bmo_id:
                logger.warning(
                    f"BMO id mismatch for {username} (PMO={canonical_name}): "
                    f"phabricator={expected_bmo_id!r} pmo={actual_bmo_id!r}"
                )
                return GitHubResolution(
                    username=None, user_id=None, reason="bmo_id_mismatch"
                )

        # Step 2: Get GitHub username from ID
        rest_response = self.get_github_username_by_id(github_id)
        github_username = extract_github_username(rest_response)

        if github_username:
            logger.info(f"Resolved {username} -> {github_username} (ID: {github_user_id})")
        else:
            logger.warning(f"Could not resolve GitHub username from ID {github_id}")

        return GitHubResolution(username=github_username, user_id=github_user_id)

    def _match_search_fallbacks(
        self,
        search_response: dict,
        phab_username: str,
        expected_bmo_id: Optional[str],
        expected_real_name: Optional[str],
    ) -> Optional[str]:
        """Run the four matching strategies against one search response.

        Returns the canonical PMO ``primary_username`` of the first matching
        dino, or ``None`` if nothing in the response matches the Phab user.
        """
        resolved = find_username_case_insensitive(search_response, phab_username)
        if not resolved and expected_bmo_id is not None:
            resolved = self._find_username_by_bmo_id(search_response, expected_bmo_id)
        if not resolved and expected_real_name is not None:
            resolved = find_username_by_real_name(search_response, expected_real_name)
        if not resolved:
            resolved = find_username_by_email_local_part(search_response, phab_username)
        return resolved

    def _find_username_by_bmo_id(
        self, search_response: dict, expected_bmo_id: str
    ) -> Optional[str]:
        """Pick the dino from a search response whose PMO profile carries
        ``expected_bmo_id``.

        Issues one PMO GraphQL request per candidate dino, returning the
        first ``username`` whose ``bugzillaMozillaOrgId`` matches. Candidates
        without a username are skipped.

        This handles the case where the Phab nickname and the PMO
        ``primary_username`` have diverged entirely (not just in case) and
        the search endpoint surfaces the right profile via fuzzy matching
        on other fields (email, real name).
        """
        for dino in search_response.get("dinos") or []:
            candidate = dino.get("username")
            if not candidate:
                continue
            bmo_response = self.get_bugzilla_id(candidate)
            candidate_bmo_id = extract_bugzilla_id(bmo_response)
            if candidate_bmo_id and candidate_bmo_id == expected_bmo_id:
                logger.info(
                    f"BMO id match: PMO username={candidate!r} bmo_id={expected_bmo_id!r}"
                )
                return str(candidate)
        return None

    def resolve_github_username(self, username: str) -> Optional[str]:
        """Resolve Phabricator username to GitHub username.

        Convenience method that only returns the username.

        Args:
            username: Phabricator username

        Returns:
            GitHub username if found, None otherwise
        """
        return self.resolve_github(username).username


def rate_limit_wait_seconds(response: requests.Response) -> Optional[float]:
    """How long to wait before retrying a rate-limited response.

    Implements GitHub's documented order of precedence for both the primary
    and the secondary rate limit:

    1. ``retry-after`` present -> wait that many seconds.
    2. ``x-ratelimit-remaining`` is ``0`` -> wait until ``x-ratelimit-reset``
       (UTC epoch seconds).
    3. Otherwise -> wait at least one minute.

    Returns None when the response is not a rate limit, so the caller can
    fail fast. A bare 403 with none of the signals above is far more likely
    an expired ``pmo-access`` cookie than a rate limit, and retrying that
    would just spin; 429 always means rate limited.
    """
    if response.status_code not in RATE_LIMIT_STATUS_CODES:
        return None

    retry_after = _parse_seconds(response.headers.get("retry-after"))
    if retry_after is not None:
        return max(0.0, retry_after)

    if (response.headers.get("x-ratelimit-remaining") or "").strip() == "0":
        reset = _parse_seconds(response.headers.get("x-ratelimit-reset"))
        if reset is None:
            return DEFAULT_RATE_LIMIT_WAIT
        return max(0.0, reset - time.time())

    if response.status_code == 429 or _mentions_rate_limit(response):
        return DEFAULT_RATE_LIMIT_WAIT

    return None


def rate_limit_backoff_seconds(error: RateLimitError, attempt: int) -> float:
    """How long to wait before retry ``attempt`` (0-based) of a rate-limited call.

    An explicit hint from the response is authoritative — GitHub tells us not
    to retry before it elapses, and inflating it would only idle longer than
    needed. The "wait at least a minute" fallback is the one that grows
    exponentially, per "if your request continues to fail due to a secondary
    rate limit, wait for an exponentially increasing amount of time between
    retries".
    """
    if error.retry_after is not None:
        return error.retry_after
    return DEFAULT_RATE_LIMIT_WAIT * (2**attempt)


def _parse_seconds(value: Optional[str]) -> Optional[float]:
    """Parse a numeric header value, or None if absent/not a number.

    GitHub sends ``retry-after`` as an integer number of seconds rather than
    the HTTP-date the RFC also allows, so anything non-numeric is treated as
    "no hint" and falls through to the next signal.
    """
    if value is None:
        return None
    try:
        return float(value.strip())
    except (AttributeError, ValueError):
        return None


def _mentions_rate_limit(response: requests.Response) -> bool:
    """Whether the response body names a rate limit.

    Secondary rate limits are only distinguishable from an ordinary 403 by
    "an error message that indicates that you exceeded a secondary rate
    limit", so fall back to sniffing the body.
    """
    try:
        return "rate limit" in response.text.lower()
    except Exception:  # pragma: no cover - defensive, decoding a body
        return False


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


def extract_bugzilla_id(response: dict) -> Optional[str]:
    """Extract Bugzilla account ID from a ``GetBugzillaId`` GraphQL response.

    The field lives at ``data.profile.identities.bugzillaMozillaOrgId.value``
    and is a numeric-looking string (e.g. ``"91159"``).

    Examples:
        >>> extract_bugzilla_id({"data": {"profile": {"identities": {
        ...     "bugzillaMozillaOrgId": {"value": "91159"}}}}})
        '91159'
        >>> extract_bugzilla_id({"data": None, "errors": [...]})  # doctest: +SKIP
        None
    """
    try:
        data = response.get("data")
        if not data:
            return None
        profile = data.get("profile")
        if not profile:
            return None
        identities = profile.get("identities") or {}
        bmo = identities.get("bugzillaMozillaOrgId") or {}
        value: Optional[str] = bmo.get("value")
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


def find_username_by_email_local_part(response: dict, phab_username: str) -> Optional[str]:
    """Find the ``username`` of a dino whose ``primaryEmail`` local part
    (everything before the ``@``) equals ``phab_username``, case-insensitive.

    Handles the common Mozilla pattern where the Phab nickname mirrors
    the mozilla.com email prefix while the PMO ``primary_username`` is
    something unrelated (e.g. Phab ``alias_loc`` / PMO ``canon_loc`` /
    ``primaryEmail`` ``alias_loc@mozilla.com``).

    Examples:
        >>> find_username_by_email_local_part(
        ...     {"dinos": [{"username": "canon_loc", "primaryEmail": "alias_loc@mozilla.com"}]},
        ...     "alias_loc",
        ... )
        'canon_loc'
        >>> find_username_by_email_local_part(
        ...     {"dinos": [{"username": "canon_loc", "primaryEmail": "alias_loc@mozilla.com"}]},
        ...     "ALIAS_LOC",
        ... )
        'canon_loc'
        >>> find_username_by_email_local_part({"dinos": []}, "alias_loc")
    """
    if not phab_username:
        return None
    target = phab_username.strip().lower()
    if not target:
        return None
    for dino in response.get("dinos") or []:
        candidate = dino.get("username")
        email = dino.get("primaryEmail") or ""
        if not candidate or "@" not in email:
            continue
        local_part = email.split("@", 1)[0].strip().lower()
        if local_part == target:
            return str(candidate)
    return None


def find_username_by_real_name(response: dict, real_name: str) -> Optional[str]:
    """Find the ``primary_username`` from a search response whose dino has
    a ``firstName + " " + lastName`` matching ``real_name`` (case-insensitive,
    whitespace-collapsed, accent-folded).

    Useful as a last-resort fallback when the Phab and PMO usernames have
    diverged and the PMO profile has no ``bugzillaMozillaOrgId`` to match
    against. Returns the first matching dino's username; callers should have
    already filtered by cheaper signals (case-insensitive username, BMO id)
    before reaching for this.

    Names are folded via NFKD + stripping of combining marks so that
    diacritics on either side don't block a match (Phab realName missing
    accents that PMO carries, or vice versa).

    Examples:
        >>> find_username_by_real_name(
        ...     {"dinos": [{"firstName": "Aaa", "lastName": "Bbb", "username": "aaab"}]},
        ...     "Aaa Bbb",
        ... )
        'aaab'
        >>> find_username_by_real_name(
        ...     {"dinos": [{"firstName": "aaa", "lastName": "BBB", "username": "aaab"}]},
        ...     "Aaa Bbb",
        ... )
        'aaab'
        >>> find_username_by_real_name(
        ...     {"dinos": [{"firstName": "Tést", "lastName": "Üser", "username": "tuser"}]},
        ...     "Test User",
        ... )
        'tuser'
        >>> find_username_by_real_name({"dinos": []}, "Aaa Bbb")
    """
    if not real_name:
        return None
    target = _fold_name(real_name)
    if not target:
        return None
    dinos = response.get("dinos") or []
    for dino in dinos:
        candidate = dino.get("username")
        first = (dino.get("firstName") or "").strip()
        last = (dino.get("lastName") or "").strip()
        if not candidate or not first or not last:
            continue
        full = _fold_name(f"{first} {last}")
        if full == target:
            return str(candidate)

    # Phab realName is sometimes a bare first name (no surname) while PMO
    # carries the full first+last. Fall back to a firstName-only fold-match,
    # but require it to be unique in the response so we don't silently pick
    # one of several people who share a first name.
    if " " not in target:
        first_matches = []
        for dino in dinos:
            candidate = dino.get("username")
            first = (dino.get("firstName") or "").strip()
            if not candidate or not first:
                continue
            if _fold_name(first) == target:
                first_matches.append(str(candidate))
        if len(first_matches) == 1:
            return first_matches[0]
    return None


def _fold_name(s: str) -> str:
    """Normalize a name for fuzzy equality: NFKD-decompose, drop combining
    marks (diacritics), lowercase, and collapse whitespace.
    """
    decomposed = unicodedata.normalize("NFKD", s)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.split()).lower()


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
