"""Tests for Herald page parsers."""

import pytest
from pathlib import Path

from herald_scraper.parsers import (
    ListingPageParser,
    RuleDetailPageParser,
    ProjectPageParser
)


FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestListingPageParser:
    """Tests for ListingPageParser."""

    @pytest.fixture
    def listing_html(self):
        """Load the listing page fixture."""
        listing_path = FIXTURES_DIR / "rules" / "listing.html"
        if not listing_path.exists():
            pytest.skip("Listing fixture not found")
        return listing_path.read_text()

    @pytest.fixture
    def parser(self, listing_html):
        """Create a parser instance."""
        return ListingPageParser(listing_html)

    def test_extract_rule_ids(self, parser):
        """Test extracting rule IDs from the listing page."""
        rule_ids = parser.extract_rule_ids()

        # Should find multiple rules
        assert len(rule_ids) > 0

        # Rule IDs should be in format H###
        for rule_id in rule_ids:
            assert rule_id.startswith("H")
            assert rule_id[1:].isdigit()

        # Should be sorted numerically
        numbers = [int(r[1:]) for r in rule_ids]
        assert numbers == sorted(numbers)

    def test_extract_rule_ids_returns_expected_range(self, parser):
        """Test that extracted rule IDs are in expected range."""
        rule_ids = parser.extract_rule_ids()

        # Based on our analysis, we expect rules in the H4xx-H5xx range
        numbers = [int(r[1:]) for r in rule_ids]
        assert min(numbers) >= 400
        assert max(numbers) < 600

    def test_filter_global_rules(self, parser):
        """Test filtering for global rules only."""
        all_rule_ids = parser.extract_rule_ids()
        global_rules = parser.filter_global_rules(all_rule_ids)

        # Should return fewer rules than the full list (personal rules filtered out)
        assert len(global_rules) > 0
        assert len(global_rules) < len(all_rule_ids)

        # All returned rules should be in the original list
        for rule_id in global_rules:
            assert rule_id in all_rule_ids

    def test_has_next_page_with_pagination(self, parser):
        """Test that has_next_page returns True when pagination exists."""
        # The listing fixture has pagination with after=417
        assert parser.has_next_page() is True

    def test_get_next_page_url_with_pagination(self, parser):
        """Test extracting next page URL when pagination exists."""
        next_url = parser.get_next_page_url()

        assert next_url is not None
        assert "after=" in next_url
        assert next_url == "/herald/query/all/?after=417"

    def test_has_next_page_no_pagination(self):
        """Test that has_next_page returns False when no pagination."""
        # Create a minimal HTML without pager
        html = """
        <html><body>
            <a href="/H100">H100</a>
            <a href="/H101">H101</a>
        </body></html>
        """
        parser = ListingPageParser(html)
        assert parser.has_next_page() is False

    def test_get_next_page_url_no_pagination(self):
        """Test that get_next_page_url returns None when no pagination."""
        html = """
        <html><body>
            <a href="/H100">H100</a>
        </body></html>
        """
        parser = ListingPageParser(html)
        assert parser.get_next_page_url() is None

    def test_has_next_page_last_page(self):
        """Test has_next_page returns False on last page (pager without after link)."""
        # Pager exists but only has a "Previous" link (before= parameter)
        html = """
        <html><body>
            <div class="phui-pager-view">
                <a href="/herald/query/all/?before=100">Previous</a>
            </div>
        </body></html>
        """
        parser = ListingPageParser(html)
        assert parser.has_next_page() is False


