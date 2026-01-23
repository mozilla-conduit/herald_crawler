"""HTML parsers for Herald rules pages."""

import logging
import re
from typing import List, Dict, Optional, Any
from bs4 import BeautifulSoup, Tag
from herald_scraper.models import Rule, Condition, Action, Reviewer

logger = logging.getLogger(__name__)


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

        Parses the listing page HTML to identify which rules are global
        by looking for "Global Rule" text in each rule's row.

        Args:
            rule_ids: List of rule IDs to filter

        Returns:
            Filtered list of global rule IDs
        """
        global_rules = []

        for rule_id in rule_ids:
            # Find the link for this rule
            for link in self.soup.find_all("a", href=f"/{rule_id}"):
                # Find the parent frame element
                frame = link.find_parent("div", class_="phui-oi-frame")
                if frame:
                    text = frame.get_text(" ", strip=True)
                    if "Global Rule" in text:
                        global_rules.append(rule_id)
                        break

        return global_rules

    def has_next_page(self) -> bool:
        """
        Check if there is a next page of results.

        Phabricator uses cursor-based pagination with a pager containing
        a "Next" link when more results are available.

        Returns:
            True if a next page exists, False otherwise
        """
        pager = self.soup.find("div", class_="phui-pager-view")
        if not pager:
            return False

        # Look for a link with "Next" text or after= parameter
        for link in pager.find_all("a", href=True):
            href = link.get("href", "")
            if "after=" in href:
                return True

        return False

    def get_next_page_url(self) -> Optional[str]:
        """
        Extract the URL for the next page of results.

        Returns:
            The URL for the next page, or None if no next page exists
        """
        pager = self.soup.find("div", class_="phui-pager-view")
        if not pager:
            return None

        # Look for a link with after= parameter (Phabricator's cursor pagination)
        for link in pager.find_all("a", href=True):
            href = link.get("href", "")
            if "after=" in href:
                return href

        return None


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
            logger.error(f"Error parsing rule: {e}")
            return None

    def is_global_rule(self) -> bool:
        """
        Determine if this rule is a global rule.

        Looks for the 'Rule Type' property in the property list and checks
        if its value is 'Global'. Returns False if the property is not found
        or has a different value (e.g., 'Personal', 'Object').

        Returns:
            True if the rule is global, False otherwise
        """
        # Look for "Rule Type" property key and check if value is "Global"
        for dt in self.soup.find_all("dt", class_="phui-property-list-key"):
            if "Rule Type" in dt.get_text(strip=True):
                # Find the corresponding dd value
                dd = dt.find_next_sibling("dd", class_="phui-property-list-value")
                if dd:
                    value = dd.get_text(strip=True)
                    return value == "Global"

        # If we can't find the Rule Type property, log a warning and return False
        logger.warning("Could not find 'Rule Type' property in page")
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
        # Look for "created this object" in the timeline
        # The author is typically the first person who created the rule
        timeline = self.soup.find("div", class_="phui-timeline-view")
        if timeline:
            # Find the text that says "created this object"
            for title_div in timeline.find_all("div", class_="phui-timeline-title"):
                text = title_div.get_text(strip=True)
                if "created this object" in text:
                    # Find the person link in this div
                    person_link = title_div.find("a", class_="phui-link-person")
                    if person_link:
                        return person_link.get_text(strip=True)

        # Fallback: look for any user link that created the object
        for link in self.soup.find_all("a", class_="phui-link-person"):
            parent_text = ""
            parent = link.find_parent("div", class_="phui-timeline-title")
            if parent:
                parent_text = parent.get_text(strip=True)
            if "created" in parent_text:
                return link.get_text(strip=True)

        return "unknown"

    def _extract_status(self) -> str:
        """Extract rule status (active/disabled).

        Looks for the status tag in the page header subheader section,
        which contains a phui-tag-view span with the status text.
        """
        # Look for status in the header subheader section
        subheader = self.soup.find("div", class_="phui-header-subheader")
        if subheader:
            # Status is in a span with phui-tag-view class
            status_tag = subheader.find("span", class_="phui-tag-view")
            if status_tag:
                status_text = status_tag.get_text(strip=True).lower()
                if "disabled" in status_text:
                    return "disabled"
                if "active" in status_text:
                    return "active"

        # Fallback: assume active if no explicit disabled indicator found
        return "active"

    def _extract_rule_type(self) -> str:
        """Extract rule type (differential-revision, commit, etc.).

        Looks for the 'Applies To' property in the property list, which
        indicates what type of objects this rule applies to.
        """
        # Look for "Applies To" property key and check the value
        for dt in self.soup.find_all("dt", class_="phui-property-list-key"):
            if "Applies To" in dt.get_text(strip=True):
                dd = dt.find_next_sibling("dd", class_="phui-property-list-value")
                if dd:
                    value = dd.get_text(strip=True).lower()
                    if "differential" in value:
                        return "differential-revision"
                    elif "commit" in value:
                        return "commit"
                    elif "task" in value:
                        return "task"

        return "unknown"

    def _extract_repository(self) -> Optional[str]:
        """Extract repository filter if present.

        Returns the repository name if there's exactly one repository condition.
        Returns None if there are no repository conditions or multiple repositories
        (in which case, the full list is available in the conditions).
        """
        conditions = self._extract_conditions()
        repo_conditions = [c for c in conditions if c.type == "repository"]

        if len(repo_conditions) == 1:
            value = repo_conditions[0].value
            if isinstance(value, list) and len(value) == 1:
                return value[0]
            elif isinstance(value, str):
                return value

        return None

    def _extract_conditions(self) -> List[Condition]:
        """Extract all conditions from the rule."""
        conditions: List[Condition] = []

        # Find the conditions header
        conditions_header = None
        for p in self.soup.find_all("p", class_="herald-list-description"):
            text = p.get_text(strip=True)
            if "When all of these conditions are met" in text:
                conditions_header = p
                break

        if not conditions_header:
            return conditions

        # Iterate through siblings until we hit the actions header
        for sibling in conditions_header.find_next_siblings():
            # Stop if we reach the actions section
            if sibling.name == "p" and "herald-list-description" in sibling.get("class", []):
                break

            # Process herald-list-item divs
            if sibling.name == "div" and "herald-list-item" in sibling.get("class", []):
                condition = self._parse_condition_item(sibling)
                if condition:
                    conditions.append(condition)

        return conditions

    def _parse_condition_item(self, item: Tag) -> Optional[Condition]:
        """Parse a single condition item div."""
        text = item.get_text(strip=True)

        # Repository condition
        if text.startswith("Repository is any of"):
            repos = self._extract_handle_names(item)
            return Condition(
                type="repository",
                operator="is-any-of",
                value=repos
            )

        # Revision status condition
        if "Revision status is not any of" in text:
            # Extract status values after "is not any of"
            match = re.search(r"is not any of\s+(.+)$", text)
            if match:
                statuses = [s.strip() for s in match.group(1).split(",")]
                return Condition(
                    type="differential-revision-status",
                    operator="is-not-any-of",
                    value=statuses
                )

        # Affected files matches regexp
        if "Affected files matches regexp" in text or "Affected files match regexp" in text:
            # Extract regexp pattern between @ delimiters
            pattern = self._extract_regexp_pattern(text)
            if pattern:
                return Condition(
                    type="differential-diff-content",
                    operator="matches-regexp",
                    value=pattern
                )

        # Reviewers exists condition
        if "Reviewers exists" in text:
            return Condition(
                type="differential-reviewers",
                operator="exists",
                value=True
            )

        # Reviewers does not exist
        if "Reviewers does not exist" in text:
            return Condition(
                type="differential-reviewers",
                operator="not-exists",
                value=True
            )

        # Generic fallback - log unknown condition types
        return Condition(
            type="unknown",
            operator="unknown",
            value=text
        )

    def _extract_handle_names(self, element: Tag) -> List[str]:
        """Extract names from phui-handle links within an element."""
        names: List[str] = []
        for link in element.find_all("a", class_="phui-handle"):
            # Get the text of the link (e.g., "rMOZILLACENTRAL mozilla-central")
            link_text = link.get_text(strip=True)
            # For repository links, extract just the readable name
            # Format is "rSHORTNAME readable-name"
            if " " in link_text:
                names.append(link_text.split(" ", 1)[1])
            else:
                names.append(link_text)
        return names

    def _extract_regexp_pattern(self, text: str) -> Optional[str]:
        """Extract regexp pattern from condition text (between @ delimiters)."""
        # Pattern is enclosed in @ symbols
        match = re.search(r"@(.+)@", text)
        if match:
            return match.group(1)
        return None

    def _extract_actions(self) -> List[Action]:
        """Extract all actions from the rule."""
        actions: List[Action] = []

        # Find the actions header
        actions_header = None
        for p in self.soup.find_all("p", class_="herald-list-description"):
            text = p.get_text(strip=True)
            if "Take these actions" in text:
                actions_header = p
                break

        if not actions_header:
            return actions

        # Iterate through siblings after the actions header
        for sibling in actions_header.find_next_siblings():
            # Stop if we reach another section (unlikely, but defensive)
            if sibling.name == "p" and "herald-list-description" in sibling.get("class", []):
                break

            # Process herald-list-item divs
            if sibling.name == "div" and "herald-list-item" in sibling.get("class", []):
                action = self._parse_action_item(sibling)
                if action:
                    actions.append(action)

        return actions

    def _parse_action_item(self, item: Tag) -> Optional[Action]:
        """Parse a single action item div."""
        text = item.get_text(strip=True)

        # Add blocking reviewers
        if "Add blocking reviewers:" in text:
            reviewer_names = self._extract_handle_names(item)
            reviewers = [
                Reviewer(target=name, blocking=True)
                for name in reviewer_names
            ]
            return Action(
                type="add-reviewers",
                reviewers=reviewers
            )

        # Add (non-blocking) reviewers
        if text.startswith("Add reviewers:") and "blocking" not in text.lower():
            reviewer_names = self._extract_handle_names(item)
            reviewers = [
                Reviewer(target=name, blocking=False)
                for name in reviewer_names
            ]
            return Action(
                type="add-reviewers",
                reviewers=reviewers
            )

        # Add subscribers
        if "Add subscribers:" in text:
            subscriber_names = self._extract_handle_names(item)
            return Action(
                type="add-subscribers",
                targets=subscriber_names
            )

        # Generic fallback
        return Action(
            type="unknown",
            targets=[text]
        )


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
                'project_id': '171',  # Numeric ID for API/URL use
                'display_name': 'Project Display Name',
                'members': ['user1', 'user2']
            }
        """
        return {
            "id": self._extract_project_slug(),
            "project_id": self._extract_project_id(),
            "display_name": self._extract_project_name(),
            "members": self._extract_members()
        }

    def _extract_project_id(self) -> Optional[str]:
        """Extract numeric project ID from various page elements.

        Tries multiple approaches to find the project ID:
        1. Members link in sidebar: /project/members/{id}/
        2. Manage link: /project/manage/{id}/
        3. Profile link: /project/profile/{id}/
        4. Subprojects link: /project/subprojects/{id}/
        """
        # Patterns to try, in order of preference
        patterns = [
            r"/project/members/(\d+)/?",
            r"/project/manage/(\d+)/?",
            r"/project/profile/(\d+)/?",
            r"/project/subprojects/(\d+)/?",
        ]

        # Find all links that might contain project ID
        for pattern in patterns:
            for link in self.soup.find_all("a", href=True):
                href = link.get("href", "")
                match = re.search(pattern, href)
                if match:
                    project_id = match.group(1)
                    logger.debug(f"Found project ID {project_id} via pattern {pattern}")
                    return project_id

        # Log what links we did find for debugging
        project_links = [
            a.get("href") for a in self.soup.find_all("a", href=True)
            if a.get("href", "").startswith("/project/")
        ]
        if project_links:
            logger.debug(f"Found project links but no ID: {project_links[:5]}")
        else:
            logger.debug("No /project/ links found on page")

        return None

    def _extract_project_slug(self) -> str:
        """Extract project slug from various page elements.

        Tries multiple approaches:
        1. 'Looks Like' property with tag link
        2. Tag link in breadcrumbs or sidebar
        3. Page title (format: "project-name · Manage")
        """
        # Try to find slug from tag link in "Looks Like" property
        for dt in self.soup.find_all("dt", class_="phui-property-list-key"):
            if "Looks Like" in dt.get_text(strip=True):
                dd = dt.find_next_sibling("dd", class_="phui-property-list-value")
                if dd:
                    tag_link = dd.find("a", href=True)
                    if tag_link:
                        href = tag_link.get("href", "")
                        # Extract slug from /tag/{slug}/ pattern
                        if href.startswith("/tag/") and href.endswith("/"):
                            slug = href[5:-1]
                            logger.debug(f"Found slug '{slug}' from 'Looks Like' property")
                            return slug

        # Try to find tag link anywhere on the page
        for link in self.soup.find_all("a", href=True):
            href = link.get("href", "")
            if href.startswith("/tag/") and href.endswith("/"):
                slug = href[5:-1]
                # Skip generic tags that aren't project names
                if slug and not slug.startswith("_"):
                    logger.debug(f"Found slug '{slug}' from tag link")
                    return slug

        # Fallback: extract from page title (format: "project-name · Manage")
        logger.debug("No tag link found, falling back to title")
        title = self.soup.find("title")
        if title:
            title_text = title.get_text(strip=True)
            if " · " in title_text:
                slug = title_text.split(" · ")[0]
                logger.debug(f"Extracted slug '{slug}' from title")
                return slug

        logger.debug("Could not extract project slug, returning default")
        return "unknown-project"

    def _extract_project_name(self) -> str:
        """Extract project display name from page title."""
        title = self.soup.find("title")
        if title:
            title_text = title.get_text(strip=True)
            # Title format: "project-name · Manage" or similar
            if " · " in title_text:
                return title_text.split(" · ")[0]
            return title_text

        # Fallback: try breadcrumbs
        logger.debug("No title found, falling back to breadcrumbs")
        breadcrumbs = self.soup.find_all("span", class_="phui-crumb-name")
        if len(breadcrumbs) >= 2:
            return breadcrumbs[-2].get_text(strip=True).strip()

        logger.debug("Could not extract project name, returning default")
        return "Unknown Project"

    def _extract_members(self) -> List[str]:
        """Extract list of project members by parsing timeline events.

        Processes "added a member" and "removed a member" events chronologically
        to compute the current membership list.
        """
        members: set[str] = set()

        timeline = self.soup.find("div", class_="phui-timeline-view")
        if not timeline:
            logger.debug("No timeline found, returning empty members list")
            return []

        for title_div in timeline.find_all("div", class_="phui-timeline-title"):
            text = title_div.get_text(" ", strip=True)

            if "added a member:" in text:
                member = self._extract_member_from_event(title_div)
                if member:
                    members.add(member)
                    logger.debug(f"Added member: {member}")
            elif "removed a member:" in text:
                member = self._extract_member_from_event(title_div)
                if member:
                    members.discard(member)
                    logger.debug(f"Removed member: {member}")

        logger.debug(f"Extracted {len(members)} members from timeline")
        return sorted(members)

    def _extract_member_from_event(self, element: Tag) -> Optional[str]:
        """Extract the member username from an add/remove event.

        The event contains two person links: the actor and the target.
        The target (member being added/removed) is the second link.
        """
        person_links = element.find_all("a", class_="phui-link-person")
        if len(person_links) >= 2:
            return person_links[1].get_text(strip=True)
        return None


