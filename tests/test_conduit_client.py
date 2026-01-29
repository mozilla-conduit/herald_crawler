"""Tests for ConduitClient and ConduitGroupCollector."""

import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest
import requests

from herald_scraper.conduit_client import ConduitClient, ConduitError
from herald_scraper.models import Action, Condition, Group, Reviewer, Rule
from herald_scraper.resolvers import ConduitGroupCollector


# --- Fixtures ---


@pytest.fixture
def fixtures_path() -> Path:
    """Path to conduit fixtures directory."""
    return Path(__file__).parent / "fixtures" / "conduit"


@pytest.fixture
def project_search_response(fixtures_path: Path) -> Dict[str, Any]:
    """Load project.search response fixture."""
    with open(fixtures_path / "project_search_response.json") as f:
        return json.load(f)


@pytest.fixture
def user_search_response(fixtures_path: Path) -> Dict[str, Any]:
    """Load user.search response fixture."""
    with open(fixtures_path / "user_search_response.json") as f:
        return json.load(f)


@pytest.fixture
def error_response(fixtures_path: Path) -> Dict[str, Any]:
    """Load error response fixture."""
    with open(fixtures_path / "error_response.json") as f:
        return json.load(f)


@pytest.fixture
def project_not_found_response(fixtures_path: Path) -> Dict[str, Any]:
    """Load project not found response fixture."""
    with open(fixtures_path / "project_not_found_response.json") as f:
        return json.load(f)


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
        delay=0,  # No delay for tests
    )


@pytest.fixture
def sample_rules() -> list[Rule]:
    """Create sample rules with group reviewers for testing."""
    return [
        Rule(
            id="H420",
            name="Test Rule 1",
            author="testuser",
            status="active",
            type="differential-revision",
            conditions=[
                Condition(type="repository", operator="is-any-of", value=["repo1"]),
            ],
            actions=[
                Action(
                    type="add-reviewers",
                    reviewers=[
                        Reviewer(target="omc-reviewers", blocking=True, is_group=True),
                    ],
                ),
            ],
        ),
        Rule(
            id="H421",
            name="Test Rule 2",
            author="testuser",
            status="active",
            type="differential-revision",
            conditions=[
                Condition(type="repository", operator="is-any-of", value=["repo2"]),
            ],
            actions=[
                Action(
                    type="add-reviewers",
                    reviewers=[
                        Reviewer(target="android-reviewers", blocking=True, is_group=True),
                        Reviewer(target="someuser", blocking=False, is_group=False),
                    ],
                ),
            ],
        ),
    ]


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
        assert client.delay == 1.0  # default
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
            delay=2.5,
            timeout=60.0,
            user_agent="TestAgent/1.0",
        )
        assert client.delay == 2.5
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
            assert "api.token" in call_args.kwargs.get("data", {}) or \
                   "api.token" in (call_args.args[1] if len(call_args.args) > 1 else {})


class TestConduitClientProjectSearch:
    """Tests for ConduitClient.project_search()."""

    def test_project_search_by_slugs(
        self, conduit_client: ConduitClient, project_search_response: Dict[str, Any]
    ) -> None:
        """Test searching projects by slugs."""
        with patch.object(conduit_client, "call_method") as mock_call:
            mock_call.return_value = project_search_response["result"]

            results = conduit_client.project_search(
                slugs=["omc-reviewers", "android-reviewers"],
                attachments={"members": True},
            )

            assert len(results) == 2
            assert results[0]["fields"]["slug"] == "omc-reviewers"
            assert results[1]["fields"]["slug"] == "android-reviewers"

    def test_project_search_returns_members(
        self, conduit_client: ConduitClient, project_search_response: Dict[str, Any]
    ) -> None:
        """Test that project search returns member PHIDs when requested."""
        with patch.object(conduit_client, "call_method") as mock_call:
            mock_call.return_value = project_search_response["result"]

            results = conduit_client.project_search(
                slugs=["omc-reviewers"],
                attachments={"members": True},
            )

            members = results[0]["attachments"]["members"]["members"]
            assert len(members) == 3
            assert members[0]["phid"] == "PHID-USER-aaaaaaaaaaaaaaaaaa01"

    def test_project_search_no_constraints_raises(
        self, conduit_client: ConduitClient
    ) -> None:
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
                phids=["PHID-USER-aaaaaaaaaaaaaaaaaa01", "PHID-USER-aaaaaaaaaaaaaaaaaa02"]
            )

            assert len(results) == 5  # fixture returns all 5 users
            assert results[0]["fields"]["username"] == "alice"

    def test_user_search_returns_usernames(
        self, conduit_client: ConduitClient, user_search_response: Dict[str, Any]
    ) -> None:
        """Test that user search returns usernames."""
        with patch.object(conduit_client, "call_method") as mock_call:
            mock_call.return_value = user_search_response["result"]

            results = conduit_client.user_search(phids=["PHID-USER-xxx"])

            usernames = [r["fields"]["username"] for r in results]
            assert "alice" in usernames
            assert "bob" in usernames

    def test_user_search_no_constraints_raises(
        self, conduit_client: ConduitClient
    ) -> None:
        """Test that calling without phids or usernames raises ValueError."""
        with pytest.raises(ValueError, match="phids.*usernames"):
            conduit_client.user_search()


# --- ConduitGroupCollector Tests ---


class TestConduitGroupCollectorInit:
    """Tests for ConduitGroupCollector initialization."""

    def test_initialization(self, conduit_client: ConduitClient) -> None:
        """Test creating a collector with a client."""
        collector = ConduitGroupCollector(conduit_client)
        assert collector.client is conduit_client
        assert collector._group_cache == {}
        assert collector._phid_to_username == {}


