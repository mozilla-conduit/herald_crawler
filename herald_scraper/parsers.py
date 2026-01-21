"""HTML parsers for Herald rules pages."""

from typing import List, Dict, Optional, Any
from bs4 import BeautifulSoup, Tag
from herald_scraper.models import Rule, Condition, Action, Reviewer


class ListingPageParser:
    """Parser for Herald rules listing page."""

    def __init__(self, html: str) -> None:
        """Initialize with HTML content."""
        self.html: str = html
        self.soup: BeautifulSoup = BeautifulSoup(html, "lxml")

    def extract_rule_ids(self) -> List[str]:
        """
        Extract all Herald rule IDs from the listing page.

        Returns:
            List of rule IDs (e.g., ['H417', 'H418', ...])
        """
        rule_ids = set()

        for link in self.soup.find_all("a", href=True):
            href = link["href"]
            if href.startswith("/H") and len(href) > 2:
                rule_id = href[1:]
                if rule_id[0] == "H" and rule_id[1:].isdigit():
                    rule_ids.add(rule_id)

        return sorted(rule_ids, key=lambda x: int(x[1:]))

    def filter_global_rules(self, rule_ids: List[str]) -> List[str]:
        """
        Filter rule IDs to only include global rules.

        This method would need to fetch or parse additional information
        to determine which rules are global. For now, returns all rules.

        Args:
            rule_ids: List of rule IDs to filter

        Returns:
            Filtered list of global rule IDs
        """
        # TODO: Implement actual filtering based on rule type in listing
        # For now, we'll need to check each rule detail page
        return rule_ids


class RuleDetailPageParser:
    """Parser for individual Herald rule detail page."""

    def __init__(self, html: str) -> None:
        """Initialize with HTML content."""
        self.html: str = html
        self.soup: BeautifulSoup = BeautifulSoup(html, "lxml")

    def parse_rule(self) -> Optional[Rule]:
        """
        Parse the rule detail page and extract all information.

        Returns:
            Rule object with all extracted information, or None if parsing fails
        """
        try:
            rule_id = self._extract_rule_id()
            name = self._extract_rule_name()
            author = self._extract_author()
            status = self._extract_status()
            rule_type = self._extract_rule_type()
            repository = self._extract_repository()
            conditions = self._extract_conditions()
            actions = self._extract_actions()

            return Rule(
                id=rule_id,
                name=name,
                author=author,
                status=status,
                type=rule_type,
                repository=repository,
                conditions=conditions,
                actions=actions
            )
        except Exception as e:
            # Log error and return None
            print(f"Error parsing rule: {e}")
            return None

    def is_global_rule(self) -> bool:
        """
        Determine if this rule is a global rule.

        Returns:
            True if the rule is global, False otherwise
        """
        # Look for rule type indicator in the page
        # Global rules are indicated by specific text or CSS classes
        page_text = self.soup.get_text()

        # Check for "Global Rule" text
        if "Global Rule" in page_text:
            return True

        # Check for rule type in metadata
        # TODO: Implement more robust detection
        return False

    def _extract_rule_id(self) -> str:
        """Extract rule ID from the page."""
        # Rule ID appears in the last breadcrumb
        # Look for the last crumb with class containing "last-crumb"
        last_crumb = self.soup.find("span", class_="phabricator-last-crumb")
        if last_crumb:
            crumb_name = last_crumb.find("span", class_="phui-crumb-name")
            if crumb_name:
                text = crumb_name.get_text(strip=True)
                # Should be in format "H###"
                if text.startswith("H") and text[1:].isdigit():
                    return text

        # Fallback: look for links to the rule itself
        for link in self.soup.find_all("a", href=True):
            href = link["href"]
            if href.startswith("/H") and len(href) > 2:
                rule_id = href[1:]
                if rule_id[0] == "H" and rule_id[1:].isdigit():
                    return rule_id

        raise ValueError("Could not extract rule ID")

    def _extract_rule_name(self) -> str:
        """Extract rule name from the page."""
        # Rule name is typically in the page title or a header
        title = self.soup.find("title")
        if title:
            title_text = title.get_text(strip=True)
            # Remove "☿ " prefix if present
            if title_text.startswith("☿ "):
                return title_text[2:]
            return title_text

        raise ValueError("Could not extract rule name")

    def _extract_author(self) -> str:
        """Extract rule author from the page."""
        # Look for author information in metadata section
        # TODO: Implement based on actual HTML structure
        return "unknown@mozilla.com"

    def _extract_status(self) -> str:
        """Extract rule status (active/disabled)."""
        # Look for status indicators
        page_text = self.soup.get_text()

        if "Disabled" in page_text or "disabled" in page_text:
            return "disabled"

        return "active"

    def _extract_rule_type(self) -> str:
        """Extract rule type (differential-revision, commit, etc.)."""
        # Look for "Differential Revisions", "Commits", etc.
        page_text = self.soup.get_text()

        if "Differential Revision" in page_text:
            return "differential-revision"
        elif "Commit" in page_text and "Commits" in page_text:
            return "commit"

        return "unknown"

    def _extract_repository(self) -> Optional[str]:
        """Extract repository filter if present."""
        # Look for repository conditions
        # TODO: Implement based on actual HTML structure
        return None

    def _extract_conditions(self) -> List[Condition]:
        """Extract all conditions from the rule."""
        conditions = []

        # Look for conditions section
        # Typically marked by "When all of these conditions are met:" or similar
        # TODO: Implement based on actual HTML structure

        return conditions

    def _extract_actions(self) -> List[Action]:
        """Extract all actions from the rule."""
        actions = []

        # Look for actions section
        # Typically marked by "Take these actions:" or similar
        # TODO: Implement based on actual HTML structure

        return actions


class ProjectPageParser:
    """Parser for project/group pages to extract membership."""

    def __init__(self, html: str) -> None:
        """Initialize with HTML content."""
        self.html: str = html
        self.soup: BeautifulSoup = BeautifulSoup(html, "lxml")

    def extract_project_info(self) -> Dict[str, Any]:
        """
        Extract project information including members.

        Returns:
            Dictionary with project info: {
                'id': 'project-slug',
                'display_name': 'Project Display Name',
                'members': ['user1@example.com', 'user2@example.com']
            }
        """
        return {
            "id": self._extract_project_slug(),
            "display_name": self._extract_project_name(),
            "members": self._extract_members()
        }

    def _extract_project_slug(self) -> str:
        """Extract project slug from the page."""
        # TODO: Implement based on actual HTML structure
        return "unknown-project"

    def _extract_project_name(self) -> str:
        """Extract project display name."""
        # TODO: Implement based on actual HTML structure
        return "Unknown Project"

    def _extract_members(self) -> List[str]:
        """Extract list of project members."""
        members = []
        # TODO: Implement based on actual HTML structure
        return members
