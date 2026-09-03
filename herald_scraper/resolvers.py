"""Resolvers for collecting group membership and other PHID resolutions."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Dict, List, Optional, Set, Tuple

from herald_scraper.conduit_client import ConduitClient
from herald_scraper.exceptions import RateLimitError
from herald_scraper.models import GitHubUser, Group, Rule, UnresolvedUser
from herald_scraper.people_client import PeopleDirectoryClient
from herald_scraper.rate_limit import MAX_RATE_LIMIT_RETRIES, rate_limit_backoff_seconds
from herald_scraper.stmo_client import StmoClient

logger = logging.getLogger(__name__)


def extract_group_slugs_from_rules(rules: List[Rule]) -> Set[str]:
    """
    Extract unique group slugs from rule reviewer actions.

    Groups are identified by the is_group field set by the parser (based on
    href pattern: /tag/ = group, /p/ = user). Falls back to '@' heuristic
    if is_group is None.

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
                    # Use is_group field if available
                    if reviewer.is_group is True:
                        group_slugs.add(reviewer.target)
                    elif reviewer.is_group is None:
                        # Fallback: assume it's a group if no '@' (legacy behavior)
                        if "@" not in reviewer.target:
                            group_slugs.add(reviewer.target)
                    # is_group == False means it's a user, skip

    logger.debug(f"Extracted {len(group_slugs)} unique group slugs from {len(rules)} rules")
    return group_slugs


REVIEW_GROUPS_TABLE = "phabricator_metrics.review_groups"
STAFF_MEMBERS_TABLE = "mozcloud.person_api_staff_members"

# Table and column names are interpolated into SQL, so restrict them to
# dotted identifiers (BigQuery project IDs may contain hyphens).
_SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][\w-]*(\.[A-Za-z_][\w-]*)*$")