class TestConduitGroupCollectorExtractSlugs:
    """Tests for ConduitGroupCollector.extract_group_slugs_from_rules()."""

    def test_extracts_group_slugs(
        self, conduit_client: ConduitClient, sample_rules: list[Rule]
    ) -> None:
        """Test extracting group slugs from rules."""
        collector = ConduitGroupCollector(conduit_client)

        slugs = collector.extract_group_slugs_from_rules(sample_rules)

        assert "omc-reviewers" in slugs
        assert "android-reviewers" in slugs
        assert "someuser" not in slugs  # is_group=False

    def test_excludes_users(
        self, conduit_client: ConduitClient, sample_rules: list[Rule]
    ) -> None:
        """Test that users (is_group=False) are excluded."""
        collector = ConduitGroupCollector(conduit_client)

        slugs = collector.extract_group_slugs_from_rules(sample_rules)

        assert len(slugs) == 2  # Only the two groups


class TestConduitGroupCollectorFetchGroup:
    """Tests for ConduitGroupCollector.fetch_group()."""

    def test_fetch_group_success(
        self,
        conduit_client: ConduitClient,
        project_search_response: Dict[str, Any],
        user_search_response: Dict[str, Any],
    ) -> None:
        """Test fetching a group successfully."""
        collector = ConduitGroupCollector(conduit_client)

        with patch.object(conduit_client, "project_search") as mock_project:
            with patch.object(conduit_client, "user_search") as mock_user:
                # Return only the first project (omc-reviewers)
                mock_project.return_value = [project_search_response["result"]["data"][0]]
                mock_user.return_value = user_search_response["result"]["data"][:3]

                group = collector.fetch_group("omc-reviewers")

                assert group is not None
                assert group.id == "omc-reviewers"
                assert group.display_name == "omc-reviewers"
                assert "alice" in group.members
                assert "bob" in group.members
                assert "charlie" in group.members

    def test_fetch_group_not_found(
        self,
        conduit_client: ConduitClient,
        project_not_found_response: Dict[str, Any],
    ) -> None:
        """Test fetching a non-existent group returns None."""
        collector = ConduitGroupCollector(conduit_client)

        with patch.object(conduit_client, "project_search") as mock_project:
            mock_project.return_value = []

            group = collector.fetch_group("nonexistent-group")

            assert group is None

    def test_fetch_group_caches_result(
        self,
        conduit_client: ConduitClient,
        project_search_response: Dict[str, Any],
        user_search_response: Dict[str, Any],
    ) -> None:
        """Test that fetch_group caches results."""
        collector = ConduitGroupCollector(conduit_client)

        with patch.object(conduit_client, "project_search") as mock_project:
            with patch.object(conduit_client, "user_search") as mock_user:
                mock_project.return_value = [project_search_response["result"]["data"][0]]
                mock_user.return_value = user_search_response["result"]["data"][:3]

                # First call
                group1 = collector.fetch_group("omc-reviewers")
                # Second call should use cache
                group2 = collector.fetch_group("omc-reviewers")

                assert group1 is group2
                assert mock_project.call_count == 1  # Only called once


class TestConduitGroupCollectorCollectAll:
    """Tests for ConduitGroupCollector.collect_all_groups()."""

    def test_collect_all_groups(
        self,
        conduit_client: ConduitClient,
        sample_rules: list[Rule],
        project_search_response: Dict[str, Any],
        user_search_response: Dict[str, Any],
    ) -> None:
        """Test collecting all groups from rules."""
        collector = ConduitGroupCollector(conduit_client)

        with patch.object(conduit_client, "project_search") as mock_project:
            with patch.object(conduit_client, "user_search") as mock_user:
                # Set up returns for each group
                def project_side_effect(slugs=None, **kwargs):
                    if slugs and "omc-reviewers" in slugs:
                        return [project_search_response["result"]["data"][0]]
                    elif slugs and "android-reviewers" in slugs:
                        return [project_search_response["result"]["data"][1]]
                    return []

                mock_project.side_effect = project_side_effect
                mock_user.return_value = user_search_response["result"]["data"]

                groups = collector.collect_all_groups(sample_rules)

                assert "omc-reviewers" in groups
                assert "android-reviewers" in groups
                assert len(groups) == 2

    def test_collect_all_groups_max_limit(
        self,
        conduit_client: ConduitClient,
        sample_rules: list[Rule],
        project_search_response: Dict[str, Any],
        user_search_response: Dict[str, Any],
    ) -> None:
        """Test that max_groups limit is respected."""
        collector = ConduitGroupCollector(conduit_client)

        with patch.object(conduit_client, "project_search") as mock_project:
            with patch.object(conduit_client, "user_search") as mock_user:
                mock_project.return_value = [project_search_response["result"]["data"][0]]
                mock_user.return_value = user_search_response["result"]["data"][:3]

                groups = collector.collect_all_groups(sample_rules, max_groups=1)

                assert len(groups) == 1


class TestConduitGroupCollectorClearCache:
    """Tests for ConduitGroupCollector.clear_cache()."""

    def test_clear_cache(self, conduit_client: ConduitClient) -> None:
        """Test clearing the cache."""
        collector = ConduitGroupCollector(conduit_client)
        collector._group_cache["test"] = Group(id="test", display_name="Test", members=[])
        collector._phid_to_username["PHID-xxx"] = "testuser"

        collector.clear_cache()

        assert collector._group_cache == {}
        assert collector._phid_to_username == {}
