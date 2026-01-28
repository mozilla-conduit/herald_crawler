"""Herald crawler for extracting rules from Phabricator."""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Generator, List, Optional, Callable, Set, Tuple, Union
from urllib.parse import urlparse

import requests

from herald_scraper.client import HeraldClient
from herald_scraper.exceptions import RuleParseError
from herald_scraper.models import GitHubUser, Group, Rule, HeraldRulesOutput, Metadata, ScrapeStatus, UnresolvedUser
from herald_scraper.parsers import ListingPageParser, RuleDetailPageParser
from herald_scraper.people_client import PeopleDirectoryClient
from herald_scraper.resolvers import GroupCollector, UsernameResolver

logger = logging.getLogger(__name__)


def _sort_rule_ids(rule_ids: List[str]) -> List[str]:
    """
    Sort rule IDs numerically by their ID number.

    Handles non-numeric IDs gracefully by sorting them after numeric IDs.

    Args:
        rule_ids: List of rule IDs (e.g., ['H420', 'H100', 'H200'])

    Returns:
        Sorted list of rule IDs
    """
    def sort_key(rule_id: str) -> Tuple[int, int, str]:
        try:
            return (0, int(rule_id[1:]), rule_id)
        except (ValueError, IndexError):
            return (1, 0, rule_id)  # Non-numeric IDs sort after numeric

    return sorted(rule_ids, key=sort_key)


def _deduplicate_rule_ids(rule_ids: List[str]) -> List[str]:
    """
    Remove duplicate rule IDs while preserving order.

    Args:
        rule_ids: List of rule IDs that may contain duplicates

    Returns:
        Deduplicated list preserving first occurrence order
    """
    seen: set[str] = set()
    unique_ids: List[str] = []
    for rule_id in rule_ids:
        if rule_id not in seen:
            seen.add(rule_id)
            unique_ids.append(rule_id)
    return unique_ids


def load_existing_output(file_path: Union[str, Path]) -> Optional[HeraldRulesOutput]:
    """
    Load existing Herald rules output from a JSON file.

    Args:
        file_path: Path to the JSON file

    Returns:
        HeraldRulesOutput if file exists and is valid, None otherwise
    """
    path = Path(file_path)
    if not path.exists():
        logger.debug(f"No existing output file at {path}")
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        output = HeraldRulesOutput.model_validate(data)
        logger.info(
            f"Loaded existing output: {len(output.rules)} rules, "
            f"{len(output.groups)} groups, {len(output.github_users)} GitHub users"
        )
        return output
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse existing output file {path}: {e}")
        return None
    except Exception as e:
        logger.warning(f"Failed to load existing output file {path}: {e}")
        return None