class TestRuleDetailPageParser:
    """Tests for RuleDetailPageParser."""

    @pytest.fixture
    def rule_fixtures(self):
        """Get all rule fixture files."""
        rules_dir = FIXTURES_DIR / "rules"
        if not rules_dir.exists():
            pytest.skip("Rule fixtures not found")

        rule_files = list(rules_dir.glob("rule_*.html"))
        if not rule_files:
            pytest.skip("No rule fixture files found")

        return rule_files

    @pytest.fixture
    def sample_rule_html(self, rule_fixtures):
        """Load a sample rule fixture."""
        # Use the first rule fixture
        return rule_fixtures[0].read_text()

    @pytest.fixture
    def parser(self, sample_rule_html):
        """Create a parser instance."""
        return RuleDetailPageParser(sample_rule_html)

    def test_parser_initialization(self, sample_rule_html):
        """Test parser initializes correctly."""
        parser = RuleDetailPageParser(sample_rule_html)
        assert parser.html == sample_rule_html
        assert parser.soup is not None

    def test_extract_rule_id(self, parser):
        """Test extracting rule ID from the page."""
        rule_id = parser._extract_rule_id()

        assert rule_id is not None
        assert rule_id.startswith("H")
        assert rule_id[1:].isdigit()

    def test_extract_rule_name(self, parser):
        """Test extracting rule name from the page."""
        rule_name = parser._extract_rule_name()

        assert rule_name is not None
        assert len(rule_name) > 0
        # Should not contain the ☿ prefix
        assert not rule_name.startswith("☿")

    def test_extract_status(self, parser):
        """Test extracting rule status."""
        status = parser._extract_status()

        assert status in ["active", "disabled"]

    def test_extract_rule_type(self, parser):
        """Test extracting rule type."""
        rule_type = parser._extract_rule_type()

        assert rule_type in ["differential-revision", "commit", "unknown"]

    def test_is_global_rule(self, parser):
        """Test checking if rule is global."""
        # This test will need to be updated based on actual HTML
        is_global = parser.is_global_rule()
        assert isinstance(is_global, bool)

    def test_parse_rule(self, parser):
        """Test parsing complete rule."""
        rule = parser.parse_rule()

        # Should return a Rule object or None
        if rule is not None:
            assert rule.id is not None
            assert rule.name is not None
            assert rule.author is not None
            assert rule.status in ["active", "disabled"]
            assert rule.type is not None
            assert isinstance(rule.conditions, list)
            assert isinstance(rule.actions, list)

    @pytest.mark.parametrize("rule_file,expected", [
        ("rule_H420.html", {
            "id": "H420",
            "name": "Blocked by omc-reviewers #1",
            "author": "dkl_admin",
            "is_global": True,
            "file_regexp": r"/?browser/locales/en-US/browser/(newtab/onboarding\.ftl|spotlight\.ftl|newtab/asrouter\.ftl|featureCallout\.ftl)",
            "reviewers": ["omc-reviewers"],
            "blocking": True,
        }),
        ("rule_H422.html", {
            "id": "H422",
            "name": "Blocked by omc-reviewers #2",
            "author": "dkl_admin",
            "is_global": True,
            "file_regexp": r"/?(browser|toolkit)/components/(asrouter|aboutwelcome|messagepreview|uitour|messaging-system)/",
            "reviewers": ["omc-reviewers"],
            "blocking": True,
        }),
        ("rule_H425.html", {
            "id": "H425",
            "name": "Blocked by android-reviewers",
            "author": "dkl_admin",
            "is_global": True,
            "file_regexp": r"/?mobile/android/(fenix|focus-android|android-components)",
            "reviewers": ["android-reviewers"],
            "blocking": True,
        }),
        ("rule_H432.html", {
            "id": "H432",
            "name": "Blocked by sidebar-reviewers-rotation",
            "author": "dkl_admin",
            "is_global": True,
            "file_regexp": r"/?browser/(components/sidebar/|base/content/browser-sidebar\.js|themes/shared/sidebar\.css)",
            "reviewers": ["sidebar-reviewers-rotation"],
            "blocking": True,
        }),
        ("rule_H483.html", {
            "id": "H483",
            "name": "Blocked by geckoview-api-reviewers",
            "author": "dkl_admin",
            "is_global": True,
            "file_regexp": r"/?mobile/android/geckoview/api.txt",
            "reviewers": ["geckoview-api-reviewers"],
            "blocking": True,
        }),
        ("rule_H507.html", {
            "id": "H507",
            "name": "Blocked by geckodriver-reviewers",
            "author": "dkl_admin",
            "is_global": True,
            "file_regexp": r"^/testing/(geckodriver|mozbase/rust|webdriver)",
            "reviewers": ["geckodriver-reviewers"],
            "blocking": True,
        }),
    ])
    def test_parse_rule_fixture(self, rule_file, expected):
        """Test parsing a specific rule fixture."""
        fixture_path = FIXTURES_DIR / "rules" / rule_file
        if not fixture_path.exists():
            pytest.skip(f"Fixture {rule_file} not found")

        html = fixture_path.read_text()
        parser = RuleDetailPageParser(html)

        # Extract all data
        rule_id = parser._extract_rule_id()
        rule_name = parser._extract_rule_name()
        rule_type = parser._extract_rule_type()
        author = parser._extract_author()
        is_global = parser.is_global_rule()
        conditions = parser._extract_conditions()
        actions = parser._extract_actions()

        # Check basic metadata
        assert rule_id == expected["id"], f"Rule ID mismatch"
        assert rule_name == expected["name"], f"Rule name mismatch"
        assert author == expected["author"], f"Author mismatch: got '{author}', expected '{expected['author']}'"
        assert is_global == expected["is_global"], f"is_global mismatch: got {is_global}, expected {expected['is_global']}"

        # Check conditions - should have at least one
        assert len(conditions) > 0, f"No conditions extracted"

        # Find file regexp condition
        file_conditions = [c for c in conditions if "file" in c.type.lower() or "diff" in c.type.lower()]
        assert len(file_conditions) > 0, f"No file/diff conditions found"

        # Check for expected regexp
        found_regexp = any(expected["file_regexp"] in str(c.value) for c in file_conditions)
        assert found_regexp, f"Expected regexp '{expected['file_regexp']}' not found"

        # Check actions - should have at least one
        assert len(actions) > 0, f"No actions extracted"

        # Find add reviewers action
        reviewer_actions = [a for a in actions if "review" in a.type.lower()]
        assert len(reviewer_actions) > 0, f"No reviewer actions found"

        # Check that expected reviewers are present
        all_reviewers = []
        for action in reviewer_actions:
            assert action.reviewers is not None, f"No reviewers in action"
            all_reviewers.extend([r.target for r in action.reviewers])

        for expected_reviewer in expected["reviewers"]:
            assert expected_reviewer in all_reviewers, \
                f"Expected reviewer '{expected_reviewer}' not found. Found: {all_reviewers}"

        # Check blocking status
        if expected["blocking"]:
            has_blocking = False
            for action in reviewer_actions:
                if action.reviewers:
                    blocking_reviewers = [r for r in action.reviewers if r.blocking]
                    if len(blocking_reviewers) > 0:
                        has_blocking = True
                        break
            assert has_blocking, f"Expected blocking reviewers but found none"


