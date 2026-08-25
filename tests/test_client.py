"""Tests for HeraldClient."""

import os
from unittest.mock import patch

import pytest
import requests
import responses

from herald_scraper.client import HeraldClient
from herald_scraper.exceptions import AuthenticationError, RateLimitError
from herald_scraper.rate_limit import MAX_RATE_LIMIT_RETRIES


class TestHeraldClientInit:
    """Tests for HeraldClient initialization."""

    def test_init_with_session_cookie(self) -> None:
        """Test initialization with a session cookie."""
        client = HeraldClient(
            base_url="https://phabricator.example.com",
            session_cookie="phsid=abc123",
            user_agent="TestAgent/1.0",
        )
        assert client.base_url == "https://phabricator.example.com"
        assert client.session_cookie == "phsid=abc123"
        assert client.user_agent == "TestAgent/1.0"

    def test_init_cookie_domain_extracted_from_url(self) -> None:
        """Test that session cookie is set with correct domain from base_url."""
        client = HeraldClient(
            base_url="https://phabricator.services.mozilla.com",
            session_cookie="abc123",
        )
        # Cookie should be set with parent domain
        cookie = client._session.cookies.get("phsid", domain=".services.mozilla.com")
        assert cookie == "abc123"

    def test_init_cookie_domain_with_subdomain(self) -> None:
        """Test cookie domain extraction for multi-level subdomains."""
        client = HeraldClient(
            base_url="https://phab.dev.example.org",
            session_cookie="xyz789",
        )
        # Cookie should use parent domain .dev.example.org
        cookie = client._session.cookies.get("phsid", domain=".dev.example.org")
        assert cookie == "xyz789"

    def test_init_cookie_domain_simple_domain(self) -> None:
        """Test cookie domain extraction for simple domain (no subdomain)."""
        client = HeraldClient(
            base_url="https://localhost",
            session_cookie="local123",
        )
        # For simple domains, use the domain as-is
        cookie = client._session.cookies.get("phsid", domain="localhost")
        assert cookie == "local123"

    def test_init_without_session_cookie(self) -> None:
        """Test initialization without a session cookie."""
        client = HeraldClient(
            base_url="https://phabricator.example.com",
        )
        assert client.base_url == "https://phabricator.example.com"
        assert client.session_cookie is None
        assert client.user_agent == "HeraldScraper/0.1"  # default

    def test_init_from_environment(self) -> None:
        """Test initialization from environment variables."""
        with patch.dict(
            os.environ,
            {
                "PHABRICATOR_URL": "https://phabricator.env.com",
                "PHABRICATOR_SESSION_COOKIE": "phsid=env123",
                "HERALD_SCRAPER_USER_AGENT": "EnvAgent/1.0",
            },
        ):
            client = HeraldClient.from_environment()
            assert client.base_url == "https://phabricator.env.com"
            assert client.session_cookie == "phsid=env123"
            assert client.user_agent == "EnvAgent/1.0"

    def test_init_from_environment_defaults(self) -> None:
        """Test initialization from environment with defaults."""
        with patch.dict(
            os.environ,
            {
                "PHABRICATOR_URL": "https://phabricator.env.com",
            },
            clear=True,
        ):
            client = HeraldClient.from_environment()
            assert client.base_url == "https://phabricator.env.com"
            assert client.session_cookie is None
            assert client.user_agent == "HeraldScraper/0.1"

    def test_init_invalid_url_raises_error(self) -> None:
        """Test that invalid URL raises ValueError."""
        with pytest.raises(ValueError, match="Invalid base_url"):
            HeraldClient(base_url="not-a-valid-url")

    def test_init_empty_url_raises_error(self) -> None:
        """Test that empty URL raises ValueError."""
        with pytest.raises(ValueError, match="Invalid base_url"):
            HeraldClient(base_url="")

    def test_from_environment_missing_url_raises_error(self) -> None:
        """Test that missing PHABRICATOR_URL raises ValueError."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(
                ValueError, match="PHABRICATOR_URL environment variable is required"
            ):
                HeraldClient.from_environment()

    def test_from_environment_invalid_timeout_raises_error(self) -> None:
        """Test that invalid HERALD_SCRAPER_TIMEOUT raises ValueError."""
        with patch.dict(
            os.environ,
            {
                "PHABRICATOR_URL": "https://phabricator.env.com",
                "HERALD_SCRAPER_TIMEOUT": "not-a-number",
            },
            clear=True,
        ):
            with pytest.raises(ValueError, match="HERALD_SCRAPER_TIMEOUT must be a number"):
                HeraldClient.from_environment()


class TestHeraldClientFetch:
    """Tests for HeraldClient fetch methods."""

    @responses.activate
    def test_fetch_page_success(self, listing_html: str) -> None:
        """Test successful page fetch."""
        responses.add(
            responses.GET,
            "https://phabricator.example.com/herald/",
            body=listing_html,
            status=200,
        )

        client = HeraldClient(base_url="https://phabricator.example.com")
        html = client.fetch_page("/herald/")

        assert html == listing_html
        assert len(responses.calls) == 1

    @responses.activate
    def test_fetch_page_auth_failure(self) -> None:
        """Test page fetch with authentication failure."""
        responses.add(
            responses.GET,
            "https://phabricator.example.com/H420",
            body="<html><body>Login required</body></html>",
            status=302,
            headers={"Location": "/auth/start/"},
        )

        client = HeraldClient(base_url="https://phabricator.example.com")

        with pytest.raises(AuthenticationError):
            client.fetch_page("/H420")

    @responses.activate
    def test_fetch_page_does_not_pace_requests(self) -> None:
        """Successive fetches run back to back, with no sleep between them."""
        for page in ("page1", "page2"):
            responses.add(
                responses.GET,
                f"https://phabricator.example.com/{page}",
                body=f"<html>{page}</html>",
                status=200,
            )

        client = HeraldClient(base_url="https://phabricator.example.com")

        with patch("herald_scraper.rate_limit.time.sleep") as sleep:
            client.fetch_page("/page1")
            client.fetch_page("/page2")

        assert len(responses.calls) == 2
        sleep.assert_not_called()

    @responses.activate
    def test_fetch_page_waits_out_rate_limit_then_retries(self) -> None:
        """A 429 with retry-after is waited out and the fetch retried."""
        responses.add(
            responses.GET,
            "https://phabricator.example.com/H420",
            body="slow down",
            status=429,
            headers={"retry-after": "7"},
        )
        responses.add(
            responses.GET,
            "https://phabricator.example.com/H420",
            body="<html>ok</html>",
            status=200,
        )

        client = HeraldClient(base_url="https://phabricator.example.com")

        with patch("herald_scraper.rate_limit.time.sleep") as sleep:
            assert client.fetch_page("/H420") == "<html>ok</html>"

        sleep.assert_called_once_with(7.0)

    @responses.activate
    def test_fetch_page_gives_up_after_max_retries(self) -> None:
        """A rate limit that never clears surfaces as an error, not a bad page."""
        for _ in range(MAX_RATE_LIMIT_RETRIES + 1):
            responses.add(
                responses.GET,
                "https://phabricator.example.com/H420",
                body="slow down",
                status=429,
                headers={"retry-after": "1"},
            )

        client = HeraldClient(base_url="https://phabricator.example.com")

        with patch("herald_scraper.rate_limit.time.sleep"):
            with pytest.raises(RateLimitError):
                client.fetch_page("/H420")

        assert len(responses.calls) == MAX_RATE_LIMIT_RETRIES + 1

    @responses.activate
    def test_fetch_page_plain_403_is_not_retried(self) -> None:
        """A 403 with no rate-limit signal is a hard failure, not a wait."""
        responses.add(
            responses.GET,
            "https://phabricator.example.com/H420",
            body="nope",
            status=403,
        )

        client = HeraldClient(base_url="https://phabricator.example.com")

        with patch("herald_scraper.rate_limit.time.sleep") as sleep:
            with pytest.raises(requests.HTTPError):
                client.fetch_page("/H420")

        assert len(responses.calls) == 1
        sleep.assert_not_called()

    @responses.activate
    def test_fetch_listing(self, listing_html: str) -> None:
        """Test fetching the Herald listing page."""
        responses.add(
            responses.GET,
            "https://phabricator.example.com/herald/query/all/",
            body=listing_html,
            status=200,
        )

        client = HeraldClient(base_url="https://phabricator.example.com")
        html = client.fetch_listing()

        assert html == listing_html
        assert "/herald/query/all/" in responses.calls[0].request.url

    @responses.activate
    def test_fetch_rule(self, rule_h420_html: str) -> None:
        """Test fetching a specific rule page."""
        responses.add(
            responses.GET,
            "https://phabricator.example.com/H420",
            body=rule_h420_html,
            status=200,
        )

        client = HeraldClient(base_url="https://phabricator.example.com")
        html = client.fetch_rule("H420")

        assert html == rule_h420_html
        assert "/H420" in responses.calls[0].request.url

    @responses.activate
    def test_fetch_project(self) -> None:
        """Test fetching a project page."""
        project_html = "<html><body>Project Page</body></html>"
        responses.add(
            responses.GET,
            "https://phabricator.example.com/tag/my-project/",
            body=project_html,
            status=200,
        )

        client = HeraldClient(base_url="https://phabricator.example.com")
        html = client.fetch_project("my-project")

        assert html == project_html
        assert "/tag/my-project/" in responses.calls[0].request.url
