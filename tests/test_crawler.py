"""Tests for HeraldCrawler."""

import json
from pathlib import Path
from typing import List
from unittest.mock import Mock


from herald_scraper.client import HeraldClient
from herald_scraper.crawler import (
    HeraldCrawler,
    _sort_rule_ids,
    _deduplicate_rule_ids,
    load_existing_output,
    atomic_write_json,
)
from herald_scraper.models import (
    Rule,
    Group,
    GitHubUser,
    HeraldRulesOutput,
    Metadata,
    ScrapeStatus,
    UnresolvedUser,
)


class TestHelperFunctions:
    """Tests for module-level helper functions."""

    def test_sort_rule_ids_numeric(self) -> None:
        """Test sorting numeric rule IDs."""
        rule_ids = ["H300", "H100", "H200"]
        result = _sort_rule_ids(rule_ids)
        assert result == ["H100", "H200", "H300"]

    def test_sort_rule_ids_handles_non_numeric(self) -> None:
        """Test that non-numeric rule IDs are sorted after numeric."""
        rule_ids = ["H100", "HFOO", "H200", "HBAR"]
        result = _sort_rule_ids(rule_ids)
        # Numeric IDs first, then non-numeric sorted alphabetically
        assert result == ["H100", "H200", "HBAR", "HFOO"]

    def test_sort_rule_ids_empty_string(self) -> None:
        """Test handling of edge case with empty string."""
        rule_ids = ["H100", "", "H200"]
        result = _sort_rule_ids(rule_ids)
        # Empty string should sort after numeric
        assert result == ["H100", "H200", ""]

    def test_deduplicate_rule_ids_removes_duplicates(self) -> None:
        """Test that duplicates are removed."""
        rule_ids = ["H100", "H200", "H100", "H300", "H200"]
        result = _deduplicate_rule_ids(rule_ids)
        assert result == ["H100", "H200", "H300"]

    def test_deduplicate_rule_ids_preserves_order(self) -> None:
        """Test that first occurrence order is preserved."""
        rule_ids = ["H300", "H100", "H200", "H100"]
        result = _deduplicate_rule_ids(rule_ids)
        assert result == ["H300", "H100", "H200"]

    def test_deduplicate_rule_ids_empty_list(self) -> None:
        """Test handling of empty list."""
        result = _deduplicate_rule_ids([])
        assert result == []


class TestHeraldCrawlerInit:
    """Tests for HeraldCrawler initialization."""

    def test_init(self) -> None:
        """Test crawler initialization with client."""
        mock_client = Mock(spec=HeraldClient)
        crawler = HeraldCrawler(client=mock_client)

        assert crawler.client is mock_client
        assert crawler.progress_callback is None

    def test_init_with_progress_callback(self) -> None:
        """Test crawler initialization with progress callback."""
        mock_client = Mock(spec=HeraldClient)
        callback = Mock()
        crawler = HeraldCrawler(client=mock_client, progress_callback=callback)

        assert crawler.client is mock_client
        assert crawler.progress_callback is callback


class TestExtractRuleIds:
    """Tests for extracting rule IDs from listing page."""

    def test_extract_rule_ids(self, listing_html: str) -> None:
        """Test extracting all rule IDs from listing page."""
        # Create a last page HTML (no next link) for pagination to stop
        last_page_html = "<html><body></body></html>"

        mock_client = Mock(spec=HeraldClient)
        mock_client.fetch_listing.return_value = listing_html
        mock_client.fetch_page.return_value = last_page_html

        crawler = HeraldCrawler(client=mock_client)
        rule_ids = crawler.extract_rule_ids()

        # Should return a list of rule IDs
        assert isinstance(rule_ids, list)
        assert all(rule_id.startswith("H") for rule_id in rule_ids)
        mock_client.fetch_listing.assert_called_once()

    def test_extract_global_rule_ids(self, listing_html: str) -> None:
        """Test extracting only global rule IDs from listing page."""
        # Create a last page HTML (no next link) for pagination to stop
        last_page_html = "<html><body></body></html>"

        mock_client = Mock(spec=HeraldClient)
        mock_client.fetch_listing.return_value = listing_html
        mock_client.fetch_page.return_value = last_page_html

        crawler = HeraldCrawler(client=mock_client)
        rule_ids = crawler.extract_global_rule_ids()

        # Should return a list of global rule IDs
        assert isinstance(rule_ids, list)
        assert all(rule_id.startswith("H") for rule_id in rule_ids)
        mock_client.fetch_listing.assert_called_once()


