"""Tests for the resolvers module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from herald_scraper.models import Action, Group, Reviewer, Rule
from herald_scraper.resolvers import GroupCollector

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestGroupCollector:
    """Tests for GroupCollector class."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock HeraldClient."""
        return MagicMock()

    @pytest.fixture
    def collector(self, mock_client):
        """Create a GroupCollector with mock client."""
        return GroupCollector(mock_client)

    @pytest.fixture
    def sample_rules(self):
        """Create sample rules with various reviewer types."""
        return [
            Rule(
                id="H420",
                name="Test Rule 1",
                author="user@mozilla.com",
                status="active",
                type="differential-revision",
                conditions=[],
                actions=[
                    Action(
                        type="add-reviewers",
                        reviewers=[
                            Reviewer(target="omc-reviewers", blocking=True),
                            Reviewer(target="user@mozilla.com", blocking=False),
                        ],
                    ),
                ],
            ),
            Rule(
                id="H421",
                name="Test Rule 2",
                author="another@mozilla.com",
                status="active",
                type="differential-revision",
                conditions=[],
                actions=[
                    Action(
                        type="add-reviewers",
                        reviewers=[
                            Reviewer(target="android-reviewers", blocking=True),
                        ],
                    ),
                ],
            ),
            Rule(
                id="H422",
                name="Test Rule 3",
                author="test@mozilla.com",
                status="active",
                type="differential-revision",
                conditions=[],
                actions=[
                    Action(
                        type="add-reviewers",
                        reviewers=[
                            Reviewer(target="omc-reviewers", blocking=True),  # Duplicate
                        ],
                    ),
                ],
            ),
        ]

    def test_extract_group_slugs_from_rules(self, collector, sample_rules):
        """Test extracting unique group slugs from rules."""
        slugs = collector.extract_group_slugs_from_rules(sample_rules)

        # Should find 2 unique groups (omc-reviewers appears twice)
        assert len(slugs) == 2
        assert "omc-reviewers" in slugs
        assert "android-reviewers" in slugs
        # Users should NOT be included
        assert "user@mozilla.com" not in slugs
        assert "another@mozilla.com" not in slugs

    def test_extract_group_slugs_empty_rules(self, collector):
        """Test extracting groups from empty rule list."""
        slugs = collector.extract_group_slugs_from_rules([])
        assert len(slugs) == 0

    def test_extract_group_slugs_no_reviewers(self, collector):
        """Test extracting groups when rules have no reviewer actions."""
        rules = [
            Rule(
                id="H999",
                name="No Reviewers",
                author="test@mozilla.com",
                status="active",
                type="differential-revision",
                conditions=[],
                actions=[
                    Action(type="add-subscribers", targets=["sub@mozilla.com"]),
                ],
            ),
        ]
        slugs = collector.extract_group_slugs_from_rules(rules)
        assert len(slugs) == 0

    def test_fetch_group_success(self, collector, mock_client):
        """Test successfully fetching a group."""
        # Load fixture
        fixture_path = FIXTURES_DIR / "groups" / "omc-reviewers.html"
        if not fixture_path.exists():
            pytest.skip("omc-reviewers fixture not found")

        mock_client.fetch_project.return_value = fixture_path.read_text()

        group = collector.fetch_group("omc-reviewers")

        assert group is not None
        assert group.id == "omc-reviewers"
        assert group.display_name == "omc-reviewers"
        assert isinstance(group.members, list)
        mock_client.fetch_project.assert_called_once_with("omc-reviewers")

    def test_fetch_group_caching(self, collector, mock_client):
        """Test that groups are cached after first fetch."""
        fixture_path = FIXTURES_DIR / "groups" / "omc-reviewers.html"
        if not fixture_path.exists():
            pytest.skip("omc-reviewers fixture not found")

        mock_client.fetch_project.return_value = fixture_path.read_text()

        # First fetch
        group1 = collector.fetch_group("omc-reviewers")
        # Second fetch (should use cache)
        group2 = collector.fetch_group("omc-reviewers")

        assert group1 is group2  # Same object from cache
        # Client should only be called once
        mock_client.fetch_project.assert_called_once()

    def test_fetch_group_failure(self, collector, mock_client):
        """Test handling fetch failure."""
        mock_client.fetch_project.side_effect = Exception("Network error")

        group = collector.fetch_group("nonexistent-group")

        assert group is None

    def test_collect_all_groups(self, collector, mock_client, sample_rules):
        """Test collecting all groups from rules."""
        # Load fixtures for both groups
        omc_fixture = FIXTURES_DIR / "groups" / "omc-reviewers.html"
        android_fixture = FIXTURES_DIR / "groups" / "android-reviewers.html"

        if not omc_fixture.exists() or not android_fixture.exists():
            pytest.skip("Group fixtures not found")

        def fetch_project_side_effect(slug):
            if slug == "omc-reviewers":
                return omc_fixture.read_text()
            elif slug == "android-reviewers":
                return android_fixture.read_text()
            raise Exception(f"Unknown group: {slug}")

        mock_client.fetch_project.side_effect = fetch_project_side_effect

        groups = collector.collect_all_groups(sample_rules)

        assert len(groups) == 2
        assert "omc-reviewers" in groups
        assert "android-reviewers" in groups
        assert isinstance(groups["omc-reviewers"], Group)
        assert isinstance(groups["android-reviewers"], Group)

    def test_collect_all_groups_partial_failure(self, collector, mock_client, sample_rules):
        """Test collecting groups when some fail to fetch."""
        omc_fixture = FIXTURES_DIR / "groups" / "omc-reviewers.html"
        if not omc_fixture.exists():
            pytest.skip("omc-reviewers fixture not found")

        def fetch_project_side_effect(slug):
            if slug == "omc-reviewers":
                return omc_fixture.read_text()
            raise Exception(f"Failed to fetch: {slug}")

        mock_client.fetch_project.side_effect = fetch_project_side_effect

        groups = collector.collect_all_groups(sample_rules)

        # Should still get omc-reviewers even though android-reviewers failed
        assert len(groups) == 1
        assert "omc-reviewers" in groups
        assert "android-reviewers" not in groups

    def test_clear_cache(self, collector, mock_client):
        """Test clearing the cache."""
        fixture_path = FIXTURES_DIR / "groups" / "omc-reviewers.html"
        if not fixture_path.exists():
            pytest.skip("omc-reviewers fixture not found")

        mock_client.fetch_project.return_value = fixture_path.read_text()

        # Fetch to populate cache
        collector.fetch_group("omc-reviewers")
        assert mock_client.fetch_project.call_count == 1

        # Clear cache
        collector.clear_cache()

        # Fetch again - should call client again
        collector.fetch_group("omc-reviewers")
        assert mock_client.fetch_project.call_count == 2


