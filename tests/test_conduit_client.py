"""Tests for ConduitClient."""

import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest
import requests

from herald_scraper.conduit_client import ConduitClient, ConduitError
from herald_scraper.exceptions import RateLimitError
from herald_scraper.rate_limit import MAX_RATE_LIMIT_RETRIES

# --- Fixtures ---


@pytest.fixture
def conduit_fixtures_path(fixtures_path: Path) -> Path:
    """Path to conduit fixtures directory."""
    return fixtures_path / "conduit"


@pytest.fixture
def project_search_response(conduit_fixtures_path: Path) -> Dict[str, Any]:
    """Load project.search response fixture."""
    with open(conduit_fixtures_path / "project_search_response.json") as f:
        data: Dict[str, Any] = json.load(f)
        return data


@pytest.fixture
def user_search_response(conduit_fixtures_path: Path) -> Dict[str, Any]:
    """Load user.search response fixture."""
    with open(conduit_fixtures_path / "user_search_response.json") as f:
        data: Dict[str, Any] = json.load(f)
        return data


@pytest.fixture
def error_response(conduit_fixtures_path: Path) -> Dict[str, Any]:
    """Load error response fixture."""
    with open(conduit_fixtures_path / "error_response.json") as f:
        data: Dict[str, Any] = json.load(f)
        return data


@pytest.fixture
def project_not_found_response(conduit_fixtures_path: Path) -> Dict[str, Any]:
    """Load project not found response fixture."""
    with open(conduit_fixtures_path / "project_not_found_response.json") as f:
        data: Dict[str, Any] = json.load(f)
        return data


@pytest.fixture
def mock_session() -> MagicMock:
    """Create a mock requests session."""
    return MagicMock(spec=requests.Session)


@pytest.fixture
def conduit_client() -> ConduitClient:
    """Create a ConduitClient instance for testing."""
    return ConduitClient(
        base_url="https://phabricator.example.com",
        api_token="api-test-token",
    )


# --- ConduitClient Tests ---


class TestConduitClientInit:
    """Tests for ConduitClient initialization."""

    def test_valid_initialization(self) -> None:
        """Test creating a client with valid parameters."""
        client = ConduitClient(
            base_url="https://phabricator.example.com",
            api_token="api-xxxxx",
        )
        assert client.base_url == "https://phabricator.example.com"
        assert client.api_token == "api-xxxxx"
        assert client.timeout == 30.0  # default

    def test_strips_trailing_slash(self) -> None:
        """Test that trailing slash is stripped from base_url."""
        client = ConduitClient(
            base_url="https://phabricator.example.com/",
            api_token="api-xxxxx",
        )
        assert client.base_url == "https://phabricator.example.com"

    def test_invalid_base_url_raises(self) -> None:
        """Test that invalid base_url raises ValueError."""
        with pytest.raises(ValueError, match="Invalid base_url"):
            ConduitClient(base_url="not-a-url", api_token="api-xxxxx")

    def test_missing_api_token_raises(self) -> None:
        """Test that empty api_token raises ValueError."""
        with pytest.raises(ValueError, match="api_token is required"):
            ConduitClient(
                base_url="https://phabricator.example.com",
                api_token="",
            )

    def test_custom_parameters(self) -> None:
        """Test creating a client with custom parameters."""
        client = ConduitClient(
            base_url="https://phabricator.example.com",
            api_token="api-xxxxx",
            timeout=60.0,
            user_agent="TestAgent/1.0",
        )
        assert client.timeout == 60.0


