#!/usr/bin/env python3
"""
Analyze HTML structure of Herald rule fixtures.

This script examines the HTML structure of Herald rule pages to understand
how conditions and actions are organized, helping with parser implementation.
"""

from pathlib import Path
from bs4 import BeautifulSoup
import re


def main() -> None:
    """Analyze HTML structure of rule fixtures."""
    fixtures_dir = Path("tests/fixtures/rules")
    rule_file = fixtures_dir / "rule_H420.html"

    if not rule_file.exists():
        print(f"Fixture not found: {rule_file}")
        return

    html = rule_file.read_text()
    soup = BeautifulSoup(html, "lxml")

    print("=" * 70)
    print(f"Analyzing: {rule_file.name}")
    print("=" * 70)

    # Find conditions section
    conditions_header = soup.find(
        'p',
        class_='herald-list-description',
        string=lambda t: t and 'When all of these conditions' in t
    )

    if conditions_header:
        print("\n=== CONDITIONS SECTION ===")
        print(f"Header: {conditions_header.get_text()}")
        print("\nCondition items:")

        for sibling in conditions_header.find_next_siblings():
            if sibling.name == 'p' and 'herald-list-description' in sibling.get('class', []):
                break
            if sibling.name == 'div' and 'herald-list-item' in sibling.get('class', []):
                text = sibling.get_text(strip=True)
                print(f"  - {text}")

                # Check for regexp patterns
                if 'matches regexp' in text:
                    regexp_match = re.search(r'@(.+?)@', text)
                    if regexp_match:
                        print(f"    -> Regexp: {regexp_match.group(1)}")

    # Find actions section
    actions_header = soup.find(
        'p',
        class_='herald-list-description',
        string=lambda t: t and 'Take these actions' in t
    )

    if actions_header:
        print("\n=== ACTIONS SECTION ===")
        print(f"Header: {actions_header.get_text()}")
        print("\nAction items:")

        for sibling in actions_header.find_next_siblings():
            if sibling.name == 'div' and 'herald-list-item' in sibling.get('class', []):
                text = sibling.get_text(strip=True)
                print(f"  - {text}")

                # Check for reviewer links
                links = sibling.find_all('a', class_='phui-handle')
                for link in links:
                    href = link.get('href', '')
                    link_text = link.get_text(strip=True)
                    print(f"    -> Reviewer: {link_text} (href: {href})")

                # Check for blocking status
                if 'blocking' in text.lower():
                    print(f"    -> Blocking: True")


if __name__ == "__main__":
    main()