def atomic_write_json(file_path: Union[str, Path], output: HeraldRulesOutput) -> None:
    """
    Write output to JSON file atomically.

    Writes to a temporary file first, then renames to avoid corruption
    if the process is interrupted.

    Args:
        file_path: Path to write the output file
        output: HeraldRulesOutput to serialize
    """
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Write to temp file in same directory (for atomic rename)
    fd, temp_path = tempfile.mkstemp(
        suffix=".tmp",
        prefix=path.stem + "_",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json_str = output.model_dump_json(indent=2, exclude_none=True)
            f.write(json_str)
            f.write("\n")

        # Atomic rename
        os.replace(temp_path, path)
        logger.debug(f"Wrote output to {path}")
    except Exception:
        # Clean up temp file on error
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise


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
        max_pages: int = 100,
        extract_groups: bool = True,
        max_groups: Optional[int] = None,
        people_client: Optional[PeopleDirectoryClient] = None,
        max_users: Optional[int] = None,
        existing_output: Optional[HeraldRulesOutput] = None,
    ) -> HeraldRulesOutput:
        """
        Extract all Herald rules and return complete output.

        Args:
            global_only: If True, only extract global rules (default)
            max_rules: Optional limit on number of rules to extract (stops fetching pages early)
            max_pages: Maximum number of listing pages to fetch (default 100, use 1 to skip pagination)
            extract_groups: If True, also extract group membership for reviewer groups (default)
            max_groups: Optional limit on number of groups to collect (stops collecting early)
            people_client: Optional PeopleDirectoryClient for GitHub username resolution
            max_users: Optional limit on number of users to resolve (stops resolving early)
            existing_output: Optional existing output to resume from (skip already-scraped items)

        Returns:
            HeraldRulesOutput with all extracted rules, groups, and metadata
        """
        # Determine what's already been scraped
        existing_rule_ids: Set[str] = set()
        existing_groups: Dict[str, Group] = {}
        existing_github_users: Dict[str, GitHubUser] = {}
        existing_unresolved: Dict[str, str] = {}  # username -> reason
        existing_rules: List[Rule] = []

        if existing_output:
            existing_rule_ids = {rule.id for rule in existing_output.rules}
            existing_rules = list(existing_output.rules)
            # Only consider groups with non-empty members as "complete"
            existing_groups = {
                slug: group for slug, group in existing_output.groups.items()
                if group.members  # non-empty members list
            }
            existing_github_users = dict(existing_output.github_users)
            existing_unresolved = {u.phabricator_username: u.reason for u in existing_output.unresolved_users}
            logger.info(
                f"Resuming from existing output: {len(existing_rule_ids)} rules, "
                f"{len(existing_groups)} groups (with members), "
                f"{len(existing_github_users)} GitHub users"
            )

        # Get all rule IDs
        if global_only:
            all_rule_ids = self.extract_global_rule_ids(max_pages=max_pages, max_rules=max_rules)
        else:
            all_rule_ids = self.extract_rule_ids(max_pages=max_pages, max_rules=max_rules)

        # Filter out already-scraped rules
        new_rule_ids = [rid for rid in all_rule_ids if rid not in existing_rule_ids]
        if existing_rule_ids:
            logger.info(f"Skipping {len(existing_rule_ids)} already-scraped rules, fetching {len(new_rule_ids)} new rules")

        # Extract new rules
        new_rules = self.extract_rules(new_rule_ids)

        # Merge rules (existing + new)
        rules = existing_rules + new_rules
        rules_complete = len(new_rule_ids) == 0 or len(new_rules) == len(new_rule_ids)

        # Collect group membership if requested
        groups: Dict[str, Group] = dict(existing_groups)
        groups_complete = True
        if extract_groups and rules:
            logger.info("Collecting group membership for reviewer groups")
            group_collector = GroupCollector(self.client)

            # Pre-populate cache with existing groups
            for slug, group in existing_groups.items():
                group_collector._cache[slug] = group

            all_groups = group_collector.collect_all_groups(rules, max_groups=max_groups)
            groups.update(all_groups)

            # Check if any groups have empty members (incomplete)
            groups_complete = all(group.members for group in groups.values())

        # Resolve GitHub usernames and user IDs if people_client provided
        github_users: Dict[str, GitHubUser] = dict(existing_github_users)
        unresolved_users: List[UnresolvedUser] = []
        github_complete = True

        if people_client and rules:
            logger.info("Resolving GitHub usernames for users")
            username_resolver = UsernameResolver(people_client)

            # Pre-populate cache with existing data
            for username, gh_user in existing_github_users.items():
                username_resolver._cache[username] = gh_user
            for username, reason in existing_unresolved.items():
                username_resolver._unresolved[username] = reason

            new_users, new_unresolved, hit_max_users = username_resolver.resolve_all(
                rules, groups, max_users=max_users, delay=people_client.delay
            )

            github_users.update(new_users)

            # Rebuild unresolved list from resolver's state
            unresolved_users = new_unresolved

            # GitHub resolution is complete if we didn't hit the max_users limit
            github_complete = not hit_max_users

            # Populate author_github on rules
            for rule in rules:
                author_lookup = rule.author.split("@")[0] if "@" in rule.author else rule.author
                if author_lookup in github_users:
                    rule.author_github = github_users[author_lookup].username

        parsed_url = urlparse(self.client.base_url)
        instance = parsed_url.netloc or self.client.base_url

        scrape_status = ScrapeStatus(
            rules_complete=rules_complete,
            groups_complete=groups_complete,
            github_complete=github_complete,
        )

        metadata = Metadata(
            extracted_at=datetime.now(timezone.utc),
            total_rules=len(rules),
            total_groups=len(groups),
            total_users_resolved=len(github_users),
            total_users_unresolved=len(unresolved_users),
            phabricator_instance=instance,
            scrape_status=scrape_status,
        )

        return HeraldRulesOutput(
            rules=rules,
            groups=groups,
            github_users=github_users,
            unresolved_users=unresolved_users,
            metadata=metadata,
        )

    def _fetch_all_listing_pages(
        self, max_pages: int = 100
    ) -> Generator[Tuple[ListingPageParser, bool], None, None]:
        """
        Yield ListingPageParser for each page of listing results.

        Handles pagination by following 'next page' links until no more
        pages exist or max_pages is reached.

        Args:
            max_pages: Maximum number of pages to fetch (safeguard against infinite loops)

        Yields:
            Tuple of (ListingPageParser, reached_max_pages) for each page.
            reached_max_pages is True only on the last yield if max_pages was hit.
        """
        page_count = 0
        next_url: Optional[str] = None

        while page_count < max_pages:
            page_count += 1

            if next_url:
                logger.info(f"Fetching listing page {page_count}: {next_url}")
                html = self.client.fetch_page(next_url)
            else:
                logger.info(f"Fetching listing page {page_count}")
                html = self.client.fetch_listing()

            parser = ListingPageParser(html)

            if parser.has_next_page():
                next_url = parser.get_next_page_url()
                yield parser, False
            else:
                yield parser, False
                return

        # Reached max_pages limit
        yield parser, True

    def extract_rule_ids(
        self, max_pages: int = 100, max_rules: Optional[int] = None
    ) -> List[str]:
        """
        Extract all rule IDs from listing pages, following pagination.

        Args:
            max_pages: Maximum number of pages to fetch (default 100, safeguard against infinite loops)
            max_rules: Stop fetching pages once this many rule IDs are collected (default None = no limit)

        Returns:
            List of rule IDs (e.g., ['H417', 'H418', ...])
        """
        all_rule_ids: List[str] = []

        for parser, reached_max in self._fetch_all_listing_pages(max_pages):
            page_rule_ids = parser.extract_rule_ids()
            all_rule_ids.extend(page_rule_ids)

            # Check if we've collected enough rules
            unique_ids = _deduplicate_rule_ids(all_rule_ids)
            if max_rules is not None and len(unique_ids) >= max_rules:
                logger.info(
                    f"Collected {len(unique_ids)} rule IDs, stopping pagination (max_rules={max_rules})"
                )
                return _sort_rule_ids(unique_ids[:max_rules])

            if reached_max:
                logger.warning(
                    f"Reached max pages limit ({max_pages}), found {len(unique_ids)} rules. "
                    f"Some rules may be missing."
                )

        unique_ids = _deduplicate_rule_ids(all_rule_ids)
        return _sort_rule_ids(unique_ids)

    def extract_global_rule_ids(
        self, max_pages: int = 100, max_rules: Optional[int] = None
    ) -> List[str]:
        """
        Extract only global rule IDs from listing pages, following pagination.

        Args:
            max_pages: Maximum number of pages to fetch (default 100, safeguard against infinite loops)
            max_rules: Stop fetching pages once this many rule IDs are collected (default None = no limit)

        Returns:
            List of global rule IDs
        """
        all_global_rule_ids: List[str] = []

        for parser, reached_max in self._fetch_all_listing_pages(max_pages):
            page_rule_ids = parser.extract_rule_ids()
            global_on_page = parser.filter_global_rules(page_rule_ids)
            all_global_rule_ids.extend(global_on_page)

            # Check if we've collected enough rules
            unique_ids = _deduplicate_rule_ids(all_global_rule_ids)
            if max_rules is not None and len(unique_ids) >= max_rules:
                logger.info(
                    f"Collected {len(unique_ids)} global rule IDs, stopping pagination (max_rules={max_rules})"
                )
                return _sort_rule_ids(unique_ids[:max_rules])

            if reached_max:
                logger.warning(
                    f"Reached max pages limit ({max_pages}), found {len(unique_ids)} global rules. "
                    f"Some rules may be missing."
                )

        unique_ids = _deduplicate_rule_ids(all_global_rule_ids)
        return _sort_rule_ids(unique_ids)

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
                    # Skip disabled rules
                    if rule.status.lower() == "disabled":
                        logger.debug(f"Skipping rule {rule_id}: disabled")
                        continue

                    # Only include rules that add at least one reviewer
                    has_reviewers = any(
                        action.reviewers for action in rule.actions
                    )
                    if has_reviewers:
                        rules.append(rule)
                    else:
                        logger.debug(f"Skipping rule {rule_id}: no reviewers")
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
