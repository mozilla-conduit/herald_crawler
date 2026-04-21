"""Tests for PeopleDirectoryClient."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from herald_scraper.people_client import (
    PeopleDirectoryClient,
    extract_bugzilla_id,
    extract_github_id,
    extract_github_username,
    find_username_case_insensitive,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "people"


class TestExtractGithubId:
    """Tests for extract_github_id function."""

    def test_extract_github_id_success(self):
        """Test extracting GitHub ID from valid response."""
        response = {"data": {"profile": {"identities": {"githubIdV3": {"value": "961291"}}}}}
        assert extract_github_id(response) == "961291"

    def test_extract_github_id_user_not_found(self):
        """Test extracting GitHub ID when user doesn't exist."""
        response = {"data": None, "errors": [{"message": "profile does not exist"}]}
        assert extract_github_id(response) is None

    def test_extract_github_id_no_github_linked(self):
        """Test extracting GitHub ID when user has no GitHub linked."""
        response = {"data": {"profile": {"identities": {"githubIdV3": None}}}}
        assert extract_github_id(response) is None

    def test_extract_github_id_empty_identities(self):
        """Test extracting GitHub ID when identities is empty."""
        response = {"data": {"profile": {"identities": {}}}}
        assert extract_github_id(response) is None

    def test_extract_github_id_missing_value(self):
        """Test extracting GitHub ID when value key is missing."""
        response = {"data": {"profile": {"identities": {"githubIdV3": {}}}}}
        assert extract_github_id(response) is None

    def test_extract_github_id_empty_response(self):
        """Test extracting GitHub ID from empty response."""
        assert extract_github_id({}) is None

    def test_extract_github_id_malformed_response(self):
        """Test extracting GitHub ID from malformed response."""
        assert extract_github_id({"data": "invalid"}) is None


class TestExtractGithubUsername:
    """Tests for extract_github_username function."""

    def test_extract_github_username_success(self):
        """Test extracting GitHub username from valid response."""
        response = {"username": "testghuser"}
        assert extract_github_username(response) == "testghuser"

    def test_extract_github_username_empty(self):
        """Test extracting GitHub username from empty response."""
        assert extract_github_username({}) is None

    def test_extract_github_username_null(self):
        """Test extracting GitHub username when null."""
        response = {"username": None}
        assert extract_github_username(response) is None


class TestExtractFromFixtures:
    """Tests that verify extraction from actual API response fixtures."""

    @pytest.fixture
    def graphql_fixtures(self):
        """Load all GraphQL response fixtures."""
        fixtures = {}
        for filepath in FIXTURES_DIR.glob("*_graphql.json"):
            username = filepath.stem.replace("_graphql", "")
            with open(filepath) as f:
                fixtures[username] = json.load(f)
        return fixtures

    @pytest.fixture
    def rest_fixtures(self):
        """Load all REST response fixtures."""
        fixtures = {}
        for filepath in FIXTURES_DIR.glob("*_rest.json"):
            username = filepath.stem.replace("_rest", "")
            with open(filepath) as f:
                fixtures[username] = json.load(f)
        return fixtures

    def test_extract_github_id_from_fixtures(self, graphql_fixtures):
        """Test extracting GitHub ID from all available fixtures."""
        if not graphql_fixtures:
            pytest.skip("No GraphQL fixtures found")

        # Test each fixture that should have a GitHub ID
        found_valid = False
        for username, data in graphql_fixtures.items():
            if "nonexistent" in username:
                # Nonexistent user should return None
                assert extract_github_id(data) is None
            else:
                # Real users should have a GitHub ID (anonymized with GHID- prefix)
                github_id = extract_github_id(data)
                if github_id is not None:
                    assert github_id.startswith(
                        "GHID-"
                    ), f"GitHub ID should have GHID- prefix: {github_id}"
                    found_valid = True

        assert found_valid, "At least one fixture should have a valid GitHub ID"

    def test_extract_github_id_nonexistent_user(self, graphql_fixtures):
        """Test extracting GitHub ID from nonexistent user fixture."""
        nonexistent_fixtures = [k for k in graphql_fixtures if "nonexistent" in k]
        if not nonexistent_fixtures:
            pytest.skip("No nonexistent user fixture found")

        for username in nonexistent_fixtures:
            github_id = extract_github_id(graphql_fixtures[username])
            assert github_id is None

    def test_extract_github_username_from_fixtures(self, rest_fixtures):
        """Test extracting GitHub username from all available REST fixtures."""
        if not rest_fixtures:
            pytest.skip("No REST fixtures found")

        # Test each fixture
        found_valid = False
        for username, data in rest_fixtures.items():
            github_username = extract_github_username(data)
            if github_username is not None:
                # GitHub usernames should be anonymized with GHUSER- prefix
                assert isinstance(github_username, str)
                assert github_username.startswith(
                    "GHUSER-"
                ), f"GitHub username should have GHUSER- prefix: {github_username}"
                found_valid = True

        assert found_valid, "At least one fixture should have a valid GitHub username"