class TestGroupCollectorIntegration:
    """Integration tests for GroupCollector with real fixtures."""

    @pytest.fixture
    def group_fixtures(self):
        """Get all available group fixtures (excluding members pages)."""
        groups_dir = FIXTURES_DIR / "groups"
        if not groups_dir.exists():
            pytest.skip("Groups fixtures directory not found")
        # Exclude members page fixtures (*-members.html)
        return {
            f.stem: f for f in groups_dir.glob("*.html")
            if not f.stem.endswith("-members")
        }

    def test_collect_groups_with_fixtures(self, group_fixtures):
        """Test collecting groups using real fixture files."""
        if not group_fixtures:
            pytest.skip("No group fixtures found")

        mock_client = MagicMock()

        def fetch_project_side_effect(slug):
            if slug in group_fixtures:
                return group_fixtures[slug].read_text()
            raise Exception(f"No fixture for: {slug}")

        mock_client.fetch_project.side_effect = fetch_project_side_effect

        collector = GroupCollector(mock_client)

        # Create rules that reference all fixture groups
        rules = [
            Rule(
                id="H999",
                name="Test All Groups",
                author="test@mozilla.com",
                status="active",
                type="differential-revision",
                conditions=[],
                actions=[
                    Action(
                        type="add-reviewers",
                        reviewers=[
                            Reviewer(target=slug, blocking=True)
                            for slug in group_fixtures.keys()
                        ],
                    ),
                ],
            ),
        ]

        groups = collector.collect_all_groups(rules)

        # Should collect all groups from fixtures
        assert len(groups) == len(group_fixtures)
        for slug in group_fixtures.keys():
            assert slug in groups
            assert isinstance(groups[slug], Group)
            assert groups[slug].id == slug
