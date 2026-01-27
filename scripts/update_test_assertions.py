#!/usr/bin/env python3
"""
Update test assertions to be PII-independent after fixture anonymization.

This script modifies test files to remove hardcoded usernames and identifiers,
replacing them with generic assertions that don't depend on specific PII values.

Usage:
    # Dry run (show what would change)
    python scripts/update_test_assertions.py --dry-run

    # Actually update
    python scripts/update_test_assertions.py
"""

import argparse
import re
import sys
from pathlib import Path
from typing import List, Tuple

TESTS_DIR = Path(__file__).parent.parent / "tests"


def update_test_people_client(content: str) -> Tuple[str, List[str]]:
    """Update test_people_client.py to be PII-independent.

    Changes:
    - Remove specific fixture name tests (use generic fixtures)
    - Replace with generic fixture tests
    - Remove hardcoded GitHub ID/username assertions

    Returns:
        Tuple of (new_content, list_of_changes)
    """
    changes = []

    # New content for TestExtractFromFixtures class
    new_fixture_tests = '''class TestExtractFromFixtures:
    """Tests that verify extraction from actual API response fixtures."""

    @pytest.fixture
    def graphql_fixtures(self):
        """Load all GraphQL response fixtures."""
        fixtures = {}
        for filepath in FIXTURES_DIR.glob("*_graphql.json"):
            username = filepath.stem.replace("_graphql", "")
            with open(filepath) as f:
                fixtures[username] = json.load(f)
        return fixtures

    @pytest.fixture
    def rest_fixtures(self):
        """Load all REST response fixtures."""
        fixtures = {}
        for filepath in FIXTURES_DIR.glob("*_rest.json"):
            username = filepath.stem.replace("_rest", "")
            with open(filepath) as f:
                fixtures[username] = json.load(f)
        return fixtures

    def test_extract_github_id_from_fixtures(self, graphql_fixtures):
        """Test extracting GitHub ID from all available fixtures."""
        if not graphql_fixtures:
            pytest.skip("No GraphQL fixtures found")

        # Test each fixture that should have a GitHub ID
        found_valid = False
        for username, data in graphql_fixtures.items():
            if "nonexistent" in username:
                # Nonexistent user should return None
                assert extract_github_id(data) is None
            else:
                # Real users should have a GitHub ID (anonymized with GHID- prefix)
                github_id = extract_github_id(data)
                if github_id is not None:
                    assert github_id.startswith("GHID-"), f"GitHub ID should have GHID- prefix: {github_id}"
                    found_valid = True

        assert found_valid, "At least one fixture should have a valid GitHub ID"

    def test_extract_github_id_nonexistent_user(self, graphql_fixtures):
        """Test extracting GitHub ID from nonexistent user fixture."""
        nonexistent_fixtures = [k for k in graphql_fixtures if "nonexistent" in k]
        if not nonexistent_fixtures:
            pytest.skip("No nonexistent user fixture found")

        for username in nonexistent_fixtures:
            github_id = extract_github_id(graphql_fixtures[username])
            assert github_id is None

    def test_extract_github_username_from_fixtures(self, rest_fixtures):
        """Test extracting GitHub username from all available REST fixtures."""
        if not rest_fixtures:
            pytest.skip("No REST fixtures found")

        # Test each fixture
        found_valid = False
        for username, data in rest_fixtures.items():
            github_username = extract_github_username(data)
            if github_username is not None:
                # GitHub usernames should be anonymized with GHUSER- prefix
                assert isinstance(github_username, str)
                assert github_username.startswith("GHUSER-"), f"GitHub username should have GHUSER- prefix: {github_username}"
                found_valid = True

        assert found_valid, "At least one fixture should have a valid GitHub username"

'''

    # Find and replace the TestExtractFromFixtures class
    # Match from "class TestExtractFromFixtures" to the next "class Test" or end
    pattern = r'class TestExtractFromFixtures:.*?(?=\nclass Test|\nclass [A-Z]|\Z)'

    if re.search(pattern, content, re.DOTALL):
        result = re.sub(pattern, new_fixture_tests.rstrip(), content, flags=re.DOTALL)
        changes.append("Replaced TestExtractFromFixtures with PII-independent version")
    else:
        result = content
        changes.append("WARNING: Could not find TestExtractFromFixtures class")

    # Also update the test data in TestExtractGithubUsername to use generic values
    result = re.sub(
        r'response = \{"username": "[^"]+"\}',
        'response = {"username": "testghuser"}',
        result
    )
    result = re.sub(
        r'assert extract_github_username\(response\) == "[^"]+"',
        'assert extract_github_username(response) == "testghuser"',
        result
    )
    if 'testghuser' in result:
        changes.append("Updated test data to use generic 'testghuser'")

    return result, changes


