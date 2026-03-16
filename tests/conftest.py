"""Shared pytest fixtures for Herald scraper tests."""

from pathlib import Path
from typing import Callable

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_path() -> Path:
    """Return the path to the fixtures directory."""
    return FIXTURES_DIR


@pytest.fixture
def load_fixture() -> Callable[[str], str]:
    """Return a function that loads fixture files."""

    def _load_fixture(filename: str) -> str:
        """
        Load a fixture file by name.

        Args:
            filename: Path relative to fixtures directory (e.g., 'rules/listing.html')

        Returns:
            Content of the fixture file

        Raises:
            FileNotFoundError: If fixture file doesn't exist, with helpful message
        """
        fixture_path = FIXTURES_DIR / filename
        try:
            return fixture_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Fixture file not found: {fixture_path}\n"
                f"Expected fixture at: {fixture_path.absolute()}\n"
                f"Make sure you've run: python scripts/fetch_fixtures.py"
            ) from None

    return _load_fixture


@pytest.fixture
def listing_html(load_fixture: Callable[[str], str]) -> str:
    """Load the Herald rules listing page fixture."""
    return load_fixture("rules/listing.html")


@pytest.fixture
def rule_h420_html(load_fixture: Callable[[str], str]) -> str:
    """Load the H420 rule page fixture."""
    return load_fixture("rules/rule_H420.html")


@pytest.fixture
def rule_h422_html(load_fixture: Callable[[str], str]) -> str:
    """Load the H422 rule page fixture."""
    return load_fixture("rules/rule_H422.html")


@pytest.fixture
def rule_h425_html(load_fixture: Callable[[str], str]) -> str:
    """Load the H425 rule page fixture."""
    return load_fixture("rules/rule_H425.html")


@pytest.fixture
def rule_h432_html(load_fixture: Callable[[str], str]) -> str:
    """Load the H432 rule page fixture."""
    return load_fixture("rules/rule_H432.html")


@pytest.fixture
def rule_h483_html(load_fixture: Callable[[str], str]) -> str:
    """Load the H483 rule page fixture."""
    return load_fixture("rules/rule_H483.html")


@pytest.fixture
def rule_h507_html(load_fixture: Callable[[str], str]) -> str:
    """Load the H507 rule page fixture."""
    return load_fixture("rules/rule_H507.html")
