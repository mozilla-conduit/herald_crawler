#!/usr/bin/env python3
"""
Fetch Conduit API responses from Phabricator for use as test fixtures.

This script requires authentication via API token.

Usage:
    # Set API token
    export PHABRICATOR_CONDUIT_TOKEN="api-xxxxx"

    # Fetch fixtures for specific project slugs
    python scripts/fetch_conduit_fixtures.py --projects omc-reviewers android-reviewers

    # Fetch fixtures for all groups referenced in existing rules output
    python scripts/fetch_conduit_fixtures.py --from-rules herald_rules.json

    # Fetch user info for specific PHIDs
    python scripts/fetch_conduit_fixtures.py --users PHID-USER-xxx PHID-USER-yyy

To get your API token:
    1. Log in to Phabricator
    2. Go to Settings > Conduit API Tokens
    3. Generate a new token
    4. export PHABRICATOR_CONDUIT_TOKEN='api-xxxxx'
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


PHABRICATOR_INSTANCE = "https://phabricator.services.mozilla.com"
FIXTURES_DIR = Path(__file__).parent.parent / "tests" / "fixtures" / "conduit"


class ConduitFetcher:
    """Fetches Conduit API responses from Phabricator."""

    def __init__(self, api_token: str, delay: float = 1.0) -> None:
        self.session = requests.Session()
        self.base_url = PHABRICATOR_INSTANCE
        self.api_token = api_token
        self.delay = delay
        self._last_request_time: Optional[float] = None

        self.session.headers["User-Agent"] = "Herald-Scraper/0.1.0 (test fixture collection)"
        print(f"Using API token authentication")

    def _rate_limit(self) -> None:
        """Apply rate limiting between requests."""
        if self._last_request_time is not None:
            elapsed = time.time() - self._last_request_time
            if elapsed < self.delay:
                time.sleep(self.delay - elapsed)
        self._last_request_time = time.time()

    def call_method(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Call a Conduit API method.

        Args:
            method: API method name (e.g., 'project.search')
            params: Optional parameters for the API call

        Returns:
            Full API response (including result, error_code, error_info)

        Raises:
            Exception: If the HTTP request fails
        """
        self._rate_limit()

        url = f"{self.base_url}/api/{method}"
        data = {"api.token": self.api_token}

        if params:
            for key, value in params.items():
                if isinstance(value, (dict, list)):
                    data[key] = json.dumps(value)
                else:
                    data[key] = value

        print(f"Calling: {method}")
        response = self.session.post(url, data=data)
        response.raise_for_status()

        result = response.json()

        # Check for API errors
        if result.get("error_code"):
            raise Exception(f"Conduit error: {result.get('error_info')} (code: {result.get('error_code')})")

        return result

    def project_search(
        self,
        slugs: Optional[List[str]] = None,
        phids: Optional[List[str]] = None,
        attachments: Optional[Dict[str, bool]] = None,
    ) -> Dict[str, Any]:
        """
        Search for projects by slug or PHID.

        Args:
            slugs: List of project slugs to search for
            phids: List of project PHIDs to search for
            attachments: Attachments to include (e.g., {"members": True})

        Returns:
            Full API response
        """
        params: Dict[str, Any] = {}

        constraints: Dict[str, Any] = {}
        if slugs:
            constraints["slugs"] = slugs
        if phids:
            constraints["phids"] = phids

        if constraints:
            params["constraints"] = constraints

        if attachments:
            params["attachments"] = attachments

        return self.call_method("project.search", params)

    def user_search(
        self,
        phids: Optional[List[str]] = None,
        usernames: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Search for users by PHID or username.

        Args:
            phids: List of user PHIDs to search for
            usernames: List of usernames to search for

        Returns:
            Full API response
        """
        params: Dict[str, Any] = {}

        constraints: Dict[str, Any] = {}
        if phids:
            constraints["phids"] = phids
        if usernames:
            constraints["usernames"] = usernames

        if constraints:
            params["constraints"] = constraints

        return self.call_method("user.search", params)


def save_json(data: Dict[str, Any], filepath: Path) -> None:
    """Save JSON data to a file."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Saved: {filepath} ({filepath.stat().st_size} bytes)")


def load_rules_output(filepath: Path) -> Dict[str, Any]:
    """Load existing herald_rules.json output."""
    with open(filepath) as f:
        return json.load(f)


