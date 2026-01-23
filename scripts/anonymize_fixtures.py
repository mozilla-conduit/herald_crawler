#!/usr/bin/env python3
"""
Anonymize PII in test fixtures.

This script replaces real usernames, GitHub IDs, and other identifying information
with fake values while maintaining consistency (same real user -> same fake user).

Usage:
    # Dry run (show what would change)
    python scripts/anonymize_fixtures.py --dry-run

    # Actually anonymize
    python scripts/anonymize_fixtures.py

    # Save mapping to file (for reference, do NOT commit)
    python scripts/anonymize_fixtures.py --save-mapping anonymization_mapping.json
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, Set, Tuple

FIXTURES_DIR = Path(__file__).parent.parent / "tests" / "fixtures"


class Anonymizer:
    """Handles consistent anonymization of usernames and identifiers."""

    def __init__(self) -> None:
        self.username_map: Dict[str, str] = {}
        self.github_id_map: Dict[str, str] = {}
        self.github_username_map: Dict[str, str] = {}
        self._username_counter = 0
        self._github_id_counter = 100000

    def get_fake_username(self, real_username: str) -> str:
        """Get or create a fake username for a real username."""
        if real_username not in self.username_map:
            self._username_counter += 1
            self.username_map[real_username] = f"user{self._username_counter}"
        return self.username_map[real_username]

    def get_fake_github_id(self, real_id: str) -> str:
        """Get or create a fake GitHub ID for a real ID."""
        if real_id not in self.github_id_map:
            self._github_id_counter += 1
            self.github_id_map[real_id] = str(self._github_id_counter)
        return self.github_id_map[real_id]

    def get_fake_github_username(self, real_username: str) -> str:
        """Get or create a fake GitHub username for a real username."""
        if real_username not in self.github_username_map:
            # Try to use same number as phabricator username if it exists
            if real_username in self.username_map:
                num = self.username_map[real_username].replace("user", "")
                self.github_username_map[real_username] = f"ghuser{num}"
            else:
                self._username_counter += 1
                self.github_username_map[real_username] = f"ghuser{self._username_counter}"
        return self.github_username_map[real_username]

    def get_mapping(self) -> Dict[str, Dict[str, str]]:
        """Get the complete anonymization mapping."""
        return {
            "usernames": self.username_map,
            "github_ids": self.github_id_map,
            "github_usernames": self.github_username_map,
        }


def extract_usernames_from_html(content: str) -> Set[str]:
    """Extract usernames from HTML content."""
    usernames = set()

    # Pattern: href="/p/username/"
    for match in re.finditer(r'href="/p/([^/"]+)/"', content):
        usernames.add(match.group(1))

    # Pattern: href="/p/username"
    for match in re.finditer(r'href="/p/([^/"]+)"', content):
        usernames.add(match.group(1))

    # Pattern: >username</a> where it's a user link
    for match in re.finditer(r'>([a-zA-Z0-9_-]+)</a>', content):
        username = match.group(1)
        # Filter out common non-username links
        if not username.startswith(("H", "PHID-", "tag-")) and len(username) > 1:
            # Only if it appears in a /p/ link context
            if f'/p/{username}' in content:
                usernames.add(username)

    return usernames


def extract_usernames_from_json(content: str, filename: str) -> Tuple[Set[str], Set[str], Set[str]]:
    """Extract usernames, GitHub IDs, and GitHub usernames from JSON content."""
    usernames = set()
    github_ids = set()
    github_usernames = set()

    try:
        data = json.loads(content)

        # GraphQL response with GitHub ID
        if "data" in data and data.get("data"):
            profile = data["data"].get("profile", {})
            if profile:
                identities = profile.get("identities", {})
                github_id_obj = identities.get("githubIdV3", {})
                if github_id_obj and github_id_obj.get("value"):
                    github_ids.add(github_id_obj["value"])

        # REST response with GitHub username
        if "username" in data and data["username"]:
            github_usernames.add(data["username"])

        # Extract username from filename (e.g., mstange_graphql.json -> mstange)
        stem = Path(filename).stem
        for suffix in ("_graphql", "_rest", ""):
            if stem.endswith(suffix):
                username = stem[:-len(suffix)] if suffix else stem
                if username and not username.startswith("nonexistent"):
                    usernames.add(username)
                break

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

        # Replace username in title attributes
        result = re.sub(
            rf'title="{re.escape(username)}"',
            f'title="{fake_username}"',
            result
        )

    return result


def anonymize_json(content: str, filename: str, anonymizer: Anonymizer) -> str:
    """Anonymize identifiers in JSON content."""
    try:
        data = json.loads(content)
        modified = False

        # GraphQL response with GitHub ID
        if "data" in data and data.get("data"):
            profile = data["data"].get("profile", {})
            if profile:
                identities = profile.get("identities", {})
                github_id_obj = identities.get("githubIdV3", {})
                if github_id_obj and github_id_obj.get("value"):
                    real_id = github_id_obj["value"]
                    github_id_obj["value"] = anonymizer.get_fake_github_id(real_id)
                    modified = True

        # REST response with GitHub username
        if "username" in data and data["username"]:
            real_username = data["username"]
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

    # Check for username patterns in filename
    for real_suffix in ("_graphql", "_rest", ""):
        if stem.endswith(real_suffix):
            username_part = stem[:-len(real_suffix)] if real_suffix else stem
            if username_part in anonymizer.username_map:
                fake_username = anonymizer.username_map[username_part]
                new_stem = fake_username + real_suffix
                return filepath.parent / (new_stem + suffix)
            elif username_part in anonymizer.github_username_map:
                # For people fixtures, use the mapped username
                for real_name, fake_name in anonymizer.username_map.items():
                    if stem.startswith(real_name):
                        new_stem = stem.replace(real_name, fake_name)
                        return filepath.parent / (new_stem + suffix)

    # Check for search_ prefix
    if stem.startswith("search_"):
        username = stem[7:]  # Remove "search_" prefix
        if username in anonymizer.username_map:
            fake_username = anonymizer.username_map[username]
            return filepath.parent / (f"search_{fake_username}" + suffix)

    return filepath


def process_fixtures(dry_run: bool = True, verbose: bool = True) -> Anonymizer:
    """Process all fixture files and anonymize PII."""
    anonymizer = Anonymizer()

    # First pass: collect all usernames
    if verbose:
        print("Pass 1: Collecting usernames...")

    for filepath in FIXTURES_DIR.rglob("*"):
        if not filepath.is_file():
            continue

        content = filepath.read_text()

        if filepath.suffix == ".html":
            usernames = extract_usernames_from_html(content)
            for username in usernames:
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

    if verbose:
        print(f"  Found {len(anonymizer.username_map)} usernames")
        print(f"  Found {len(anonymizer.github_id_map)} GitHub IDs")
        print(f"  Found {len(anonymizer.github_username_map)} GitHub usernames")

    # Second pass: anonymize content
    if verbose:
        print("\nPass 2: Anonymizing content...")

    files_to_rename = []

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
        else:
            continue

        if content != new_content:
            if verbose:
                print(f"  Modified: {filepath.relative_to(FIXTURES_DIR.parent.parent)}")

            if not dry_run:
                filepath.write_text(new_content)

        # Check if file needs to be renamed
        new_path = rename_file_if_needed(filepath, anonymizer)
        if new_path != filepath:
            files_to_rename.append((filepath, new_path))

    # Rename files (after content is modified)
    if verbose and files_to_rename:
        print("\nFiles to rename:")

    for old_path, new_path in files_to_rename:
        if verbose:
            print(f"  {old_path.name} -> {new_path.name}")
        if not dry_run:
            old_path.rename(new_path)

    return anonymizer


def main():
    parser = argparse.ArgumentParser(
        description="Anonymize PII in test fixtures",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without modifying files",
    )
    parser.add_argument(
        "--save-mapping",
        help="Save anonymization mapping to file (DO NOT COMMIT)",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress verbose output",
    )

    args = parser.parse_args()

    if args.dry_run:
        print("=== DRY RUN - No files will be modified ===\n")

    anonymizer = process_fixtures(dry_run=args.dry_run, verbose=not args.quiet)

    if args.save_mapping:
        mapping = anonymizer.get_mapping()
        with open(args.save_mapping, "w") as f:
            json.dump(mapping, f, indent=2)
        print(f"\nMapping saved to: {args.save_mapping}")
        print("WARNING: Do NOT commit this file - it contains the real-to-fake mapping!")

    if args.dry_run:
        print("\n=== DRY RUN COMPLETE - Run without --dry-run to apply changes ===")

    return 0


if __name__ == "__main__":
    sys.exit(main())
