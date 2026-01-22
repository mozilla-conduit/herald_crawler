"""Tests for HeraldClient."""

import os
from unittest.mock import patch

import pytest
import responses

from herald_scraper.client import HeraldClient
from herald_scraper.exceptions import AuthenticationError


class TestHeraldClientInit:
    """Tests for HeraldClient initialization."""

    def test_init_with_session_cookie(self) -> None:
        """Test initialization with a session cookie."""
        client = HeraldClient(
            base_url="https://phabricator.example.com",
            session_cookie="phsid=abc123",
            delay=0.5,
            user_agent="TestAgent/1.0",
        )
        assert client.base_url == "https://phabricator.example.com"
        assert client.session_cookie == "phsid=abc123"
        assert client.delay == 0.5
        assert client.user_agent == "TestAgent/1.0"

    def test_init_without_session_cookie(self) -> None:
        """Test initialization without a session cookie."""
        client = HeraldClient(
            base_url="https://phabricator.example.com",
        )
        assert client.base_url == "https://phabricator.example.com"
        assert client.session_cookie is None
        assert client.delay == 1.0  # default
        assert client.user_agent == "HeraldScraper/0.1"  # default

    def test_init_from_environment(self) -> None:
        """Test initialization from environment variables."""
        with patch.dict(
            os.environ,
            {
                "PHABRICATOR_URL": "https://phabricator.env.com",
                "PHABRICATOR_SESSION_COOKIE": "phsid=env123",
                "HERALD_SCRAPER_DELAY": "2.0",
                "HERALD_SCRAPER_USER_AGENT": "EnvAgent/1.0",
            },
        ):
            client = HeraldClient.from_environment()
            assert client.base_url == "https://phabricator.env.com"
            assert client.session_cookie == "phsid=env123"
            assert client.delay == 2.0
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
            assert client.delay == 1.0
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
            with pytest.raises(ValueError, match="PHABRICATOR_URL environment variable is required"):
                HeraldClient.from_environment()

    def test_from_environment_invalid_delay_raises_error(self) -> None:
        """Test that invalid HERALD_SCRAPER_DELAY raises ValueError."""
        with patch.dict(
            os.environ,
            {
                "PHABRICATOR_URL": "https://phabricator.env.com",
                "HERALD_SCRAPER_DELAY": "not-a-number",
            },
            clear=True,
        ):
            with pytest.raises(ValueError, match="HERALD_SCRAPER_DELAY must be a number"):
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
    def test_fetch_page_rate_limiting(self) -> None:
        """Test that requests respect rate limiting delay."""
        responses.add(
            responses.GET,
            "https://phabricator.example.com/page1",
            body="<html>page1</html>",
            status=200,
        )
        responses.add(
            responses.GET,
            "https://phabricator.example.com/page2",
            body="<html>page2</html>",
            status=200,
        )

        client = HeraldClient(
            base_url="https://phabricator.example.com",
            delay=0.1,
        )

        client.fetch_page("/page1")
        client.fetch_page("/page2")

        # Both requests should succeed
        assert len(responses.calls) == 2

    @responses.activate
    def test_fetch_listing(self, listing_html: str) -> None:
        """Test fetching the Herald listing page."""
        responses.add(
            responses.GET,
            "https://phabricator.example.com/herald/",
            body=listing_html,
            status=200,
        )

        client = HeraldClient(base_url="https://phabricator.example.com")
        html = client.fetch_listing()

        assert html == listing_html
        assert "/herald/" in responses.calls[0].request.url

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
