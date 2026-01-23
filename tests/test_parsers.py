"""Tests for Herald page parsers."""

import pytest
from pathlib import Path

from herald_scraper.parsers import (
    ListingPageParser,
    RuleDetailPageParser,
    ProjectPageParser,
    ProjectMembersPageParser,
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

    @pytest.mark.parametrize("fixture_file,expected", [
        ("omc-reviewers.html", {
            "slug": "omc-reviewers",
            "project_id": "171",
            "display_name": "omc-reviewers",
            # Full timeline available - exact member verification
            # Note: aminomancer is the actor (adds others), not a member
            "expected_members": ["lsmith", "mviar", "sachung"],
        }),
        ("android-reviewers.html", {
            "slug": "android-reviewers",
            "project_id": "200",
            "display_name": "android-reviewers",
            # Large group with many members - verify minimum
            "min_members": 30,
        }),
        ("sidebar-reviewers-rotation.html", {
            "slug": "sidebar-reviewers-rotation",
            "project_id": "207",
            "display_name": "sidebar-reviewers-rotation",
            # Timeline only shows removals, not initial members
            "min_members": 0,
        }),
        ("geckoview-api-reviewers.html", {
            "slug": "geckoview-api-reviewers",
            "project_id": "226",
            "display_name": "geckoview-api-reviewers",
            # Only one addition visible in timeline
            "expected_members": ["tcampbell"],
        }),
        ("geckodriver-reviewers.html", {
            "slug": "geckodriver-reviewers",
            "project_id": "232",
            "display_name": "geckodriver-reviewers",
            # No member events in timeline - empty is acceptable
            "min_members": 0,
        }),
    ])
    def test_parse_project_fixture(self, fixture_file, expected):
        """Test parsing specific project fixtures with expected values."""
        fixture_path = FIXTURES_DIR / "groups" / fixture_file
        if not fixture_path.exists():
            pytest.skip(f"Fixture {fixture_file} not found")

        html = fixture_path.read_text()
        parser = ProjectPageParser(html)
        info = parser.extract_project_info()

        # Check slug
        assert info["id"] == expected["slug"], \
            f"Slug mismatch: got '{info['id']}', expected '{expected['slug']}'"

        # Check project_id
        assert info["project_id"] == expected["project_id"], \
            f"Project ID mismatch: got '{info['project_id']}', expected '{expected['project_id']}'"

        # Check display name
        assert info["display_name"] == expected["display_name"], \
            f"Display name mismatch: got '{info['display_name']}', expected '{expected['display_name']}'"

        # Check members
        assert isinstance(info["members"], list), "Members should be a list"

        if "expected_members" in expected:
            # Check exact member list (sorted for comparison)
            assert sorted(info["members"]) == sorted(expected["expected_members"]), \
                f"Members mismatch: got {sorted(info['members'])}, expected {sorted(expected['expected_members'])}"
        elif "min_members" in expected:
            # Check minimum member count
            assert len(info["members"]) >= expected["min_members"], \
                f"Expected at least {expected['min_members']} members, got {len(info['members'])}"

    def test_extract_slug_from_tag_link(self):
        """Test extracting project slug from tag link in 'Looks Like' section."""
        fixture_path = FIXTURES_DIR / "groups" / "omc-reviewers.html"
        if not fixture_path.exists():
            pytest.skip("omc-reviewers fixture not found")

        html = fixture_path.read_text()
        parser = ProjectPageParser(html)

        slug = parser._extract_project_slug()
        assert slug == "omc-reviewers"

    def test_extract_project_id_from_members_link(self):
        """Test extracting project ID from members link in sidebar."""
        fixture_path = FIXTURES_DIR / "groups" / "omc-reviewers.html"
        if not fixture_path.exists():
            pytest.skip("omc-reviewers fixture not found")

        html = fixture_path.read_text()
        parser = ProjectPageParser(html)

        project_id = parser._extract_project_id()
        assert project_id == "171"

    def test_extract_project_id_not_found(self):
        """Test project ID extraction when members link is missing."""
        html = '<html><body><div>No members link here</div></body></html>'
        parser = ProjectPageParser(html)
        assert parser._extract_project_id() is None

    def test_extract_display_name_from_title(self):
        """Test extracting display name from page title."""
        fixture_path = FIXTURES_DIR / "groups" / "omc-reviewers.html"
        if not fixture_path.exists():
            pytest.skip("omc-reviewers fixture not found")

        html = fixture_path.read_text()
        parser = ProjectPageParser(html)

        name = parser._extract_project_name()
        assert name == "omc-reviewers"

    def test_extract_members_from_timeline(self):
        """Test extracting current members by parsing timeline events."""
        fixture_path = FIXTURES_DIR / "groups" / "omc-reviewers.html"
        if not fixture_path.exists():
            pytest.skip("omc-reviewers fixture not found")

        html = fixture_path.read_text()
        parser = ProjectPageParser(html)

        members = parser._extract_members()

        # Based on timeline analysis of omc-reviewers fixture:
        # - Created by zeid_admin
        # - beth removed herself (but was never added in visible timeline)
        # - aminomancer added yozhang, then removed yozhang
        # - aminomancer added lsmith
        # - hanna_a added mviar
        # - mviar added sachung
        # - aminomancer removed pdahiya and Mardak (but they were never added in visible timeline)
        # Note: aminomancer is the actor (adds others), not a member
        # Current members based on timeline: lsmith, mviar, sachung
        assert isinstance(members, list)
        assert "lsmith" in members
        assert "mviar" in members
        assert "sachung" in members
        # These should NOT be in members (removed or never added)
        assert "aminomancer" not in members  # Actor, not member
        assert "yozhang" not in members  # Added then removed
        assert "pdahiya" not in members  # Removed
        assert "Mardak" not in members  # Removed

    def test_extract_slug_minimal_html(self):
        """Test slug extraction with minimal HTML containing just title."""
        html = '<html><head><title>my-project · Manage</title></head><body></body></html>'
        parser = ProjectPageParser(html)
        assert parser._extract_project_slug() == "my-project"

    def test_extract_name_minimal_html(self):
        """Test name extraction with minimal HTML containing just title."""
        html = '<html><head><title>my-project · Manage</title></head><body></body></html>'
        parser = ProjectPageParser(html)
        assert parser._extract_project_name() == "my-project"

    def test_extract_members_no_timeline(self):
        """Test member extraction when no timeline exists."""
        html = '<html><body><div>No timeline here</div></body></html>'
        parser = ProjectPageParser(html)
        members = parser._extract_members()
        assert members == []

    def test_extract_members_empty_timeline(self):
        """Test member extraction with empty timeline (no member events)."""
        html = '''
        <html><body>
            <div class="phui-timeline-view">
                <div class="phui-timeline-title">
                    <a href="/p/user1/" class="phui-link-person">user1</a>
                    created this project.
                </div>
            </div>
        </body></html>
        '''
        parser = ProjectPageParser(html)
        members = parser._extract_members()
        assert members == []

    def test_extract_info_malformed_html(self):
        """Test extraction with minimal/malformed HTML returns defaults."""
        html = '<html><body></body></html>'
        parser = ProjectPageParser(html)
        info = parser.extract_project_info()

        # Should return defaults without crashing
        assert info["id"] == "unknown-project"
        assert info["display_name"] == "Unknown Project"
        assert info["members"] == []


class TestProjectMembersPageParser:
    """Tests for ProjectMembersPageParser (members page parsing)."""

    @pytest.fixture
    def members_fixtures(self):
        """Get all members page fixture files."""
        groups_dir = FIXTURES_DIR / "groups"
        if not groups_dir.exists():
            pytest.skip("Project fixtures not found")
        return {f.stem.replace("-members", ""): f for f in groups_dir.glob("*-members.html")}

    # Expected members for each group fixture (sorted alphabetically)
    EXPECTED_MEMBERS = {
        "omc-reviewers": [
            "aminomancer", "dmose", "emcminn", "hanna_a", "jprickett",
            "lsmith", "mimi", "mviar", "sachung"
        ],
        "geckodriver-reviewers": ["Sasha", "jgraham", "whimboo"],
        "sidebar-reviewers-rotation": [
            "jsudiaman", "kcochrane", "nsharpley", "sclements", "sfoster"
        ],
        "geckoview-api-reviewers": [
            "bclark", "botond", "calu", "hiro", "m_kato", "nalexander",
            "nika", "ohall", "owlish", "pollymce", "tcampbell", "tthibaud"
        ],
        "android-reviewers": [
            "007", "RJ", "Roger", "adhingra", "anpopa", "apindiprolu",
            "avirvara", "azinovyev", "boek", "calu", "devota", "fmasalha",
            "giorga", "gl", "gmalekpour", "harrisono", "jdelorenzo",
            "joberhauser", "jonalmeida", "kaya", "lmccracken", "marcin",
            "matt-tighe", "mavduevskiy", "mcarare", "moyin", "nalexander",
            "npoon", "ohall", "owlish", "petru", "pollymce",
            "rebecatudor273", "rsainani", "sfamisa", "skhan", "tcampbell",
            "tchoh", "tjorjani", "tthibaud", "twhite", "vdreghici"
        ],
        "desktop-theme-reviewers": [
            "Itiel", "Julian", "dao", "emilio", "hjones", "jules",
            "kcochrane", "mstriemer", "sfoster", "tgiles", "zrhoffman"
        ],
        "profiler-reviewers": [
            "canaltinova", "dcarver", "fatadel", "florian", "mstange"
        ],
        "win-reviewers": [
            "cdupuis", "cmartin", "gstoll", "handyman", "mpohle",
            "nrishel", "rkraesig", "yjuglaret"
        ],
        "dom-storage-reviewers": [
            "asuth", "edenchuang", "hsingh", "hsinyi", "janv",
            "jari", "jesup", "jstutte"
        ],
        "reusable-components-reviewers-rotation": [
            "akulyk", "hjones", "jules", "mkennedy", "mstriemer", "tgiles"
        ],
    }

    @pytest.mark.parametrize("group_slug,expected_count", [
        ("omc-reviewers", 9),
        ("geckodriver-reviewers", 3),
        ("sidebar-reviewers-rotation", 5),
        ("geckoview-api-reviewers", 12),
        ("android-reviewers", 42),
        ("desktop-theme-reviewers", 11),
        ("profiler-reviewers", 5),
        ("win-reviewers", 8),
        ("dom-storage-reviewers", 8),
        ("reusable-components-reviewers-rotation", 6),
    ])
    def test_extract_members_count(self, members_fixtures, group_slug, expected_count):
        """Test that the correct number of members is extracted."""
        if group_slug not in members_fixtures:
            pytest.skip(f"No fixture for {group_slug}")

        html = members_fixtures[group_slug].read_text()
        parser = ProjectMembersPageParser(html)
        members = parser.extract_members()

        assert len(members) == expected_count, (
            f"Expected {expected_count} members for {group_slug}, "
            f"got {len(members)}"
        )

    def test_extract_members_returns_sorted(self, members_fixtures):
        """Test that members are returned in sorted order."""
        if "omc-reviewers" not in members_fixtures:
            pytest.skip("omc-reviewers fixture not found")

        html = members_fixtures["omc-reviewers"].read_text()
        parser = ProjectMembersPageParser(html)
        members = parser.extract_members()

        assert members == sorted(members), "Members should be sorted alphabetically"

    def test_extract_members_no_duplicates(self, members_fixtures):
        """Test that no duplicate members are returned."""
        if "omc-reviewers" not in members_fixtures:
            pytest.skip("omc-reviewers fixture not found")

        html = members_fixtures["omc-reviewers"].read_text()
        parser = ProjectMembersPageParser(html)
        members = parser.extract_members()

        assert len(members) == len(set(members)), "Members should have no duplicates"

    def test_extract_members_empty_page(self):
        """Test extraction with empty HTML returns empty list."""
        html = '<html><body></body></html>'
        parser = ProjectMembersPageParser(html)
        members = parser.extract_members()
        assert members == []

    def test_extract_members_no_member_list(self):
        """Test extraction when page has no member list."""
        html = '''
        <html><body>
            <div class="phui-header-shell">
                <h1>Members and Watchers</h1>
            </div>
            <div class="phui-info-view phui-info-severity-nodata">
                This project does not have any members.
            </div>
        </body></html>
        '''
        parser = ProjectMembersPageParser(html)
        members = parser.extract_members()
        assert members == []


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