class TestPeopleDirectoryClient:
    """Tests for PeopleDirectoryClient class."""

    def test_client_initialization(self):
        """Test client initializes with cookie."""
        client = PeopleDirectoryClient(cookie="test-cookie")
        assert client.delay == 0.5
        # Cookie should be set on session
        cookie = client._session.cookies.get("pmo-access", domain=".mozilla.org")
        assert cookie == "test-cookie"

    def test_resolve_github_username_success(self):
        """Test full resolution flow with mocked responses."""
        client = PeopleDirectoryClient(cookie="test-cookie")

        # Mock the session methods
        client._session = MagicMock()

        # Mock GraphQL response
        graphql_response = MagicMock()
        graphql_response.json.return_value = {
            "data": {"profile": {"identities": {"githubIdV3": {"value": "12345"}}}}
        }

        # Mock REST response
        rest_response = MagicMock()
        rest_response.json.return_value = {"username": "testuser"}

        client._session.post.return_value = graphql_response
        client._session.get.return_value = rest_response

        result = client.resolve_github_username("phabuser")

        assert result == "testuser"
        client._session.post.assert_called_once()
        client._session.get.assert_called_once()

    def test_resolve_github_username_user_not_found(self):
        """Test resolution when user doesn't exist."""
        client = PeopleDirectoryClient(cookie="test-cookie", delay=0)
        client._session = MagicMock()

        graphql_response = MagicMock()
        graphql_response.json.return_value = {
            "data": None,
            "errors": [{"message": "profile does not exist"}],
        }
        search_response = MagicMock()
        search_response.json.return_value = {"total": 0, "next": "", "dinos": []}

        client._session.post.return_value = graphql_response
        client._session.get.return_value = search_response

        result = client.resolve_github_username("nonexistent")

        assert result is None
        # After the initial GraphQL miss, we fall back to search/simple to
        # look for a case-insensitive match; when none is found we stop.
        assert client._session.post.call_count == 1
        client._session.get.assert_called_once()

    def test_resolve_github_username_no_github_linked(self):
        """Test resolution when user has no GitHub linked."""
        client = PeopleDirectoryClient(cookie="test-cookie")
        client._session = MagicMock()

        graphql_response = MagicMock()
        graphql_response.json.return_value = {
            "data": {"profile": {"identities": {"githubIdV3": None}}}
        }

        client._session.post.return_value = graphql_response

        result = client.resolve_github_username("user_no_github")

        assert result is None
        client._session.post.assert_called_once()
        client._session.get.assert_not_called()

    def test_resolve_github_username_case_insensitive_fallback(self):
        """When PMO case differs from Phabricator, fall back to search/simple."""
        client = PeopleDirectoryClient(cookie="test-cookie", delay=0)
        client._session = MagicMock()

        # First GraphQL call: lowercase lookup misses.
        miss_response = MagicMock()
        miss_response.json.return_value = {
            "data": None,
            "errors": [{"message": "profile does not exist"}],
        }
        # Retry with canonical case succeeds.
        hit_response = MagicMock()
        hit_response.json.return_value = {
            "data": {"profile": {"identities": {"githubIdV3": {"value": "12345"}}}}
        }
        client._session.post.side_effect = [miss_response, hit_response]

        # Search surfaces the same user with different casing, plus noise.
        search_response = MagicMock()
        search_response.json.return_value = {
            "total": 2,
            "next": "",
            "dinos": [
                {"username": "SomeoneElse"},
                {"username": "PhabUser"},
            ],
        }
        rest_response = MagicMock()
        rest_response.json.return_value = {"username": "ghuser"}
        client._session.get.side_effect = [search_response, rest_response]

        result = client.resolve_github_username("phabuser")

        assert result == "ghuser"
        assert client._session.post.call_count == 2
        # The retry must use the canonical casing returned by search.
        retry_payload = client._session.post.call_args_list[1].kwargs["json"]
        assert retry_payload["variables"]["username"] == "PhabUser"

    def test_resolve_github_username_search_no_match(self):
        """Case-insensitive fallback returns None when search surfaces no match."""
        client = PeopleDirectoryClient(cookie="test-cookie", delay=0)
        client._session = MagicMock()

        miss_response = MagicMock()
        miss_response.json.return_value = {"data": None, "errors": []}
        client._session.post.return_value = miss_response

        # Search returns fuzzy matches, none of which match the query by case.
        search_response = MagicMock()
        search_response.json.return_value = {
            "dinos": [{"username": "unrelated_user"}],
        }
        client._session.get.return_value = search_response

        result = client.resolve_github_username("missing")

        assert result is None
        # No retry, no GitHub username lookup.
        assert client._session.post.call_count == 1
        assert client._session.get.call_count == 1


