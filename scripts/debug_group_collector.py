#!/usr/bin/env python3
"""Debug script to trace GroupCollector flow."""

import os
import logging

from herald_scraper.client import HeraldClient
from herald_scraper.resolvers import GroupCollector
from herald_scraper.parsers import ProjectMembersPageParser

logging.basicConfig(level=logging.DEBUG, format='%(name)s - %(levelname)s - %(message)s')

cookie = os.environ.get('PHABRICATOR_SESSION_COOKIE', '')
print(f'Cookie set: {bool(cookie)}')

client = HeraldClient(
    base_url='https://phabricator.services.mozilla.com',
    session_cookie=cookie if cookie else None,
)

# Check client cookie state
print()
print('=== Client cookie state ===')
for c in client._session.cookies:
    print(f'  {c.name}: domain={c.domain}')

# Manually fetch members page and parse
print()
print('=== Manual fetch of members page ===')
html = client.fetch_page('/project/members/171/')
print(f'HTML length: {len(html)}')
print(f'Title in HTML: {"Members and Watchers" in html}')

parser = ProjectMembersPageParser(html)
members = parser.extract_members()
print(f'Members found: {len(members)}')
print(f'Members: {members[:5]}...')

# Now try via GroupCollector
print()
print('=== Via GroupCollector ===')
collector = GroupCollector(client)
group = collector.fetch_group('omc-reviewers')
if group:
    print(f'Group: {group.id}')
    print(f'Members: {len(group.members)}')
    print(f'Sample: {group.members[:5]}')
else:
    print('Group fetch failed')
