"""Resolvers for collecting group membership and other PHID resolutions."""

import logging
import re
import time
from typing import Dict, List, Optional, Set, Tuple

from herald_scraper.client import HeraldClient
from herald_scraper.exceptions import AuthenticationError
from herald_scraper.models import GitHubUser, Group, Rule, UnresolvedUser
from herald_scraper.parsers import ProjectMembersPageParser, ProjectPageParser
from herald_scraper.people_client import PeopleDirectoryClient

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

        Uses the dedicated members page (/project/members/{id}/) for authoritative
        membership data.

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

            # First fetch project page to get project_id and basic info
            project_html = self.client.fetch_project(slug)
            logger.debug(f"Fetched project page for {slug}: {len(project_html)} bytes")

            project_parser = ProjectPageParser(project_html)
            project_info = project_parser.extract_project_info()
            logger.debug(
                f"Parsed project info for {slug}: "
                f"id={project_info['id']}, "
                f"project_id={project_info.get('project_id')}, "
                f"display_name={project_info['display_name']}"
            )

            # Fetch members from dedicated members page
            members = []
            project_id = project_info.get("project_id")
            if project_id:
                try:
                    logger.info(f"Fetching members page for {slug} (ID: {project_id})")
                    members_html = self.client.fetch_project_members(project_id)
                    logger.info(f"Fetched members page: {len(members_html)} bytes")

                    # Check what links are in the page
                    profile_links = re.findall(r'href="/p/([^"]+)/"', members_html)
                    has_oi_link = "phui-oi-link" in members_html
                    logger.info(f"Page has phui-oi-link: {has_oi_link}, profile links: {len(profile_links)}")

                    members_parser = ProjectMembersPageParser(members_html)
                    members = members_parser.extract_members()
                    logger.info(f"Got {len(members)} members from members page for {slug}")

                    if len(members) == 0:
                        # Log detailed info to help debug
                        logger.warning(
                            f"No members extracted for {slug}. "
                            f"HTML size: {len(members_html)} bytes, "
                            f"has phui-oi-link: {has_oi_link}, "
                            f"profile links found: {profile_links[:10]}"
                        )
                        if not has_oi_link:
                            logger.warning(f"HTML snippet: {members_html[:1000]}")
                except AuthenticationError as e:
                    logger.warning(
                        f"Authentication required to fetch members for {slug}. "
                        f"Group will have empty members list. Error: {e}"
                    )
            else:
                logger.warning(f"No project_id found for {slug}, cannot fetch members")

            group = Group(
                id=project_info["id"],
                display_name=project_info["display_name"],
                members=members,
            )

            # Cache the result
            self._cache[slug] = group
            logger.info(f"Collected group {slug}: {len(group.members)} members")
            return group

        except Exception as e:
            logger.warning(f"Failed to fetch group {slug}: {e}", exc_info=True)
            return None

    def collect_all_groups(
        self, rules: List[Rule], max_groups: Optional[int] = None
    ) -> Dict[str, Group]:
        """
        Collect all groups referenced in the given rules.

        Args:
            rules: List of Rule objects to collect groups from
            max_groups: Optional limit on number of groups to collect (stops collecting early)

        Returns:
            Dictionary mapping group slugs to Group objects
        """
        group_slugs = self.extract_group_slugs_from_rules(rules)
        groups: Dict[str, Group] = {}

        for slug in sorted(group_slugs):
            # Check if we've collected enough groups
            if max_groups is not None and len(groups) >= max_groups:
                logger.info(
                    f"Collected {len(groups)} groups, stopping (max_groups={max_groups})"
                )
                break

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