def extract_group_slugs_from_rules(rules_data: Dict[str, Any]) -> List[str]:
    """Extract group slugs from rules output."""
    slugs = set()

    for rule in rules_data.get("rules", []):
        for action in rule.get("actions", []):
            for reviewer in action.get("reviewers", []):
                if reviewer.get("is_group") is True:
                    slugs.add(reviewer["target"])

    return sorted(slugs)


def extract_member_phids_from_response(response: Dict[str, Any]) -> List[str]:
    """Extract member PHIDs from a project.search response."""
    phids = []
    for project in response.get("result", {}).get("data", []):
        members = project.get("attachments", {}).get("members", {}).get("members", [])
        for member in members:
            phids.append(member["phid"])
    return phids


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch Conduit API responses for test fixtures",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--projects",
        nargs="+",
        help="Project slugs to fetch (e.g., omc-reviewers android-reviewers)",
    )
    parser.add_argument(
        "--from-rules",
        type=Path,
        metavar="FILE",
        help="Extract group slugs from existing herald_rules.json",
    )
    parser.add_argument(
        "--users",
        nargs="+",
        help="User PHIDs to fetch (e.g., PHID-USER-xxx)",
    )
    parser.add_argument(
        "--usernames",
        nargs="+",
        help="Usernames to fetch",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Delay between requests in seconds (default: 1.0)",
    )
    parser.add_argument(
        "--max-projects",
        type=int,
        default=5,
        help="Maximum number of projects to fetch (default: 5)",
    )

    args = parser.parse_args()

    # Get API token from environment
    api_token = os.environ.get("PHABRICATOR_CONDUIT_TOKEN")

    if not api_token:
        print("ERROR: No API token provided!")
        print("Please set PHABRICATOR_CONDUIT_TOKEN environment variable")
        print("\nTo get your API token:")
        print("1. Log in to Phabricator")
        print("2. Go to Settings > Conduit API Tokens")
        print("3. Generate a new token")
        print("4. export PHABRICATOR_CONDUIT_TOKEN='api-xxxxx'")
        sys.exit(1)

    fetcher = ConduitFetcher(api_token=api_token, delay=args.delay)

    try:
        # Determine which projects to fetch
        project_slugs: List[str] = []

        if args.from_rules:
            print(f"\n=== Loading groups from {args.from_rules} ===")
            rules_data = load_rules_output(args.from_rules)
            project_slugs = extract_group_slugs_from_rules(rules_data)
            print(f"Found {len(project_slugs)} groups: {', '.join(project_slugs[:10])}...")
            project_slugs = project_slugs[: args.max_projects]
        elif args.projects:
            project_slugs = args.projects

        # Fetch project data
        if project_slugs:
            print(f"\n=== Fetching {len(project_slugs)} projects with members ===")

            # Fetch projects with members attachment
            response = fetcher.project_search(
                slugs=project_slugs,
                attachments={"members": True},
            )
            save_json(response, FIXTURES_DIR / "project_search_response.json")

            # Extract member PHIDs and fetch user info
            member_phids = extract_member_phids_from_response(response)
            print(f"Found {len(member_phids)} member PHIDs")

            if member_phids:
                print(f"\n=== Fetching user info for {len(member_phids)} members ===")
                user_response = fetcher.user_search(phids=member_phids)
                save_json(user_response, FIXTURES_DIR / "user_search_response.json")

        # Fetch specific users if requested
        if args.users:
            print(f"\n=== Fetching {len(args.users)} users by PHID ===")
            user_response = fetcher.user_search(phids=args.users)
            save_json(user_response, FIXTURES_DIR / "user_search_by_phid.json")

        if args.usernames:
            print(f"\n=== Fetching {len(args.usernames)} users by username ===")
            user_response = fetcher.user_search(usernames=args.usernames)
            save_json(user_response, FIXTURES_DIR / "user_search_by_username.json")

        # Also fetch an error response example (invalid project)
        print("\n=== Fetching error/empty response example ===")
        empty_response = fetcher.project_search(slugs=["nonexistent-project-xyz123"])
        save_json(empty_response, FIXTURES_DIR / "project_not_found_response.json")

        print("\n=== Done! ===")
        print(f"Fixtures saved to: {FIXTURES_DIR}")

    except Exception as e:
        print(f"\nERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
