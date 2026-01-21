"""Herald crawler for extracting rules from Phabricator."""

from typing import List, Optional, Callable

from herald_scraper.client import HeraldClient
from herald_scraper.models import Rule, HeraldRulesOutput


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
                Called with (current, total, message) for each rule processed.
        """
        raise NotImplementedError

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
        raise NotImplementedError

    def extract_rule_ids(self) -> List[str]:
        """
        Extract all rule IDs from the listing page.

        Returns:
            List of rule IDs (e.g., ['H417', 'H418', ...])
        """
        raise NotImplementedError

    def extract_global_rule_ids(self) -> List[str]:
        """
        Extract only global rule IDs from the listing page.

        Returns:
            List of global rule IDs
        """
        raise NotImplementedError

    def extract_rule(self, rule_id: str) -> Optional[Rule]:
        """
        Extract a single rule by ID.

        Args:
            rule_id: Rule ID to extract (e.g., 'H420')

        Returns:
            Rule object if successful, None if parsing fails

        Raises:
            RuleParseError: If the rule page cannot be parsed
        """
        raise NotImplementedError

    def extract_rules(self, rule_ids: List[str]) -> List[Rule]:
        """
        Extract multiple rules by their IDs.

        Args:
            rule_ids: List of rule IDs to extract

        Returns:
            List of successfully extracted rules
        """
        raise NotImplementedError