class TestExtractRuleIdsPagination:
    """Tests for pagination handling when extracting rule IDs."""

    def test_single_page_no_pagination(self) -> None:
        """Test extraction when there's only one page (no pagination)."""
        # HTML without pagination
        single_page_html = """
        <html><body>
            <a href="/H100">H100</a>
            <a href="/H101">H101</a>
            <div class="phui-oi-frame">
                <a href="/H100">Rule 100</a>
                Global Rule
            </div>
        </body></html>
        """
        mock_client = Mock(spec=HeraldClient)
        mock_client.fetch_listing.return_value = single_page_html

        crawler = HeraldCrawler(client=mock_client)
        rule_ids = crawler.extract_rule_ids()

        assert rule_ids == ["H100", "H101"]
        mock_client.fetch_listing.assert_called_once()
        # fetch_page should not be called since there's no next page
        mock_client.fetch_page.assert_not_called()

    def test_multi_page_pagination(self) -> None:
        """Test extraction iterates through multiple pages."""
        page1_html = """
        <html><body>
            <a href="/H100">H100</a>
            <a href="/H101">H101</a>
            <div class="phui-pager-view">
                <a href="/herald/query/all/?after=101">Next</a>
            </div>
        </body></html>
        """
        page2_html = """
        <html><body>
            <a href="/H102">H102</a>
            <a href="/H103">H103</a>
            <div class="phui-pager-view">
                <a href="/herald/query/all/?before=102">Previous</a>
            </div>
        </body></html>
        """
        mock_client = Mock(spec=HeraldClient)
        mock_client.fetch_listing.return_value = page1_html
        mock_client.fetch_page.return_value = page2_html

        crawler = HeraldCrawler(client=mock_client)
        rule_ids = crawler.extract_rule_ids()

        assert rule_ids == ["H100", "H101", "H102", "H103"]
        mock_client.fetch_listing.assert_called_once()
        mock_client.fetch_page.assert_called_once_with("/herald/query/all/?after=101")

    def test_max_pages_limit(self) -> None:
        """Test that max_pages parameter prevents infinite loops."""
        # Page that always has a "next" link
        infinite_page_html = """
        <html><body>
            <a href="/H100">H100</a>
            <div class="phui-pager-view">
                <a href="/herald/query/all/?after=100">Next</a>
            </div>
        </body></html>
        """
        mock_client = Mock(spec=HeraldClient)
        mock_client.fetch_listing.return_value = infinite_page_html
        mock_client.fetch_page.return_value = infinite_page_html

        crawler = HeraldCrawler(client=mock_client)
        crawler.extract_rule_ids(max_pages=3)

        # Should stop after max_pages
        assert mock_client.fetch_listing.call_count == 1
        assert mock_client.fetch_page.call_count == 2  # 3 pages total - 1 listing

    def test_deduplicates_rule_ids(self) -> None:
        """Test that duplicate rule IDs across pages are removed."""
        page1_html = """
        <html><body>
            <a href="/H100">H100</a>
            <a href="/H101">H101</a>
            <div class="phui-pager-view">
                <a href="/herald/query/all/?after=101">Next</a>
            </div>
        </body></html>
        """
        # Page 2 has duplicate H101
        page2_html = """
        <html><body>
            <a href="/H101">H101</a>
            <a href="/H102">H102</a>
        </body></html>
        """
        mock_client = Mock(spec=HeraldClient)
        mock_client.fetch_listing.return_value = page1_html
        mock_client.fetch_page.return_value = page2_html

        crawler = HeraldCrawler(client=mock_client)
        rule_ids = crawler.extract_rule_ids()

        # H101 should appear only once
        assert rule_ids == ["H100", "H101", "H102"]

    def test_extract_global_rule_ids_pagination(self) -> None:
        """Test global rule extraction with pagination."""
        page1_html = """
        <html><body>
            <div class="phui-oi-frame">
                <a href="/H100">Rule 100</a>
                Global Rule
            </div>
            <div class="phui-oi-frame">
                <a href="/H101">Rule 101</a>
                Personal Rule
            </div>
            <div class="phui-pager-view">
                <a href="/herald/query/all/?after=101">Next</a>
            </div>
        </body></html>
        """
        page2_html = """
        <html><body>
            <div class="phui-oi-frame">
                <a href="/H102">Rule 102</a>
                Global Rule
            </div>
        </body></html>
        """
        mock_client = Mock(spec=HeraldClient)
        mock_client.fetch_listing.return_value = page1_html
        mock_client.fetch_page.return_value = page2_html

        crawler = HeraldCrawler(client=mock_client)
        rule_ids = crawler.extract_global_rule_ids()

        # Should only include global rules from both pages
        assert rule_ids == ["H100", "H102"]

    def test_empty_page_handling(self) -> None:
        """Test handling of empty listing page."""
        empty_html = "<html><body></body></html>"
        mock_client = Mock(spec=HeraldClient)
        mock_client.fetch_listing.return_value = empty_html

        crawler = HeraldCrawler(client=mock_client)
        rule_ids = crawler.extract_rule_ids()

        assert rule_ids == []