class TestExtractBugzillaId:
    """Tests for extract_bugzilla_id function."""

    def test_extract_bugzilla_id_success(self):
        response = {
            "data": {
                "profile": {
                    "identities": {"bugzillaMozillaOrgId": {"value": "91159"}}
                }
            }
        }
        assert extract_bugzilla_id(response) == "91159"

    def test_extract_bugzilla_id_from_fixture(self):
        with open(FIXTURES_DIR / "USER-5e421c00_graphql_bmo.json") as f:
            data = json.load(f)
        assert extract_bugzilla_id(data) == "99999999"

    def test_extract_bugzilla_id_profile_missing(self):
        response = {"data": None, "errors": [{"message": "profile does not exist"}]}
        assert extract_bugzilla_id(response) is None

    def test_extract_bugzilla_id_field_absent(self):
        response = {"data": {"profile": {"identities": {}}}}
        assert extract_bugzilla_id(response) is None

    def test_extract_bugzilla_id_null_value(self):
        response = {
            "data": {"profile": {"identities": {"bugzillaMozillaOrgId": None}}}
        }
        assert extract_bugzilla_id(response) is None


class TestFindUsernameCaseInsensitive:
    """Tests for find_username_case_insensitive helper."""

    def test_finds_canonical_case(self):
        response = {"dinos": [{"username": "MixedCase"}]}
        assert find_username_case_insensitive(response, "mixedcase") == "MixedCase"

    def test_prefers_exact_match_among_fuzzy_hits(self):
        response = {
            "dinos": [
                {"username": "other"},
                {"username": "TheUser"},
                {"username": "TheUserExtra"},
            ]
        }
        assert find_username_case_insensitive(response, "theuser") == "TheUser"

    def test_returns_none_when_no_match(self):
        response = {"dinos": [{"username": "someone"}]}
        assert find_username_case_insensitive(response, "other") is None

    def test_handles_empty_response(self):
        assert find_username_case_insensitive({}, "anything") is None
        assert find_username_case_insensitive({"dinos": []}, "anything") is None

    def test_skips_dinos_without_username(self):
        response = {"dinos": [{}, {"username": None}, {"username": "Match"}]}
        assert find_username_case_insensitive(response, "match") == "Match"


