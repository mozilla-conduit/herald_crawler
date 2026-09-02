"""Tests for StmoGroupCollector."""

from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from herald_scraper.models import Action, Condition, Reviewer, Rule
from herald_scraper.resolvers import (
    REVIEW_GROUPS_TABLE,
    StmoGroupCollector,
    _coerce_string_list,
    _slugify,
)
from herald_scraper.stmo_client import StmoClient


def review_group_row(
    group_name: str,
    usernames: Any = None,
    emails: Any = None,
) -> Dict[str, Any]:
    """Build one row as returned by the review groups query."""
    return {
        "group_name": group_name,
        "group_usernames": usernames,
        "group_emails": emails,
    }


def rule_with_reviewers(rule_id: str, *targets: str) -> Rule:
    """Build a rule whose add-reviewers action targets the given groups."""
    return Rule(
        id=rule_id,
        name=f"Rule {rule_id}",
        author="ruleauthor",
        status="active",
        type="differential-revision",
        conditions=[Condition(type="repository", operator="is-any-of", value=["repo"])],
        actions=[
            Action(
                type="add-reviewers",
                reviewers=[
                    Reviewer(target=target, blocking=True, is_group=True) for target in targets
                ],
            )
        ],
    )


@pytest.fixture
def mock_stmo_client() -> MagicMock:
    """A mock StmoClient returning no rows by default."""
    client = MagicMock(spec=StmoClient)
    client.run_query.return_value = []
    return client


@pytest.fixture
def collector(mock_stmo_client: MagicMock) -> StmoGroupCollector:
    """A collector wired to the mock client."""
    return StmoGroupCollector(mock_stmo_client)


@pytest.fixture
def sample_rules() -> List[Rule]:
    """Rules referencing two reviewer groups."""
    return [
        rule_with_reviewers("H420", "alpha-reviewers"),
        rule_with_reviewers("H421", "beta-reviewers"),
    ]


class TestSlugify:
    """Tests for the group name slug normalizer."""

    def test_lowercases_and_hyphenates(self) -> None:
        assert _slugify("Alpha Reviewers") == "alpha-reviewers"

    def test_passes_through_existing_slug(self) -> None:
        assert _slugify("alpha-reviewers") == "alpha-reviewers"

    def test_collapses_runs_of_punctuation(self) -> None:
        assert _slugify("Alpha // Reviewers!") == "alpha-reviewers"

    def test_strips_leading_and_trailing_separators(self) -> None:
        assert _slugify("  #Alpha Reviewers#  ") == "alpha-reviewers"


class TestCoerceStringList:
    """Tests for coercing STMO cells into member lists."""

    def test_none_is_empty(self) -> None:
        assert _coerce_string_list(None) == []

    def test_empty_string_is_empty(self) -> None:
        assert _coerce_string_list("   ") == []

    def test_passes_through_list(self) -> None:
        assert _coerce_string_list(["a", "b"]) == ["a", "b"]

    def test_parses_json_array_string(self) -> None:
        assert _coerce_string_list('["a", "b"]') == ["a", "b"]

    def test_splits_comma_separated_string(self) -> None:
        assert _coerce_string_list("a, b,c") == ["a", "b", "c"]

    def test_splits_semicolon_and_whitespace(self) -> None:
        assert _coerce_string_list("a; b\nc") == ["a", "b", "c"]

    def test_flattens_nested_lists(self) -> None:
        assert _coerce_string_list([["a"], ["b", "c"]]) == ["a", "b", "c"]

    def test_unparseable_json_array_falls_back_to_splitting(self) -> None:
        assert _coerce_string_list("[a, b") == ["[a", "b"]

    def test_scalar_is_stringified(self) -> None:
        assert _coerce_string_list(42) == ["42"]


class TestStmoGroupCollectorInit:
    """Tests for StmoGroupCollector initialization."""

    def test_defaults(self, collector: StmoGroupCollector) -> None:
        assert collector.table == REVIEW_GROUPS_TABLE

    def test_rejects_non_identifier_table(self, mock_stmo_client: MagicMock) -> None:
        with pytest.raises(ValueError, match="Invalid STMO table name"):
            StmoGroupCollector(mock_stmo_client, table="t; DROP TABLE x")

    def test_accepts_hyphenated_bigquery_project(self, mock_stmo_client: MagicMock) -> None:
        table = "moz-fx-data-shared-prod.phabricator_metrics.review_groups"
        assert StmoGroupCollector(mock_stmo_client, table=table).table == table


class TestStmoGroupCollectorBuildQuery:
    """Tests for the generated SQL."""

    def test_selects_expected_columns(self, collector: StmoGroupCollector) -> None:
        assert "SELECT group_name, group_usernames, group_emails" in collector.build_query()

    def test_deduplicates_to_one_row_per_group(self, collector: StmoGroupCollector) -> None:
        sql = collector.build_query()
        assert f"FROM {REVIEW_GROUPS_TABLE}" in sql
        assert "ROW_NUMBER() OVER (PARTITION BY group_name) AS row_num" in sql
        assert "WHERE row_num = 1" in sql

    def test_uses_custom_table(self, mock_stmo_client: MagicMock) -> None:
        sql = StmoGroupCollector(mock_stmo_client, table="other.groups").build_query()
        assert "FROM other.groups" in sql