class TestProjectPageParser:
    """Tests for ProjectPageParser."""

    @pytest.fixture
    def project_fixtures(self):
        """Get all project fixture files."""
        groups_dir = FIXTURES_DIR / "groups"
        if not groups_dir.exists():
            pytest.skip("Project fixtures not found")

        project_files = list(groups_dir.glob("*.html"))
        if not project_files:
            pytest.skip("No project fixture files found")

        return project_files

    @pytest.fixture
    def sample_project_html(self, project_fixtures):
        """Load a sample project fixture."""
        return project_fixtures[0].read_text()

    def test_parser_initialization(self, sample_project_html):
        """Test parser initializes correctly."""
        parser = ProjectPageParser(sample_project_html)
        assert parser.html == sample_project_html
        assert parser.soup is not None

    def test_extract_project_info(self, sample_project_html):
        """Test extracting project information."""
        parser = ProjectPageParser(sample_project_html)
        info = parser.extract_project_info()

        assert "id" in info
        assert "display_name" in info
        assert "members" in info
        assert isinstance(info["members"], list)


class TestParserIntegration:
    """Integration tests using fixtures."""

    def test_listing_to_rules_workflow(self):
        """Test the workflow from listing to individual rules."""
        listing_path = FIXTURES_DIR / "rules" / "listing.html"
        if not listing_path.exists():
            pytest.skip("Listing fixture not found")

        # Parse listing
        listing_parser = ListingPageParser(listing_path.read_text())
        rule_ids = listing_parser.extract_rule_ids()

        assert len(rule_ids) > 0

        # Check if we have fixtures for any of these rules
        rules_dir = FIXTURES_DIR / "rules"
        available_fixtures = {
            f.stem.replace("rule_", ""): f
            for f in rules_dir.glob("rule_*.html")
        }

        # Try to parse rules we have fixtures for
        parsed_rules = []
        for rule_id in rule_ids:
            if rule_id in available_fixtures:
                html = available_fixtures[rule_id].read_text()
                parser = RuleDetailPageParser(html)
                rule = parser.parse_rule()
                if rule:
                    parsed_rules.append(rule)

        # Should have parsed at least one rule if we have fixtures
        if len(available_fixtures) > 0:
            assert len(parsed_rules) > 0, f"Expected to parse at least one rule, but parsed {len(parsed_rules)} from {len(available_fixtures)} fixtures"

    def test_global_rules_fixture_coverage(self):
        """Test that global rules from listing have corresponding fixtures.

        This test identifies which global rules are missing fixtures.
        It's informational - it won't fail if some rules are missing.
        """
        listing_path = FIXTURES_DIR / "rules" / "listing.html"
        if not listing_path.exists():
            pytest.skip("Listing fixture not found")

        # Get all global rules from the listing
        listing_parser = ListingPageParser(listing_path.read_text())
        all_rule_ids = listing_parser.extract_rule_ids()
        global_rules = listing_parser.filter_global_rules(all_rule_ids)

        # Check which have fixtures
        rules_dir = FIXTURES_DIR / "rules"
        available_fixtures = {
            f.stem.replace("rule_", "")
            for f in rules_dir.glob("rule_*.html")
        }

        covered = [r for r in global_rules if r in available_fixtures]
        missing = [r for r in global_rules if r not in available_fixtures]

        # Report coverage
        print(f"\nGlobal rules coverage: {len(covered)}/{len(global_rules)}")
        print(f"Covered: {covered}")
        print(f"Missing: {missing}")

        # Verify at least some coverage exists
        assert len(covered) > 0, "No global rules have fixtures"

        # This assertion is intentionally lenient - we know not all rules have fixtures yet
        # Uncomment the line below when all fixtures are available:
        # assert len(missing) == 0, f"Missing fixtures for global rules: {missing}"
