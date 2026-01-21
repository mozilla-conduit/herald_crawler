#!/usr/bin/env python3
"""
Analyze the Herald rules listing page to identify diverse examples to fetch.

Usage:
    python scripts/analyze_listing_simple.py
"""

from pathlib import Path
import re


def analyze_listing():
    """Analyze the listing page and suggest diverse rule examples."""

    fixtures_dir = Path(__file__).parent.parent / "tests" / "fixtures"
    listing_path = fixtures_dir / "rules" / "listing.html"

    if not listing_path.exists():
        print(f"ERROR: {listing_path} not found")
        return

    html = listing_path.read_text()

    print("=== Herald Rules Listing Analysis ===\n")

    # Extract all rule IDs
    rule_ids = set()
    for match in re.finditer(r'href="/H(\d+)"', html):
        rule_ids.add(f"H{match.group(1)}")

    rule_ids_sorted = sorted(rule_ids, key=lambda x: int(x[1:]))

    print(f"Found {len(rule_ids_sorted)} rules")
    print(f"Rule ID range: {rule_ids_sorted[0]} to {rule_ids_sorted[-1]}\n")

    # Look for PHIDs
    print("=== PHIDs Found ===\n")

    phid_types = {}
    for match in re.finditer(r'PHID-([A-Z]{4})-[a-z0-9]{20}', html):
        phid_type = match.group(1)
        phid_types[phid_type] = phid_types.get(phid_type, 0) + 1

    print(f"Found {sum(phid_types.values())} PHIDs of {len(phid_types)} types:")
    for phid_type, count in sorted(phid_types.items()):
        print(f"  - PHID-{phid_type}: {count} occurrences")
    print()

    # Sample some actual PHIDs
    sample_phids = {}
    for phid_type in list(phid_types.keys())[:5]:
        match = re.search(f'PHID-{phid_type}-[a-z0-9]{{20}}', html)
        if match:
            sample_phids[phid_type] = match.group(0)

    if sample_phids:
        print("Sample PHIDs:")
        for phid_type, phid in sample_phids.items():
            print(f"  - {phid}")
        print()

    # Look for project/tag references
    print("=== Projects/Tags Found ===\n")

    projects = set()
    for match in re.finditer(r'/tag/([a-z0-9_-]+)/', html):
        projects.add(match.group(1))

    print(f"Found {len(projects)} project/tag references")
    if projects:
        projects_list = sorted(projects)
        print("Sample projects (first 15):")
        for proj in projects_list[:15]:
            print(f"  - {proj}")
    print()

    # Look for common keywords to identify rule types
    print("=== Keywords Analysis ===\n")

    keywords = {
        "differential": html.lower().count("differential"),
        "commit": html.lower().count("commit"),
        "reviewer": html.lower().count("reviewer"),
        "blocking": html.lower().count("blocking"),
        "path": html.lower().count("path"),
        "file": html.lower().count("file"),
        "repository": html.lower().count("repository"),
    }

    print("Keyword frequencies (approximate, may include UI text):")
    for keyword, count in sorted(keywords.items(), key=lambda x: -x[1]):
        print(f"  - {keyword}: {count}")
    print()

    # Recommendations
    print("=== Recommended Examples to Fetch ===\n")
    print("To get a diverse set of examples, fetch these rules:\n")

    # Strategic sampling
    total = len(rule_ids_sorted)
    sample_indices = [
        0,           # First
        5,           # Early
        15,          # Early-middle
        total // 3,  # One third
        total // 2,  # Middle
        2 * total // 3,  # Two thirds
        total - 10,  # Near end
        total - 1,   # Last
    ]

    sample_rules = [rule_ids_sorted[i] for i in sample_indices if i < total]

    for i, rule_id in enumerate(sample_rules, 1):
        print(f"  {i}. {rule_id}")

    print(f"\nTo fetch these {len(sample_rules)} rules:")
    print("  export PHAB_SESSION_COOKIE='your-cookie-value'")
    print(f"  python scripts/fetch_fixtures.py --rules {' '.join(sample_rules)}")
    print()

    # Also suggest fetching project pages
    if projects:
        print("=== Project Pages to Fetch ===\n")
        print("These projects appear in the rules (sample):")
        sample_projects = sorted(projects)[:5]
        for proj in sample_projects:
            print(f"  - {proj}")
            print(f"    URL: https://phabricator.services.mozilla.com/tag/{proj}/")
        print()

    # Summary
    print("=== Next Steps ===\n")
    print("1. Get your Phabricator session cookie:")
    print("   - Log in to https://phabricator.services.mozilla.com/")
    print("   - Open Developer Tools > Application > Cookies")
    print("   - Copy the 'phsid' cookie value")
    print()
    print("2. Fetch the diverse rule examples:")
    print(f"   export PHAB_SESSION_COOKIE='your-cookie-value'")
    print(f"   python scripts/fetch_fixtures.py --rules {' '.join(sample_rules[:6])}")
    print()
    print("3. Manually fetch a few more interesting rules by browsing:")
    print("   https://phabricator.services.mozilla.com/herald/query/all/")
    print("   Look for rules with different characteristics:")
    print("   - Different content types (Differential Revisions vs Commits)")
    print("   - Complex conditions (multiple path patterns)")
    print("   - Multiple reviewer groups")
    print("   - Blocking vs non-blocking reviewers")
    print()


if __name__ == "__main__":
    analyze_listing()
