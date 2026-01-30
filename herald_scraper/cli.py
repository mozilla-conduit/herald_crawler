"""Command-line interface for Herald scraper."""

import argparse
import logging
import os
import sys

import requests

from herald_scraper.client import HeraldClient
from herald_scraper.conduit_client import ConduitClient
from herald_scraper.crawler import HeraldCrawler, atomic_write_json, load_existing_output
from herald_scraper.exceptions import AuthenticationError
from herald_scraper.people_client import PeopleDirectoryClient


def setup_logging(verbose: bool = False) -> None:
    """
    Configure logging for the CLI.

    Args:
        verbose: If True, enable debug logging
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
    )


def main() -> int:
    """
    Main entry point for the CLI.

    Returns:
        Exit code:
            0 - Success
            1 - Unexpected error
            2 - Authentication error
            3 - Network error
            4 - Configuration error
            130 - Interrupted by user
    """
    parser = argparse.ArgumentParser(
        description="Extract Herald rules from Phabricator"
    )
    parser.add_argument(
        "--url",
        help="Phabricator instance URL (or set PHABRICATOR_URL env var)",
    )
    parser.add_argument(
        "--cookie",
        help="Session cookie for authentication (or set PHABRICATOR_SESSION_COOKIE env var)",
    )
    parser.add_argument(
        "--output", "-o",
        help="Output file (default: stdout)",
    )
    parser.add_argument(
        "--max-rules",
        type=int,
        help="Maximum number of rules to extract (stops fetching pages early)",
    )
    parser.add_argument(
        "--max-groups",
        type=int,
        help="Maximum number of reviewer groups to collect (stops collecting early)",
    )
    parser.add_argument(
        "--all-rules",
        action="store_true",
        help="Extract all rules, not just global ones",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=100,
        help="Maximum number of listing pages to fetch (default: 100)",
    )
    parser.add_argument(
        "--single-page",
        action="store_true",
        help="Only fetch the first page of rules (equivalent to --max-pages 1)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Delay between requests in seconds (default: 1.0)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Request timeout in seconds (default: 30.0)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    # Resume/force options
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from existing output file, skipping already-scraped items",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore existing output file and start fresh",
    )
    parser.add_argument(
        "--input", "-i",
        help="Input file to resume from (defaults to output file if --resume is used)",
    )

    # Conduit API option (alternative to HTML scraping for groups)
    parser.add_argument(
        "--conduit-token",
        help="Conduit API token for group membership (or set PHABRICATOR_CONDUIT_TOKEN env var). "
             "Preferred over HTML scraping.",
    )

    # GitHub username resolution options
    parser.add_argument(
        "--no-resolve-github",
        action="store_true",
        help="Skip resolving Phabricator usernames to GitHub usernames",
    )
    parser.add_argument(
        "--pmo-cookie",
        help="People Mozilla access cookie (or set PEOPLE_MOZILLA_COOKIE env var)",
    )
    parser.add_argument(
        "--max-users",
        type=int,
        help="Maximum number of users to resolve GitHub usernames for",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    logger = logging.getLogger(__name__)

    # Validate conflicting options
    if args.resume and args.force:
        logger.error("Cannot use both --resume and --force")
        return 4

    try:
        if args.url:
            client = HeraldClient(
                base_url=args.url,
                session_cookie=args.cookie,
                delay=args.delay,
                timeout=args.timeout,
            )
        else:
            client = HeraldClient.from_environment()

        def progress_callback(current: int, total: int, message: str) -> None:
            logger.info(f"[{current}/{total}] {message}")

        crawler = HeraldCrawler(client=client, progress_callback=progress_callback)

        max_pages = 1 if args.single_page else args.max_pages

        # Load existing output for resume
        existing_output = None
        if args.resume or args.input:
            input_file = args.input or args.output
            if input_file:
                existing_output = load_existing_output(input_file)
                if existing_output:
                    logger.info(f"Resuming from {input_file}")
                elif args.resume:
                    logger.info(f"No existing output at {input_file}, starting fresh")
            elif args.resume:
                logger.warning("--resume specified but no output file given, starting fresh")

        # Set up Conduit client for group membership (preferred over HTML scraping)
        conduit_client = None
        conduit_token = args.conduit_token or os.environ.get("PHABRICATOR_CONDUIT_TOKEN")
        if conduit_token:
            base_url = args.url or os.environ.get("PHABRICATOR_URL", "")
            if base_url:
                conduit_client = ConduitClient(
                    base_url=base_url,
                    api_token=conduit_token,
                    delay=args.delay,
                    timeout=args.timeout,
                )
                logger.info("Using Conduit API for group membership collection")
            else:
                logger.warning(
                    "Conduit token provided but no Phabricator URL. "
                    "Falling back to HTML scraping for groups."
                )

        # Set up People Directory client for GitHub resolution (enabled by default)
        people_client = None
        if not args.no_resolve_github:
            pmo_cookie = args.pmo_cookie or os.environ.get("PEOPLE_MOZILLA_COOKIE")
            if pmo_cookie:
                people_client = PeopleDirectoryClient(cookie=pmo_cookie, delay=args.delay)
                logger.info("GitHub username resolution enabled")
            else:
                logger.warning(
                    "GitHub resolution skipped: no PMO cookie available. "
                    "Set PEOPLE_MOZILLA_COOKIE env var or use --pmo-cookie"
                )

        logger.info("Starting Herald rules extraction...")
        output = crawler.extract_all_rules(
            global_only=not args.all_rules,
            max_rules=args.max_rules,
            max_pages=max_pages,
            max_groups=args.max_groups,
            people_client=people_client,
            max_users=args.max_users,
            existing_output=existing_output,
            conduit_client=conduit_client,
        )

        if args.output:
            atomic_write_json(args.output, output)
            logger.info(f"Output written to {args.output}")
        else:
            json_output = output.model_dump_json(indent=2, exclude_none=True)
            print(json_output)

        logger.info(f"Extracted {len(output.rules)} rules, {len(output.groups)} groups")
        if output.github_users:
            logger.info(
                f"Resolved {len(output.github_users)} GitHub usernames, "
                f"{len(output.unresolved_users)} unresolved"
            )
        if output.metadata and output.metadata.scrape_status:
            status = output.metadata.scrape_status
            logger.info(
                f"Scrape status: rules_complete={status.rules_complete}, "
                f"groups_complete={status.groups_complete}, github_complete={status.github_complete}"
            )
        return 0

    except AuthenticationError as e:
        logger.error(f"Authentication failed: {e}")
        logger.error("Please check your PHABRICATOR_SESSION_COOKIE environment variable")
        return 2
    except requests.RequestException as e:
        logger.error(f"Network error: {e}")
        url = args.url or "PHABRICATOR_URL"
        logger.error(f"Could not connect to {url}")
        return 3
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        return 4
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 130
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
