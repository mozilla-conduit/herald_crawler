"""Resolvers for collecting group membership and other PHID resolutions."""

import logging
from typing import Dict, List, Optional, Set

from herald_scraper.client import HeraldClient
from herald_scraper.models import Group, Rule
from herald_scraper.parsers import ProjectPageParser

logger = logging.getLogger(__name__)


class GroupCollector:
    """Collects group membership for reviewer groups referenced in Herald rules."""

    def __init__(self, client: HeraldClient) -> None:
        """
        Initialize the GroupCollector.

        Args:
            client: HeraldClient instance for fetching project pages
        """
        self.client = client
        self._cache: Dict[str, Group] = {}

    def extract_group_slugs_from_rules(self, rules: List[Rule]) -> Set[str]:
        """
        Extract unique group slugs from rule reviewer actions.

        Groups are identified by not containing '@' (users have email addresses).

        Args:
            rules: List of Rule objects to extract groups from

        Returns:
            Set of unique group slugs
        """
        group_slugs: Set[str] = set()

        for rule in rules:
            for action in rule.actions:
                if action.reviewers:
                    for reviewer in action.reviewers:
                        target = reviewer.target
                        # Groups don't have '@' in their name (users have emails)
                        if "@" not in target:
                            group_slugs.add(target)

        logger.debug(f"Extracted {len(group_slugs)} unique group slugs from {len(rules)} rules")
        return group_slugs

    def fetch_group(self, slug: str) -> Optional[Group]:
        """
        Fetch and parse group info for a single group.

        Uses caching to avoid duplicate fetches.

        Args:
            slug: Project/group slug (e.g., 'omc-reviewers')

        Returns:
            Group object if successful, None if fetch/parse fails
        """
        # Check cache first
        if slug in self._cache:
            logger.debug(f"Cache hit for group: {slug}")
            return self._cache[slug]

        try:
            logger.info(f"Fetching group: {slug}")
            html = self.client.fetch_project(slug)
            parser = ProjectPageParser(html)
            info = parser.extract_project_info()

            group = Group(
                id=info["id"],
                display_name=info["display_name"],
                members=info["members"],
            )

            # Cache the result
            self._cache[slug] = group
            logger.debug(f"Cached group {slug} with {len(group.members)} members")
            return group

        except Exception as e:
            logger.warning(f"Failed to fetch group {slug}: {e}")
            return None

    def collect_all_groups(self, rules: List[Rule]) -> Dict[str, Group]:
        """
        Collect all groups referenced in the given rules.

        Args:
            rules: List of Rule objects to collect groups from

        Returns:
            Dictionary mapping group slugs to Group objects
        """
        group_slugs = self.extract_group_slugs_from_rules(rules)
        groups: Dict[str, Group] = {}

        for slug in sorted(group_slugs):
            group = self.fetch_group(slug)
            if group:
                groups[slug] = group
            else:
                logger.warning(f"Could not collect group: {slug}")

        logger.info(f"Collected {len(groups)} of {len(group_slugs)} groups")
        return groups

    def clear_cache(self) -> None:
        """Clear the internal group cache."""
        self._cache.clear()
        logger.debug("Group cache cleared")