_MEMBER_SEPARATOR_RE = re.compile(r"[,;\s]+")
_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class StmoGroupCollector:
    """Collects reviewer group membership from STMO's review groups table.

    ``phabricator_metrics.review_groups`` holds one row per group, with the
    members already resolved to usernames and emails, so a single query
    fetches every group at once -- replacing the per-group Phabricator
    lookups this collector supersedes.

    Example:
        from herald_scraper.stmo_client import StmoClient

        collector = StmoGroupCollector(StmoClient(api_key="...", data_source_id=63))
        groups = collector.collect_all_groups(rules)
    """

    def __init__(
        self,
        client: StmoClient,
        table: str = REVIEW_GROUPS_TABLE,
    ) -> None:
        """
        Initialize the STMO group collector.

        Args:
            client: StmoClient used to run the query
            table: Fully-qualified review groups table

        Raises:
            ValueError: If table is not a plain SQL identifier
        """
        if not _SQL_IDENTIFIER_RE.match(table):
            raise ValueError(f"Invalid STMO table name: {table!r}")

        self.client = client
        self.table = table
        self._groups_by_name: Optional[Dict[str, Group]] = None
        self._groups_by_slug: Dict[str, Group] = {}
        self._emails_by_username: Dict[str, str] = {}

    def extract_group_slugs_from_rules(self, rules: List[Rule]) -> Set[str]:
        """
        Extract unique group slugs from rule reviewer actions.

        Delegates to the module-level function for implementation.

        Args:
            rules: List of Rule objects to extract groups from

        Returns:
            Set of unique group slugs
        """
        return extract_group_slugs_from_rules(rules)

    def collect_all_groups(
        self, rules: List[Rule], max_groups: Optional[int] = None
    ) -> Dict[str, Group]:
        """
        Collect the groups referenced by the given rules.

        Args:
            rules: List of Rule objects to collect groups from
            max_groups: Optional limit on number of groups to collect

        Returns:
            Dictionary mapping group slugs (as referenced in rules) to Groups
        """
        group_slugs = self.extract_group_slugs_from_rules(rules)
        if not group_slugs:
            logger.info("No reviewer groups referenced in rules, skipping STMO query")
            return {}

        available = self.fetch_all_groups()
        groups: Dict[str, Group] = {}

        for slug in sorted(group_slugs):
            if max_groups is not None and len(groups) >= max_groups:
                logger.info(f"Collected {len(groups)} groups, stopping (max_groups={max_groups})")
                break

            group = self._lookup_group(slug)
            if group is None:
                logger.warning(f"Group not present in {self.table}: {slug}")
                continue

            # Key the output by the slug the rules use, not STMO's group_name,
            # so downstream lookups by reviewer target keep working.
            groups[slug] = group.model_copy(update={"id": slug})

        logger.info(
            f"Collected {len(groups)} of {len(group_slugs)} referenced groups "
            f"from {len(available)} STMO review groups"
        )
        return groups

    def fetch_all_groups(self) -> Dict[str, Group]:
        """
        Fetch every review group, one row each, keyed by STMO group name.

        The result is cached, so repeated calls run a single query.

        Raises:
            StmoError: If the query fails
        """
        if self._groups_by_name is None:
            rows = self.client.run_query(self.build_query())
            self._groups_by_name = self._groups_from_rows(rows)
            self._groups_by_slug = self._index_by_slug(self._groups_by_name)
            self._emails_by_username = self._emails_from_rows(rows)
        return self._groups_by_name

    def fetch_user_emails(self) -> Dict[str, str]:
        """
        Return ``phab_username -> email`` for every member of every group.

        The addresses come from the same query as the groups themselves, so
        this costs nothing beyond ``fetch_all_groups``. They are the join key
        into STMO's email-keyed GitHub login map (see StmoGitHubMapper).

        Raises:
            StmoError: If the query fails
        """
        self.fetch_all_groups()
        return self._emails_by_username

    def build_query(self) -> str:
        """Build the SQL selecting one row per group.

        The table keeps a row per group per collection run (tens of thousands
        of rows for ~150 groups), so rows are numbered within each
        ``group_name`` and only the first is kept. With no ``ORDER BY`` in the
        window that is the row inserted last, i.e. the current membership --
        a property of the append-only table being scanned in insertion order,
        not something the query itself pins down.
        """
        return (
            "SELECT group_name, group_usernames, group_emails\n"
            "FROM (\n"
            "  SELECT group_name, group_usernames, group_emails,\n"
            "         ROW_NUMBER() OVER (PARTITION BY group_name) AS row_num\n"
            f"  FROM {self.table}\n"
            ")\n"
            "WHERE row_num = 1"
        )

    def clear_cache(self) -> None:
        """Clear the cached query result."""
        self._groups_by_name = None
        self._groups_by_slug = {}
        self._emails_by_username = {}
        logger.debug("STMO group collector cache cleared")

    def _lookup_group(self, slug: str) -> Optional[Group]:
        """Find a group by the slug a rule referenced.

        Rules reference Phabricator project slugs, while ``group_name`` may
        hold either the slug or the human-readable name, so fall back to
        comparing slugified names.
        """
        groups = self.fetch_all_groups()
        if slug in groups:
            return groups[slug]
        return self._groups_by_slug.get(_slugify(slug))

    def _groups_from_rows(self, rows: List[Dict[str, object]]) -> Dict[str, Group]:
        """Turn query rows into Groups keyed by their STMO ``group_name``."""
        groups: Dict[str, Group] = {}

        for row in rows:
            raw_name = row.get("group_name")
            if not raw_name:
                logger.warning(f"Skipping {self.table} row with no group_name: {row}")
                continue

            name = str(raw_name)
            members = _coerce_string_list(row.get("group_usernames"))
            if not members:
                logger.warning(f"Group {name} has no usernames in {self.table}")

            groups[name] = Group(id=name, display_name=name, members=members)

        return groups

    def _emails_from_rows(self, rows: List[Dict[str, object]]) -> Dict[str, str]:
        """Pair up each group's usernames with its emails.

        The table carries the two as parallel arrays, so they only line up
        positionally; a row where they disagree in length carries no usable
        pairing and is dropped whole rather than mis-attributed.
        """
        emails: Dict[str, str] = {}

        for row in rows:
            usernames = _coerce_string_list(row.get("group_usernames"))
            addresses = _coerce_string_list(row.get("group_emails"))
            if len(usernames) != len(addresses):
                logger.warning(
                    f"Group {row.get('group_name')} in {self.table} lists "
                    f"{len(usernames)} usernames but {len(addresses)} emails; "
                    f"ignoring its email mapping"
                )
                continue

            for username, address in zip(usernames, addresses):
                if not _looks_like_email(address):
                    continue
                email = address.strip().lower()
                known = emails.setdefault(username, email)
                if known != email:
                    logger.warning(
                        f"Conflicting emails for {username} in {self.table}: "
                        f"keeping {known}, ignoring {email}"
                    )

        logger.debug(f"Collected emails for {len(emails)} users from {self.table}")
        return emails

    def _index_by_slug(self, groups: Dict[str, Group]) -> Dict[str, Group]:
        """Index groups by slugified name, dropping ambiguous collisions."""
        index: Dict[str, Group] = {}
        collisions: Set[str] = set()

        for name, group in groups.items():
            key = _slugify(name)
            if not key:
                continue
            if key in index and index[key] is not group:
                collisions.add(key)
                continue
            index[key] = group

        for key in collisions:
            logger.warning(f"Ambiguous slugified group name in {self.table}: {key}")
            del index[key]

        return index


