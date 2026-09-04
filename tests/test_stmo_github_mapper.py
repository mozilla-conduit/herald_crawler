"""Tests for StmoGitHubMapper."""

from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from herald_scraper.models import GitHubUser
from herald_scraper.resolvers import (
    STAFF_MEMBERS_TABLE,
    StmoGitHubMapper,
    _looks_like_email,
)
from herald_scraper.stmo_client import StmoClient


def staff_row(
    email: Any = None,
    username: Any = None,
    github_username: Any = None,
    github_id: Any = None,
    bugzilla_email: Any = None,
    bugzilla_id: Any = None,
) -> Dict[str, Any]:
    """Build one row as returned by the staff members query."""
    return {
        "email": email,
        "username": username,
        "bugzilla_email": bugzilla_email,
        "bugzilla_id": bugzilla_id,
        "github_username": github_username,
        "github_id": github_id,
    }


@pytest.fixture
def mock_stmo_client() -> MagicMock:
    """A mock StmoClient returning no rows by default."""
    client = MagicMock(spec=StmoClient)
    client.run_query.return_value = []
    return client


@pytest.fixture
def mapper(mock_stmo_client: MagicMock) -> StmoGitHubMapper:
    """A mapper backed by the mock client."""
    return StmoGitHubMapper(mock_stmo_client)


class TestLooksLikeEmail:
    """Tests for the email/group-name discriminator."""

    def test_accepts_plain_address(self) -> None:
        assert _looks_like_email("userone@example.com")

    def test_accepts_surrounding_whitespace(self) -> None:
        assert _looks_like_email("  userone@example.com  ")

    def test_rejects_group_name(self) -> None:
        assert not _looks_like_email("alpha-reviewers")

    def test_rejects_address_without_dotted_domain(self) -> None:
        assert not _looks_like_email("userone@localhost")

    def test_rejects_empty(self) -> None:
        assert not _looks_like_email("")


class TestStmoGitHubMapperInit:
    """Tests for StmoGitHubMapper initialization."""

    def test_defaults(self, mapper: StmoGitHubMapper) -> None:
        assert mapper.table == STAFF_MEMBERS_TABLE

    def test_rejects_non_identifier_table(self, mock_stmo_client: MagicMock) -> None:
        with pytest.raises(ValueError, match="Invalid STMO table name"):
            StmoGitHubMapper(mock_stmo_client, table="t; DROP TABLE x")

    def test_accepts_hyphenated_bigquery_project(self, mock_stmo_client: MagicMock) -> None:
        table = "moz-fx-data-shared-prod.mozcloud.person_api_staff_members"
        assert StmoGitHubMapper(mock_stmo_client, table=table).table == table


class TestStmoGitHubMapperBuildQuery:
    """Tests for the generated SQL."""

    def test_selects_the_identity_columns(self, mapper: StmoGitHubMapper) -> None:
        sql = mapper.build_query()
        assert (
            "SELECT DISTINCT email, username, bugzilla_email, bugzilla_id, "
            "github_username, github_id" in sql
        )
        assert f"FROM {STAFF_MEMBERS_TABLE}" in sql
        assert "WHERE github_username IS NOT NULL OR github_id IS NOT NULL" in sql

    def test_uses_custom_table(self, mock_stmo_client: MagicMock) -> None:
        sql = StmoGitHubMapper(mock_stmo_client, table="other.members").build_query()
        assert "FROM other.members" in sql