def update_test_parsers(content: str) -> Tuple[str, List[str]]:
    """Update test_parsers.py to be PII-independent.

    Changes:
    - Remove author field from test_parse_rule_fixture expected dicts
    - Change expected_members lists to member_count integers
    - Update test_extract_members_from_timeline to use count-based assertions
    - Remove EXPECTED_MEMBERS dict with hardcoded usernames
    - Remove test_extract_members_exact test

    Returns:
        Tuple of (new_content, list_of_changes)
    """
    changes = []
    result = content

    # 1. Remove "author": "..." lines from test_parse_rule_fixture expected dicts
    author_pattern = r'\s*"author": "[^"]+",\n'
    if re.search(author_pattern, result):
        result = re.sub(author_pattern, '\n', result)
        changes.append("Removed author from test_parse_rule_fixture expected dicts")

    # 2. Update the author assertion to check pattern instead of exact value
    # Use str.replace to avoid regex escaping issues
    old_author_line = 'assert author == expected["author"], f"Author mismatch: got \'{author}\', expected \'{expected[\'author\']}\'"'
    new_author_lines = '''# Author is anonymized - just verify it exists and matches pattern
        assert author is not None, "Author should not be None"
        assert author.startswith("USER-"), f"Author should be anonymized with USER- prefix, got '{author}'"'''
    if old_author_line in result:
        result = result.replace(old_author_line, new_author_lines)
        changes.append("Updated author assertion to check USER- prefix pattern")

    # 3. Convert "expected_members": ["name1", "name2", ...] to "member_count": N
    def replace_expected_members(match: re.Match) -> str:
        members_str = match.group(1)
        count = len(re.findall(r'"[^"]+"', members_str))
        return f'"member_count": {count}'

    expected_members_pattern = r'"expected_members": \[([^\]]*)\]'
    if re.search(expected_members_pattern, result):
        result = re.sub(expected_members_pattern, replace_expected_members, result)
        changes.append("Changed expected_members to member_count")

    # 4. Update the assertion logic for expected_members -> member_count
    old_members_check = '''if "expected_members" in expected:
            # Check exact member list (sorted for comparison)
            assert sorted(info["members"]) == sorted(expected["expected_members"]), \\
                f"Members mismatch: got {sorted(info['members'])}, expected {sorted(expected['expected_members'])}"'''
    new_members_check = '''if "member_count" in expected:
            # Check member count (names are anonymized with USER- prefix)
            assert len(info["members"]) == expected["member_count"], \\
                f"Member count mismatch: got {len(info['members'])}, expected {expected['member_count']}"'''
    if old_members_check in result:
        result = result.replace(old_members_check, new_members_check)
        changes.append("Updated member assertion to check count instead of exact names")

    # 5. Update test_extract_members_from_timeline to use count-based assertions
    # Note: The old pattern has been removed as fixtures are now anonymized.
    # This step is kept for documentation purposes.
    new_timeline_block = '''        # Verify member extraction works (names are anonymized with USER- prefix)
        assert isinstance(members, list)
        # Based on timeline, should have 3 current members
        assert len(members) == 3, f"Expected 3 members from timeline, got {len(members)}"
        # All members should be anonymized with USER- prefix
        for member in members:
            assert member.startswith("USER-"), f"Member should have USER- prefix, got '{member}'"'''
    # Pattern matching removed - test file already anonymized

    # 6. Remove test_extract_members_exact test entirely
    test_exact_pattern = (
        r'\n    @pytest\.mark\.parametrize\("group_slug", \[\s*'
        r'[^\]]+\]\)\s*'
        r'def test_extract_members_exact\(self[^)]*\):.*?'
        r'(?=\n    (?:@|def )|$)'
    )
    if re.search(test_exact_pattern, result, re.DOTALL):
        result = re.sub(test_exact_pattern, '', result, flags=re.DOTALL)
        changes.append("Removed test_extract_members_exact test")

    # 7. Remove the entire EXPECTED_MEMBERS dict (multi-line)
    # Match from comment line through the closing brace
    # The dict ends with "    }\n" (4 spaces + } + newline)
    expected_members_dict_pattern = (
        r'    # Expected members for each group fixture[^\n]*\n'
        r'    EXPECTED_MEMBERS = \{.*?\n    \}\n'
    )
    if re.search(expected_members_dict_pattern, result, re.DOTALL):
        result = re.sub(expected_members_dict_pattern, '', result, flags=re.DOTALL)
        changes.append("Removed EXPECTED_MEMBERS dict with hardcoded usernames")

    # 8. Update remaining EXPECTED_MEMBERS references
    if 'self.EXPECTED_MEMBERS' in result:
        result = re.sub(r'self\.EXPECTED_MEMBERS\[[^\]]+\]', '[]', result)
        changes.append("Removed remaining EXPECTED_MEMBERS references")

    # 9. Remove comments that contain usernames (PII)
    # Pattern: # Note: username is the actor...
    result = re.sub(
        r'\s*# Note: [a-zA-Z0-9_-]+ is the actor[^\n]*\n',
        '\n',
        result
    )
    # Pattern: comments mentioning specific users in timeline analysis
    result = re.sub(
        r'\s*# - [a-zA-Z0-9_-]+ (added|removed|created)[^\n]*\n',
        '',
        result
    )
    # Pattern: # Current members based on timeline: user1, user2, ...
    result = re.sub(
        r'\s*# Current members based on timeline:[^\n]*\n',
        '',
        result
    )
    # Check if any PII-containing comments were removed
    if re.search(r'# - [a-zA-Z0-9_-]+ (added|removed|created)', content) and \
       not re.search(r'# - [a-zA-Z0-9_-]+ (added|removed|created)', result):
        changes.append("Removed comments containing usernames")

    return result, changes


