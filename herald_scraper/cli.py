"""Command-line interface for Herald scraper."""

import argparse
import logging
import os
import sys

import requests

from herald_scraper.client import HeraldClient
from herald_scraper.conduit_client import ConduitClient
from herald_scraper.crawler import (
    HeraldCrawler,
    atomic_write_json,
    load_existing_output,
    load_manual_github_mapping,
)
from herald_scraper.exceptions import AuthenticationError
from herald_scraper.people_client import PeopleDirectoryClient
from herald_scraper.resolvers import REVIEW_GROUPS_TABLE, StmoGroupCollector
from herald_scraper.stmo_client import (
    DEFAULT_DATA_SOURCE,
    DEFAULT_STMO_URL,
    StmoClient,
    StmoError,
)


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
    parser = argparse.ArgumentParser(description="Extract Herald rules from Phabricator")
    parser.add_argument(
        "--url",
        help="Phabricator instance URL (or set PHABRICATOR_URL env var)",
    )
    parser.add_argument(
        "--phab-cookie",
        help="Phabricator session cookie for authenticating HTML scraping "
        "(or set PHABRICATOR_SESSION_COOKIE env var)",
    )
    parser.add_argument(
        "--output",
        "-o",
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
        "--timeout",
        type=float,
        default=30.0,
        help="Request timeout in seconds (default: 30.0)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    # Resume/force options
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Resume from existing output file: rules are re-fetched and updated, "
            "already-resolved groups and GitHub users are reused"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore existing output file and start fresh",
    )
    parser.add_argument(
        "--input",
        "-i",
        help="Input file to resume from (defaults to output file if --resume is used)",
    )

    # Conduit API option (cross-checks GitHub resolution against Phabricator)
    parser.add_argument(
        "--conduit-token",
        help="Conduit API token used to cross-check GitHub resolution against "
        "Phabricator's Bugzilla account and real name data "
        "(or set PHABRICATOR_CONDUIT_TOKEN env var)",
    )

    # STMO options (source of reviewer group membership)
    parser.add_argument(
        "--stmo-api-key",
        help="Redash API key for sql.telemetry.mozilla.org, the source of reviewer "
        "group membership (or set REDASH_API_KEY env var). Without it, groups "
        "are not collected.",
    )
    parser.add_argument(
        "--stmo-url",
        help=f"Redash instance URL (or set REDASH_URL env var, default: {DEFAULT_STMO_URL})",
    )
    parser.add_argument(
        "--stmo-data-source",
        help="STMO data source hosting the review groups table, as a name or a "
        f"numeric ID (or set STMO_DATA_SOURCE env var, default: {DEFAULT_DATA_SOURCE!r})",
    )
    parser.add_argument(
        "--stmo-table",
        default=REVIEW_GROUPS_TABLE,
        help=f"Review groups table to query (default: {REVIEW_GROUPS_TABLE})",
    )
    parser.add_argument(
        "--stmo-query-timeout",
        type=float,
        default=300.0,
        help="Seconds to wait for the STMO query to finish (default: 300.0)",
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
    parser.add_argument(
        "--github-user-mapping",
        help="Path to a JSON file of Phabricator -> GitHub username overrides. "
        "Entries bypass API resolution and win over the automatic path. "
        "See crawler.load_manual_github_mapping for the accepted format.",
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
                session_cookie=args.phab_cookie,
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

        # Set up Conduit client to cross-check GitHub resolution
        conduit_client = None
        conduit_token = args.conduit_token or os.environ.get("PHABRICATOR_CONDUIT_TOKEN")
        if conduit_token:
            base_url = args.url or os.environ.get("PHABRICATOR_URL", "")
            if base_url:
                conduit_client = ConduitClient(
                    base_url=base_url,
                    api_token=conduit_token,
                    timeout=args.timeout,
                )
                logger.info("Using Conduit API to cross-check GitHub resolution")
            else:
                logger.warning(
                    "Conduit token provided but no Phabricator URL. "
                    "GitHub resolution will not be cross-checked against Phabricator."
                )

        # Set up STMO client for reviewer group membership
        stmo_collector = None
        if args.stmo_api_key or os.environ.get("REDASH_API_KEY"):
            stmo_client = StmoClient.from_environment(
                api_key=args.stmo_api_key,
                data_source=args.stmo_data_source,
                base_url=args.stmo_url,
                timeout=args.timeout,
                poll_timeout=args.stmo_query_timeout,
            )
            stmo_collector = StmoGroupCollector(stmo_client, table=args.stmo_table)
            logger.info(f"Collecting reviewer groups from {args.stmo_table} on STMO")

        # Set up People Directory client for GitHub resolution (enabled by default)
        people_client = None
        if not args.no_resolve_github:
            pmo_cookie = args.pmo_cookie or os.environ.get("PEOPLE_MOZILLA_COOKIE")
            if pmo_cookie:
                people_client = PeopleDirectoryClient(cookie=pmo_cookie)
                logger.info("GitHub username resolution enabled")
            else:
                logger.warning(
                    "No PMO cookie: GitHub usernames will only come from "
                    "--github-user-mapping, and every other user will be reported as "
                    "unresolved. Set PEOPLE_MOZILLA_COOKIE or use --pmo-cookie to "
                    "resolve them."
                )

        manual_github_mapping = None
        if args.github_user_mapping:
            manual_github_mapping = load_manual_github_mapping(args.github_user_mapping)
            logger.info(
                f"Loaded {len(manual_github_mapping)} manual GitHub overrides "
                f"from {args.github_user_mapping}"
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
            manual_github_mapping=manual_github_mapping,
            stmo_collector=stmo_collector,
            resolve_github=not args.no_resolve_github,
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
    except StmoError as e:
        logger.error(f"STMO query failed: {e}")
        return 3
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
