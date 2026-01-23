#!/usr/bin/env python3
"""
Resolve Phabricator usernames to GitHub usernames via Mozilla People Directory.

This script takes a list of Phabricator usernames and resolves them to GitHub
usernames using the two-step PMO API. Results are cached to avoid repeated
lookups, and unresolved users are tracked separately.

Usage:
    export PEOPLE_MOZILLA_COOKIE="your-pmo-access-cookie-value"

    # Resolve usernames from command line
    python scripts/resolve_github_usernames.py --usernames user1 user2 user3

    # Resolve usernames from a file (one per line)
    python scripts/resolve_github_usernames.py --file usernames.txt

    # Use custom cache file
    python scripts/resolve_github_usernames.py --usernames someuser --cache my_cache.json

Output files:
    - github_usernames_cache.json: Cached resolved usernames with timestamps
    - unresolved_github_users.json: Users that couldn't be resolved (not found, no GitHub)
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add the project root to path so we can import herald_scraper
sys.path.insert(0, str(Path(__file__).parent.parent))

from herald_scraper.people_client import PeopleDirectoryClient


class GitHubUsernameResolver:
    """Batch resolver for Phabricator to GitHub username resolution."""

    def __init__(
        self,
        client: PeopleDirectoryClient,
        cache_file: Path,
        unresolved_file: Path,
        delay: float = 0.5,
    ) -> None:
        """Initialize the resolver.

        Args:
            client: PeopleDirectoryClient for API calls
            cache_file: Path to cache file for resolved usernames
            unresolved_file: Path to file for tracking unresolved users
            delay: Delay between API requests in seconds
        """
        self.client = client
        self.cache_file = cache_file
        self.unresolved_file = unresolved_file
        self.delay = delay
        self._cache = self._load_cache()
        self._unresolved = self._load_unresolved()

    def _load_cache(self) -> dict:
        """Load cache from file."""
        if self.cache_file.exists():
            with open(self.cache_file) as f:
                return json.load(f)
        return {"usernames": {}, "metadata": {"created": datetime.now().isoformat()}}

    def _load_unresolved(self) -> dict:
        """Load unresolved users from file."""
        if self.unresolved_file.exists():
            with open(self.unresolved_file) as f:
                return json.load(f)
        return {"users": {}, "metadata": {"created": datetime.now().isoformat()}}

    def _save_cache(self) -> None:
        """Save cache to file."""
        self._cache["metadata"]["updated"] = datetime.now().isoformat()
        with open(self.cache_file, "w") as f:
            json.dump(self._cache, f, indent=2)

    def _save_unresolved(self) -> None:
        """Save unresolved users to file."""
        self._unresolved["metadata"]["updated"] = datetime.now().isoformat()
        with open(self.unresolved_file, "w") as f:
            json.dump(self._unresolved, f, indent=2)

    def get_cached(self, username: str) -> Optional[str]:
        """Get cached GitHub username if available.

        Args:
            username: Phabricator username

        Returns:
            Cached GitHub username or None if not cached
        """
        entry = self._cache["usernames"].get(username)
        if entry:
            return entry.get("github_username")
        return None

    def is_unresolved(self, username: str) -> bool:
        """Check if username was previously marked as unresolved.

        Args:
            username: Phabricator username

        Returns:
            True if the username is in the unresolved list
        """
        return username in self._unresolved["users"]

    def resolve(self, username: str, force: bool = False) -> Optional[str]:
        """Resolve a single username.

        Args:
            username: Phabricator username to resolve
            force: If True, bypass cache and re-fetch

        Returns:
            GitHub username if resolved, None otherwise
        """
        # Check cache first
        if not force:
            cached = self.get_cached(username)
            if cached:
                return cached

            # Skip previously unresolved users unless forced
            if self.is_unresolved(username):
                return None

        # Fetch from API
        try:
            github_username = self.client.resolve_github_username(username)
            time.sleep(self.delay)

            if github_username:
                # Cache the result
                self._cache["usernames"][username] = {
                    "github_username": github_username,
                    "resolved_at": datetime.now().isoformat(),
                }
                # Remove from unresolved if it was there
                if username in self._unresolved["users"]:
                    del self._unresolved["users"][username]
                return github_username
            else:
                # Track as unresolved
                self._unresolved["users"][username] = {
                    "reason": "no_github_linked_or_not_found",
                    "checked_at": datetime.now().isoformat(),
                }
                return None

        except Exception as e:
            # Track as unresolved with error
            self._unresolved["users"][username] = {
                "reason": f"error: {str(e)}",
                "checked_at": datetime.now().isoformat(),
            }
            return None

    def resolve_batch(
        self,
        usernames: list[str],
        force: bool = False,
        verbose: bool = True,
    ) -> dict[str, Optional[str]]:
        """Resolve a batch of usernames.

        Args:
            usernames: List of Phabricator usernames
            force: If True, bypass cache and re-fetch all
            verbose: If True, print progress

        Returns:
            Dict mapping Phabricator usernames to GitHub usernames (or None)
        """
        results = {}
        stats = {"cached": 0, "resolved": 0, "unresolved": 0, "skipped": 0, "errors": 0}

        for i, username in enumerate(usernames, 1):
            if verbose:
                print(f"[{i}/{len(usernames)}] {username}...", end=" ", flush=True)

            # Check cache
            if not force:
                cached = self.get_cached(username)
                if cached:
                    results[username] = cached
                    stats["cached"] += 1
                    if verbose:
                        print(f"(cached) -> {cached}")
                    continue

                if self.is_unresolved(username):
                    results[username] = None
                    stats["skipped"] += 1
                    if verbose:
                        reason = self._unresolved["users"][username].get("reason", "unknown")
                        print(f"(skipped - {reason})")
                    continue

            # Resolve
            github_username = self.resolve(username, force=force)
            results[username] = github_username

            if github_username:
                stats["resolved"] += 1
                if verbose:
                    print(f"-> {github_username}")
            else:
                reason = self._unresolved["users"].get(username, {}).get("reason", "unknown")
                if "error" in reason:
                    stats["errors"] += 1
                else:
                    stats["unresolved"] += 1
                if verbose:
                    print(f"-> (unresolved: {reason})")

        # Save results
        self._save_cache()
        self._save_unresolved()

        if verbose:
            print()
            print("=== Summary ===")
            print(f"  From cache:   {stats['cached']}")
            print(f"  Resolved:     {stats['resolved']}")
            print(f"  Unresolved:   {stats['unresolved']}")
            print(f"  Skipped:      {stats['skipped']}")
            print(f"  Errors:       {stats['errors']}")
            print(f"\nCache saved to: {self.cache_file}")
            print(f"Unresolved saved to: {self.unresolved_file}")

        return results


def main():
    parser = argparse.ArgumentParser(
        description="Resolve Phabricator usernames to GitHub usernames",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--usernames",
        nargs="+",
        help="Phabricator usernames to resolve",
    )
    parser.add_argument(
        "--file",
        help="File containing usernames (one per line)",
    )
    parser.add_argument(
        "--cache",
        default="github_usernames_cache.json",
        help="Cache file path (default: github_usernames_cache.json)",
    )
    parser.add_argument(
        "--unresolved",
        default="unresolved_github_users.json",
        help="Unresolved users file path (default: unresolved_github_users.json)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay between API requests in seconds (default: 0.5)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-fetch even if cached",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Output file for results (optional, prints to stdout if not specified)",
    )

    args = parser.parse_args()

    # Collect usernames
    usernames = []
    if args.usernames:
        usernames.extend(args.usernames)
    if args.file:
        with open(args.file) as f:
            usernames.extend(line.strip() for line in f if line.strip() and not line.startswith("#"))

    if not usernames:
        print("ERROR: Must specify --usernames or --file")
        parser.print_help()
        sys.exit(1)

    # Deduplicate while preserving order
    seen = set()
    unique_usernames = []
    for u in usernames:
        if u not in seen:
            seen.add(u)
            unique_usernames.append(u)
    usernames = unique_usernames

    # Check for cookie
    cookie = os.environ.get("PEOPLE_MOZILLA_COOKIE")
    if not cookie:
        print("ERROR: PEOPLE_MOZILLA_COOKIE environment variable not set")
        print("\nTo get your pmo-access cookie:")
        print("1. Log in to people.mozilla.org in your browser")
        print("2. Open Developer Tools > Application > Cookies")
        print("3. Copy the 'pmo-access' cookie value")
        print("4. export PEOPLE_MOZILLA_COOKIE='your-pmo-access-cookie-value'")
        sys.exit(1)

    # Initialize client and resolver
    client = PeopleDirectoryClient(cookie=cookie, delay=args.delay)
    resolver = GitHubUsernameResolver(
        client=client,
        cache_file=Path(args.cache),
        unresolved_file=Path(args.unresolved),
        delay=args.delay,
    )

    print(f"Resolving {len(usernames)} usernames")
    print(f"Cache file: {args.cache}")
    print(f"Unresolved file: {args.unresolved}")
    print()

    # Resolve
    results = resolver.resolve_batch(usernames, force=args.force, verbose=True)

    # Output results
    if args.output:
        output_data = {
            "resolved": {k: v for k, v in results.items() if v is not None},
            "unresolved": [k for k, v in results.items() if v is None],
        }
        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2)
        print(f"\nResults written to: {args.output}")


if __name__ == "__main__":
    main()