class UsernameResolver:
    """Resolves Phabricator usernames to GitHub usernames and user IDs."""

    def __init__(self, client: PeopleDirectoryClient) -> None:
        """
        Initialize the UsernameResolver.

        Args:
            client: PeopleDirectoryClient instance for resolving usernames
        """
        self.client = client
        self._cache: Dict[str, GitHubUser] = {}
        self._unresolved: Dict[str, str] = {}  # username -> reason

    def extract_usernames_from_rules(
        self, rules: List[Rule], group_slugs: Set[str]
    ) -> Dict[str, List[str]]:
        """
        Extract unique usernames from rules, excluding group names.

        Args:
            rules: List of Rule objects to extract usernames from
            group_slugs: Set of known group slugs to exclude

        Returns:
            Dictionary mapping usernames to list of rule IDs that reference them
        """
        username_refs: Dict[str, List[str]] = {}

        for rule in rules:
            # Rule author
            author = rule.author
            if author and "@" in author:  # Users have email-like format
                if author not in username_refs:
                    username_refs[author] = []
                username_refs[author].append(rule.id)

            # Reviewers in actions
            for action in rule.actions:
                if action.reviewers:
                    for reviewer in action.reviewers:
                        target = reviewer.target
                        # Skip groups (no @ and in group_slugs)
                        if target in group_slugs:
                            continue
                        # Users have @ in their name (email format)
                        if "@" in target:
                            if target not in username_refs:
                                username_refs[target] = []
                            username_refs[target].append(rule.id)

        logger.debug(
            f"Extracted {len(username_refs)} unique usernames from {len(rules)} rules"
        )
        return username_refs

    def extract_usernames_from_groups(
        self, groups: Dict[str, Group]
    ) -> Dict[str, List[str]]:
        """
        Extract unique usernames from group members.

        Args:
            groups: Dictionary of group slug to Group objects

        Returns:
            Dictionary mapping usernames to list of group slugs that contain them
        """
        username_refs: Dict[str, List[str]] = {}

        for slug, group in groups.items():
            for member in group.members:
                if member not in username_refs:
                    username_refs[member] = []
                username_refs[member].append(f"group:{slug}")

        logger.debug(
            f"Extracted {len(username_refs)} unique usernames from {len(groups)} groups"
        )
        return username_refs

    def resolve_username(self, username: str) -> Optional[GitHubUser]:
        """
        Resolve a single Phabricator username to GitHub user info.

        Uses caching to avoid duplicate lookups.

        Args:
            username: Phabricator username (may include @domain)

        Returns:
            GitHubUser with username and user_id, or None if unresolved
        """
        # Extract just the username part if it's an email
        lookup_name = username.split("@")[0] if "@" in username else username

        # Check cache first
        if lookup_name in self._cache:
            logger.debug(f"Cache hit for username: {lookup_name}")
            return self._cache[lookup_name]

        # Check if already marked as unresolved
        if lookup_name in self._unresolved:
            logger.debug(f"Already unresolved: {lookup_name}")
            return None

        try:
            resolution = self.client.resolve_github(lookup_name)

            if resolution.username or resolution.user_id:
                github_user = GitHubUser(username=resolution.username, user_id=resolution.user_id)
                self._cache[lookup_name] = github_user
                logger.debug(f"Resolved {lookup_name} -> {resolution.username} (ID: {resolution.user_id})")
                return github_user
            else:
                self._unresolved[lookup_name] = "no_github_linked_or_not_found"
                logger.debug(f"Could not resolve: {lookup_name}")
                return None

        except Exception as e:
            reason = f"error: {str(e)}"
            self._unresolved[lookup_name] = reason
            logger.warning(f"Error resolving {lookup_name}: {e}")
            return None

    def resolve_all(
        self,
        rules: List[Rule],
        groups: Dict[str, Group],
        max_users: Optional[int] = None,
        delay: float = 0.5,
    ) -> Tuple[Dict[str, GitHubUser], List[UnresolvedUser], bool]:
        """
        Resolve all usernames found in rules and groups.

        Args:
            rules: List of Rule objects
            groups: Dictionary of group slug to Group objects
            max_users: Optional limit on number of users to resolve
            delay: Delay between API requests in seconds

        Returns:
            Tuple of (resolved_users dict, unresolved_users list, hit_max_users flag)
            hit_max_users is True if we stopped early due to max_users limit
        """
        # Extract all usernames
        group_slugs = set(groups.keys())
        rule_usernames = self.extract_usernames_from_rules(rules, group_slugs)
        group_usernames = self.extract_usernames_from_groups(groups)

        # Merge references (user can appear in both rules and groups)
        all_refs: Dict[str, List[str]] = {}
        for username, refs in rule_usernames.items():
            all_refs[username] = refs.copy()
        for username, refs in group_usernames.items():
            if username in all_refs:
                all_refs[username].extend(refs)
            else:
                all_refs[username] = refs.copy()

        logger.info(f"Resolving GitHub usernames for {len(all_refs)} users")

        resolved_users: Dict[str, GitHubUser] = {}
        count = 0
        hit_max_users = False

        for username in sorted(all_refs.keys()):
            if max_users is not None and count >= max_users:
                logger.info(f"Reached max_users limit ({max_users}), stopping")
                hit_max_users = True
                break

            github_user = self.resolve_username(username)
            # Store with the lookup name (without @domain)
            lookup_name = username.split("@")[0] if "@" in username else username
            if github_user:
                resolved_users[lookup_name] = github_user

            count += 1

            # Rate limiting between requests
            if count < len(all_refs):
                time.sleep(delay)

        # Build unresolved users list with references
        unresolved_list: List[UnresolvedUser] = []
        for username, reason in self._unresolved.items():
            # Find references for this username
            refs = all_refs.get(username, [])
            # Also check with @domain variations
            for full_username in all_refs:
                if full_username.startswith(username + "@"):
                    refs.extend(all_refs[full_username])

            unresolved_list.append(
                UnresolvedUser(
                    phabricator_username=username,
                    reason=reason,
                    referenced_in=sorted(set(refs)),
                )
            )

        logger.info(
            f"Resolved {len(resolved_users)} users, {len(unresolved_list)} unresolved"
        )
        return resolved_users, unresolved_list, hit_max_users

    def clear_cache(self) -> None:
        """Clear the internal caches."""
        self._cache.clear()
        self._unresolved.clear()
        logger.debug("Username resolver cache cleared")