class TestStmoGroupCollectorFetchAllGroups:
    """Tests for StmoGroupCollector.fetch_all_groups()."""

    def test_builds_groups_from_rows(
        self, collector: StmoGroupCollector, mock_stmo_client: MagicMock
    ) -> None:
        mock_stmo_client.run_query.return_value = [
            review_group_row(
                "alpha-reviewers",
                usernames=["userone", "usertwo"],
                emails=["userone@example.com", "usertwo@example.com"],
            )
        ]

        groups = collector.fetch_all_groups()

        assert list(groups) == ["alpha-reviewers"]
        group = groups["alpha-reviewers"]
        assert group.id == "alpha-reviewers"
        assert group.display_name == "alpha-reviewers"
        assert group.members == ["userone", "usertwo"]

    def test_runs_a_single_query_for_repeated_calls(
        self, collector: StmoGroupCollector, mock_stmo_client: MagicMock
    ) -> None:
        collector.fetch_all_groups()
        collector.fetch_all_groups()

        assert mock_stmo_client.run_query.call_count == 1

    def test_clear_cache_forces_requery(
        self, collector: StmoGroupCollector, mock_stmo_client: MagicMock
    ) -> None:
        collector.fetch_all_groups()
        collector.clear_cache()
        collector.fetch_all_groups()

        assert mock_stmo_client.run_query.call_count == 2

    def test_skips_rows_without_group_name(
        self, collector: StmoGroupCollector, mock_stmo_client: MagicMock
    ) -> None:
        mock_stmo_client.run_query.return_value = [
            review_group_row("", usernames=["userone"]),
            review_group_row("alpha-reviewers", usernames=["usertwo"]),
        ]

        assert list(collector.fetch_all_groups()) == ["alpha-reviewers"]

    def test_keeps_group_with_no_members(
        self, collector: StmoGroupCollector, mock_stmo_client: MagicMock
    ) -> None:
        mock_stmo_client.run_query.return_value = [review_group_row("alpha-reviewers")]

        assert collector.fetch_all_groups()["alpha-reviewers"].members == []


class TestStmoGroupCollectorFetchUserEmails:
    """Tests for StmoGroupCollector.fetch_user_emails()."""

    def test_pairs_usernames_with_emails(
        self, collector: StmoGroupCollector, mock_stmo_client: MagicMock
    ) -> None:
        mock_stmo_client.run_query.return_value = [
            review_group_row(
                "alpha-reviewers",
                usernames=["userone", "usertwo"],
                emails=["UserOne@Example.com", "usertwo@example.com"],
            )
        ]

        assert collector.fetch_user_emails() == {
            "userone": "userone@example.com",
            "usertwo": "usertwo@example.com",
        }

    def test_merges_across_groups(
        self, collector: StmoGroupCollector, mock_stmo_client: MagicMock
    ) -> None:
        mock_stmo_client.run_query.return_value = [
            review_group_row(
                "alpha-reviewers", usernames=["userone"], emails=["userone@example.com"]
            ),
            review_group_row(
                "beta-reviewers", usernames=["usertwo"], emails=["usertwo@example.com"]
            ),
        ]

        assert sorted(collector.fetch_user_emails()) == ["userone", "usertwo"]

    def test_drops_rows_whose_arrays_disagree_in_length(
        self, collector: StmoGroupCollector, mock_stmo_client: MagicMock
    ) -> None:
        mock_stmo_client.run_query.return_value = [
            review_group_row(
                "alpha-reviewers",
                usernames=["userone", "usertwo"],
                emails=["userone@example.com"],
            ),
            review_group_row(
                "beta-reviewers", usernames=["userthree"], emails=["userthree@example.com"]
            ),
        ]

        assert collector.fetch_user_emails() == {"userthree": "userthree@example.com"}

    def test_ignores_non_email_values(
        self, collector: StmoGroupCollector, mock_stmo_client: MagicMock
    ) -> None:
        mock_stmo_client.run_query.return_value = [
            review_group_row(
                "alpha-reviewers",
                usernames=["userone", "usertwo"],
                emails=["not-an-email", "usertwo@example.com"],
            )
        ]

        assert collector.fetch_user_emails() == {"usertwo": "usertwo@example.com"}

    def test_keeps_first_of_conflicting_emails(
        self, collector: StmoGroupCollector, mock_stmo_client: MagicMock
    ) -> None:
        mock_stmo_client.run_query.return_value = [
            review_group_row(
                "alpha-reviewers", usernames=["userone"], emails=["userone@example.com"]
            ),
            review_group_row(
                "beta-reviewers", usernames=["userone"], emails=["userone@example.org"]
            ),
        ]

        assert collector.fetch_user_emails() == {"userone": "userone@example.com"}

    def test_shares_the_group_query(
        self, collector: StmoGroupCollector, mock_stmo_client: MagicMock
    ) -> None:
        mock_stmo_client.run_query.return_value = [
            review_group_row(
                "alpha-reviewers", usernames=["userone"], emails=["userone@example.com"]
            )
        ]

        collector.fetch_all_groups()
        collector.fetch_user_emails()

        assert mock_stmo_client.run_query.call_count == 1

    def test_cleared_along_with_the_group_cache(
        self, collector: StmoGroupCollector, mock_stmo_client: MagicMock
    ) -> None:
        mock_stmo_client.run_query.return_value = [
            review_group_row(
                "alpha-reviewers", usernames=["userone"], emails=["userone@example.com"]
            )
        ]
        collector.fetch_user_emails()

        collector.clear_cache()
        mock_stmo_client.run_query.return_value = []

        assert collector.fetch_user_emails() == {}


