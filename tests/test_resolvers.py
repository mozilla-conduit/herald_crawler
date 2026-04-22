"""Tests for the resolvers module."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from herald_scraper.models import Action, Group, Reviewer, Rule
from herald_scraper.resolvers import GroupCollector, UsernameResolver

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
        # Load fixtures
        project_fixture = FIXTURES_DIR / "groups" / "omc-reviewers.html"
        members_fixture = FIXTURES_DIR / "groups" / "omc-reviewers-members.html"
        if not project_fixture.exists() or not members_fixture.exists():
            pytest.skip("omc-reviewers fixtures not found")

        mock_client.fetch_project.return_value = project_fixture.read_text()
        mock_client.fetch_project_members.return_value = members_fixture.read_text()

        group = collector.fetch_group("omc-reviewers")

        assert group is not None
        assert group.id == "omc-reviewers"
        assert group.display_name == "omc-reviewers"
        assert isinstance(group.members, list)
        assert len(group.members) == 9  # Expected from members page
        mock_client.fetch_project.assert_called_once_with("omc-reviewers")
        mock_client.fetch_project_members.assert_called_once_with("171")

    def test_fetch_group_caching(self, collector, mock_client):
        """Test that groups are cached after first fetch."""
        project_fixture = FIXTURES_DIR / "groups" / "omc-reviewers.html"
        members_fixture = FIXTURES_DIR / "groups" / "omc-reviewers-members.html"
        if not project_fixture.exists() or not members_fixture.exists():
            pytest.skip("omc-reviewers fixtures not found")

        mock_client.fetch_project.return_value = project_fixture.read_text()
        mock_client.fetch_project_members.return_value = members_fixture.read_text()

        # First fetch
        group1 = collector.fetch_group("omc-reviewers")
        # Second fetch (should use cache)
        group2 = collector.fetch_group("omc-reviewers")

        assert group1 is group2  # Same object from cache
        # Client should only be called once
        mock_client.fetch_project.assert_called_once()
        mock_client.fetch_project_members.assert_called_once()

    def test_fetch_group_failure(self, collector, mock_client):
        """Test handling fetch failure."""
        mock_client.fetch_project.side_effect = Exception("Network error")

        group = collector.fetch_group("nonexistent-group")

        assert group is None

    def test_collect_all_groups(self, collector, mock_client, sample_rules):
        """Test collecting all groups from rules."""
        # Load fixtures for both groups
        omc_project = FIXTURES_DIR / "groups" / "omc-reviewers.html"
        omc_members = FIXTURES_DIR / "groups" / "omc-reviewers-members.html"
        android_project = FIXTURES_DIR / "groups" / "android-reviewers.html"
        android_members = FIXTURES_DIR / "groups" / "android-reviewers-members.html"

        if not all(
            f.exists() for f in [omc_project, omc_members, android_project, android_members]
        ):
            pytest.skip("Group fixtures not found")

        def fetch_project_side_effect(slug):
            if slug == "omc-reviewers":
                return omc_project.read_text()
            elif slug == "android-reviewers":
                return android_project.read_text()
            raise Exception(f"Unknown group: {slug}")

        def fetch_members_side_effect(project_id):
            if project_id == "171":  # omc-reviewers
                return omc_members.read_text()
            elif project_id == "200":  # android-reviewers
                return android_members.read_text()
            raise Exception(f"Unknown project_id: {project_id}")

        mock_client.fetch_project.side_effect = fetch_project_side_effect
        mock_client.fetch_project_members.side_effect = fetch_members_side_effect

        groups = collector.collect_all_groups(sample_rules)

        assert len(groups) == 2
        assert "omc-reviewers" in groups
        assert "android-reviewers" in groups
        assert isinstance(groups["omc-reviewers"], Group)
        assert isinstance(groups["android-reviewers"], Group)
        assert len(groups["omc-reviewers"].members) == 9
        assert len(groups["android-reviewers"].members) == 42

    def test_collect_all_groups_partial_failure(self, collector, mock_client, sample_rules):
        """Test collecting groups when some fail to fetch."""
        omc_project = FIXTURES_DIR / "groups" / "omc-reviewers.html"
        omc_members = FIXTURES_DIR / "groups" / "omc-reviewers-members.html"
        if not omc_project.exists() or not omc_members.exists():
            pytest.skip("omc-reviewers fixtures not found")

        def fetch_project_side_effect(slug):
            if slug == "omc-reviewers":
                return omc_project.read_text()
            raise Exception(f"Failed to fetch: {slug}")

        def fetch_members_side_effect(project_id):
            if project_id == "171":  # omc-reviewers
                return omc_members.read_text()
            raise Exception(f"Failed to fetch members: {project_id}")

        mock_client.fetch_project.side_effect = fetch_project_side_effect
        mock_client.fetch_project_members.side_effect = fetch_members_side_effect

        groups = collector.collect_all_groups(sample_rules)

        # Should still get omc-reviewers even though android-reviewers failed
        assert len(groups) == 1
        assert "omc-reviewers" in groups
        assert "android-reviewers" not in groups

    def test_clear_cache(self, collector, mock_client):
        """Test clearing the cache."""
        project_fixture = FIXTURES_DIR / "groups" / "omc-reviewers.html"
        members_fixture = FIXTURES_DIR / "groups" / "omc-reviewers-members.html"
        if not project_fixture.exists() or not members_fixture.exists():
            pytest.skip("omc-reviewers fixtures not found")

        mock_client.fetch_project.return_value = project_fixture.read_text()
        mock_client.fetch_project_members.return_value = members_fixture.read_text()

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
        return {f.stem: f for f in groups_dir.glob("*.html") if not f.stem.endswith("-members")}

    @pytest.fixture
    def members_fixtures(self):
        """Get all available members page fixtures."""
        groups_dir = FIXTURES_DIR / "groups"
        if not groups_dir.exists():
            pytest.skip("Groups fixtures directory not found")
        return {f.stem.replace("-members", ""): f for f in groups_dir.glob("*-members.html")}

    # Project ID mapping for fixtures (extracted from project page fixtures)
    PROJECT_IDS = {
        "android-reviewers": "200",
        "desktop-theme-reviewers": "141",
        "dom-storage-reviewers": "147",
        "geckodriver-reviewers": "232",
        "geckoview-api-reviewers": "226",
        "omc-reviewers": "171",
        "profiler-reviewers": "190",
        "reusable-components-reviewers-rotation": "185",
        "sidebar-reviewers-rotation": "207",
        "win-reviewers": "189",
    }

    def test_collect_groups_with_fixtures(self, group_fixtures, members_fixtures):
        """Test collecting groups using real fixture files."""
        if not group_fixtures or not members_fixtures:
            pytest.skip("No group fixtures found")

        mock_client = MagicMock()

        def fetch_project_side_effect(slug):
            if slug in group_fixtures:
                return group_fixtures[slug].read_text()
            raise Exception(f"No fixture for: {slug}")

        def fetch_members_side_effect(project_id):
            # Find the slug for this project_id
            for slug, pid in self.PROJECT_IDS.items():
                if pid == project_id and slug in members_fixtures:
                    return members_fixtures[slug].read_text()
            raise Exception(f"No members fixture for project_id: {project_id}")

        mock_client.fetch_project.side_effect = fetch_project_side_effect
        mock_client.fetch_project_members.side_effect = fetch_members_side_effect

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
                            Reviewer(target=slug, blocking=True) for slug in group_fixtures.keys()
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
            # Verify members were extracted from members page
            if slug in members_fixtures:
                assert len(groups[slug].members) > 0, f"Expected members for {slug}"


class TestUsernameResolver:
    """Tests for UsernameResolver class."""

    @pytest.fixture
    def mock_people_client(self):
        """Create a mock PeopleDirectoryClient."""
        return MagicMock()

    @pytest.fixture
    def resolver(self, mock_people_client):
        """Create a UsernameResolver with mock client."""
        return UsernameResolver(mock_people_client)

    @pytest.fixture
    def sample_rules(self):
        """Create sample rules with various user types."""
        return [
            Rule(
                id="H420",
                name="Test Rule 1",
                author="alice@mozilla.com",
                status="active",
                type="differential-revision",
                conditions=[],
                actions=[
                    Action(
                        type="add-reviewers",
                        reviewers=[
                            Reviewer(target="omc-reviewers", blocking=True),
                            Reviewer(target="bob@mozilla.com", blocking=False),
                        ],
                    ),
                ],
            ),
            Rule(
                id="H421",
                name="Test Rule 2",
                author="charlie@mozilla.com",
                status="active",
                type="differential-revision",
                conditions=[],
                actions=[
                    Action(
                        type="add-reviewers",
                        reviewers=[
                            Reviewer(target="android-reviewers", blocking=True),
                            Reviewer(target="alice@mozilla.com", blocking=False),  # Duplicate
                        ],
                    ),
                ],
            ),
        ]

    @pytest.fixture
    def sample_groups(self):
        """Create sample groups with members."""
        return {
            "omc-reviewers": Group(
                id="omc-reviewers",
                display_name="OMC Reviewers",
                members=["dan", "eve", "alice"],  # alice appears in both rules and group
            ),
            "android-reviewers": Group(
                id="android-reviewers",
                display_name="Android Reviewers",
                members=["frank", "grace"],
            ),
        }

    def test_extract_usernames_from_rules(self, resolver, sample_rules):
        """Test extracting usernames from rules, excluding groups."""
        group_slugs = {"omc-reviewers", "android-reviewers"}
        username_refs = resolver.extract_usernames_from_rules(sample_rules, group_slugs)

        # Should find 3 unique users with @ (excluding groups)
        assert len(username_refs) == 3
        assert "alice@mozilla.com" in username_refs
        assert "bob@mozilla.com" in username_refs
        assert "charlie@mozilla.com" in username_refs
        # Groups should NOT be included
        assert "omc-reviewers" not in username_refs
        assert "android-reviewers" not in username_refs

    def test_extract_usernames_from_rules_tracks_references(self, resolver, sample_rules):
        """Test that username references are tracked correctly."""
        group_slugs = {"omc-reviewers", "android-reviewers"}
        username_refs = resolver.extract_usernames_from_rules(sample_rules, group_slugs)

        # alice appears in both rules (as author in H420, as reviewer in H421)
        assert "H420" in username_refs["alice@mozilla.com"]
        assert "H421" in username_refs["alice@mozilla.com"]
        # bob only appears in H420
        assert username_refs["bob@mozilla.com"] == ["H420"]
        # charlie is author of H421
        assert username_refs["charlie@mozilla.com"] == ["H421"]

    def test_extract_usernames_from_groups(self, resolver, sample_groups):
        """Test extracting usernames from group members."""
        username_refs = resolver.extract_usernames_from_groups(sample_groups)

        # Should find 5 unique members
        assert len(username_refs) == 5
        assert "dan" in username_refs
        assert "eve" in username_refs
        assert "alice" in username_refs
        assert "frank" in username_refs
        assert "grace" in username_refs

    def test_extract_usernames_from_groups_tracks_references(self, resolver, sample_groups):
        """Test that group member references are tracked correctly."""
        username_refs = resolver.extract_usernames_from_groups(sample_groups)

        assert username_refs["dan"] == ["group:omc-reviewers"]
        assert username_refs["frank"] == ["group:android-reviewers"]

    def test_resolve_username_success(self, resolver, mock_people_client):
        """Test successfully resolving a username."""
        from herald_scraper.people_client import GitHubResolution

        mock_people_client.resolve_github.return_value = GitHubResolution(
            username="alice-gh", user_id=12345
        )

        github_user = resolver.resolve_username("alice@mozilla.com")

        assert github_user is not None
        assert github_user.username == "alice-gh"
        assert github_user.user_id == 12345
        mock_people_client.resolve_github.assert_called_once_with(
            "alice", expected_bmo_id=None, expected_real_name=None
        )

    def test_resolve_username_not_found(self, resolver, mock_people_client):
        """Test resolving a username that doesn't exist."""
        from herald_scraper.people_client import GitHubResolution

        mock_people_client.resolve_github.return_value = GitHubResolution(
            username=None, user_id=None, reason="pmo_profile_not_found"
        )

        github_user = resolver.resolve_username("unknown@mozilla.com")

        assert github_user is None
        assert resolver._unresolved["unknown"] == "pmo_profile_not_found"

    def test_resolve_username_no_github_linked(self, resolver, mock_people_client):
        """Distinct reason when PMO profile exists but has no GitHub."""
        from herald_scraper.people_client import GitHubResolution

        mock_people_client.resolve_github.return_value = GitHubResolution(
            username=None, user_id=None, reason="no_github_linked"
        )

        assert resolver.resolve_username("tobyp@mozilla.com") is None
        assert resolver._unresolved["tobyp"] == "no_github_linked"

    def test_resolve_username_caching(self, resolver, mock_people_client):
        """Test that resolved usernames are cached."""
        from herald_scraper.people_client import GitHubResolution

        mock_people_client.resolve_github.return_value = GitHubResolution(
            username="alice-gh", user_id=12345
        )

        # First call
        github_user1 = resolver.resolve_username("alice@mozilla.com")
        # Second call (should use cache)
        github_user2 = resolver.resolve_username("alice@mozilla.com")

        assert github_user1.username == github_user2.username == "alice-gh"
        assert github_user1.user_id == github_user2.user_id == 12345
        # Client should only be called once
        mock_people_client.resolve_github.assert_called_once()

    def test_resolve_username_error_handling(self, resolver, mock_people_client):
        """Test handling API errors during resolution."""
        mock_people_client.resolve_github.side_effect = Exception("API error")

        github_user = resolver.resolve_username("error@mozilla.com")

        assert github_user is None
        assert "error" in resolver._unresolved
        assert "API error" in resolver._unresolved["error"]

    def test_resolve_all_success(self, resolver, mock_people_client, sample_rules, sample_groups):
        """Test resolving all usernames from rules and groups."""
        from herald_scraper.people_client import GitHubResolution

        def mock_resolve(username, expected_bmo_id=None, expected_real_name=None):
            return GitHubResolution(username=f"{username}-gh", user_id=hash(username) % 100000)

        mock_people_client.resolve_github.side_effect = mock_resolve

        github_users, unresolved, hit_max = resolver.resolve_all(
            sample_rules, sample_groups, delay=0
        )

        # Should resolve users from both rules and groups
        assert len(github_users) > 0
        assert all(v.username.endswith("-gh") for v in github_users.values())
        assert all(v.user_id is not None for v in github_users.values())
        assert len(unresolved) == 0
        assert hit_max is False  # No limit was set

    def test_resolve_all_partial_failure(
        self, resolver, mock_people_client, sample_rules, sample_groups
    ):
        """Test resolving usernames with some failures."""
        from herald_scraper.people_client import GitHubResolution

        def mock_resolve(username, expected_bmo_id=None, expected_real_name=None):
            if username == "alice":
                return GitHubResolution(username="alice-gh", user_id=12345)
            return GitHubResolution(username=None, user_id=None)

        mock_people_client.resolve_github.side_effect = mock_resolve

        github_users, unresolved, hit_max = resolver.resolve_all(
            sample_rules, sample_groups, delay=0
        )

        # Only alice should be resolved
        assert "alice" in github_users
        assert github_users["alice"].username == "alice-gh"
        assert github_users["alice"].user_id == 12345
        # Others should be unresolved
        assert len(unresolved) > 0
        unresolved_names = {u.phabricator_username for u in unresolved}
        assert "bob" in unresolved_names or "charlie" in unresolved_names
        assert hit_max is False

    def test_resolve_all_max_users(self, resolver, mock_people_client):
        """Test limiting the number of users resolved."""
        from herald_scraper.people_client import GitHubResolution

        # Create rules with unique users to avoid caching effects
        rules = [
            Rule(
                id="H999",
                name="Test Rule",
                author="user1@mozilla.com",
                status="active",
                type="differential-revision",
                conditions=[],
                actions=[
                    Action(
                        type="add-reviewers",
                        reviewers=[
                            Reviewer(target="user2@mozilla.com", blocking=False),
                            Reviewer(target="user3@mozilla.com", blocking=False),
                            Reviewer(target="user4@mozilla.com", blocking=False),
                        ],
                    ),
                ],
            ),
        ]

        def mock_resolve(username, expected_bmo_id=None, expected_real_name=None):
            return GitHubResolution(username=f"{username}-gh", user_id=hash(username) % 100000)

        mock_people_client.resolve_github.side_effect = mock_resolve

        github_users, unresolved, hit_max = resolver.resolve_all(rules, {}, max_users=2, delay=0)

        # Should only resolve 2 users
        assert len(github_users) == 2
        assert mock_people_client.resolve_github.call_count == 2
        assert hit_max is True  # Should indicate we hit the limit

    def test_resolve_all_empty_inputs(self, resolver, mock_people_client):
        """Test resolving with empty rules and groups."""
        github_users, unresolved, hit_max = resolver.resolve_all([], {}, delay=0)

        assert github_users == {}
        assert unresolved == []
        assert hit_max is False
        mock_people_client.resolve_github.assert_not_called()

    def test_clear_cache(self, resolver, mock_people_client):
        """Test clearing the resolver caches."""
        from herald_scraper.people_client import GitHubResolution

        mock_people_client.resolve_github.return_value = GitHubResolution(
            username="alice-gh", user_id=12345
        )

        # Populate cache
        resolver.resolve_username("alice@mozilla.com")
        assert mock_people_client.resolve_github.call_count == 1

        # Clear cache
        resolver.clear_cache()

        # Should call client again
        resolver.resolve_username("alice@mozilla.com")
        assert mock_people_client.resolve_github.call_count == 2