class TestSearchSimpleFixture:
    """Tests that verify parsing of the /api/v4/search/simple/ response."""

    SEARCH_FIXTURE = FIXTURES_DIR / "search_simple_USER-c0ffee12.json"

    @pytest.fixture
    def search_response(self):
        with open(self.SEARCH_FIXTURE) as f:
            return json.load(f)

    def test_fixture_has_expected_shape(self, search_response):
        """The endpoint returns {total, next, dinos: [...]}."""
        assert set(search_response.keys()) >= {"total", "next", "dinos"}
        assert isinstance(search_response["dinos"], list)
        assert search_response["dinos"], "fixture should contain at least one dino"

    def test_fixture_contains_no_real_username(self, search_response):
        """Guard against regressions that would re-introduce PII."""
        for dino in search_response["dinos"]:
            username = dino.get("username", "")
            assert username.startswith("USER-"), (
                f"fixture leaked a non-anonymized username: {username}"
            )

    def test_find_username_case_insensitive_on_fixture(self, search_response):
        """find_username_case_insensitive picks the canonical casing out of a real response."""
        canonical = search_response["dinos"][0]["username"]
        assert find_username_case_insensitive(search_response, canonical.lower()) == canonical
        assert find_username_case_insensitive(search_response, canonical.upper()) == canonical
        assert find_username_case_insensitive(search_response, canonical) == canonical

    def test_find_username_case_insensitive_rejects_non_match(self, search_response):
        """A query that doesn't match any dino returns None even on a populated response."""
        assert find_username_case_insensitive(search_response, "not-in-fixture") is None

    def test_search_simple_calls_endpoint_and_returns_parsed_json(self, search_response):
        """Client hits the simple search URL with the right params and returns parsed JSON."""
        from herald_scraper.people_client import PMO_SEARCH_SIMPLE_URL

        client = PeopleDirectoryClient(cookie="test-cookie", delay=0)
        client._session = MagicMock()
        mocked = MagicMock()
        mocked.json.return_value = search_response
        client._session.get.return_value = mocked

        result = client.search_simple("c0ffee12")

        assert result == search_response
        client._session.get.assert_called_once_with(
            PMO_SEARCH_SIMPLE_URL, params={"q": "c0ffee12", "w": "all"}
        )
        mocked.raise_for_status.assert_called_once()

    def test_resolve_github_verifies_bmo_id(self, search_response):
        """When expected_bmo_id is provided, the resolver confirms it against PMO."""
        client = PeopleDirectoryClient(cookie="test-cookie", delay=0)
        client._session = MagicMock()

        github_hit = MagicMock()
        github_hit.json.return_value = {
            "data": {"profile": {"identities": {"githubIdV3": {"value": "42"}}}}
        }
        bmo_hit = MagicMock()
        bmo_hit.json.return_value = {
            "data": {"profile": {"identities": {"bugzillaMozillaOrgId": {"value": "99999999"}}}}
        }
        client._session.post.side_effect = [github_hit, bmo_hit]

        rest = MagicMock()
        rest.json.return_value = {"username": "gh-canonical"}
        client._session.get.return_value = rest

        result = client.resolve_github("phabuser", expected_bmo_id="99999999")

        assert result.username == "gh-canonical"
        assert result.user_id == 42
        assert client._session.post.call_count == 2
        bmo_payload = client._session.post.call_args_list[1].kwargs["json"]
        assert bmo_payload["operationName"] == "GetBugzillaId"

    def test_resolve_github_rejects_on_bmo_id_mismatch(self, search_response):
        """A mismatched BMO id drops the resolution and skips the REST lookup."""
        client = PeopleDirectoryClient(cookie="test-cookie", delay=0)
        client._session = MagicMock()

        github_hit = MagicMock()
        github_hit.json.return_value = {
            "data": {"profile": {"identities": {"githubIdV3": {"value": "42"}}}}
        }
        bmo_hit = MagicMock()
        bmo_hit.json.return_value = {
            "data": {"profile": {"identities": {"bugzillaMozillaOrgId": {"value": "11111111"}}}}
        }
        client._session.post.side_effect = [github_hit, bmo_hit]
        client._session.get.return_value = MagicMock()  # would be the REST call

        result = client.resolve_github("phabuser", expected_bmo_id="99999999")

        assert result.username is None
        assert result.user_id is None
        # REST /whoami/github/username/ must not be called when verification fails.
        client._session.get.assert_not_called()

    def test_resolve_github_uses_fixture_for_case_recovery(self, search_response):
        """Full flow: initial GraphQL miss -> simple search -> retry GraphQL with canonical case."""
        canonical = search_response["dinos"][0]["username"]
        query = canonical.lower()
        assert query != canonical, (
            "fixture username must be mixed-case to exercise the fallback"
        )

        client = PeopleDirectoryClient(cookie="test-cookie", delay=0)
        client._session = MagicMock()

        miss = MagicMock()
        miss.json.return_value = {
            "data": None,
            "errors": [{"message": "profile does not exist"}],
        }
        hit = MagicMock()
        hit.json.return_value = {
            "data": {"profile": {"identities": {"githubIdV3": {"value": "42"}}}}
        }
        client._session.post.side_effect = [miss, hit]

        search = MagicMock()
        search.json.return_value = search_response
        rest = MagicMock()
        rest.json.return_value = {"username": "gh-canonical"}
        client._session.get.side_effect = [search, rest]

        result = client.resolve_github(query)

        assert result.username == "gh-canonical"
        assert result.user_id == 42
        retry_payload = client._session.post.call_args_list[1].kwargs["json"]
        assert retry_payload["variables"]["username"] == canonical