class TestConduitClientCallMethod:
    """Tests for ConduitClient.call_method()."""

    def test_call_method_success(
        self, conduit_client: ConduitClient, project_search_response: Dict[str, Any]
    ) -> None:
        """Test successful API call."""
        with patch.object(conduit_client, "_session") as mock_session:
            mock_response = MagicMock()
            mock_response.json.return_value = project_search_response
            mock_response.raise_for_status = MagicMock()
            mock_session.post.return_value = mock_response

            result = conduit_client.call_method("project.search", {"constraints": {}})

            mock_session.post.assert_called_once()
            assert result == project_search_response["result"]

    def test_call_method_error_response(
        self, conduit_client: ConduitClient, error_response: Dict[str, Any]
    ) -> None:
        """Test that API error responses raise ConduitError."""
        with patch.object(conduit_client, "_session") as mock_session:
            mock_response = MagicMock()
            mock_response.json.return_value = error_response
            mock_response.raise_for_status = MagicMock()
            mock_session.post.return_value = mock_response

            with pytest.raises(ConduitError, match="Invalid API token"):
                conduit_client.call_method("project.search", {})

    def test_call_method_includes_api_token(
        self, conduit_client: ConduitClient, project_search_response: Dict[str, Any]
    ) -> None:
        """Test that API token is included in request."""
        with patch.object(conduit_client, "_session") as mock_session:
            mock_response = MagicMock()
            mock_response.json.return_value = project_search_response
            mock_response.raise_for_status = MagicMock()
            mock_session.post.return_value = mock_response

            conduit_client.call_method("project.search", {"foo": "bar"})

            call_args = mock_session.post.call_args
            # Check that api.token is in the form data
            assert "api.token" in call_args.kwargs.get("data", {}) or "api.token" in (
                call_args.args[1] if len(call_args.args) > 1 else {}
            )


class TestConduitClientProjectSearch:
    """Tests for ConduitClient.project_search()."""

    def test_project_search_by_slugs(
        self, conduit_client: ConduitClient, project_search_response: Dict[str, Any]
    ) -> None:
        """Test searching projects by slugs."""
        with patch.object(conduit_client, "call_method") as mock_call:
            mock_call.return_value = project_search_response["result"]

            results = conduit_client.project_search(
                slugs=["android-reviewers", "desktop-theme-reviewers"],
                attachments={"members": True},
            )

            # Fixture contains 5 projects
            assert len(results) == 5
            # First project in fixture is android-reviewers
            assert results[0]["fields"]["slug"] == "android-reviewers"

    def test_project_search_returns_members(
        self, conduit_client: ConduitClient, project_search_response: Dict[str, Any]
    ) -> None:
        """Test that project search returns member PHIDs when requested."""
        with patch.object(conduit_client, "call_method") as mock_call:
            mock_call.return_value = project_search_response["result"]

            results = conduit_client.project_search(
                slugs=["android-reviewers"],
                attachments={"members": True},
            )

            members = results[0]["attachments"]["members"]["members"]
            # First project (android-reviewers) has 42 members
            assert len(members) == 42
            # First member PHID from fixture
            assert members[0]["phid"] == "PHID-USER-io424dlf7a5y7w6u5eoj"

    def test_project_search_no_constraints_raises(self, conduit_client: ConduitClient) -> None:
        """Test that calling without slugs or phids raises ValueError."""
        with pytest.raises(ValueError, match="slugs.*phids"):
            conduit_client.project_search()

    def test_project_search_not_found(
        self, conduit_client: ConduitClient, project_not_found_response: Dict[str, Any]
    ) -> None:
        """Test searching for non-existent project returns empty list."""
        with patch.object(conduit_client, "call_method") as mock_call:
            mock_call.return_value = project_not_found_response["result"]

            results = conduit_client.project_search(slugs=["nonexistent-project"])

            assert results == []


class TestConduitClientUserSearch:
    """Tests for ConduitClient.user_search()."""

    def test_user_search_by_phids(
        self, conduit_client: ConduitClient, user_search_response: Dict[str, Any]
    ) -> None:
        """Test searching users by PHIDs."""
        with patch.object(conduit_client, "call_method") as mock_call:
            mock_call.return_value = user_search_response["result"]

            results = conduit_client.user_search(
                phids=["PHID-USER-io424dlf7a5y7w6u5eoj", "PHID-USER-72vunn4hyp5oto4bseme"]
            )

            # Fixture returns 67 users
            assert len(results) == 67
            # First user has anonymized username
            assert results[0]["fields"]["username"] == "USER-858a93f1"

    def test_user_search_returns_usernames(
        self, conduit_client: ConduitClient, user_search_response: Dict[str, Any]
    ) -> None:
        """Test that user search returns usernames."""
        with patch.object(conduit_client, "call_method") as mock_call:
            mock_call.return_value = user_search_response["result"]

            results = conduit_client.user_search(phids=["PHID-USER-io424dlf7a5y7w6u5eoj"])

            usernames = [r["fields"]["username"] for r in results]
            # Usernames are anonymized in fixtures
            assert "USER-858a93f1" in usernames
            assert "USER-4799c2f1" in usernames

    def test_user_search_no_constraints_raises(self, conduit_client: ConduitClient) -> None:
        """Test that calling without phids or usernames raises ValueError."""
        with pytest.raises(ValueError, match="phids.*usernames"):
            conduit_client.user_search()


