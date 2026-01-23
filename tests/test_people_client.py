"""Tests for PeopleDirectoryClient."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from herald_scraper.people_client import (
    PeopleDirectoryClient,
    extract_github_id,
    extract_github_username,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "people"


class TestExtractGithubId:
    """Tests for extract_github_id function."""

    def test_extract_github_id_success(self):
        """Test extracting GitHub ID from valid response."""
        response = {
            "data": {
                "profile": {
                    "identities": {
                        "githubIdV3": {"value": "961291"}
                    }
                }
            }
        }
        assert extract_github_id(response) == "961291"

    def test_extract_github_id_user_not_found(self):
        """Test extracting GitHub ID when user doesn't exist."""
        response = {
            "data": None,
            "errors": [{"message": "profile does not exist"}]
        }
        assert extract_github_id(response) is None

    def test_extract_github_id_no_github_linked(self):
        """Test extracting GitHub ID when user has no GitHub linked."""
        response = {
            "data": {
                "profile": {
                    "identities": {
                        "githubIdV3": None
                    }
                }
            }
        }
        assert extract_github_id(response) is None

    def test_extract_github_id_empty_identities(self):
        """Test extracting GitHub ID when identities is empty."""
        response = {
            "data": {
                "profile": {
                    "identities": {}
                }
            }
        }
        assert extract_github_id(response) is None

    def test_extract_github_id_missing_value(self):
        """Test extracting GitHub ID when value key is missing."""
        response = {
            "data": {
                "profile": {
                    "identities": {
                        "githubIdV3": {}
                    }
                }
            }
        }
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
                    assert github_id.startswith("GHID-"), f"GitHub ID should have GHID- prefix: {github_id}"
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
                assert github_username.startswith("GHUSER-"), f"GitHub username should have GHUSER- prefix: {github_username}"
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
            "data": {
                "profile": {
                    "identities": {
                        "githubIdV3": {"value": "12345"}
                    }
                }
            }
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
        client = PeopleDirectoryClient(cookie="test-cookie")
        client._session = MagicMock()

        graphql_response = MagicMock()
        graphql_response.json.return_value = {
            "data": None,
            "errors": [{"message": "profile does not exist"}]
        }

        client._session.post.return_value = graphql_response

        result = client.resolve_github_username("nonexistent")

        assert result is None
        client._session.post.assert_called_once()
        client._session.get.assert_not_called()  # Should not call REST API

    def test_resolve_github_username_no_github_linked(self):
        """Test resolution when user has no GitHub linked."""
        client = PeopleDirectoryClient(cookie="test-cookie")
        client._session = MagicMock()

        graphql_response = MagicMock()
        graphql_response.json.return_value = {
            "data": {
                "profile": {
                    "identities": {
                        "githubIdV3": None
                    }
                }
            }
        }

        client._session.post.return_value = graphql_response

        result = client.resolve_github_username("user_no_github")

        assert result is None
        client._session.post.assert_called_once()
        client._session.get.assert_not_called()
