"""Tests for HeraldCrawler."""

from typing import Callable, List
from unittest.mock import Mock, call

import pytest

from herald_scraper.client import HeraldClient
from herald_scraper.crawler import HeraldCrawler
from herald_scraper.exceptions import RuleParseError
from herald_scraper.models import Rule


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
        mock_client = Mock(spec=HeraldClient)
        mock_client.fetch_listing.return_value = listing_html

        crawler = HeraldCrawler(client=mock_client)
        rule_ids = crawler.extract_rule_ids()

        # Should return a list of rule IDs
        assert isinstance(rule_ids, list)
        assert all(rule_id.startswith("H") for rule_id in rule_ids)
        mock_client.fetch_listing.assert_called_once()

    def test_extract_global_rule_ids(self, listing_html: str) -> None:
        """Test extracting only global rule IDs from listing page."""
        mock_client = Mock(spec=HeraldClient)
        mock_client.fetch_listing.return_value = listing_html

        crawler = HeraldCrawler(client=mock_client)
        rule_ids = crawler.extract_global_rule_ids()

        # Should return a list of global rule IDs
        assert isinstance(rule_ids, list)
        assert all(rule_id.startswith("H") for rule_id in rule_ids)
        mock_client.fetch_listing.assert_called_once()


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
        mock_client = Mock(spec=HeraldClient)
        mock_client.fetch_listing.return_value = listing_html
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
        mock_client = Mock(spec=HeraldClient)
        mock_client.fetch_listing.return_value = listing_html
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
        mock_client = Mock(spec=HeraldClient)
        mock_client.fetch_listing.return_value = listing_html
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
        mock_client = Mock(spec=HeraldClient)
        mock_client.fetch_listing.return_value = listing_html
        mock_client.fetch_rule.return_value = rule_h420_html

        crawler = HeraldCrawler(client=mock_client)
        output = crawler.extract_all_rules(global_only=False, max_rules=1)

        assert len(output.rules) <= 1