class TestStmoGroupCollectorCollectAllGroups:
    """Tests for StmoGroupCollector.collect_all_groups()."""

    def test_returns_only_referenced_groups(
        self,
        collector: StmoGroupCollector,
        mock_stmo_client: MagicMock,
        sample_rules: List[Rule],
    ) -> None:
        mock_stmo_client.run_query.return_value = [
            review_group_row("alpha-reviewers", usernames=["userone"]),
            review_group_row("beta-reviewers", usernames=["usertwo"]),
            review_group_row("gamma-reviewers", usernames=["userthree"]),
        ]

        groups = collector.collect_all_groups(sample_rules)

        assert sorted(groups) == ["alpha-reviewers", "beta-reviewers"]

    def test_matches_display_name_by_slug(
        self, collector: StmoGroupCollector, mock_stmo_client: MagicMock
    ) -> None:
        mock_stmo_client.run_query.return_value = [
            review_group_row("Alpha Reviewers", usernames=["userone"])
        ]

        groups = collector.collect_all_groups([rule_with_reviewers("H420", "alpha-reviewers")])

        assert list(groups) == ["alpha-reviewers"]
        # Keyed by the slug the rule used, but keeps STMO's human-readable name.
        assert groups["alpha-reviewers"].id == "alpha-reviewers"
        assert groups["alpha-reviewers"].display_name == "Alpha Reviewers"

    def test_prefers_exact_name_over_slug_match(
        self, collector: StmoGroupCollector, mock_stmo_client: MagicMock
    ) -> None:
        mock_stmo_client.run_query.return_value = [
            review_group_row("Alpha Reviewers", usernames=["fromdisplayname"]),
            review_group_row("alpha-reviewers", usernames=["fromslug"]),
        ]

        groups = collector.collect_all_groups([rule_with_reviewers("H420", "alpha-reviewers")])

        assert groups["alpha-reviewers"].members == ["fromslug"]

    def test_ambiguous_slug_collision_is_dropped(
        self, collector: StmoGroupCollector, mock_stmo_client: MagicMock
    ) -> None:
        mock_stmo_client.run_query.return_value = [
            review_group_row("Alpha Reviewers", usernames=["userone"]),
            review_group_row("alpha reviewers", usernames=["usertwo"]),
        ]

        groups = collector.collect_all_groups([rule_with_reviewers("H420", "alpha-reviewers")])

        assert groups == {}

    def test_missing_group_is_omitted(
        self, collector: StmoGroupCollector, mock_stmo_client: MagicMock
    ) -> None:
        mock_stmo_client.run_query.return_value = [
            review_group_row("alpha-reviewers", usernames=["userone"])
        ]

        groups = collector.collect_all_groups(
            [rule_with_reviewers("H420", "alpha-reviewers", "nonexistent-reviewers")]
        )

        assert list(groups) == ["alpha-reviewers"]

    def test_max_groups_stops_early(
        self,
        collector: StmoGroupCollector,
        mock_stmo_client: MagicMock,
        sample_rules: List[Rule],
    ) -> None:
        mock_stmo_client.run_query.return_value = [
            review_group_row("alpha-reviewers", usernames=["userone"]),
            review_group_row("beta-reviewers", usernames=["usertwo"]),
        ]

        groups = collector.collect_all_groups(sample_rules, max_groups=1)

        assert list(groups) == ["alpha-reviewers"]

    def test_no_rules_runs_no_query(
        self, collector: StmoGroupCollector, mock_stmo_client: MagicMock
    ) -> None:
        assert collector.collect_all_groups([]) == {}
        mock_stmo_client.run_query.assert_not_called()

    def test_collects_all_referenced_groups_in_one_query(
        self,
        collector: StmoGroupCollector,
        mock_stmo_client: MagicMock,
        sample_rules: List[Rule],
    ) -> None:
        mock_stmo_client.run_query.return_value = [
            review_group_row("alpha-reviewers", usernames=["userone"]),
            review_group_row("beta-reviewers", usernames=["usertwo"]),
        ]

        collector.collect_all_groups(sample_rules)

        assert mock_stmo_client.run_query.call_count == 1

    def test_extract_group_slugs_delegates(
        self, collector: StmoGroupCollector, sample_rules: List[Rule]
    ) -> None:
        assert collector.extract_group_slugs_from_rules(sample_rules) == {
            "alpha-reviewers",
            "beta-reviewers",
        }
