"""Tests for the resolvers module."""

from unittest.mock import MagicMock, patch

import pytest

from herald_scraper.exceptions import RateLimitError
from herald_scraper.models import Action, GitHubUser, Group, Reviewer, Rule
from herald_scraper.people_client import GitHubResolution
from herald_scraper.rate_limit import MAX_RATE_LIMIT_RETRIES
from herald_scraper.resolvers import (
    StmoGitHubMapper,
    UsernameResolver,
    _clean_phab_real_name,
)
from herald_scraper.stmo_client import StmoError


class TestCleanPhabRealName:
    """Tests for the Phab realName cleaner."""

    def test_passes_through_clean_name(self):
        assert _clean_phab_real_name("Aaa Bbb") == "Aaa Bbb"

    def test_strips_trailing_irc_nick_suffix(self):
        assert _clean_phab_real_name("Aaa Bbb [:nick]") == "Aaa Bbb"

    def test_strips_irc_nick_with_no_leading_space(self):
        assert _clean_phab_real_name("Aaa Bbb[:nick]") == "Aaa Bbb"

    def test_strips_inline_irc_nick(self):
        assert _clean_phab_real_name("Aaa [:nick] Bbb") == "Aaa Bbb"

    def test_collapses_extra_whitespace_after_strip(self):
        assert _clean_phab_real_name("Aaa   Bbb [:nick]") == "Aaa Bbb"

    def test_returns_none_for_empty_after_strip(self):
        assert _clean_phab_real_name("[:nick]") is None
        assert _clean_phab_real_name("   [:nick]   ") is None

    def test_returns_none_for_empty_input(self):
        assert _clean_phab_real_name("") is None
        assert _clean_phab_real_name("   ") is None


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
            sample_rules, sample_groups
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
            sample_rules, sample_groups
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

        github_users, unresolved, hit_max = resolver.resolve_all(rules, {}, max_users=2)

        # Should only resolve 2 users
        assert len(github_users) == 2
        assert mock_people_client.resolve_github.call_count == 2
        assert hit_max is True  # Should indicate we hit the limit

    def test_resolve_all_empty_inputs(self, resolver, mock_people_client):
        """Test resolving with empty rules and groups."""
        github_users, unresolved, hit_max = resolver.resolve_all([], {})

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

    def test_manual_mapping_wins_over_api(self, mock_people_client, mock_conduit_client):
        """Operator overrides bypass all API calls and take precedence."""
        from herald_scraper.models import GitHubUser

        override = {"alice": GitHubUser(username="alice-manual", user_id=9999)}
        resolver = UsernameResolver(
            mock_people_client,
            conduit_client=mock_conduit_client,
            manual_mapping=override,
        )

        user = resolver.resolve_username("alice@mozilla.com")

        assert user is not None
        assert user.username == "alice-manual"
        assert user.user_id == 9999
        # No API calls made — neither Phab nor PMO.
        mock_people_client.resolve_github.assert_not_called()
        mock_conduit_client.user_search.assert_not_called()
        # Entry persists in the resolution cache so repeat lookups are free.
        assert resolver._cache["alice"].username == "alice-manual"

    def test_manual_mapping_username_only(self, mock_people_client):
        """Entry with no user_id still wins; user_id is None on the result."""
        from herald_scraper.models import GitHubUser

        override = {"tobyp": GitHubUser(username="toby-on-github")}
        resolver = UsernameResolver(mock_people_client, manual_mapping=override)

        user = resolver.resolve_username("tobyp")

        assert user is not None
        assert user.username == "toby-on-github"
        assert user.user_id is None
        mock_people_client.resolve_github.assert_not_called()

    def test_manual_mapping_missing_user_falls_through(self, mock_people_client):
        """Users not in the mapping go through the normal resolution path."""
        from herald_scraper.models import GitHubUser
        from herald_scraper.people_client import GitHubResolution

        override = {"alice": GitHubUser(username="alice-manual")}
        mock_people_client.resolve_github.return_value = GitHubResolution(
            username="bob-gh", user_id=42
        )
        resolver = UsernameResolver(mock_people_client, manual_mapping=override)

        user = resolver.resolve_username("bob")

        assert user is not None
        assert user.username == "bob-gh"
        assert user.user_id == 42
        mock_people_client.resolve_github.assert_called_once_with(
            "bob", expected_bmo_id=None, expected_real_name=None
        )

    def test_real_name_irc_nick_suffix_stripped_before_passing(
        self, mock_people_client, mock_conduit_client
    ):
        """Phab's "[:nick]" annotation in realName is stripped before reaching PMO."""
        from herald_scraper.people_client import GitHubResolution

        mock_conduit_client.user_search.return_value = [
            {
                "phid": "PHID-USER-x",
                "fields": {"username": "alice", "realName": "Aaa Bbb [:alias]"},
            }
        ]
        mock_conduit_client.bugzilla_account_search.return_value = []
        mock_people_client.resolve_github.return_value = GitHubResolution(
            username="alice-gh", user_id=42
        )

        resolver = UsernameResolver(mock_people_client, conduit_client=mock_conduit_client)
        resolver.resolve_username("alice")

        mock_people_client.resolve_github.assert_called_once_with(
            "alice", expected_bmo_id=None, expected_real_name="Aaa Bbb"
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


class TestUsernameResolverWithoutPeopleClient:
    """Resolution still runs without a PMO cookie, on overrides alone."""

    @staticmethod
    def _rule(author: str, *reviewers: str) -> Rule:
        return Rule(
            id="H420",
            name="Test Rule",
            author=author,
            status="active",
            type="differential-revision",
            actions=[
                Action(
                    type="add-reviewers",
                    reviewers=[Reviewer(target=r, is_group=False) for r in reviewers],
                )
            ],
        )

    def test_manual_mapping_still_resolves(self):
        resolver = UsernameResolver(
            None, manual_mapping={"mappeduser": GitHubUser(username="mapped-gh", user_id=1)}
        )

        resolved, unresolved, hit_max = resolver.resolve_all(
            [self._rule("mappeduser")], {}
        )

        assert resolved["mappeduser"].username == "mapped-gh"
        assert unresolved == []
        assert hit_max is False

    def test_unmapped_users_are_reported_not_dropped(self):
        resolver = UsernameResolver(None)

        resolved, unresolved, _ = resolver.resolve_all(
            [self._rule("ruleauthor", "someuser")], {}
        )

        assert resolved == {}
        assert {u.phabricator_username for u in unresolved} == {"ruleauthor", "someuser"}
        assert all(u.reason == "no_people_directory_cookie" for u in unresolved)

    def test_group_members_are_reported_too(self):
        resolver = UsernameResolver(None)
        groups = {
            "alpha-reviewers": Group(
                id="alpha-reviewers", display_name="Alpha", members=["memberone"]
            )
        }

        _, unresolved, _ = resolver.resolve_all([self._rule("ruleauthor")], groups)

        by_name = {u.phabricator_username: u for u in unresolved}
        assert by_name["memberone"].referenced_in == ["group:alpha-reviewers"]

    def test_no_sleeping_without_a_client(self):
        resolver = UsernameResolver(None)
        rule = self._rule("ruleauthor", "usera", "userb")

        with patch("herald_scraper.resolvers.time.sleep") as sleep:
            resolver.resolve_all([rule], {})

        sleep.assert_not_called()


class TestUsernameResolverRateLimit:
    """Rate-limited PMO lookups are retried, not recorded as failures."""

    @staticmethod
    def _resolver(client: MagicMock) -> UsernameResolver:
        return UsernameResolver(client)

    @staticmethod
    def _client() -> MagicMock:
        return MagicMock()

    def test_waits_and_retries_the_whole_lookup(self):
        client = self._client()
        client.resolve_github.side_effect = [
            RateLimitError("limited", retry_after=20.0),
            GitHubResolution(username="alice-gh", user_id=12345),
        ]
        resolver = self._resolver(client)

        with patch("herald_scraper.resolvers.time.sleep") as sleep:
            github_user = resolver.resolve_username("alice@mozilla.com")

        sleep.assert_called_once_with(20.0)
        assert github_user.username == "alice-gh"
        assert "alice" not in resolver._unresolved

    def test_hintless_limit_backs_off_exponentially(self):
        client = self._client()
        client.resolve_github.side_effect = [
            RateLimitError("limited"),
            RateLimitError("limited"),
            GitHubResolution(username="alice-gh", user_id=12345),
        ]
        resolver = self._resolver(client)

        with patch("herald_scraper.resolvers.time.sleep") as sleep:
            assert resolver.resolve_username("alice") is not None

        assert [call.args[0] for call in sleep.call_args_list] == [60.0, 120.0]

    def test_gives_up_after_max_retries(self):
        client = self._client()
        client.resolve_github.side_effect = RateLimitError("limited", retry_after=1.0)
        resolver = self._resolver(client)

        with patch("herald_scraper.resolvers.time.sleep") as sleep:
            assert resolver.resolve_username("alice") is None

        assert client.resolve_github.call_count == MAX_RATE_LIMIT_RETRIES + 1
        assert sleep.call_count == MAX_RATE_LIMIT_RETRIES
        assert resolver._unresolved["alice"] == "rate_limited"

    def test_rate_limited_user_is_not_confused_with_a_real_failure(self):
        """"rate_limited" is distinct from the PMO reasons, so a resume retries it."""
        client = self._client()
        client.resolve_github.side_effect = RateLimitError("limited", retry_after=0.0)
        resolver = self._resolver(client)

        with patch("herald_scraper.resolvers.time.sleep"):
            _, unresolved, _ = resolver.resolve_all(
                [
                    Rule(
                        id="H420",
                        name="Test Rule",
                        author="alice@mozilla.com",
                        status="active",
                        type="differential-revision",
                    )
                ],
                {},
            )

        assert [u.reason for u in unresolved] == ["rate_limited"]

    def test_phab_info_is_fetched_once_across_retries(self):
        """The Conduit cross-check is not re-run for every rate-limit retry."""
        client = self._client()
        client.resolve_github.side_effect = [
            RateLimitError("limited", retry_after=0.0),
            GitHubResolution(username="alice-gh", user_id=12345),
        ]
        conduit = MagicMock()
        conduit.user_search.return_value = [
            {"phid": "PHID-USER-1", "fields": {"realName": "Aaa Bbb"}}
        ]
        conduit.bugzilla_account_search.return_value = [{"id": 91159}]
        resolver = UsernameResolver(client, conduit_client=conduit)

        with patch("herald_scraper.resolvers.time.sleep"):
            assert resolver.resolve_username("alice") is not None

        conduit.user_search.assert_called_once()
        assert client.resolve_github.call_count == 2


class TestUsernameResolverStmoGitHubMap:
    """The bulk STMO map answers ahead of the per-user PMO lookups."""

    @staticmethod
    def _mapper(**logins: str) -> MagicMock:
        """A mapper knowing the given users by email local part only.

        Keying on the local part, and not the LDAP username, keeps these
        cases exercising the email and local-part fallbacks; the
        username-first path has its own tests below.
        """
        mapper = MagicMock(spec=StmoGitHubMapper)
        users = {
            local: GitHubUser(username=login, user_id=index)
            for index, (local, login) in enumerate(logins.items(), start=1)
        }
        by_email = {f"{local}@example.com": user for local, user in users.items()}
        mapper.user_for_username.return_value = None
        mapper.user_for_email.side_effect = lambda email: by_email.get(email)
        mapper.user_for_local_part.side_effect = lambda part: users.get(part)
        mapper.user_for_bugzilla_id.return_value = None
        return mapper

    def test_resolves_via_recorded_email_before_calling_pmo(self):
        people = MagicMock()
        resolver = UsernameResolver(
            people,
            github_mapper=self._mapper(userone="gh-one"),
            user_emails={"phabone": "userone@example.com"},
        )

        user = resolver.resolve_username("phabone")

        assert user is not None
        assert user.username == "gh-one"
        # The staff table carries the numeric ID alongside the username.
        assert user.user_id == 1
        people.resolve_github.assert_not_called()

    def test_falls_back_to_email_local_part(self):
        people = MagicMock()
        resolver = UsernameResolver(people, github_mapper=self._mapper(userone="gh-one"))

        user = resolver.resolve_username("userone")

        assert user is not None
        assert user.username == "gh-one"
        people.resolve_github.assert_not_called()

    def test_uses_an_email_shaped_reviewer_target(self):
        people = MagicMock()
        resolver = UsernameResolver(people, github_mapper=self._mapper(userone="gh-one"))

        user = resolver.resolve_username("userone@example.com")

        assert user is not None
        assert user.username == "gh-one"

    def test_recorded_email_wins_over_local_part(self):
        mapper = self._mapper(userone="gh-one", phabone="gh-other")
        resolver = UsernameResolver(
            MagicMock(),
            github_mapper=mapper,
            user_emails={"phabone": "userone@example.com"},
        )

        assert resolver.resolve_username("phabone").username == "gh-one"

    def test_manual_mapping_still_wins(self):
        resolver = UsernameResolver(
            MagicMock(),
            manual_mapping={"userone": GitHubUser(username="gh-manual", user_id=7)},
            github_mapper=self._mapper(userone="gh-one"),
        )

        user = resolver.resolve_username("userone")

        assert user.username == "gh-manual"
        assert user.user_id == 7

    def test_unknown_user_falls_through_to_pmo(self):
        people = MagicMock()
        people.resolve_github.return_value = GitHubResolution(username="gh-two", user_id=42)
        resolver = UsernameResolver(people, github_mapper=self._mapper(userone="gh-one"))

        user = resolver.resolve_username("usertwo")

        assert user.username == "gh-two"
        assert user.user_id == 42
        people.resolve_github.assert_called_once()

    def test_resolves_without_a_people_client(self):
        resolver = UsernameResolver(None, github_mapper=self._mapper(userone="gh-one"))

        assert resolver.resolve_username("userone").username == "gh-one"

    def test_unknown_user_without_a_people_client_stays_unresolved(self):
        resolver = UsernameResolver(None, github_mapper=self._mapper(userone="gh-one"))

        assert resolver.resolve_username("usertwo") is None
        assert resolver._unresolved["usertwo"] == "no_people_directory_cookie"

    def test_result_is_cached(self):
        mapper = self._mapper(userone="gh-one")
        resolver = UsernameResolver(None, github_mapper=mapper)

        resolver.resolve_username("userone")
        resolver.resolve_username("userone")

        assert mapper.user_for_local_part.call_count == 1

    def test_query_failure_disables_the_map_and_falls_back(self):
        mapper = MagicMock(spec=StmoGitHubMapper)
        mapper.user_for_username.side_effect = StmoError("boom")
        people = MagicMock()
        people.resolve_github.return_value = GitHubResolution(username="gh-one", user_id=1)
        resolver = UsernameResolver(people, github_mapper=mapper)

        assert resolver.resolve_username("userone").username == "gh-one"
        assert resolver.resolve_username("usertwo").username == "gh-one"

        # Tried once, then dropped rather than retried for every user.
        assert mapper.user_for_username.call_count == 1
        assert resolver.github_mapper is None

    def test_resolves_by_phabricator_username_without_an_email(self):
        """The Phab username is the LDAP one for most people."""
        mapper = MagicMock(spec=StmoGitHubMapper)
        mapper.user_for_username.side_effect = lambda name: (
            GitHubUser(username="gh-one", user_id=1) if name == "phabone" else None
        )
        people = MagicMock()
        resolver = UsernameResolver(people, github_mapper=mapper)

        user = resolver.resolve_username("phabone")

        assert user == GitHubUser(username="gh-one", user_id=1)
        mapper.user_for_email.assert_not_called()
        mapper.user_for_local_part.assert_not_called()
        people.resolve_github.assert_not_called()

    def test_resolves_by_bugzilla_id_when_name_and_email_both_miss(self):
        """Phab supplies the BMO id; the staff table turns it into an account."""
        mapper = MagicMock(spec=StmoGitHubMapper)
        mapper.user_for_username.return_value = None
        mapper.user_for_email.return_value = None
        mapper.user_for_local_part.return_value = None
        mapper.user_for_bugzilla_id.side_effect = lambda bmo_id: (
            GitHubUser(username="gh-one", user_id=1) if bmo_id == "219880" else None
        )
        conduit = MagicMock()
        conduit.user_search.return_value = [
            {"phid": "PHID-USER-x", "fields": {"realName": "Aaa Bbb"}}
        ]
        conduit.bugzilla_account_search.return_value = [{"id": 219880}]
        people = MagicMock()
        resolver = UsernameResolver(people, conduit_client=conduit, github_mapper=mapper)

        user = resolver.resolve_username("phabone")

        assert user == GitHubUser(username="gh-one", user_id=1)
        mapper.user_for_bugzilla_id.assert_called_once_with("219880")
        people.resolve_github.assert_not_called()

    def test_bugzilla_id_path_works_without_a_people_client(self):
        mapper = MagicMock(spec=StmoGitHubMapper)
        mapper.user_for_username.return_value = None
        mapper.user_for_email.return_value = None
        mapper.user_for_local_part.return_value = None
        mapper.user_for_bugzilla_id.return_value = GitHubUser(username="gh-one", user_id=1)
        conduit = MagicMock()
        conduit.user_search.return_value = [{"phid": "PHID-USER-x", "fields": {}}]
        conduit.bugzilla_account_search.return_value = [{"id": 219880}]
        resolver = UsernameResolver(None, conduit_client=conduit, github_mapper=mapper)

        assert resolver.resolve_username("phabone").username == "gh-one"

    def test_name_or_email_match_skips_the_bugzilla_lookup(self):
        """A hit on the cheap keys must not cost a Conduit round trip."""
        conduit = MagicMock()
        mapper = self._mapper(userone="gh-one")
        resolver = UsernameResolver(MagicMock(), conduit_client=conduit, github_mapper=mapper)

        assert resolver.resolve_username("userone").username == "gh-one"
        conduit.user_search.assert_not_called()
        mapper.user_for_bugzilla_id.assert_not_called()

    def test_unknown_bugzilla_id_falls_through_to_pmo(self):
        mapper = MagicMock(spec=StmoGitHubMapper)
        mapper.user_for_username.return_value = None
        mapper.user_for_email.return_value = None
        mapper.user_for_local_part.return_value = None
        mapper.user_for_bugzilla_id.return_value = None
        conduit = MagicMock()
        conduit.user_search.return_value = [
            {"phid": "PHID-USER-x", "fields": {"realName": "Aaa Bbb"}}
        ]
        conduit.bugzilla_account_search.return_value = [{"id": 219880}]
        people = MagicMock()
        people.resolve_github.return_value = GitHubResolution(username="gh-pmo", user_id=42)
        resolver = UsernameResolver(people, conduit_client=conduit, github_mapper=mapper)

        assert resolver.resolve_username("phabone").username == "gh-pmo"
        # The id Phab already fetched is still handed to PMO to cross-check.
        people.resolve_github.assert_called_once_with(
            "phabone", expected_bmo_id="219880", expected_real_name="Aaa Bbb"
        )

    def test_username_match_wins_over_the_recorded_email(self):
        mapper = MagicMock(spec=StmoGitHubMapper)
        mapper.user_for_username.return_value = GitHubUser(username="gh-ldap", user_id=1)
        mapper.user_for_email.return_value = GitHubUser(username="gh-email", user_id=2)
        resolver = UsernameResolver(
            MagicMock(),
            github_mapper=mapper,
            user_emails={"phabone": "userone@example.com"},
        )

        assert resolver.resolve_username("phabone").username == "gh-ldap"
