#!/usr/bin/env python3
"""
Extract conditions and actions text from Herald rule fixture files.

This script parses Herald rule HTML fixtures and extracts the raw text
of conditions and actions sections. It helps understand the natural
language structure of rules for parser development.

Usage:
    python scripts/extract_conditions_actions.py

Output:
    For each rule fixture, prints:
    - Conditions section text (up to 800 characters)
    - Actions section text (up to 500 characters)
"""

from pathlib import Path
from bs4 import BeautifulSoup

fixtures_dir = Path("tests/fixtures/rules")
rule_files = sorted(fixtures_dir.glob("rule_*.html"))

for rule_file in rule_files:
    print(f"\n{'='*70}")
    print(f"File: {rule_file.name}")
    print('='*70)

    html = rule_file.read_text()
    soup = BeautifulSoup(html, "lxml")

    # Get all text
    full_text = soup.get_text()

    # Find "When all of these conditions are met:" section
    conditions_start = full_text.find("When all of these conditions are met:")
    if conditions_start != -1:
        # Find the end (before "Take these actions")
        actions_start = full_text.find("Take these actions", conditions_start)
        if actions_start != -1:
            conditions_text = full_text[conditions_start:actions_start]
            print("\n--- CONDITIONS ---")
            print(conditions_text[:800])

    # Find "Take these actions" section
    if actions_start != -1:
        # Find the end (before something like "Event Timeline" or similar)
        timeline_start = full_text.find("Event Timeline", actions_start)
        if timeline_start == -1:
            timeline_start = actions_start + 500  # fallback

        actions_text = full_text[actions_start:timeline_start]
        print("\n--- ACTIONS ---")
        print(actions_text[:500])