class TestExtractRule:
    """Tests for extracting individual rules."""

    def test_extract_rule_success(self, rule_h420_html: str) -> None:
        """Test successfully extracting a rule."""
        mock_client = Mock(spec=HeraldClient)
        mock_client.fetch_rule.return_value = rule_h420_html

        crawler = HeraldCrawler(client=mock_client)
        rule = crawler.extract_rule("H420")

        assert rule is not None
        assert isinstance(rule, Rule)
        assert rule.id == "H420"
        mock_client.fetch_rule.assert_called_once_with("H420")

    def test_extract_rule_parse_failure(self) -> None:
        """Test extracting a rule that cannot be parsed."""
        mock_client = Mock(spec=HeraldClient)
        mock_client.fetch_rule.return_value = "<html><body>Invalid page</body></html>"

        crawler = HeraldCrawler(client=mock_client)

        # Should return None for unparseable rules (parser returns None on error)
        rule = crawler.extract_rule("H999")
        assert rule is None


class TestExtractRules:
    """Tests for extracting multiple rules."""

    def test_extract_rules(
        self,
        rule_h420_html: str,
        rule_h422_html: str,
    ) -> None:
        """Test extracting multiple rules."""
        mock_client = Mock(spec=HeraldClient)
        mock_client.fetch_rule.side_effect = [rule_h420_html, rule_h422_html]

        crawler = HeraldCrawler(client=mock_client)
        rules = crawler.extract_rules(["H420", "H422"])

        assert len(rules) == 2
        assert all(isinstance(rule, Rule) for rule in rules)
        assert mock_client.fetch_rule.call_count == 2


class TestExtractAllRules:
    """Tests for extracting all rules."""

    def test_extract_all_rules(
        self,
        listing_html: str,
        rule_h420_html: str,
        rule_h422_html: str,
    ) -> None:
        """Test extracting all rules with complete output."""
        last_page_html = "<html><body></body></html>"

        mock_client = Mock(spec=HeraldClient)
        mock_client.base_url = "https://phabricator.example.com"
        mock_client.fetch_listing.return_value = listing_html
        mock_client.fetch_page.return_value = last_page_html
        mock_client.fetch_rule.side_effect = [rule_h420_html, rule_h422_html]

        crawler = HeraldCrawler(client=mock_client)
        output = crawler.extract_all_rules(global_only=False, max_rules=2)

        assert output is not None
        assert hasattr(output, "rules")
        assert hasattr(output, "metadata")
        assert len(output.rules) <= 2

    def test_extract_all_rules_global_only(
        self,
        listing_html: str,
        rule_h420_html: str,
    ) -> None:
        """Test extracting only global rules."""
        last_page_html = "<html><body></body></html>"

        mock_client = Mock(spec=HeraldClient)
        mock_client.base_url = "https://phabricator.example.com"
        mock_client.fetch_listing.return_value = listing_html
        mock_client.fetch_page.return_value = last_page_html
        mock_client.fetch_rule.return_value = rule_h420_html

        crawler = HeraldCrawler(client=mock_client)
        output = crawler.extract_all_rules(global_only=True, max_rules=1)

        assert output is not None
        # All returned rules should be from global rule extraction
        mock_client.fetch_listing.assert_called()

    def test_extract_all_rules_with_progress_callback(
        self,
        listing_html: str,
        rule_h420_html: str,
        rule_h422_html: str,
    ) -> None:
        """Test that progress callback is called during extraction."""
        last_page_html = "<html><body></body></html>"

        mock_client = Mock(spec=HeraldClient)
        mock_client.base_url = "https://phabricator.example.com"
        mock_client.fetch_listing.return_value = listing_html
        mock_client.fetch_page.return_value = last_page_html
        mock_client.fetch_rule.side_effect = [rule_h420_html, rule_h422_html]

        progress_calls: List[tuple] = []

        def progress_callback(current: int, total: int, message: str) -> None:
            progress_calls.append((current, total, message))

        crawler = HeraldCrawler(client=mock_client, progress_callback=progress_callback)
        crawler.extract_all_rules(global_only=False, max_rules=2)

        # Progress callback should have been called
        assert len(progress_calls) > 0

    def test_extract_all_rules_max_rules_limit(
        self,
        listing_html: str,
        rule_h420_html: str,
    ) -> None:
        """Test that max_rules parameter limits extraction."""
        last_page_html = "<html><body></body></html>"

        mock_client = Mock(spec=HeraldClient)
        mock_client.base_url = "https://phabricator.example.com"
        mock_client.fetch_listing.return_value = listing_html
        mock_client.fetch_page.return_value = last_page_html
        mock_client.fetch_rule.return_value = rule_h420_html

        crawler = HeraldCrawler(client=mock_client)
        output = crawler.extract_all_rules(global_only=False, max_rules=1)

        assert len(output.rules) <= 1


