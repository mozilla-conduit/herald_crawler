#!/usr/bin/env python3
"""Debug script to diagnose cookie-related issues with HeraldClient."""

import os
import re
import requests

cookie = os.environ.get('PHABRICATOR_SESSION_COOKIE', '')
print(f'Cookie set: {bool(cookie)}')
if cookie:
    cleaned = cookie.replace('phsid=', '')
else:
    cleaned = ''
    print('No cookie - testing without auth')

s = requests.Session()
s.headers["User-Agent"] = "HeraldScraper/0.1"
netloc = 'phabricator.services.mozilla.com'
cookie_domain = '.' + netloc.split('.', 1)[1]
print(f'Cookie domain: {cookie_domain}')
print(f'User-Agent: {s.headers["User-Agent"]}')

if cleaned:
    s.cookies.set('phsid', cleaned, domain=cookie_domain)
    print(f'Cookie configured for domain: {cookie_domain}')

print()
print('=== Testing with allow_redirects=False (like client.py) ===')
r1 = s.get('https://phabricator.services.mozilla.com/herald/', allow_redirects=False)
print(f'Status: {r1.status_code}')
print(f'Location header: {r1.headers.get("Location", "(none)")}')
print(f'HTML length: {len(r1.text)}')

title_match = re.search(r'<title>([^<]+)</title>', r1.text)
print(f'Page title: {title_match.group(1) if title_match else "not found"}')

rule_count = r1.text.count('href="/H')
print(f'Rule link count: {rule_count}')

print()
print('=== Testing with allow_redirects=True (like fetch_fixtures.py) ===')
r2 = s.get('https://phabricator.services.mozilla.com/herald/', allow_redirects=True)
print(f'Status: {r2.status_code}')
print(f'Final URL: {r2.url}')
print(f'HTML length: {len(r2.text)}')

title_match = re.search(r'<title>([^<]+)</title>', r2.text)
print(f'Page title: {title_match.group(1) if title_match else "not found"}')

rule_count = r2.text.count('href="/H')
print(f'Rule link count: {rule_count}')
