#!/usr/bin/env python3
"""
Fetch Mozilla People Directory API responses for use as test fixtures.

This script fetches GitHub username lookups via the two-step PMO API:
1. GraphQL API to get GitHub ID from Phabricator username
2. REST API to get GitHub username from GitHub ID

Usage:
    export PEOPLE_MOZILLA_COOKIE="your-pmo-access-cookie-value"

    # Fetch fixtures for specific usernames
    python scripts/fetch_people_fixtures.py --usernames user1 user2 user3

    # Fetch fixtures for usernames from a file (one per line)
    python scripts/fetch_people_fixtures.py --file usernames.txt
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

# Add the project root to path so we can import herald_scraper
sys.path.insert(0, str(Path(__file__).parent.parent))

from herald_scraper.people_client import extract_github_id

PMO_GRAPHQL_URL = "https://people.mozilla.org/api/v4/graphql"
PMO_GITHUB_USERNAME_URL = "https://people.mozilla.org/whoami/github/username/{github_id}"
FIXTURES_DIR = Path(__file__).parent.parent / "tests" / "fixtures" / "people"

GITHUB_ID_QUERY = """
query GetGitHubId($username: String) {
  profile(username: $username) {
    identities {
      githubIdV3 { value }
    }
  }
}
"""


class PeopleDirectoryClient:
    """Client for Mozilla People Directory API."""

    def __init__(self, cookie: str):
        self.session = requests.Session()
        self.session.cookies.set("pmo-access", cookie, domain=".mozilla.org")
        self.session.headers["User-Agent"] = "Herald-Scraper/0.1.0 (test fixture collection)"

    def get_github_id(self, username: str) -> dict:
        """Step 1: Get GitHub ID from Phabricator username via GraphQL."""
        headers = {"Content-Type": "application/json"}
        payload = {
            "operationName": "GetGitHubId",
            "variables": {"username": username},
            "query": GITHUB_ID_QUERY,
        }
        response = self.session.post(PMO_GRAPHQL_URL, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()

    def get_github_username(self, github_id: str) -> dict:
        """Step 2: Get GitHub username from GitHub ID via REST."""
        url = PMO_GITHUB_USERNAME_URL.format(github_id=github_id)
        response = self.session.get(url)
        response.raise_for_status()
        return response.json()


def save_fixture(data: dict, filepath: Path):
    """Save JSON fixture to file."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  Saved: {filepath}")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch People Directory API responses for test fixtures",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        "--usernames",
        nargs="+",
        help="Phabricator usernames to look up"
    )
    parser.add_argument(
        "--file",
        help="File containing usernames (one per line)"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay between requests in seconds (default: 0.5)"
    )

    args = parser.parse_args()

    usernames = []
    if args.usernames:
        usernames.extend(args.usernames)
    if args.file:
        with open(args.file) as f:
            usernames.extend(line.strip() for line in f if line.strip())

    if not usernames:
        print("ERROR: Must specify --usernames or --file")
        parser.print_help()
        sys.exit(1)

    cookie = os.environ.get("PEOPLE_MOZILLA_COOKIE")
    if not cookie:
        print("ERROR: PEOPLE_MOZILLA_COOKIE environment variable not set")
        print("\nTo get your pmo-access cookie:")
        print("1. Log in to people.mozilla.org in your browser")
        print("2. Open Developer Tools > Application > Cookies")
        print("3. Copy the 'pmo-access' cookie value")
        print("4. export PEOPLE_MOZILLA_COOKIE='your-pmo-access-cookie-value'")
        sys.exit(1)

    client = PeopleDirectoryClient(cookie=cookie)

    print(f"Fetching fixtures for {len(usernames)} usernames")
    print(f"Output directory: {FIXTURES_DIR}")
    print()

    results = {"found_with_github": 0, "found_no_github": 0, "not_found": 0, "errors": 0}

    for username in usernames:
        print(f"Processing: {username}")

        try:
            # Step 1: Get GitHub ID
            graphql_response = client.get_github_id(username)
            save_fixture(graphql_response, FIXTURES_DIR / f"{username}_graphql.json")
            time.sleep(args.delay)

            github_id = extract_github_id(graphql_response)

            if not github_id:
                profile = graphql_response.get("data", {}).get("profile")
                if profile:
                    print("  -> User found, no GitHub ID linked")
                    results["found_no_github"] += 1
                else:
                    print("  -> User not found in directory")
                    results["not_found"] += 1
                continue

            # Step 2: Get GitHub username
            rest_response = client.get_github_username(github_id)
            save_fixture(rest_response, FIXTURES_DIR / f"{username}_rest.json")
            time.sleep(args.delay)

            github_username = rest_response.get("username")
            print(f"  -> GitHub: {github_username}")
            results["found_with_github"] += 1

        except requests.exceptions.HTTPError as e:
            print(f"  -> HTTP Error: {e}")
            results["errors"] += 1
        except Exception as e:
            print(f"  -> Error: {e}")
            results["errors"] += 1

    print()
    print("=== Summary ===")
    print(f"  Found with GitHub: {results['found_with_github']}")
    print(f"  Found, no GitHub:  {results['found_no_github']}")
    print(f"  Not found:         {results['not_found']}")
    print(f"  Errors:            {results['errors']}")
    print(f"\nFixtures saved to: {FIXTURES_DIR}")


if __name__ == "__main__":
    main()