class TestConduitClientBugzillaAccountSearch:
    """Tests for ConduitClient.bugzilla_account_search()."""

    @pytest.fixture
    def bugzilla_account_search_response(self, conduit_fixtures_path: Path) -> Dict[str, Any]:
        with open(conduit_fixtures_path / "bugzilla_account_search_response.json") as f:
            data: Dict[str, Any] = json.load(f)
            return data

    @pytest.fixture
    def bugzilla_account_search_empty_response(
        self, conduit_fixtures_path: Path
    ) -> Dict[str, Any]:
        with open(conduit_fixtures_path / "bugzilla_account_search_empty_response.json") as f:
            data: Dict[str, Any] = json.load(f)
            return data

    def test_by_phid_returns_id(
        self,
        conduit_client: ConduitClient,
        bugzilla_account_search_response: Dict[str, Any],
    ) -> None:
        with patch.object(conduit_client, "call_method") as mock_call:
            mock_call.return_value = bugzilla_account_search_response["result"]

            results = conduit_client.bugzilla_account_search(
                phids=["PHID-USER-aabe0232e32f0b571107"]
            )

            assert len(results) == 1
            assert results[0]["id"] == "99999999"
            assert results[0]["phid"] == "PHID-USER-aabe0232e32f0b571107"

    def test_flattens_direct_phids_param(self, conduit_client: ConduitClient) -> None:
        """phids/ids go at the top level, not inside `constraints`."""
        with patch.object(conduit_client, "call_method") as mock_call:
            mock_call.return_value = []

            conduit_client.bugzilla_account_search(phids=["PHID-USER-x"])

            mock_call.assert_called_once_with(
                "bugzilla.account.search", {"phids": ["PHID-USER-x"]}
            )

    def test_by_id(self, conduit_client: ConduitClient) -> None:
        with patch.object(conduit_client, "call_method") as mock_call:
            mock_call.return_value = [{"id": "91159", "phid": "PHID-USER-x"}]

            results = conduit_client.bugzilla_account_search(ids=["91159"])

            mock_call.assert_called_once_with("bugzilla.account.search", {"ids": ["91159"]})
            assert results[0]["id"] == "91159"

    def test_empty_result(
        self,
        conduit_client: ConduitClient,
        bugzilla_account_search_empty_response: Dict[str, Any],
    ) -> None:
        """Users without a BMO account yield an empty list (not an error)."""
        with patch.object(conduit_client, "call_method") as mock_call:
            mock_call.return_value = bugzilla_account_search_empty_response["result"]

            results = conduit_client.bugzilla_account_search(phids=["PHID-USER-x"])

            assert results == []

    def test_no_constraints_raises(self, conduit_client: ConduitClient) -> None:
        with pytest.raises(ValueError, match="ids.*phids"):
            conduit_client.bugzilla_account_search()


class TestConduitClientRateLimit:
    """Conduit calls back off on a rate limit instead of failing."""

    @staticmethod
    def _limited_response(retry_after: str, status_code: int = 429) -> MagicMock:
        response = MagicMock()
        response.status_code = status_code
        response.headers = {"retry-after": retry_after}
        response.text = ""
        return response

    def test_call_method_retries_after_rate_limit(
        self, conduit_client: ConduitClient, project_search_response: Dict[str, Any]
    ) -> None:
        ok = MagicMock()
        ok.status_code = 200
        ok.headers = {}
        ok.json.return_value = project_search_response

        with patch.object(conduit_client, "_session") as mock_session:
            mock_session.post.side_effect = [
                self._limited_response("3"),
                ok,
            ]

            with patch("herald_scraper.rate_limit.time.sleep") as sleep:
                result = conduit_client.call_method("project.search", {})

        sleep.assert_called_once_with(3.0)
        assert result == project_search_response["result"]
        assert mock_session.post.call_count == 2

    def test_call_method_gives_up_after_max_retries(
        self, conduit_client: ConduitClient
    ) -> None:
        with patch.object(conduit_client, "_session") as mock_session:
            mock_session.post.return_value = self._limited_response("1")

            with patch("herald_scraper.rate_limit.time.sleep"):
                with pytest.raises(RateLimitError):
                    conduit_client.call_method("project.search", {})

        assert mock_session.post.call_count == MAX_RATE_LIMIT_RETRIES + 1