class ProjectMembersPageParser:
    """Parser for project members list page (/project/members/{id}/).

    This parser extracts the authoritative list of current project members
    from the dedicated members page, rather than computing membership from
    timeline events.
    """

    def __init__(self, html: str) -> None:
        """Initialize with HTML content."""
        self.html: str = html
        self.soup: BeautifulSoup = BeautifulSoup(html, "lxml")

    def extract_members(self) -> List[str]:
        """Extract list of current project members.

        Returns:
            List of usernames who are currently members of the project.
        """
        members: List[str] = []

        # Look for member cards with profile links: <a href="/p/{username}/" class="phui-oi-link">
        # The members page shows a list of user cards in a phui-oi-list-view
        member_links = self.soup.find_all(
            "a", class_="phui-oi-link", href=lambda h: h and h.startswith("/p/")
        )
        for link in member_links:
            href = link.get("href", "")
            # Extract username from /p/{username}/
            if href.startswith("/p/") and href.endswith("/"):
                username = href[3:-1]  # Remove "/p/" prefix and "/" suffix
                if username and username not in members:
                    members.append(username)

        logger.debug(f"Extracted {len(members)} members from members page")
        return sorted(members)

    def has_pagination(self) -> bool:
        """Check if the members list has pagination.

        Returns:
            True if there are more pages of members.
        """
        pager = self.soup.find("div", class_="phui-pager-view")
        if not pager:
            return False
        # Check for "Next" link
        next_link = pager.find("a", string=lambda s: s and "Next" in s)
        return next_link is not None

    def get_next_page_url(self) -> Optional[str]:
        """Get URL for the next page of members if pagination exists.

        Returns:
            URL for the next page, or None if no next page.
        """
        pager = self.soup.find("div", class_="phui-pager-view")
        if not pager:
            return None
        next_link = pager.find("a", string=lambda s: s and "Next" in s)
        if next_link:
            return next_link.get("href")
        return None