class StmoGitHubMapper:
    """Maps Mozilla staff to their GitHub account from an STMO table.

    ``mozcloud.person_api_staff_members`` has one row per staff member,
    pairing their LDAP username and email with their GitHub username and
    numeric ID. One query pulls the whole directory, so it covers most
    people without the per-user People Directory lookups it fronts.

    Rows are indexed three ways, so a Phabricator username usually matches
    without any fuzzy search: by LDAP username, by email address, and by
    email local part.

    Example:
        mapper = StmoGitHubMapper(StmoClient(api_key="..."))
        user = mapper.user_for_username("someone")
    """

    def __init__(
        self,
        client: StmoClient,
        table: str = STAFF_MEMBERS_TABLE,
    ) -> None:
        """
        Initialize the STMO GitHub mapper.

        Args:
            client: StmoClient used to run the query
            table: Fully-qualified staff members table

        Raises:
            ValueError: If table is not a plain SQL identifier
        """
        if not _SQL_IDENTIFIER_RE.match(table):
            raise ValueError(f"Invalid STMO table name: {table!r}")

        self.client = client
        self.table = table
        self._users_by_email: Optional[Dict[str, GitHubUser]] = None
        self._users_by_username: Dict[str, GitHubUser] = {}
        self._users_by_local_part: Dict[str, GitHubUser] = {}

    def user_for_username(self, username: str) -> Optional[GitHubUser]:
        """
        Look up a GitHub account by LDAP username (case-insensitive).

        The Phabricator username *is* the LDAP username for most people, so
        this is the cheapest and most direct of the three lookups.

        Args:
            username: LDAP username, e.g. a Phabricator username

        Returns:
            GitHubUser, or None if the username isn't in the table

        Raises:
            StmoError: If the query fails
        """
        if not username:
            return None
        self.fetch_all_users()
        return self._users_by_username.get(username.strip().lower())

    def user_for_email(self, email: str) -> Optional[GitHubUser]:
        """
        Look up a GitHub account by exact email address (case-insensitive).

        Args:
            email: Email address to look up

        Returns:
            GitHubUser, or None if the address isn't in the table

        Raises:
            StmoError: If the query fails
        """
        if not email:
            return None
        return self.fetch_all_users().get(email.strip().lower())

    def user_for_local_part(self, local_part: str) -> Optional[GitHubUser]:
        """
        Look up a GitHub account by the local part of its email address.

        A last resort for people whose email local part matches neither
        their LDAP username nor an address we hold. Local parts shared by
        several addresses with different accounts are ambiguous and never
        match.

        Args:
            local_part: Email local part, e.g. a Phabricator username

        Returns:
            GitHubUser, or None if nothing matched unambiguously

        Raises:
            StmoError: If the query fails
        """
        if not local_part:
            return None
        self.fetch_all_users()
        return self._users_by_local_part.get(local_part.strip().lower())

    def fetch_all_users(self) -> Dict[str, GitHubUser]:
        """
        Fetch the whole ``email -> GitHubUser`` map, keyed by lowercase email.

        Building it also populates the username and local-part indexes. The
        result is cached, so repeated calls run a single query.

        Raises:
            StmoError: If the query fails
        """
        if self._users_by_email is None:
            rows = self.client.run_query(self.build_query())
            self._index_rows(rows)
        assert self._users_by_email is not None  # set by _index_rows
        return self._users_by_email

    def build_query(self) -> str:
        """Build the SQL selecting every staff member with a GitHub account."""
        return (
            "SELECT DISTINCT email, username, github_username, github_id\n"
            f"FROM {self.table}\n"
            "WHERE github_username IS NOT NULL OR github_id IS NOT NULL"
        )

    def clear_cache(self) -> None:
        """Clear the cached query result."""
        self._users_by_email = None
        self._users_by_username = {}
        self._users_by_local_part = {}
        logger.debug("STMO GitHub mapper cache cleared")

    def _index_rows(self, rows: List[Dict[str, object]]) -> None:
        """Index query rows by email, LDAP username, and email local part.

        Rows carrying neither a GitHub username nor a GitHub ID describe
        someone we cannot resolve, so they are dropped; a row missing an
        email or a username is still indexed under whichever it has.
        """
        by_email: Dict[str, GitHubUser] = {}
        by_username: Dict[str, GitHubUser] = {}
        skipped = 0

        for row in rows:
            github_user = _github_user_from_row(row)
            if github_user is None:
                skipped += 1
                continue

            email = _clean_cell(row.get("email"))
            if email and _looks_like_email(email):
                self._record(by_email, email.lower(), github_user, "email")

            username = _clean_cell(row.get("username"))
            if username:
                self._record(by_username, username.lower(), github_user, "username")

        self._users_by_email = by_email
        self._users_by_username = by_username
        self._users_by_local_part = self._index_by_local_part(by_email)

        logger.info(
            f"Loaded {len(by_email)} email and {len(by_username)} username -> "
            f"GitHub account pairs from {self.table} ({skipped} rows without a "
            f"GitHub account skipped)"
        )

    def _record(
        self,
        index: Dict[str, GitHubUser],
        key: str,
        user: GitHubUser,
        kind: str,
    ) -> None:
        """Add a key to an index, keeping the first of any conflicting rows."""
        known = index.setdefault(key, user)
        if known != user:
            logger.warning(
                f"Conflicting GitHub accounts for {kind} {key} in {self.table}: "
                f"keeping {known.username} ({known.user_id}), ignoring "
                f"{user.username} ({user.user_id})"
            )

    def _index_by_local_part(
        self, users: Dict[str, GitHubUser]
    ) -> Dict[str, GitHubUser]:
        """Index users by email local part, dropping ambiguous collisions."""
        index: Dict[str, GitHubUser] = {}
        collisions: Set[str] = set()

        for email, user in users.items():
            key = email.split("@", 1)[0]
            if not key:
                continue
            if index.get(key, user) != user:
                collisions.add(key)
                continue
            index[key] = user

        for key in collisions:
            logger.warning(f"Ambiguous email local part in {self.table}: {key}")
            del index[key]

        return index


