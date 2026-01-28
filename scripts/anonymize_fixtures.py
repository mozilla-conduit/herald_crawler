#!/usr/bin/env python3
"""
Anonymize PII in test fixtures.

This script replaces real usernames, GitHub IDs, and other identifying information
with hashed values. Uses one-way hashing to ensure:
- Same input always produces the same output (deterministic)
- Cannot reverse the hash to get the original value
- Clear prefixes identify the type of anonymized value

Prefixes used:
- USER-xxxx: Phabricator usernames
- GHID-xxxx: GitHub numeric IDs
- GHUSER-xxxx: GitHub usernames

Usage:
    # Dry run (show what would change)
    python scripts/anonymize_fixtures.py --dry-run

    # Actually anonymize
    python scripts/anonymize_fixtures.py

    # Save mapping file for use with rewrite_git_history.py
    python scripts/anonymize_fixtures.py --save-mapping anonymization_mapping.json
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Dict, Set, Tuple

FIXTURES_DIR = Path(__file__).parent.parent / "tests" / "fixtures"

# Hash length for anonymized identifiers (characters after prefix)
HASH_LENGTH = 8


def hash_value(value: str, prefix: str) -> str:
    """Generate a deterministic hash-based anonymized value.

    Args:
        value: The original PII value to anonymize
        prefix: The prefix to use (e.g., "USER-", "GHID-")

    Returns:
        Anonymized value like "USER-a1b2c3d4"
    """
    # Use SHA-256 for consistent hashing, take first HASH_LENGTH hex chars
    hash_hex = hashlib.sha256(value.encode()).hexdigest()[:HASH_LENGTH]
    return f"{prefix}{hash_hex}"


class Anonymizer:
    """Handles consistent anonymization of usernames and identifiers using one-way hashing."""

    def __init__(self) -> None:
        # Track what we've anonymized (for reporting, not for reversal)
        self.username_map: Dict[str, str] = {}
        self.github_id_map: Dict[str, str] = {}
        self.github_username_map: Dict[str, str] = {}

    def get_fake_username(self, real_username: str) -> str:
        """Get a hashed username for a real username."""
        if real_username not in self.username_map:
            self.username_map[real_username] = hash_value(real_username, "USER-")
        return self.username_map[real_username]

    def get_fake_github_id(self, real_id: str) -> str:
        """Get a hashed GitHub ID for a real ID."""
        if real_id not in self.github_id_map:
            self.github_id_map[real_id] = hash_value(real_id, "GHID-")
        return self.github_id_map[real_id]

    def get_fake_github_username(self, real_username: str) -> str:
        """Get a hashed GitHub username for a real username."""
        if real_username not in self.github_username_map:
            self.github_username_map[real_username] = hash_value(real_username, "GHUSER-")
        return self.github_username_map[real_username]

    def get_mapping(self) -> Dict[str, Dict[str, str]]:
        """Get the complete anonymization mapping (for debugging only)."""
        return {
            "usernames": self.username_map,
            "github_ids": self.github_id_map,
            "github_usernames": self.github_username_map,
        }


def is_already_anonymized(value: str) -> bool:
    """Check if a value has already been anonymized (has our prefix)."""
    return value.startswith(("USER-", "GHID-", "GHUSER-"))


def extract_usernames_from_html(content: str) -> Set[str]:
    """Extract usernames from HTML content."""
    usernames = set()

    # Pattern: href="/p/username/"
    for match in re.finditer(r'href="/p/([^/"]+)/"', content):
        username = match.group(1)
        if not is_already_anonymized(username):
            usernames.add(username)

    # Pattern: href="/p/username"
    for match in re.finditer(r'href="/p/([^/"]+)"', content):
        username = match.group(1)
        if not is_already_anonymized(username):
            usernames.add(username)

    # Pattern: >username</a> where it's a user link
    for match in re.finditer(r'>([a-zA-Z0-9_-]+)</a>', content):
        username = match.group(1)
        # Filter out common non-username links and already anonymized
        if not username.startswith(("H", "PHID-", "tag-")) and len(username) > 1:
            if not is_already_anonymized(username):
                # Only if it appears in a /p/ link context
                if f'/p/{username}' in content:
                    usernames.add(username)

    # Pattern: title="username (Real Name)" - extract username from title attributes
    # Use [^"]+ to match everything until the closing quote (handles nested parens)
    for match in re.finditer(r'title="([a-zA-Z0-9_-]+) \([^"]+\)"', content):
        username = match.group(1)
        if not is_already_anonymized(username):
            usernames.add(username)

    # Pattern: title="username" (simple title)
    for match in re.finditer(r'title="([a-zA-Z0-9_-]+)"', content):
        username = match.group(1)
        # Filter out non-usernames
        if len(username) > 1 and not is_already_anonymized(username):
            # Only if it looks like a username (appears in /p/ context or title with parens)
            if f'/p/{username}' in content or f'title="{username} (' in content:
                usernames.add(username)

    # Pattern: >username (Real Name)</a> - username with real name in link text
    # Use [^<]+ to match everything until the closing tag (handles nested parens)
    for match in re.finditer(r'>([a-zA-Z0-9_-]+) \([^<]+\)</a>', content):
        username = match.group(1)
        if not is_already_anonymized(username):
            usernames.add(username)

    return usernames


def extract_usernames_from_json(content: str, filename: str) -> Tuple[Set[str], Set[str], Set[str]]:
    """Extract usernames, GitHub IDs, and GitHub usernames from JSON content."""
    usernames = set()
    github_ids = set()
    github_usernames = set()

    try:
        data = json.loads(content)

        # GraphQL response with GitHub ID and primaryUsername
        if "data" in data and data.get("data"):
            profile = data["data"].get("profile", {})
            if profile:
                # Extract GitHub ID
                identities = profile.get("identities", {})
                github_id_obj = identities.get("githubIdV3", {})
                if github_id_obj and github_id_obj.get("value"):
                    value = github_id_obj["value"]
                    if not is_already_anonymized(value):
                        github_ids.add(value)

                # Extract primaryUsername (Phabricator username)
                primary_username_obj = profile.get("primaryUsername", {})
                if primary_username_obj and primary_username_obj.get("value"):
                    value = primary_username_obj["value"]
                    if not is_already_anonymized(value):
                        usernames.add(value)

        # REST response with GitHub username
        if "username" in data and data["username"]:
            value = data["username"]
            if not is_already_anonymized(value):
                github_usernames.add(value)

        # Extract username from filename (e.g., username_graphql.json -> username)
        stem = Path(filename).stem
        for suffix in ("_graphql", "_rest", ""):
            if stem.endswith(suffix):
                username = stem[:-len(suffix)] if suffix else stem
                if username and not username.startswith("nonexistent"):
                    if not is_already_anonymized(username):
                        usernames.add(username)
                break

        # Also check search_ prefix in filename
        if stem.startswith("search_"):
            username = stem[7:]
            if username and not is_already_anonymized(username):
                usernames.add(username)

    except json.JSONDecodeError:
        pass

    return usernames, github_ids, github_usernames


def anonymize_html(content: str, anonymizer: Anonymizer) -> str:
    """Anonymize usernames in HTML content."""
    result = content

    # Get all usernames first
    usernames = extract_usernames_from_html(content)

    # Sort by length (longest first) to avoid partial replacements
    for username in sorted(usernames, key=len, reverse=True):
        fake_username = anonymizer.get_fake_username(username)

        # Replace href="/p/username/"
        result = re.sub(
            rf'href="/p/{re.escape(username)}/"',
            f'href="/p/{fake_username}/"',
            result
        )

        # Replace href="/p/username"
        result = re.sub(
            rf'href="/p/{re.escape(username)}"',
            f'href="/p/{fake_username}"',
            result
        )

        # Replace >username</a> in user links
        result = re.sub(
            rf'>({re.escape(username)})</a>',
            f'>{fake_username}</a>',
            result
        )

        # Replace title="username (Real Name)" - anonymize both username and real name
        # Use [^"]+ to handle nested parentheses
        result = re.sub(
            rf'title="{re.escape(username)} \([^"]+\)"',
            f'title="{fake_username}"',
            result
        )

        # Replace title="username" (simple)
        result = re.sub(
            rf'title="{re.escape(username)}"',
            f'title="{fake_username}"',
            result
        )

        # Replace >username (Real Name)</a> - anonymize username and real name in link text
        # Use [^<]+ to handle nested parentheses
        result = re.sub(
            rf'>{re.escape(username)} \([^<]+\)</a>',
            f'>{fake_username}</a>',
            result
        )

        # Replace "Log Out username" patterns
        result = re.sub(
            rf'Log Out {re.escape(username)}',
            f'Log Out {fake_username}',
            result
        )

    return result


def extract_usernames_from_markdown(content: str) -> Set[str]:
    """Extract usernames from Markdown content."""
    usernames = set()

    # Pattern: [username](url) - markdown links
    for match in re.finditer(r'\[([a-zA-Z0-9_-]+)\]\([^)]+\)', content):
        username = match.group(1)
        if not is_already_anonymized(username):
            usernames.add(username)

    # Pattern: @username - mentions
    for match in re.finditer(r'@([a-zA-Z0-9_-]+)', content):
        username = match.group(1)
        if not is_already_anonymized(username):
            usernames.add(username)

    # Pattern: username in backticks `username`
    for match in re.finditer(r'`([a-zA-Z0-9_-]+)`', content):
        username = match.group(1)
        if not is_already_anonymized(username):
            usernames.add(username)

    return usernames


def anonymize_markdown(content: str, anonymizer: Anonymizer) -> str:
    """Anonymize usernames in Markdown content."""
    result = content

    # Get all usernames first
    usernames = extract_usernames_from_markdown(content)

    # Sort by length (longest first) to avoid partial replacements
    for username in sorted(usernames, key=len, reverse=True):
        if username not in anonymizer.username_map:
            continue  # Only anonymize known usernames

        fake_username = anonymizer.get_fake_username(username)

        # Replace [username](url) - markdown links
        result = re.sub(
            rf'\[{re.escape(username)}\](\([^)]+\))',
            f'[{fake_username}]\\1',
            result
        )

        # Replace @username - mentions
        result = re.sub(
            rf'@{re.escape(username)}(?![a-zA-Z0-9_-])',
            f'@{fake_username}',
            result
        )

        # Replace `username` - backticks
        result = re.sub(
            rf'`{re.escape(username)}`',
            f'`{fake_username}`',
            result
        )

    return result


def anonymize_json(content: str, filename: str, anonymizer: Anonymizer) -> str:
    """Anonymize identifiers in JSON content."""
    try:
        data = json.loads(content)
        modified = False

        # GraphQL response with GitHub ID and primaryUsername
        if "data" in data and data.get("data"):
            profile = data["data"].get("profile", {})
            if profile:
                # Anonymize GitHub ID
                identities = profile.get("identities", {})
                github_id_obj = identities.get("githubIdV3", {})
                if github_id_obj and github_id_obj.get("value"):
                    real_id = github_id_obj["value"]
                    if not is_already_anonymized(real_id):
                        github_id_obj["value"] = anonymizer.get_fake_github_id(real_id)
                        modified = True

                # Anonymize primaryUsername
                primary_username_obj = profile.get("primaryUsername", {})
                if primary_username_obj and primary_username_obj.get("value"):
                    real_username = primary_username_obj["value"]
                    if not is_already_anonymized(real_username):
                        primary_username_obj["value"] = anonymizer.get_fake_username(real_username)
                        modified = True

        # REST response with GitHub username
        if "username" in data and data["username"]:
            real_username = data["username"]
            if not is_already_anonymized(real_username):
                data["username"] = anonymizer.get_fake_github_username(real_username)
                modified = True

        if modified:
            return json.dumps(data, indent=2) + "\n"

    except json.JSONDecodeError:
        pass

    return content


def rename_file_if_needed(filepath: Path, anonymizer: Anonymizer) -> Path:
    """Return new path if filename contains a username that should be anonymized."""
    stem = filepath.stem
    suffix = filepath.suffix

    # Skip if filename already contains anonymized prefix
    if is_already_anonymized(stem) or stem.startswith("search_USER-"):
        return filepath

    # Check for username patterns in filename (e.g., username_graphql.json)
    for file_suffix in ("_graphql", "_rest", ""):
        if stem.endswith(file_suffix):
            username_part = stem[:-len(file_suffix)] if file_suffix else stem
            if username_part in anonymizer.username_map:
                fake_username = anonymizer.username_map[username_part]
                new_stem = fake_username + file_suffix
                return filepath.parent / (new_stem + suffix)

    # Check for search_ prefix (e.g., search_username.html)
    if stem.startswith("search_"):
        username = stem[7:]  # Remove "search_" prefix
        if username in anonymizer.username_map:
            fake_username = anonymizer.username_map[username]
            return filepath.parent / (f"search_{fake_username}" + suffix)

    return filepath


def process_fixtures(dry_run: bool = True, verbose: bool = True, force: bool = False) -> Anonymizer:
    """Process all fixture files and anonymize PII."""
    anonymizer = Anonymizer()

    # First pass: collect all usernames from content and filenames
    if verbose:
        print("Pass 1: Collecting usernames...")

    for filepath in FIXTURES_DIR.rglob("*"):
        if not filepath.is_file():
            continue

        content = filepath.read_text()

        if filepath.suffix == ".html":
            # Extract usernames from HTML content
            usernames = extract_usernames_from_html(content)
            for username in usernames:
                anonymizer.get_fake_username(username)

            # Also check for usernames in HTML filenames (search_ prefix)
            stem = filepath.stem
            if stem.startswith("search_"):
                username = stem[7:]
                if username and not is_already_anonymized(username):
                    anonymizer.get_fake_username(username)

        elif filepath.suffix == ".json":
            usernames, github_ids, github_usernames = extract_usernames_from_json(
                content, filepath.name
            )
            for username in usernames:
                anonymizer.get_fake_username(username)
            for gid in github_ids:
                anonymizer.get_fake_github_id(gid)
            for gh_username in github_usernames:
                anonymizer.get_fake_github_username(gh_username)

        elif filepath.suffix == ".md":
            # Extract usernames from Markdown content
            usernames = extract_usernames_from_markdown(content)
            for username in usernames:
                anonymizer.get_fake_username(username)

    if verbose:
        print(f"  Found {len(anonymizer.username_map)} usernames")
        print(f"  Found {len(anonymizer.github_id_map)} GitHub IDs")
        print(f"  Found {len(anonymizer.github_username_map)} GitHub usernames")

    # Second pass: anonymize content
    if verbose:
        print("\nPass 2: Anonymizing content...")

    files_to_rename = []
    modified_count = 0

    for filepath in FIXTURES_DIR.rglob("*"):
        if not filepath.is_file():
            continue

        if filepath.name == "README.md":
            continue

        content = filepath.read_text()

        if filepath.suffix == ".html":
            new_content = anonymize_html(content, anonymizer)
        elif filepath.suffix == ".json":
            new_content = anonymize_json(content, filepath.name, anonymizer)
        elif filepath.suffix == ".md":
            new_content = anonymize_markdown(content, anonymizer)
        else:
            continue

        if content != new_content:
            modified_count += 1
            if verbose:
                print(f"  Modified: {filepath.relative_to(FIXTURES_DIR.parent.parent)}")

            if not dry_run:
                filepath.write_text(new_content)

        # Check if file needs to be renamed
        new_path = rename_file_if_needed(filepath, anonymizer)
        if new_path != filepath:
            files_to_rename.append((filepath, new_path))

    if verbose:
        if modified_count == 0:
            print("  No files needed content modification")

    # Third pass: rename files (after content is modified)
    if verbose:
        print("\nPass 3: Renaming files...")
        if not files_to_rename:
            print("  No files need renaming")

    for old_path, new_path in files_to_rename:
        if verbose:
            print(f"  {old_path.name} -> {new_path.name}")
        if not dry_run:
            if new_path.exists():
                if force:
                    if verbose:
                        print(f"    (overwriting existing file)")
                    new_path.unlink()
                else:
                    print(f"  WARNING: {new_path.name} already exists, skipping (use --force to overwrite)")
                    continue
            old_path.rename(new_path)

    if verbose:
        print(f"\nSummary:")
        print(f"  Files modified: {modified_count}")
        print(f"  Files renamed: {len(files_to_rename)}")

    return anonymizer


def main():
    parser = argparse.ArgumentParser(
        description="Anonymize PII in test fixtures using one-way hashing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without modifying files",
    )
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Overwrite existing files when renaming",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress verbose output",
    )
    parser.add_argument(
        "--save-mapping",
        metavar="FILE",
        help="Save anonymization mapping to JSON file (for use with rewrite_git_history.py)",
    )

    args = parser.parse_args()

    if args.dry_run:
        print("=== DRY RUN - No files will be modified ===\n")

    anonymizer = process_fixtures(dry_run=args.dry_run, verbose=not args.quiet, force=args.force)

    # Save mapping file if requested
    if args.save_mapping:
        mapping = anonymizer.get_mapping()
        with open(args.save_mapping, "w") as f:
            json.dump(mapping, f, indent=2, sort_keys=True)
        if not args.quiet:
            print(f"\nMapping saved to: {args.save_mapping}")

    if args.dry_run:
        print("\n=== DRY RUN COMPLETE - Run without --dry-run to apply changes ===")

    return 0


if __name__ == "__main__":
    sys.exit(main())