class TestLoadExistingOutput:
    """Tests for load_existing_output function."""

    def test_load_nonexistent_file(self, tmp_path: Path) -> None:
        """Test loading from a file that doesn't exist."""
        result = load_existing_output(tmp_path / "nonexistent.json")
        assert result is None

    def test_load_valid_output(self, tmp_path: Path) -> None:
        """Test loading a valid output file."""
        from datetime import datetime, timezone

        output = HeraldRulesOutput(
            rules=[
                Rule(
                    id="H420",
                    name="Test Rule",
                    author="test@example.com",
                    status="active",
                    type="differential-revision",
                )
            ],
            groups={
                "test-group": Group(id="test-group", display_name="Test Group", members=["user1"])
            },
            github_users={"user1": GitHubUser(username="user1-gh", user_id=12345)},
            metadata=Metadata(
                extracted_at=datetime.now(timezone.utc),
                total_rules=1,
                total_groups=1,
                phabricator_instance="phabricator.example.com",
            ),
        )

        file_path = tmp_path / "output.json"
        with open(file_path, "w") as f:
            f.write(output.model_dump_json(indent=2))

        result = load_existing_output(file_path)
        assert result is not None
        assert len(result.rules) == 1
        assert result.rules[0].id == "H420"
        assert "test-group" in result.groups
        assert result.github_users.get("user1").username == "user1-gh"
        assert result.github_users.get("user1").user_id == 12345

    def test_load_invalid_json(self, tmp_path: Path) -> None:
        """Test loading an invalid JSON file."""
        file_path = tmp_path / "invalid.json"
        with open(file_path, "w") as f:
            f.write("{invalid json")

        result = load_existing_output(file_path)
        assert result is None

    def test_load_invalid_schema(self, tmp_path: Path) -> None:
        """Test loading a file with invalid schema."""
        file_path = tmp_path / "wrong_schema.json"
        with open(file_path, "w") as f:
            json.dump({"not": "valid schema"}, f)

        result = load_existing_output(file_path)
        assert result is None