def _clean_cell(value: object) -> str:
    """Normalize an STMO cell to a stripped string ('' when absent)."""
    if value is None:
        return ""
    return str(value).strip()


def _github_user_from_row(row: Dict[str, object]) -> Optional[GitHubUser]:
    """Build a GitHubUser from a staff row, or None when it has no account.

    ``github_id`` arrives as a number or as its string form depending on the
    data source; anything that isn't an integer is dropped with a warning
    rather than failing the whole directory load.
    """
    username = _clean_cell(row.get("github_username")) or None

    user_id: Optional[int] = None
    raw_id = _clean_cell(row.get("github_id"))
    if raw_id:
        try:
            user_id = int(raw_id)
        except ValueError:
            logger.warning(f"Ignoring non-numeric github_id {raw_id!r}")

    if username is None and user_id is None:
        return None
    return GitHubUser(username=username, user_id=user_id)


def _slugify(name: str) -> str:
    """Normalize a group name for slug comparison ('OMC Reviewers' -> 'omc-reviewers')."""
    return _NON_SLUG_RE.sub("-", name.lower()).strip("-")


def _looks_like_email(value: str) -> bool:
    """Tell a member's email address from a nested group's name."""
    return bool(_EMAIL_RE.match(value.strip()))


def _coerce_string_list(value: object) -> List[str]:
    """Coerce an STMO cell into a list of non-empty strings.

    Repeated BigQuery columns arrive as lists, but the same data is also
    seen as a JSON array or a delimited string depending on how the view
    is materialized, so accept all three.
    """
    if value is None:
        return []

    if isinstance(value, (list, tuple)):
        items: List[str] = []
        for item in value:
            items.extend(_coerce_string_list(item))
        return items

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                return _coerce_string_list(json.loads(text))
            except json.JSONDecodeError:
                logger.debug(f"Value looks like a JSON array but did not parse: {text!r}")
        return [part for part in _MEMBER_SEPARATOR_RE.split(text) if part]

    return [str(value)]


