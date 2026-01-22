"""Command-line interface for Herald scraper."""

import argparse
import logging
import sys

import requests

from herald_scraper.client import HeraldClient
from herald_scraper.crawler import HeraldCrawler
from herald_scraper.exceptions import AuthenticationError


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
        help="Maximum number of rules to extract",
    )
    parser.add_argument(
        "--all-rules",
        action="store_true",
        help="Extract all rules, not just global ones",
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

    args = parser.parse_args()
    setup_logging(args.verbose)

    logger = logging.getLogger(__name__)

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

        logger.info("Starting Herald rules extraction...")
        output = crawler.extract_all_rules(
            global_only=not args.all_rules,
            max_rules=args.max_rules,
        )

        json_output = output.model_dump_json(indent=2)

        if args.output:
            with open(args.output, "w") as f:
                f.write(json_output)
            logger.info(f"Output written to {args.output}")
        else:
            print(json_output)

        logger.info(f"Extracted {len(output.rules)} rules")
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
