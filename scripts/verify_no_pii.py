#!/usr/bin/env python3
"""
Verify that no PII remains in test fixtures after anonymization.

This script scans fixture files for patterns that look like real usernames
or identifiers that weren't anonymized.

Usage:
    # Check for PII in fixtures
    python scripts/verify_no_pii.py

    # Check with a mapping file to verify all real names are gone
    python scripts/verify_no_pii.py --mapping anonymization_mapping.json

    # Verbose output
    python scripts/verify_no_pii.py --verbose
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

FIXTURES_DIR = Path(__file__).parent.parent / "tests" / "fixtures"

# Patterns that indicate anonymized values (these are OK)
ANONYMIZED_PATTERNS = [
    r"^user\d+$",           # user1, user2, etc.
    r"^ghuser\d+$",         # ghuser1, ghuser2, etc.
    r"^\d{6}$",             # 6-digit GitHub IDs like 100001
]

# Known non-PII values that are OK to keep
KNOWN_SAFE_VALUES = {
    # Repository names (not PII)
    "mozilla-central",
    "firefox-autoland",
    "phabricator",
    # Phabricator system values
    "PHID-",
    "differential-revision",
    # Project/tag names (not PII)
    "omc-reviewers",
    "android-reviewers",
    "sidebar-reviewers-rotation",
    "geckoview-api-reviewers",
    "geckodriver-reviewers",
    "profiler-reviewers",
    "desktop-theme-reviewers",
    "reusable-components-reviewers-rotation",
    # Test placeholder values
    "nonexistent_user_xyz123",
    "nonexistent",
    # Status values
    "Active",
    "Disabled",
    "Global",
    "Personal",
}


def is_anonymized(value: str) -> bool:
    """Check if a value matches anonymized patterns."""
    for pattern in ANONYMIZED_PATTERNS:
        if re.match(pattern, value):
            return True
    return False


def is_safe_value(value: str) -> bool:
    """Check if a value is known to be safe (not PII)."""
    for safe in KNOWN_SAFE_VALUES:
        if safe in value:
            return True
    return False


def extract_potential_usernames_from_html(content: str) -> Set[str]:
    """Extract potential usernames from HTML content."""
    potential = set()

    # Pattern: href="/p/username/"
    for match in re.finditer(r'href="/p/([^/"]+)/"?', content):
        username = match.group(1)
        if not is_anonymized(username) and not is_safe_value(username):
            potential.add(username)

    # Pattern: >text</a> that could be usernames in user links
    for match in re.finditer(r'>([a-zA-Z][a-zA-Z0-9_-]{2,30})</a>', content):
        text = match.group(1)
        # Only if it looks like a username (not HTML tags, not all caps, etc.)
        if (not text.isupper() and
            not is_anonymized(text) and
            not is_safe_value(text) and
            not text.startswith(("H", "PHID-", "rMOZILLA", "rFIREFOX"))):
            potential.add(text)

    return potential


def extract_potential_usernames_from_json(content: str) -> Set[str]:
    """Extract potential usernames from JSON content."""
    potential = set()

    try:
        data = json.loads(content)

        # Check "username" field in REST responses
        if "username" in data and data["username"]:
            username = data["username"]
            if not is_anonymized(username) and not is_safe_value(username):
                potential.add(username)

        # Check GitHub ID values
        if "data" in data and data.get("data"):
            profile = data["data"].get("profile", {})
            if profile:
                identities = profile.get("identities", {})
                github_id = identities.get("githubIdV3", {})
                if github_id and github_id.get("value"):
                    value = github_id["value"]
                    # Real GitHub IDs are typically 5-8 digits
                    if not re.match(r"^10000\d+$", value):  # Our fake IDs start with 10000
                        potential.add(f"github_id:{value}")

    except json.JSONDecodeError:
        pass

    return potential


def check_fixture_filenames(mapping: Dict[str, Dict[str, str]] = None) -> List[str]:
    """Check if any fixture filenames contain real usernames."""
    issues = []

    if mapping:
        real_usernames = set(mapping.get("usernames", {}).keys())
    else:
        real_usernames = set()

    for filepath in FIXTURES_DIR.rglob("*"):
        if not filepath.is_file():
            continue

        filename = filepath.stem  # Without extension

        # Check if filename contains a known real username
        for username in real_usernames:
            if username in filename:
                issues.append(f"Filename contains real username: {filepath.name} ({username})")

        # Check for non-anonymized username patterns
        # Extract potential username from filename
        for suffix in ("_graphql", "_rest", ""):
            if filename.endswith(suffix):
                potential_username = filename[:-len(suffix)] if suffix else filename
                if (potential_username and
                    not is_anonymized(potential_username) and
                    not is_safe_value(potential_username) and
                    not potential_username.startswith(("rule_", "listing", "search_", "nonexistent"))):
                    # This might be a real username
                    issues.append(f"Filename may contain real username: {filepath.name}")
                break

    return issues


def scan_fixtures(
    mapping: Dict[str, Dict[str, str]] = None,
    verbose: bool = False
) -> Tuple[List[str], List[str]]:
    """Scan all fixtures for potential PII.

    Returns:
        Tuple of (errors, warnings)
    """
    errors = []
    warnings = []

    real_usernames = set(mapping.get("usernames", {}).keys()) if mapping else set()
    real_github_ids = set(mapping.get("github_ids", {}).keys()) if mapping else set()
    real_github_usernames = set(mapping.get("github_usernames", {}).keys()) if mapping else set()

    for filepath in FIXTURES_DIR.rglob("*"):
        if not filepath.is_file():
            continue

        if filepath.name == "README.md":
            continue

        try:
            content = filepath.read_text()
        except UnicodeDecodeError:
            continue

        relative_path = filepath.relative_to(FIXTURES_DIR)

        if filepath.suffix == ".html":
            potential = extract_potential_usernames_from_html(content)

            # Check against known real usernames
            for username in potential:
                if username in real_usernames:
                    errors.append(f"{relative_path}: Contains real username '{username}'")
                elif verbose:
                    warnings.append(f"{relative_path}: Potential username '{username}'")

        elif filepath.suffix == ".json":
            potential = extract_potential_usernames_from_json(content)

            for item in potential:
                if item.startswith("github_id:"):
                    gid = item.split(":")[1]
                    if gid in real_github_ids:
                        errors.append(f"{relative_path}: Contains real GitHub ID '{gid}'")
                    elif verbose:
                        warnings.append(f"{relative_path}: Potential GitHub ID '{gid}'")
                else:
                    if item in real_usernames or item in real_github_usernames:
                        errors.append(f"{relative_path}: Contains real username '{item}'")
                    elif verbose:
                        warnings.append(f"{relative_path}: Potential username '{item}'")

    # Check filenames
    filename_issues = check_fixture_filenames(mapping)
    errors.extend(filename_issues)

    return errors, warnings


def main():
    parser = argparse.ArgumentParser(
        description="Verify no PII remains in test fixtures",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--mapping",
        help="Path to anonymization mapping JSON file (for stricter checking)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show warnings about potential PII (not just confirmed)",
    )

    args = parser.parse_args()

    mapping = {}
    if args.mapping:
        with open(args.mapping) as f:
            mapping = json.load(f)
        print(f"Loaded mapping with {len(mapping.get('usernames', {}))} known usernames")

    print(f"\nScanning fixtures in: {FIXTURES_DIR}")
    print("-" * 60)

    errors, warnings = scan_fixtures(mapping, verbose=args.verbose)

    if warnings:
        print("\nWarnings (potential PII, may be false positives):")
        for warning in warnings:
            print(f"  [WARN] {warning}")

    if errors:
        print("\nErrors (confirmed PII found):")
        for error in errors:
            print(f"  [ERROR] {error}")
        print(f"\n{len(errors)} PII issue(s) found!")
        return 1

    print("\nNo PII found in fixtures.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
