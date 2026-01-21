#!/usr/bin/env python3
"""
Fetch Herald rule pages from Phabricator for use as test fixtures.

This script requires authentication via session cookie.

Usage:
    # Using session cookie
    export PHAB_SESSION_COOKIE="your-phsid-cookie-value"
    python scripts/fetch_fixtures.py

    # Fetch specific rule IDs
    python scripts/fetch_fixtures.py --rules H416 H417 H420

    # Fetch all rules from listing
    python scripts/fetch_fixtures.py --all
"""

import argparse
import os
import sys
import time
from pathlib import Path
from typing import List
import requests
from bs4 import BeautifulSoup


PHABRICATOR_INSTANCE = "https://phabricator.services.mozilla.com"
FIXTURES_DIR = Path(__file__).parent.parent / "tests" / "fixtures"


class PhabricatorFetcher:
    """Fetches pages from Phabricator with authentication."""

    def __init__(self, session_cookie: str):
        self.session = requests.Session()
        self.base_url = PHABRICATOR_INSTANCE

        # Set up authentication with session cookie
        self.session.cookies.set("phsid", session_cookie, domain=".services.mozilla.com")
        print(f"Using session cookie authentication")

        # Set user agent
        self.session.headers["User-Agent"] = "Herald-Scraper/0.1.0 (test fixture collection)"

    def fetch_page(self, url: str) -> str:
        """Fetch a page and return its HTML content."""
        print(f"Fetching: {url}")
        response = self.session.get(url)
        response.raise_for_status()

        # Check if we got a login page instead
        if "Log In" in response.text and "/auth/login/" in response.text:
            raise Exception("Authentication failed - got login page")

        return response.text

    def fetch_listing(self) -> str:
        """Fetch the Herald rules listing page."""
        url = f"{self.base_url}/herald/query/all/"
        return self.fetch_page(url)

    def fetch_rule(self, rule_id: str) -> str:
        """Fetch a specific Herald rule page."""
        # Ensure rule_id has 'H' prefix
        if not rule_id.startswith("H"):
            rule_id = f"H{rule_id}"

        url = f"{self.base_url}/{rule_id}"
        return self.fetch_page(url)

    def fetch_project(self, project_slug: str) -> str:
        """Fetch a project/group page."""
        url = f"{self.base_url}/project/profile/{project_slug}/"
        return self.fetch_page(url)

    def extract_rule_ids_from_listing(self, html: str) -> List[str]:
        """Extract Herald rule IDs from the listing page."""
        soup = BeautifulSoup(html, "lxml")
        rule_ids = set()

        # Look for links to /H### (e.g., /H416)
        for link in soup.find_all("a", href=True):
            href = link["href"]
            if href.startswith("/H") and len(href) > 2:
                rule_id = href[1:]  # Remove leading slash
                if rule_id[0] == "H" and rule_id[1:].isdigit():
                    rule_ids.add(rule_id)

        return sorted(rule_ids, key=lambda x: int(x[1:]))


def save_file(content: str, filepath: Path):
    """Save content to a file."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(content)
    print(f"Saved: {filepath} ({len(content)} bytes)")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch Phabricator pages for test fixtures",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        "--rules",
        nargs="+",
        help="Specific rule IDs to fetch (e.g., H416 H417)"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Fetch all rules from the listing page"
    )
    parser.add_argument(
        "--max-rules",
        type=int,
        default=10,
        help="Maximum number of rules to fetch when using --all (default: 10)"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Delay between requests in seconds (default: 1.0)"
    )

    args = parser.parse_args()

    # Get credentials from environment
    session_cookie = os.environ.get("PHAB_SESSION_COOKIE")

    if not session_cookie:
        print("ERROR: No authentication provided!")
        print("Please set PHAB_SESSION_COOKIE environment variable")
        print("\nTo get your session cookie:")
        print("1. Log in to Phabricator in your browser")
        print("2. Open Developer Tools > Application > Cookies")
        print("3. Copy the 'phsid' cookie value")
        print("4. export PHAB_SESSION_COOKIE='your-cookie-value'")
        sys.exit(1)

    fetcher = PhabricatorFetcher(session_cookie=session_cookie)

    try:
        # Always fetch and save the listing page
        print("\n=== Fetching Herald rules listing ===")
        listing_html = fetcher.fetch_listing()
        save_file(listing_html, FIXTURES_DIR / "rules" / "listing.html")
        time.sleep(args.delay)

        # Determine which rules to fetch
        rule_ids = []
        if args.all:
            print("\n=== Extracting rule IDs from listing ===")
            all_rule_ids = fetcher.extract_rule_ids_from_listing(listing_html)
            print(f"Found {len(all_rule_ids)} rule IDs: {', '.join(all_rule_ids[:20])}")
            rule_ids = all_rule_ids[:args.max_rules]
            print(f"Will fetch first {len(rule_ids)} rules")
        elif args.rules:
            rule_ids = args.rules
        else:
            # Default: fetch a few example rules
            rule_ids = ["H416", "H417", "H420", "H425"]
            print(f"\n=== Fetching default example rules: {', '.join(rule_ids)} ===")

        # Fetch individual rule pages
        print(f"\n=== Fetching {len(rule_ids)} rule pages ===")
        for rule_id in rule_ids:
            try:
                rule_html = fetcher.fetch_rule(rule_id)
                save_file(rule_html, FIXTURES_DIR / "rules" / f"rule_{rule_id}.html")
                time.sleep(args.delay)
            except Exception as e:
                print(f"ERROR fetching {rule_id}: {e}")
                continue

        print("\n=== Done! ===")
        print(f"Fixtures saved to: {FIXTURES_DIR}")

    except Exception as e:
        print(f"\nERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
