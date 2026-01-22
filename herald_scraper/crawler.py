"""Herald crawler for extracting rules from Phabricator."""

import logging
from datetime import datetime, timezone
from typing import List, Optional, Callable
from urllib.parse import urlparse

import requests

from herald_scraper.client import HeraldClient
from herald_scraper.exceptions import RuleParseError
from herald_scraper.models import Rule, HeraldRulesOutput, Metadata
from herald_scraper.parsers import ListingPageParser, RuleDetailPageParser

logger = logging.getLogger(__name__)


class HeraldCrawler:
    """Crawler that fetches and parses Herald rules from Phabricator."""

    def __init__(
        self,
        client: HeraldClient,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> None:
        """
        Initialize the crawler.

        Args:
            client: HeraldClient instance for fetching pages
            progress_callback: Optional callback for progress updates.
                Called with (current_index, total_count, message).
                Example: progress_callback(5, 100, "Extracting H420") means
                processing rule 5 of 100 (currently extracting rule H420).
        """
        self.client = client
        self.progress_callback = progress_callback

    def extract_all_rules(
        self,
        global_only: bool = True,
        max_rules: Optional[int] = None,
    ) -> HeraldRulesOutput:
        """
        Extract all Herald rules and return complete output.

        Args:
            global_only: If True, only extract global rules (default)
            max_rules: Optional limit on number of rules to extract

        Returns:
            HeraldRulesOutput with all extracted rules and metadata
        """
        if global_only:
            rule_ids = self.extract_global_rule_ids()
        else:
            rule_ids = self.extract_rule_ids()

        if max_rules is not None:
            rule_ids = rule_ids[:max_rules]

        rules = self.extract_rules(rule_ids)

        parsed_url = urlparse(self.client.base_url)
        instance = parsed_url.netloc or self.client.base_url

        metadata = Metadata(
            extracted_at=datetime.now(timezone.utc),
            total_rules=len(rules),
            total_groups=0,
            phabricator_instance=instance,
        )

        return HeraldRulesOutput(
            rules=rules,
            groups={},
            metadata=metadata,
        )

    def extract_rule_ids(self) -> List[str]:
        """
        Extract all rule IDs from the listing page.

        Returns:
            List of rule IDs (e.g., ['H417', 'H418', ...])
        """
        html = self.client.fetch_listing()
        parser = ListingPageParser(html)
        return parser.extract_rule_ids()

    def extract_global_rule_ids(self) -> List[str]:
        """
        Extract only global rule IDs from the listing page.

        Returns:
            List of global rule IDs
        """
        html = self.client.fetch_listing()
        parser = ListingPageParser(html)
        all_rule_ids = parser.extract_rule_ids()
        return parser.filter_global_rules(all_rule_ids)

    def extract_rule(self, rule_id: str) -> Optional[Rule]:
        """
        Extract a single rule by ID.

        Args:
            rule_id: Rule ID to extract (e.g., 'H420')

        Returns:
            Rule object if successful, None if parsing fails
        """
        html = self.client.fetch_rule(rule_id)
        parser = RuleDetailPageParser(html)
        return parser.parse_rule()

    def extract_rules(self, rule_ids: List[str]) -> List[Rule]:
        """
        Extract multiple rules by their IDs.

        Args:
            rule_ids: List of rule IDs to extract

        Returns:
            List of successfully extracted rules
        """
        rules: List[Rule] = []
        total = len(rule_ids)

        for i, rule_id in enumerate(rule_ids):
            if self.progress_callback:
                self.progress_callback(i + 1, total, f"Extracting {rule_id}")

            try:
                rule = self.extract_rule(rule_id)
                if rule is not None:
                    rules.append(rule)
                else:
                    logger.warning(f"Failed to parse rule {rule_id}")
            except requests.RequestException as e:
                logger.error(f"Network error extracting rule {rule_id}: {e}")
            except RuleParseError as e:
                logger.error(f"Parse error extracting rule {rule_id}: {e}")
            except Exception as e:
                logger.exception(f"Unexpected error extracting rule {rule_id}: {e}")
                raise

        return rules