_IRC_NICK_SUFFIX_RE = re.compile(r"\s*\[:[^\]]*\]\s*")


def _clean_phab_real_name(raw: str) -> Optional[str]:
    """Strip Phab's ``[:irc-nick]`` annotation from a realName.

    Phab convention is to embed an IRC handle in brackets within the
    realName field (e.g. ``"Aaa Bbb [:nick]"``). The bracket portion is
    metadata, not part of the name — it breaks PMO's search and our
    exact ``first + " " + last`` fold-match.
    """
    cleaned = _IRC_NICK_SUFFIX_RE.sub(" ", raw)
    cleaned = " ".join(cleaned.split()).strip()
    return cleaned or None


class UsernameResolver:
    """Resolves Phabricator usernames to GitHub usernames and user IDs."""

    def __init__(
        self,
        client: Optional[PeopleDirectoryClient] = None,
        conduit_client: Optional[ConduitClient] = None,
        manual_mapping: Optional[Dict[str, GitHubUser]] = None,
        github_mapper: Optional[StmoGitHubMapper] = None,
        user_emails: Optional[Dict[str, str]] = None,
    ) -> None:
        """
        Initialize the UsernameResolver.

        Args:
            client: Optional PeopleDirectoryClient for resolving usernames.
                Without one (no PMO cookie), only ``manual_mapping`` and
                ``github_mapper`` can resolve a user; everyone else is
                reported as unresolved rather than silently omitted.
            conduit_client: Optional ConduitClient. When provided, each
                resolution cross-checks the PMO profile's
                ``bugzillaMozillaOrgId`` against Phabricator's
                ``bugzilla.account.search`` result for the same user.
            manual_mapping: Optional operator-supplied Phab username ->
                GitHubUser override. Entries bypass all API calls and win
                over automatic resolution. Keys are matched case-sensitively
                against the Phab username (after stripping ``@domain`` like
                other paths).
            github_mapper: Optional StmoGitHubMapper. Its bulk staff
                directory is consulted before the People Directory, since it
                answers for most users off a single query, with both the
                GitHub username and its numeric ID.
            user_emails: Optional ``phab_username -> email`` map used as a
                secondary join key into ``github_mapper``, typically
                ``StmoGroupCollector.fetch_user_emails()``.
        """
        self.client = client
        self.conduit_client = conduit_client
        self.manual_mapping = manual_mapping or {}
        self.github_mapper = github_mapper
        self.user_emails = user_emails or {}
        self._cache: Dict[str, GitHubUser] = {}
        self._unresolved: Dict[str, str] = {}  # username -> reason
        # username -> (bmo_id, real_name); either value may be None
        self._phab_info_cache: Dict[str, Tuple[Optional[str], Optional[str]]] = {}

    def extract_usernames_from_rules(
        self, rules: List[Rule], group_slugs: Set[str]
    ) -> Dict[str, List[str]]:
        """
        Extract unique usernames from rules, excluding group names.

        Users are identified by:
        - is_group == False (set by parser based on /p/ href)
        - Contains '@' (email format like user@domain)

        Args:
            rules: List of Rule objects to extract usernames from
            group_slugs: Set of known group slugs to exclude

        Returns:
            Dictionary mapping usernames to list of rule IDs that reference them
        """
        username_refs: Dict[str, List[str]] = {}

        for rule in rules:
            # Rule author (always treat as user if present)
            author = rule.author
            if author:
                if author not in username_refs:
                    username_refs[author] = []
                username_refs[author].append(rule.id)

            # Reviewers in actions
            for action in rule.actions:
                if action.reviewers:
                    for reviewer in action.reviewers:
                        target = reviewer.target

                        # Skip if explicitly marked as a group
                        if reviewer.is_group is True:
                            continue

                        # Skip if in known group_slugs (from fallback logic)
                        if target in group_slugs:
                            continue

                        # Include if explicitly marked as a user, or has '@'
                        if reviewer.is_group is False or "@" in target:
                            if target not in username_refs:
                                username_refs[target] = []
                            username_refs[target].append(rule.id)

        logger.debug(f"Extracted {len(username_refs)} unique usernames from {len(rules)} rules")
        return username_refs

    def extract_usernames_from_groups(self, groups: Dict[str, Group]) -> Dict[str, List[str]]:
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

        logger.debug(f"Extracted {len(username_refs)} unique usernames from {len(groups)} groups")
        return username_refs

    def _fetch_phab_info(
        self, username: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """Fetch ``(bmo_id, real_name)`` for a Phabricator user.

        Returns ``(None, None)`` if no Conduit client is configured, the
        user doesn't exist in Phabricator, or the lookups fail. Either
        component may independently be ``None`` (no linked Bugzilla
        account, no real name set).
        """
        if self.conduit_client is None:
            return (None, None)
        if username in self._phab_info_cache:
            return self._phab_info_cache[username]

        bmo_id: Optional[str] = None
        real_name: Optional[str] = None
        try:
            users = self.conduit_client.user_search(usernames=[username])
            if users:
                first = users[0]
                phid = first.get("phid")
                raw_name = first.get("fields", {}).get("realName")
                if raw_name:
                    real_name = _clean_phab_real_name(str(raw_name))
                if phid:
                    accounts = self.conduit_client.bugzilla_account_search(phids=[phid])
                    if accounts:
                        raw_id = accounts[0].get("id")
                        bmo_id = str(raw_id) if raw_id is not None else None
        except Exception as e:
            logger.warning(f"Failed to fetch Phab info for {username}: {e}")

        self._phab_info_cache[username] = (bmo_id, real_name)
        return (bmo_id, real_name)

    def resolve_username(self, username: str) -> Optional[GitHubUser]:
        """
        Resolve a single Phabricator username to GitHub user info.

        Uses caching to avoid duplicate lookups.

        Lookups run at full speed; when PMO (or GitHub, which it proxies)
        reports a rate limit, the lookup waits as long as the response asks
        for and retries, up to ``MAX_RATE_LIMIT_RETRIES`` times. Only after
        that does the user get recorded as unresolved.

        Args:
            username: Phabricator username (may include @domain)

        Returns:
            GitHubUser with username and user_id, or None if unresolved
        """
        # Extract just the username part if it's an email
        lookup_name = username.split("@")[0] if "@" in username else username

        # Operator-supplied overrides take precedence over everything,
        # including the auto-resolution cache — they're the escape hatch
        # for users the automatic path can't resolve correctly.
        if lookup_name in self.manual_mapping:
            override = self.manual_mapping[lookup_name]
            self._cache[lookup_name] = override
            logger.info(
                f"Manual mapping: {lookup_name} -> {override.username} "
                f"(ID: {override.user_id})"
            )
            return override

        # Check cache first
        if lookup_name in self._cache:
            logger.debug(f"Cache hit for username: {lookup_name}")
            return self._cache[lookup_name]

        # Check if already marked as unresolved
        if lookup_name in self._unresolved:
            logger.debug(f"Already unresolved: {lookup_name}")
            return None

        # STMO's directory answers for most people off a single query, so it
        # runs ahead of the per-user PMO lookups, and carries both the GitHub
        # username and its numeric ID.
        stmo_user = self._github_user_from_stmo(username, lookup_name)
        if stmo_user:
            self._cache[lookup_name] = stmo_user
            logger.debug(
                f"STMO GitHub map: {lookup_name} -> {stmo_user.username} "
                f"(ID: {stmo_user.user_id})"
            )
            return stmo_user

        # No People Directory access: record the user as unresolved instead of
        # dropping them, so the output still lists who needs a manual mapping.
        if self.client is None:
            self._unresolved[lookup_name] = "no_people_directory_cookie"
            logger.debug(f"No PMO cookie, cannot resolve {lookup_name}")
            return None

        # Conduit, not PMO, so it stays outside the rate-limit retry loop.
        expected_bmo_id, expected_real_name = self._fetch_phab_info(lookup_name)

        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            try:
                resolution = self.client.resolve_github(
                    lookup_name,
                    expected_bmo_id=expected_bmo_id,
                    expected_real_name=expected_real_name,
                )
            except RateLimitError as e:
                if attempt >= MAX_RATE_LIMIT_RETRIES:
                    self._unresolved[lookup_name] = "rate_limited"
                    logger.error(
                        f"Giving up on {lookup_name} after {attempt + 1} rate-limited "
                        f"attempts: {e}"
                    )
                    return None
                wait = rate_limit_backoff_seconds(e, attempt)
                logger.warning(
                    f"Rate limited resolving {lookup_name} "
                    f"(attempt {attempt + 1}/{MAX_RATE_LIMIT_RETRIES + 1}); "
                    f"waiting {wait:.0f}s before retrying: {e}"
                )
                time.sleep(wait)
                continue
            except Exception as e:
                reason = f"error: {str(e)}"
                self._unresolved[lookup_name] = reason
                logger.warning(f"Error resolving {lookup_name}: {e}")
                return None

            if resolution.username or resolution.user_id:
                github_user = GitHubUser(username=resolution.username, user_id=resolution.user_id)
                self._cache[lookup_name] = github_user
                logger.debug(
                    f"Resolved {lookup_name} -> {resolution.username} (ID: {resolution.user_id})"
                )
                return github_user

            self._unresolved[lookup_name] = resolution.reason or "unresolved"
            logger.debug(f"Could not resolve {lookup_name}: {resolution.reason or 'unresolved'}")
            return None

        return None  # pragma: no cover - loop always returns or continues

    def _github_user_from_stmo(
        self, username: str, lookup_name: str
    ) -> Optional[GitHubUser]:
        """Look a user up in STMO's staff directory.

        The Phab username is tried against the directory's LDAP username
        first, which matches most people outright. Failing that, the user is
        translated to an email -- via the group membership emails, or via the
        reviewer target itself when a rule already names an address -- and
        finally matched on the email local part.

        A failed query disables the mapper rather than being retried for
        every remaining user; resolution carries on through PMO.
        """
        if self.github_mapper is None:
            return None

        email = self.user_emails.get(lookup_name)
        if not email and _looks_like_email(username):
            email = username

        try:
            found = self.github_mapper.user_for_username(lookup_name)
            if not found and email:
                found = self.github_mapper.user_for_email(email)
            return found or self.github_mapper.user_for_local_part(lookup_name)
        except Exception as e:
            logger.warning(f"STMO GitHub map unavailable, falling back to PMO: {e}")
            self.github_mapper = None
            return None

    def resolve_all(
        self,
        rules: List[Rule],
        groups: Dict[str, Group],
        max_users: Optional[int] = None,
    ) -> Tuple[Dict[str, GitHubUser], List[UnresolvedUser], bool]:
        """
        Resolve all usernames found in rules and groups.

        Users are looked up back to back without artificial pacing;
        ``resolve_username`` backs off only when a response reports a rate
        limit.

        Args:
            rules: List of Rule objects
            groups: Dictionary of group slug to Group objects
            max_users: Optional limit on number of users to resolve

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

        logger.info(f"Resolved {len(resolved_users)} users, {len(unresolved_list)} unresolved")
        return resolved_users, unresolved_list, hit_max_users

    def clear_cache(self) -> None:
        """Clear the internal caches."""
        self._cache.clear()
        self._unresolved.clear()
        self._phab_info_cache.clear()
        logger.debug("Username resolver cache cleared")