class TestUsernameResolverBMOVerification:
    """Tests for UsernameResolver's Conduit-backed BMO id verification."""

    @pytest.fixture
    def mock_people_client(self):
        return MagicMock()

    @pytest.fixture
    def mock_conduit_client(self):
        return MagicMock()

    def _setup_conduit_happy_path(
        self,
        conduit: MagicMock,
        *,
        phid: str = "PHID-USER-x",
        bmo_id: str = "99999999",
        real_name: str = "Alice Example",
    ) -> None:
        conduit.user_search.return_value = [
            {"phid": phid, "fields": {"username": "alice", "realName": real_name}}
        ]
        conduit.bugzilla_account_search.return_value = [{"id": bmo_id, "phid": phid}]

    def test_passes_phab_info_to_people_client(
        self, mock_people_client, mock_conduit_client
    ):
        from herald_scraper.people_client import GitHubResolution

        self._setup_conduit_happy_path(
            mock_conduit_client, bmo_id="99999999", real_name="Alice Example"
        )
        mock_people_client.resolve_github.return_value = GitHubResolution(
            username="alice-gh", user_id=42
        )

        resolver = UsernameResolver(mock_people_client, conduit_client=mock_conduit_client)
        user = resolver.resolve_username("alice")

        assert user is not None
        mock_people_client.resolve_github.assert_called_once_with(
            "alice",
            expected_bmo_id="99999999",
            expected_real_name="Alice Example",
        )
        mock_conduit_client.user_search.assert_called_once_with(usernames=["alice"])
        mock_conduit_client.bugzilla_account_search.assert_called_once_with(
            phids=["PHID-USER-x"]
        )

    def test_no_conduit_client_skips_verification(self, mock_people_client):
        from herald_scraper.people_client import GitHubResolution

        mock_people_client.resolve_github.return_value = GitHubResolution(
            username="alice-gh", user_id=42
        )

        resolver = UsernameResolver(mock_people_client, conduit_client=None)
        resolver.resolve_username("alice")

        mock_people_client.resolve_github.assert_called_once_with(
            "alice", expected_bmo_id=None, expected_real_name=None
        )

    def test_user_not_in_phab_leaves_everything_none(
        self, mock_people_client, mock_conduit_client
    ):
        """Missing Phab user means no BMO id / real name to verify against."""
        from herald_scraper.people_client import GitHubResolution

        mock_conduit_client.user_search.return_value = []
        mock_people_client.resolve_github.return_value = GitHubResolution(
            username="alice-gh", user_id=42
        )

        resolver = UsernameResolver(mock_people_client, conduit_client=mock_conduit_client)
        resolver.resolve_username("alice")

        mock_people_client.resolve_github.assert_called_once_with(
            "alice", expected_bmo_id=None, expected_real_name=None
        )
        mock_conduit_client.bugzilla_account_search.assert_not_called()

    def test_no_bmo_account_linked_still_passes_real_name(
        self, mock_people_client, mock_conduit_client
    ):
        """Phab user exists without a linked BMO account: real name still flows."""
        from herald_scraper.people_client import GitHubResolution

        mock_conduit_client.user_search.return_value = [
            {"phid": "PHID-USER-x", "fields": {"username": "alice", "realName": "Alice"}}
        ]
        mock_conduit_client.bugzilla_account_search.return_value = []
        mock_people_client.resolve_github.return_value = GitHubResolution(
            username="alice-gh", user_id=42
        )

        resolver = UsernameResolver(mock_people_client, conduit_client=mock_conduit_client)
        resolver.resolve_username("alice")

        mock_people_client.resolve_github.assert_called_once_with(
            "alice", expected_bmo_id=None, expected_real_name="Alice"
        )

    def test_conduit_error_is_swallowed(self, mock_people_client, mock_conduit_client):
        """A Phab lookup failure must not derail the PMO resolution path."""
        from herald_scraper.people_client import GitHubResolution

        mock_conduit_client.user_search.side_effect = RuntimeError("phab down")
        mock_people_client.resolve_github.return_value = GitHubResolution(
            username="alice-gh", user_id=42
        )

        resolver = UsernameResolver(mock_people_client, conduit_client=mock_conduit_client)
        user = resolver.resolve_username("alice")

        assert user is not None
        mock_people_client.resolve_github.assert_called_once_with(
            "alice", expected_bmo_id=None, expected_real_name=None
        )

    def test_phab_bmo_id_cached_per_lookup(self, mock_people_client, mock_conduit_client):
        """Repeat resolutions for the same user must not re-query Phab."""
        from herald_scraper.people_client import GitHubResolution

        self._setup_conduit_happy_path(mock_conduit_client)
        mock_people_client.resolve_github.return_value = GitHubResolution(
            username="alice-gh", user_id=42
        )

        resolver = UsernameResolver(mock_people_client, conduit_client=mock_conduit_client)
        # First call goes through the full flow and populates both caches.
        resolver.resolve_username("alice")
        # Second call hits the resolution cache — Phab must not be touched again.
        resolver.resolve_username("alice")

        assert mock_conduit_client.user_search.call_count == 1
        assert mock_conduit_client.bugzilla_account_search.call_count == 1