class TestStmoGitHubMapperFetchAllUsers:
    """Tests for StmoGitHubMapper.fetch_all_users()."""

    def test_builds_map_from_rows(
        self, mapper: StmoGitHubMapper, mock_stmo_client: MagicMock
    ) -> None:
        mock_stmo_client.run_query.return_value = [
            staff_row("userone@example.com", "userone", "gh-one", 1),
            staff_row("usertwo@example.com", "usertwo", "gh-two", 2),
        ]

        assert mapper.fetch_all_users() == {
            "userone@example.com": GitHubUser(username="gh-one", user_id=1),
            "usertwo@example.com": GitHubUser(username="gh-two", user_id=2),
        }

    def test_keeps_a_row_with_only_a_github_username(
        self, mapper: StmoGitHubMapper, mock_stmo_client: MagicMock
    ) -> None:
        mock_stmo_client.run_query.return_value = [
            staff_row("userone@example.com", "userone", "gh-one", None)
        ]

        assert mapper.fetch_all_users() == {
            "userone@example.com": GitHubUser(username="gh-one", user_id=None)
        }

    def test_drops_rows_with_no_github_account(
        self, mapper: StmoGitHubMapper, mock_stmo_client: MagicMock
    ) -> None:
        mock_stmo_client.run_query.return_value = [
            staff_row("userone@example.com", "userone", None, None),
            staff_row("usertwo@example.com", "usertwo", "   ", None),
            staff_row("userthree@example.com", "userthree", "gh-three", 3),
        ]

        assert mapper.fetch_all_users() == {
            "userthree@example.com": GitHubUser(username="gh-three", user_id=3)
        }

    def test_drops_non_email_values(
        self, mapper: StmoGitHubMapper, mock_stmo_client: MagicMock
    ) -> None:
        mock_stmo_client.run_query.return_value = [
            staff_row("not-an-address", "userone", "gh-one", 1)
        ]

        assert mapper.fetch_all_users() == {}
        # The row is still reachable by its username.
        assert mapper.user_for_username("userone") == GitHubUser(
            username="gh-one", user_id=1
        )

    def test_normalizes_case_and_whitespace(
        self, mapper: StmoGitHubMapper, mock_stmo_client: MagicMock
    ) -> None:
        mock_stmo_client.run_query.return_value = [
            staff_row("  UserOne@Example.COM ", " UserOne ", " gh-one ", " 1 ")
        ]

        assert mapper.fetch_all_users() == {
            "userone@example.com": GitHubUser(username="gh-one", user_id=1)
        }

    def test_ignores_a_non_numeric_github_id(
        self, mapper: StmoGitHubMapper, mock_stmo_client: MagicMock
    ) -> None:
        mock_stmo_client.run_query.return_value = [
            staff_row("userone@example.com", "userone", "gh-one", "not-a-number")
        ]

        assert mapper.fetch_all_users() == {
            "userone@example.com": GitHubUser(username="gh-one", user_id=None)
        }

    def test_keeps_first_of_conflicting_accounts(
        self, mapper: StmoGitHubMapper, mock_stmo_client: MagicMock
    ) -> None:
        mock_stmo_client.run_query.return_value = [
            staff_row("userone@example.com", "userone", "gh-one", 1),
            staff_row("userone@example.com", "userone", "gh-other", 2),
        ]

        assert mapper.fetch_all_users() == {
            "userone@example.com": GitHubUser(username="gh-one", user_id=1)
        }

    def test_runs_a_single_query_for_repeated_calls(
        self, mapper: StmoGitHubMapper, mock_stmo_client: MagicMock
    ) -> None:
        mock_stmo_client.run_query.return_value = [
            staff_row("userone@example.com", "userone", "gh-one", 1)
        ]

        mapper.fetch_all_users()
        mapper.fetch_all_users()

        assert mock_stmo_client.run_query.call_count == 1

    def test_clear_cache_forces_a_new_query(
        self, mapper: StmoGitHubMapper, mock_stmo_client: MagicMock
    ) -> None:
        mapper.fetch_all_users()
        mapper.clear_cache()
        mapper.fetch_all_users()

        assert mock_stmo_client.run_query.call_count == 2


class TestStmoGitHubMapperBugzillaKeys:
    """The Bugzilla address and account id are join keys of their own."""

    @pytest.fixture
    def populated(self, mock_stmo_client: MagicMock) -> StmoGitHubMapper:
        mock_stmo_client.run_query.return_value = [
            staff_row(
                email="userone@example.com",
                username="phabone",
                github_username="gh-one",
                github_id=1,
                bugzilla_email="bugzilla-one@example.org",
                bugzilla_id=219880,
            )
        ]
        return StmoGitHubMapper(mock_stmo_client)

    def test_user_for_bugzilla_id(self, populated: StmoGitHubMapper) -> None:
        assert populated.user_for_bugzilla_id("219880") == GitHubUser(
            username="gh-one", user_id=1
        )

    def test_user_for_bugzilla_id_ignores_a_trailing_zero_decimal(
        self, populated: StmoGitHubMapper
    ) -> None:
        """BigQuery hands whole numbers back as floats often enough to matter."""
        assert populated.user_for_bugzilla_id(" 219880.0 ") == GitHubUser(
            username="gh-one", user_id=1
        )

    def test_user_for_unknown_bugzilla_id(self, populated: StmoGitHubMapper) -> None:
        assert populated.user_for_bugzilla_id("999999") is None

    def test_user_for_empty_bugzilla_id_skips_query(
        self, populated: StmoGitHubMapper, mock_stmo_client: MagicMock
    ) -> None:
        assert populated.user_for_bugzilla_id("") is None
        assert mock_stmo_client.run_query.call_count == 0

    def test_float_shaped_bugzilla_id_in_the_table_still_indexes(
        self, mock_stmo_client: MagicMock
    ) -> None:
        mock_stmo_client.run_query.return_value = [
            staff_row(
                username="phabone",
                github_username="gh-one",
                github_id=1,
                bugzilla_id=219880.0,
            )
        ]
        mapper = StmoGitHubMapper(mock_stmo_client)

        assert mapper.user_for_bugzilla_id("219880") == GitHubUser(
            username="gh-one", user_id=1
        )

    def test_bugzilla_email_resolves_like_the_ldap_one(
        self, populated: StmoGitHubMapper
    ) -> None:
        expected = GitHubUser(username="gh-one", user_id=1)
        assert populated.user_for_email("bugzilla-one@example.org") == expected
        assert populated.user_for_email("userone@example.com") == expected

    def test_bugzilla_email_widens_the_local_part_index(
        self, populated: StmoGitHubMapper
    ) -> None:
        assert populated.user_for_local_part("bugzilla-one") == GitHubUser(
            username="gh-one", user_id=1
        )

    def test_non_email_bugzilla_value_is_dropped(
        self, mock_stmo_client: MagicMock
    ) -> None:
        mock_stmo_client.run_query.return_value = [
            staff_row(
                email="userone@example.com",
                username="phabone",
                github_username="gh-one",
                github_id=1,
                bugzilla_email="not-an-address",
            )
        ]
        mapper = StmoGitHubMapper(mock_stmo_client)

        assert list(mapper.fetch_all_users()) == ["userone@example.com"]


