#!/usr/bin/env python3
"""
Inspect Herald rule fixture files to understand their HTML structure.

This script analyzes the HTML structure of saved Herald rule fixtures,
extracting key metadata like rule IDs, titles, breadcrumbs, and rule types.
It helps identify patterns in the HTML that can be used for parsing.

Usage:
    python scripts/inspect_fixtures.py

Output:
    - Rule ID from filename and its occurrences in HTML
    - Page title
    - Breadcrumb navigation elements
    - Rule type indicators (Global Rule, Personal Rule, Object Rule)
"""

from pathlib import Path
from bs4 import BeautifulSoup
import re

fixtures_dir = Path("tests/fixtures/rules")

# Get all rule fixtures
rule_files = sorted(fixtures_dir.glob("rule_*.html"))

print("=== Rule Fixture Analysis ===\n")

for rule_file in rule_files:
    print(f"File: {rule_file.name}")

    html = rule_file.read_text()
    soup = BeautifulSoup(html, "lxml")

    # Extract title
    title = soup.find("title")
    if title:
        print(f"  Title: {title.get_text(strip=True)}")

    # Look for the rule ID in the filename
    filename_match = re.search(r'rule_(H\d+)\.html', rule_file.name)
    if filename_match:
        expected_id = filename_match.group(1)
        print(f"  Expected ID (from filename): {expected_id}")

        # Check if this ID appears in the HTML
        if expected_id in html:
            # Find context
            matches = list(re.finditer(re.escape(expected_id), html))
            print(f"  ID appears {len(matches)} times in HTML")

            # Find first occurrence with context
            if matches:
                pos = matches[0].start()
                context_start = max(0, pos - 100)
                context_end = min(len(html), pos + 150)
                context = html[context_start:context_end]
                # Clean up for display
                context = context.replace('\n', ' ').replace('\r', '')
                context = re.sub(r'\s+', ' ', context)
                print(f"  First context: ...{context}...")

    # Check for breadcrumbs or headers
    crumbs = soup.find_all("a", class_=re.compile("crumb"))
    if crumbs:
        print(f"  Breadcrumbs found: {len(crumbs)}")
        for crumb in crumbs[:3]:
            print(f"    - {crumb.get_text(strip=True)}")

    # Look for specific patterns
    if "Global Rule" in html:
        print(f"  ✓ Contains 'Global Rule'")
    if "Personal Rule" in html:
        print(f"  ✓ Contains 'Personal Rule'")
    if "Object Rule" in html:
        print(f"  ✓ Contains 'Object Rule'")

    print()