class TestAtomicWriteJson:
    """Tests for atomic_write_json function."""

    def test_write_creates_file(self, tmp_path: Path) -> None:
        """Test that atomic write creates the output file."""
        from datetime import datetime, timezone

        output = HeraldRulesOutput(
            rules=[],
            metadata=Metadata(
                extracted_at=datetime.now(timezone.utc),
                total_rules=0,
                total_groups=0,
                phabricator_instance="test.example.com",
            ),
        )

        file_path = tmp_path / "output.json"
        atomic_write_json(file_path, output)

        assert file_path.exists()
        with open(file_path) as f:
            data = json.load(f)
        assert data["rules"] == []

    def test_write_creates_parent_dirs(self, tmp_path: Path) -> None:
        """Test that atomic write creates parent directories."""
        from datetime import datetime, timezone

        output = HeraldRulesOutput(
            rules=[],
            metadata=Metadata(
                extracted_at=datetime.now(timezone.utc),
                total_rules=0,
                total_groups=0,
                phabricator_instance="test.example.com",
            ),
        )

        file_path = tmp_path / "subdir" / "nested" / "output.json"
        atomic_write_json(file_path, output)

        assert file_path.exists()

    def test_write_overwrites_existing(self, tmp_path: Path) -> None:
        """Test that atomic write overwrites existing file."""
        from datetime import datetime, timezone

        file_path = tmp_path / "output.json"

        # Write initial file
        with open(file_path, "w") as f:
            f.write('{"old": "data"}')

        # Overwrite with atomic write
        output = HeraldRulesOutput(
            rules=[
                Rule(
                    id="H999",
                    name="New Rule",
                    author="test@example.com",
                    status="active",
                    type="differential-revision",
                )
            ],
            metadata=Metadata(
                extracted_at=datetime.now(timezone.utc),
                total_rules=1,
                total_groups=0,
                phabricator_instance="test.example.com",
            ),
        )
        atomic_write_json(file_path, output)

        with open(file_path) as f:
            data = json.load(f)
        assert len(data["rules"]) == 1
        assert data["rules"][0]["id"] == "H999"