def process_test_files(dry_run: bool = True, verbose: bool = True) -> bool:
    """Process all test files and update assertions.

    Returns:
        True if any changes were made (or would be made in dry run)
    """
    any_changes = False

    # Process test_people_client.py
    people_client_test = TESTS_DIR / "test_people_client.py"
    if people_client_test.exists():
        content = people_client_test.read_text()
        new_content, changes = update_test_people_client(content)

        if changes and content != new_content:
            any_changes = True
            if verbose:
                print(f"\n{people_client_test.name}:")
                for change in changes:
                    print(f"  - {change}")

            if not dry_run:
                people_client_test.write_text(new_content)
                if verbose:
                    print(f"  Written: {people_client_test}")

    # Process test_parsers.py
    parsers_test = TESTS_DIR / "test_parsers.py"
    if parsers_test.exists():
        content = parsers_test.read_text()
        new_content, changes = update_test_parsers(content)

        if changes and content != new_content:
            any_changes = True
            if verbose:
                print(f"\n{parsers_test.name}:")
                for change in changes:
                    print(f"  - {change}")

            if not dry_run:
                parsers_test.write_text(new_content)
                if verbose:
                    print(f"  Written: {parsers_test}")

    return any_changes


def main():
    parser = argparse.ArgumentParser(
        description="Update test assertions to be PII-independent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without modifying files",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress verbose output",
    )

    args = parser.parse_args()

    if args.dry_run:
        print("=== DRY RUN - No files will be modified ===\n")

    any_changes = process_test_files(dry_run=args.dry_run, verbose=not args.quiet)

    if args.dry_run:
        if any_changes:
            print("\n=== DRY RUN COMPLETE - Run without --dry-run to apply changes ===")
        else:
            print("\nNo changes needed.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
