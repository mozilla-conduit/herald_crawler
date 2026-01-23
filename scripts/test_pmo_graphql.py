#!/usr/bin/env python3
"""
Test script to explore Mozilla People Directory API for GitHub username lookup.

This is a two-step process:
1. Get GitHub ID from Phabricator username via GraphQL API
2. Get GitHub username from GitHub ID via REST endpoint

Usage:
    export PEOPLE_MOZILLA_COOKIE="your-pmo-access-cookie-value"
    python scripts/test_pmo_graphql.py --username someuser

API Details:
    Step 1 - GraphQL (get GitHub ID):
        - Endpoint: https://people.mozilla.org/api/v4/graphql
        - Method: POST
        - Headers: Content-Type: application/json, Cookie: pmo-access=...
        - Returns: {"data": {"profile": {"identities": {"githubIdV3": {"value": "<id>"}}}}}

    Step 2 - REST (get GitHub username):
        - Endpoint: https://people.mozilla.org/whoami/github/username/{github_id}
        - Method: GET
        - Headers: Cookie: pmo-access=...
        - Returns: {"username": "<github-username>"}
"""

import argparse
import json
import os
import sys

import requests

PMO_GRAPHQL_URL = "https://people.mozilla.org/api/v4/graphql"
PMO_GITHUB_USERNAME_URL = "https://people.mozilla.org/whoami/github/username/{github_id}"

# Minimal query to get GitHub ID
GITHUB_ID_QUERY = """
query GetGitHubId($username: String) {
  profile(username: $username) {
    identities {
      githubIdV3 { value }
    }
  }
}
"""


def create_session(cookie: str) -> requests.Session:
    """Create a requests session with PMO cookie."""
    session = requests.Session()
    session.cookies.set("pmo-access", cookie, domain=".mozilla.org")
    return session


def query_github_id(session: requests.Session, username: str, verbose: bool = False) -> dict:
    """Step 1: Query the PMO GraphQL API for a user's GitHub ID.

    Args:
        session: Requests session with cookie
        username: Phabricator username to look up
        verbose: Print debug info

    Returns:
        JSON response from the API
    """
    headers = {
        "Content-Type": "application/json",
    }

    payload = {
        "operationName": "GetGitHubId",
        "variables": {"username": username},
        "query": GITHUB_ID_QUERY,
    }

    if verbose:
        print(f"Step 1: Get GitHub ID")
        print(f"POST {PMO_GRAPHQL_URL}")
        print(f"Payload: {json.dumps(payload, indent=2)}")
        print()

    response = session.post(PMO_GRAPHQL_URL, headers=headers, json=payload)

    if verbose:
        print(f"Status: {response.status_code}")
        print()

    response.raise_for_status()
    return response.json()


def query_github_username(session: requests.Session, github_id: str, verbose: bool = False) -> dict:
    """Step 2: Query the PMO REST API for GitHub username from ID.

    Args:
        session: Requests session with cookie
        github_id: GitHub numeric ID
        verbose: Print debug info

    Returns:
        JSON response from the API
    """
    url = PMO_GITHUB_USERNAME_URL.format(github_id=github_id)

    if verbose:
        print(f"Step 2: Get GitHub username")
        print(f"GET {url}")
        print()

    response = session.get(url)

    if verbose:
        print(f"Status: {response.status_code}")
        print()

    response.raise_for_status()
    return response.json()


def extract_github_id(response: dict) -> str | None:
    """Extract GitHub ID from GraphQL response.

    Args:
        response: JSON response from GraphQL API

    Returns:
        GitHub ID if found, None otherwise
    """
    try:
        profile = response.get("data", {}).get("profile")
        if not profile:
            return None

        identities = profile.get("identities", {})
        github_id = identities.get("githubIdV3", {})
        return github_id.get("value")
    except (KeyError, TypeError):
        return None


def extract_github_username(response: dict) -> str | None:
    """Extract GitHub username from REST response.

    Args:
        response: JSON response from REST API

    Returns:
        GitHub username if found, None otherwise
    """
    return response.get("username")


def main():
    parser = argparse.ArgumentParser(
        description="Test PMO API for GitHub username lookup (two-step process)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        "--username",
        required=True,
        help="Phabricator username to look up"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed request/response info"
    )
    parser.add_argument(
        "--save-dir",
        help="Save responses to directory (for fixtures)"
    )

    args = parser.parse_args()

    cookie = os.environ.get("PEOPLE_MOZILLA_COOKIE")
    if not cookie:
        print("ERROR: PEOPLE_MOZILLA_COOKIE environment variable not set")
        print("\nTo get your pmo-access cookie:")
        print("1. Log in to people.mozilla.org in your browser")
        print("2. Open Developer Tools > Application > Cookies")
        print("3. Copy the 'pmo-access' cookie value")
        print("4. export PEOPLE_MOZILLA_COOKIE='your-pmo-access-cookie-value'")
        sys.exit(1)

    print(f"Looking up GitHub username for: {args.username}")
    print()

    session = create_session(cookie)

    try:
        # Step 1: Get GitHub ID from Phabricator username
        graphql_response = query_github_id(session, args.username, verbose=args.verbose)

        print("Step 1 Response (GraphQL - GitHub ID):")
        print(json.dumps(graphql_response, indent=2))
        print()

        github_id = extract_github_id(graphql_response)

        if not github_id:
            profile = graphql_response.get("data", {}).get("profile")
            if profile:
                print("Result: User found but no GitHub ID linked")
            else:
                print("Result: User not found in People Directory")

            if args.save_dir:
                from pathlib import Path
                save_dir = Path(args.save_dir)
                save_dir.mkdir(parents=True, exist_ok=True)
                filepath = save_dir / f"{args.username}_graphql.json"
                with open(filepath, "w") as f:
                    json.dump(graphql_response, f, indent=2)
                print(f"\nGraphQL response saved to: {filepath}")
            return

        print(f"GitHub ID: {github_id}")
        print()

        # Step 2: Get GitHub username from GitHub ID
        rest_response = query_github_username(session, github_id, verbose=args.verbose)

        print("Step 2 Response (REST - GitHub username):")
        print(json.dumps(rest_response, indent=2))
        print()

        github_username = extract_github_username(rest_response)
        if github_username:
            print(f"Result: GitHub username = {github_username}")
        else:
            print("Result: Could not resolve GitHub username from ID")

        if args.save_dir:
            from pathlib import Path
            save_dir = Path(args.save_dir)
            save_dir.mkdir(parents=True, exist_ok=True)
            graphql_path = save_dir / f"{args.username}_graphql.json"
            rest_path = save_dir / f"{args.username}_rest.json"
            with open(graphql_path, "w") as f:
                json.dump(graphql_response, f, indent=2)
            with open(rest_path, "w") as f:
                json.dump(rest_response, f, indent=2)
            print(f"\nResponses saved to: {args.save_dir}/")

    except requests.exceptions.HTTPError as e:
        print(f"HTTP Error: {e}")
        print(f"Response: {e.response.text if e.response else 'N/A'}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