class TestStmoGitHubMapperLookup:
    """Tests for the name and email lookup paths."""

    @pytest.fixture
    def populated(self, mock_stmo_client: MagicMock) -> StmoGitHubMapper:
        mock_stmo_client.run_query.return_value = [
            staff_row("userone@example.com", "phabone", "gh-one", 1),
            staff_row("usertwo@example.org", "usertwo", "gh-two", 2),
        ]
        return StmoGitHubMapper(mock_stmo_client)

    def test_user_for_username(self, populated: StmoGitHubMapper) -> None:
        assert populated.user_for_username("phabone") == GitHubUser(
            username="gh-one", user_id=1
        )

    def test_user_for_username_is_case_insensitive(
        self, populated: StmoGitHubMapper
    ) -> None:
        assert populated.user_for_username(" PhabOne ") == GitHubUser(
            username="gh-one", user_id=1
        )

    def test_user_for_unknown_username(self, populated: StmoGitHubMapper) -> None:
        assert populated.user_for_username("nobody") is None

    def test_user_for_empty_username_skips_query(
        self, populated: StmoGitHubMapper, mock_stmo_client: MagicMock
    ) -> None:
        assert populated.user_for_username("") is None
        assert mock_stmo_client.run_query.call_count == 0

    def test_user_for_email(self, populated: StmoGitHubMapper) -> None:
        assert populated.user_for_email("userone@example.com") == GitHubUser(
            username="gh-one", user_id=1
        )

    def test_user_for_email_is_case_insensitive(self, populated: StmoGitHubMapper) -> None:
        assert populated.user_for_email(" UserOne@Example.com ") == GitHubUser(
            username="gh-one", user_id=1
        )

    def test_user_for_unknown_email(self, populated: StmoGitHubMapper) -> None:
        assert populated.user_for_email("nobody@example.com") is None

    def test_user_for_empty_email_skips_query(
        self, populated: StmoGitHubMapper, mock_stmo_client: MagicMock
    ) -> None:
        assert populated.user_for_email("") is None
        assert mock_stmo_client.run_query.call_count == 0

    def test_user_for_local_part(self, populated: StmoGitHubMapper) -> None:
        assert populated.user_for_local_part("usertwo") == GitHubUser(
            username="gh-two", user_id=2
        )

    def test_user_for_unknown_local_part(self, populated: StmoGitHubMapper) -> None:
        assert populated.user_for_local_part("nobody") is None

    def test_user_for_empty_local_part_skips_query(
        self, populated: StmoGitHubMapper, mock_stmo_client: MagicMock
    ) -> None:
        assert populated.user_for_local_part("") is None
        assert mock_stmo_client.run_query.call_count == 0

    def test_ambiguous_local_part_never_matches(self, mock_stmo_client: MagicMock) -> None:
        mock_stmo_client.run_query.return_value = [
            staff_row("shared@example.com", "userone", "gh-one", 1),
            staff_row("shared@example.org", "usertwo", "gh-two", 2),
        ]
        mapper = StmoGitHubMapper(mock_stmo_client)

        assert mapper.user_for_local_part("shared") is None
        # The exact addresses stay resolvable.
        assert mapper.user_for_email("shared@example.org") == GitHubUser(
            username="gh-two", user_id=2
        )

    def test_same_account_on_two_addresses_is_not_ambiguous(
        self, mock_stmo_client: MagicMock
    ) -> None:
        mock_stmo_client.run_query.return_value = [
            staff_row("userone@example.com", "userone", "gh-one", 1),
            staff_row("userone@example.org", "userone", "gh-one", 1),
        ]
        mapper = StmoGitHubMapper(mock_stmo_client)

        assert mapper.user_for_local_part("userone") == GitHubUser(
            username="gh-one", user_id=1
        )
