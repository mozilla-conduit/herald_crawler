"""Tests for HeraldCrawler."""

from typing import Callable, List
from unittest.mock import Mock, call

import pytest

from herald_scraper.client import HeraldClient
from herald_scraper.crawler import HeraldCrawler, _sort_rule_ids, _deduplicate_rule_ids
from herald_scraper.exceptions import RuleParseError
from herald_scraper.models import Rule


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
        rule_ids = crawler.extract_rule_ids(max_pages=3)

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