class TestResumeFromExistingOutput:
    """Tests for resuming extraction from existing output."""

    def test_resume_skips_existing_rules(
        self,
        listing_html: str,
        rule_h420_html: str,
        rule_h422_html: str,
    ) -> None:
        """Test that existing rules are skipped during resume."""
        from datetime import datetime, timezone

        # Create existing output with H420 already scraped
        existing_output = HeraldRulesOutput(
            rules=[
                Rule(
                    id="H420",
                    name="Existing Rule",
                    author="existing@example.com",
                    status="active",
                    type="differential-revision",
                )
            ],
            metadata=Metadata(
                extracted_at=datetime.now(timezone.utc),
                total_rules=1,
                total_groups=0,
                phabricator_instance="phabricator.example.com",
            ),
        )

        # Mock client that returns listing with H420 and H422
        listing_with_two = """
        <html><body>
            <a href="/H420">H420</a>
            <a href="/H422">H422</a>
        </body></html>
        """
        mock_client = Mock(spec=HeraldClient)
        mock_client.base_url = "https://phabricator.example.com"
        mock_client.fetch_listing.return_value = listing_with_two
        mock_client.fetch_rule.return_value = rule_h422_html

        crawler = HeraldCrawler(client=mock_client)
        output = crawler.extract_all_rules(
            global_only=False,
            existing_output=existing_output,
            extract_groups=False,
        )

        # Should have both rules (1 existing + 1 new)
        assert len(output.rules) == 2
        # Only H422 should have been fetched (H420 was in existing)
        mock_client.fetch_rule.assert_called_once_with("H422")

    def test_resume_preserves_existing_github_users(
        self,
        listing_html: str,
        rule_h420_html: str,
    ) -> None:
        """Test that existing GitHub users are preserved."""
        from datetime import datetime, timezone

        existing_output = HeraldRulesOutput(
            rules=[],
            github_users={"existinguser": GitHubUser(username="existing-gh", user_id=99999)},
            metadata=Metadata(
                extracted_at=datetime.now(timezone.utc),
                total_rules=0,
                total_groups=0,
                phabricator_instance="phabricator.example.com",
            ),
        )

        mock_client = Mock(spec=HeraldClient)
        mock_client.base_url = "https://phabricator.example.com"
        mock_client.fetch_listing.return_value = "<html><body></body></html>"

        crawler = HeraldCrawler(client=mock_client)
        output = crawler.extract_all_rules(
            global_only=False,
            existing_output=existing_output,
            extract_groups=False,
        )

        # Existing GitHub users should be preserved
        assert output.github_users.get("existinguser").username == "existing-gh"
        assert output.github_users.get("existinguser").user_id == 99999

    def test_resume_skips_groups_with_members(
        self,
        rule_h420_html: str,
    ) -> None:
        """Test that groups with members are skipped during resume."""
        from datetime import datetime, timezone

        existing_output = HeraldRulesOutput(
            rules=[
                Rule(
                    id="H420",
                    name="Test Rule",
                    author="test@example.com",
                    status="active",
                    type="differential-revision",
                )
            ],
            groups={
                "complete-group": Group(
                    id="complete-group",
                    display_name="Complete Group",
                    members=["user1", "user2"],  # Non-empty = complete
                ),
            },
            metadata=Metadata(
                extracted_at=datetime.now(timezone.utc),
                total_rules=1,
                total_groups=1,
                phabricator_instance="phabricator.example.com",
            ),
        )

        mock_client = Mock(spec=HeraldClient)
        mock_client.base_url = "https://phabricator.example.com"
        mock_client.fetch_listing.return_value = "<html><body></body></html>"

        crawler = HeraldCrawler(client=mock_client)
        output = crawler.extract_all_rules(
            global_only=False,
            existing_output=existing_output,
            extract_groups=True,
        )

        # Existing group should be preserved
        assert "complete-group" in output.groups
        assert output.groups["complete-group"].members == ["user1", "user2"]

    def test_scrape_status_is_set(self) -> None:
        """Test that scrape_status is set in metadata."""
        mock_client = Mock(spec=HeraldClient)
        mock_client.base_url = "https://phabricator.example.com"
        mock_client.fetch_listing.return_value = "<html><body></body></html>"

        crawler = HeraldCrawler(client=mock_client)
        output = crawler.extract_all_rules(
            global_only=False,
            extract_groups=False,
        )

        assert output.metadata is not None
        assert output.metadata.scrape_status is not None
        assert isinstance(output.metadata.scrape_status, ScrapeStatus)

    def test_resume_preserves_original_unresolved_reasons(self) -> None:
        """Test that original unresolved reasons are preserved on resume."""
        from datetime import datetime, timezone

        existing_output = HeraldRulesOutput(
            rules=[
                Rule(
                    id="H420",
                    name="Test Rule",
                    author="existinguser@example.com",
                    status="active",
                    type="differential-revision",
                )
            ],
            unresolved_users=[
                UnresolvedUser(
                    phabricator_username="alice",
                    reason="not_found",
                    referenced_in=["H420"],
                ),
                UnresolvedUser(
                    phabricator_username="bob",
                    reason="no_github_linked",
                    referenced_in=["H420"],
                ),
            ],
            metadata=Metadata(
                extracted_at=datetime.now(timezone.utc),
                total_rules=1,
                total_groups=0,
                phabricator_instance="phabricator.example.com",
            ),
        )

        mock_client = Mock(spec=HeraldClient)
        mock_client.base_url = "https://phabricator.example.com"
        mock_client.fetch_listing.return_value = "<html><body></body></html>"

        mock_people_client = Mock()

        crawler = HeraldCrawler(client=mock_client)
        output = crawler.extract_all_rules(
            global_only=False,
            existing_output=existing_output,
            extract_groups=False,
            people_client=mock_people_client,
        )

        # Original reasons should be preserved
        unresolved_by_name = {u.phabricator_username: u for u in output.unresolved_users}
        assert "alice" in unresolved_by_name
        assert unresolved_by_name["alice"].reason == "not_found"
        assert "bob" in unresolved_by_name
        assert unresolved_by_name["bob"].reason == "no_github_linked"

    def test_resume_prepopulates_github_cache_no_api_calls(self) -> None:
        """Test that pre-populated GitHub users don't cause API calls."""
        from datetime import datetime, timezone

        # Existing output with cached GitHub users
        existing_output = HeraldRulesOutput(
            rules=[
                Rule(
                    id="H420",
                    name="Test Rule",
                    author="alice@example.com",
                    status="active",
                    type="differential-revision",
                )
            ],
            github_users={
                "alice": GitHubUser(username="alice-gh", user_id=12345),
            },
            metadata=Metadata(
                extracted_at=datetime.now(timezone.utc),
                total_rules=1,
                total_groups=0,
                phabricator_instance="phabricator.example.com",
            ),
        )

        mock_client = Mock(spec=HeraldClient)
        mock_client.base_url = "https://phabricator.example.com"
        mock_client.fetch_listing.return_value = "<html><body></body></html>"

        mock_people_client = Mock()
        mock_people_client.delay = 0

        crawler = HeraldCrawler(client=mock_client)
        output = crawler.extract_all_rules(
            global_only=False,
            existing_output=existing_output,
            extract_groups=False,
            people_client=mock_people_client,
        )

        # Alice was already cached, so no API calls should be made for her
        # The resolve_github method should not be called for cached users
        # (since there are no new rules with new users, no calls should be made)
        mock_people_client.resolve_github.assert_not_called()
        # Alice should still be in the output
        assert "alice" in output.github_users
        assert output.github_users["alice"].username == "alice-gh"
