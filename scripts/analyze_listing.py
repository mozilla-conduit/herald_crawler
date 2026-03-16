#!/usr/bin/env python3
"""
Analyze the Herald rules listing page to identify diverse examples to fetch.

Usage:
    python scripts/analyze_listing.py
"""

from pathlib import Path
from bs4 import BeautifulSoup
import re


def analyze_listing():
    """Analyze the listing page and suggest diverse rule examples."""

    fixtures_dir = Path(__file__).parent.parent / "tests" / "fixtures"
    listing_path = fixtures_dir / "rules" / "listing.html"

    if not listing_path.exists():
        print(f"ERROR: {listing_path} not found")
        print("Please run: curl -o tests/fixtures/rules/listing.html 'https://phabricator.services.mozilla.com/herald/query/all/'")
        return

    html = listing_path.read_text()
    soup = BeautifulSoup(html, "lxml")

    print("=== Herald Rules Listing Analysis ===\n")

    # Extract all rule links
    rule_links = []
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if href.startswith("/H") and len(href) > 2:
            rule_id = href[1:]
            if rule_id[0] == "H" and rule_id[1:].isdigit():
                # Get the link text and any nearby context
                text = link.get_text(strip=True)
                rule_links.append({
                    "id": rule_id,
                    "text": text,
                    "href": href
                })

    print(f"Found {len(rule_links)} rule links")
    print(f"Rule ID range: {rule_links[0]['id']} to {rule_links[-1]['id']}\n")

    # Look for table rows with rule information
    print("=== Extracting Rule Details ===\n")

    # Try to find the main table
    tables = soup.find_all("table")
    print(f"Found {len(tables)} tables in the page")

    # Look for specific patterns in the HTML
    # Herald rule rows typically have the rule ID as a link
    rules_info = []

    for link in rule_links[:20]:  # Check first 20 rules
        rule_id = link['id']

        # Find the link element in the soup
        link_elem = soup.find("a", href=f"/{rule_id}")
        if link_elem:
            # Get parent row context
            row = link_elem.find_parent("tr")
            if row:
                cells = row.find_all(["td", "th"])
                row_text = " | ".join([cell.get_text(strip=True) for cell in cells])

                # Try to identify rule characteristics from the row
                characteristics = []
                if "differential" in row_text.lower():
                    characteristics.append("differential-revision")
                if "commit" in row_text.lower():
                    characteristics.append("commit")
                if "blocking" in row_text.lower():
                    characteristics.append("blocking-reviewer")
                if "project" in row_text.lower() or "group" in row_text.lower():
                    characteristics.append("has-groups")
                if "path" in row_text.lower() or "file" in row_text.lower():
                    characteristics.append("path-conditions")

                rules_info.append({
                    "id": rule_id,
                    "text": link['text'],
                    "row_text": row_text[:200],  # First 200 chars
                    "characteristics": characteristics
                })

    # Print some examples
    if rules_info:
        print("\nSample rules with details:\n")
        for i, rule in enumerate(rules_info[:10]):
            print(f"{i+1}. {rule['id']}: {rule['text']}")
            if rule['characteristics']:
                print(f"   Characteristics: {', '.join(rule['characteristics'])}")
            print(f"   Row: {rule['row_text'][:150]}...")
            print()

    # Recommendations for diverse examples
    print("=== Recommended Examples to Fetch ===\n")
    print("To get a diverse set of examples, fetch these rules:")
    print()

    # Sample selection
    sample_rules = [
        rule_links[0]['id'],   # First rule
        rule_links[5]['id'],   # Early rule
        rule_links[15]['id'],  # Middle-early
        rule_links[30]['id'],  # Middle
        rule_links[-10]['id'], # Near end
        rule_links[-1]['id'],  # Last rule
    ]

    for i, rule_id in enumerate(sample_rules, 1):
        print(f"{i}. {rule_id}")

    print("\nTo fetch these:")
    print("export PHABRICATOR_SESSION_COOKIE='your-cookie-value'")
    print(f"python scripts/fetch_fixtures.py --rules {' '.join(sample_rules)}")
    print()

    # Look for PHIDs and projects
    print("=== PHIDs and Projects in Listing ===\n")

    phids = set()
    for match in re.finditer(r'PHID-[A-Z]{4}-[a-z0-9]{20}', html):
        phids.add(match.group(0))

    print(f"Found {len(phids)} unique PHIDs in the listing")
    if phids:
        print("Sample PHIDs:")
        for phid in list(phids)[:10]:
            print(f"  - {phid}")
    print()

    # Look for project/group mentions
    projects = set()
    for match in re.finditer(r'/tag/([a-z0-9_-]+)/', html):
        projects.add(match.group(1))

    print(f"Found {len(projects)} project/tag references")
    if projects:
        print("Sample projects:")
        for proj in list(projects)[:10]:
            print(f"  - {proj}")
    print()


if __name__ == "__main__":
    analyze_listing()
