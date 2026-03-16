#!/usr/bin/env python3
"""
Rewrite git history to remove PII from all commits.

This script uses git-filter-repo to replace real usernames/identifiers with
anonymized values throughout the entire git history.

IMPORTANT: This is a destructive operation that rewrites git history.
- Make a backup of your repository first
- All collaborators will need to re-clone after this operation
- Force push will be required

Prerequisites:
    pip install git-filter-repo

Usage:
    # Generate replacement expressions file (dry run)
    python scripts/rewrite_git_history.py --mapping anonymization_mapping.json --dry-run

    # Actually rewrite history
    python scripts/rewrite_git_history.py --mapping anonymization_mapping.json

    # Generate expressions file only (for manual git-filter-repo run)
    python scripts/rewrite_git_history.py --mapping anonymization_mapping.json --expressions-only
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple

REPO_ROOT = Path(__file__).parent.parent


def load_mapping(mapping_file: str) -> Dict[str, Dict[str, str]]:
    """Load the anonymization mapping from file."""
    with open(mapping_file) as f:
        data: Dict[str, Dict[str, str]] = json.load(f)
        return data


def generate_expressions(mapping: Dict[str, Dict[str, str]]) -> List[Tuple[str, str]]:
    """Generate replacement expressions from the mapping.

    Returns:
        List of (pattern, replacement) tuples
    """
    expressions = []

    # Username replacements (Phabricator usernames)
    usernames = mapping.get("usernames", {})
    for real, fake in usernames.items():
        # Replace in /p/username/ paths
        expressions.append((f"/p/{real}/", f"/p/{fake}/"))
        expressions.append((f"/p/{real}", f"/p/{fake}"))

        # Replace in title attributes
        expressions.append((f'title="{real}"', f'title="{fake}"'))

        # Replace username in link text (be careful with word boundaries)
        # Only replace when it's clearly a username context
        expressions.append((f">{real}</a>", f">{fake}</a>"))

        # Replace in JSON filenames mentioned in code/comments
        expressions.append((f"{real}_graphql.json", f"{fake}_graphql.json"))
        expressions.append((f"{real}_rest.json", f"{fake}_rest.json"))

        # Replace in test assertions and comments
        expressions.append((f'"{real}"', f'"{fake}"'))

    # GitHub ID replacements
    github_ids = mapping.get("github_ids", {})
    for real, fake in github_ids.items():
        expressions.append((f'"value": "{real}"', f'"value": "{fake}"'))
        expressions.append((f'== "{real}"', f'== "{fake}"'))

    # GitHub username replacements
    github_usernames = mapping.get("github_usernames", {})
    for real, fake in github_usernames.items():
        expressions.append((f'"username": "{real}"', f'"username": "{fake}"'))
        expressions.append((f'== "{real}"', f'== "{fake}"'))

    return expressions


def write_expressions_file(
    expressions: List[Tuple[str, str]],
    output_path: str
) -> None:
    """Write expressions to a file for git-filter-repo.

    Format: literal:old==>new (one per line)
    """
    with open(output_path, "w") as f:
        for old, new in expressions:
            # Use literal replacement (not regex)
            f.write(f"literal:{old}==>{new}\n")


def generate_blob_callback_script(mapping: Dict[str, Dict[str, str]]) -> str:
    """Generate a Python callback script for git-filter-repo.

    This is more powerful than simple expressions and can handle
    file renames and complex replacements.
    """
    # Escape the mapping for embedding in Python code
    mapping_json = json.dumps(mapping)

    return f'''#!/usr/bin/env python3
"""Blob callback for git-filter-repo to anonymize PII."""

import re

MAPPING = {mapping_json}

def process_content(content):
    """Process blob content and replace PII."""
    result = content

    # Replace usernames
    for real, fake in MAPPING.get("usernames", {{}}).items():
        # Various patterns where usernames appear
        patterns = [
            (rb'/p/' + real.encode() + rb'/', b'/p/' + fake.encode() + b'/'),
            (rb'/p/' + real.encode() + rb'"', b'/p/' + fake.encode() + b'"'),
            (rb'title="' + real.encode() + rb'"', b'title="' + fake.encode() + b'"'),
            (rb'>' + real.encode() + rb'</a>', b'>' + fake.encode() + b'</a>'),
            (rb'"' + real.encode() + rb'"', b'"' + fake.encode() + b'"'),
        ]
        for old, new in patterns:
            result = result.replace(old, new)

    # Replace GitHub IDs
    for real, fake in MAPPING.get("github_ids", {{}}).items():
        result = result.replace(
            b'"value": "' + real.encode() + b'"',
            b'"value": "' + fake.encode() + b'"'
        )

    # Replace GitHub usernames
    for real, fake in MAPPING.get("github_usernames", {{}}).items():
        result = result.replace(
            b'"username": "' + real.encode() + b'"',
            b'"username": "' + fake.encode() + b'"'
        )

    return result

def blob_callback(blob, callback_metadata):
    """Callback function for git-filter-repo."""
    blob.data = process_content(blob.data)
'''


def generate_filename_callback_script(mapping: Dict[str, Dict[str, str]]) -> str:
    """Generate a filename callback script for git-filter-repo."""
    mapping_json = json.dumps(mapping)

    return f'''#!/usr/bin/env python3
"""Filename callback for git-filter-repo to rename files with PII."""

MAPPING = {mapping_json}

def filename_callback(filename):
    """Callback function for renaming files."""
    result = filename

    # Replace usernames in filenames
    for real, fake in MAPPING.get("usernames", {{}}).items():
        # Replace in fixture filenames
        result = result.replace(
            (real + "_graphql.json").encode(),
            (fake + "_graphql.json").encode()
        )
        result = result.replace(
            (real + "_rest.json").encode(),
            (fake + "_rest.json").encode()
        )
        result = result.replace(
            (real + ".json").encode(),
            (fake + ".json").encode()
        )

    return result
'''


def check_git_filter_repo() -> bool:
    """Check if git-filter-repo is installed."""
    try:
        result = subprocess.run(
            ["git", "filter-repo", "--version"],
            capture_output=True,
            text=True
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def run_git_filter_repo(
    expressions_file: str,
    dry_run: bool = True
) -> int:
    """Run git-filter-repo with the expressions file."""
    cmd = [
        "git", "filter-repo",
        "--replace-text", expressions_file,
        "--force",
    ]

    if dry_run:
        print(f"Would run: {' '.join(cmd)}")
        return 0

    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=REPO_ROOT)
    return result.returncode


def main():
    parser = argparse.ArgumentParser(
        description="Rewrite git history to remove PII",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--mapping",
        required=True,
        help="Path to anonymization mapping JSON file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    parser.add_argument(
        "--expressions-only",
        action="store_true",
        help="Generate expressions file only (don't run git-filter-repo)",
    )
    parser.add_argument(
        "--output",
        default="git_filter_expressions.txt",
        help="Output file for expressions (default: git_filter_expressions.txt)",
    )

    args = parser.parse_args()

    # Load mapping
    print(f"Loading mapping from: {args.mapping}")
    mapping = load_mapping(args.mapping)
    print(f"  {len(mapping.get('usernames', {}))} usernames")
    print(f"  {len(mapping.get('github_ids', {}))} GitHub IDs")
    print(f"  {len(mapping.get('github_usernames', {}))} GitHub usernames")

    # Generate expressions
    expressions = generate_expressions(mapping)
    print(f"\nGenerated {len(expressions)} replacement expressions")

    # Write expressions file
    write_expressions_file(expressions, args.output)
    print(f"Wrote expressions to: {args.output}")

    if args.expressions_only:
        print("\n--expressions-only specified, stopping here.")
        print("\nTo manually run git-filter-repo:")
        print(f"  git filter-repo --replace-text {args.output} --force")
        return 0

    # Check for git-filter-repo
    if not check_git_filter_repo():
        print("\nERROR: git-filter-repo is not installed.")
        print("Install it with: pip install git-filter-repo")
        print("\nAlternatively, you can manually run git-filter-repo with the expressions file.")
        return 1

    if args.dry_run:
        print("\n=== DRY RUN - No changes will be made ===")

    # Warn about destructive operation
    if not args.dry_run:
        print("\n" + "=" * 60)
        print("WARNING: This will rewrite git history!")
        print("=" * 60)
        print("""
This operation will:
1. Modify all commits that contain PII
2. Change commit hashes
3. Require force push to remote
4. Require all collaborators to re-clone

Make sure you have:
- A backup of the repository
- Informed all collaborators
- No pending changes

The expressions file has been written to: {args.output}
You can review it before proceeding.

To proceed manually, run:
  git filter-repo --replace-text {args.output} --force
""")
        return 0

    # Run git-filter-repo
    return run_git_filter_repo(args.output, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
