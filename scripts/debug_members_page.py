#!/usr/bin/env python3
"""Debug script to diagnose members page auth issues."""

import os
import re
import requests

cookie = os.environ.get('PHABRICATOR_SESSION_COOKIE', '')
print(f'Cookie set: {bool(cookie)}')

s = requests.Session()
s.headers['User-Agent'] = 'HeraldScraper/0.1'
if cookie:
    s.cookies.set('phsid', cookie.replace('phsid=', ''), domain='.services.mozilla.com')

print()
print('=== Project page /tag/omc-reviewers/ ===')
r1 = s.get('https://phabricator.services.mozilla.com/tag/omc-reviewers/', allow_redirects=False)
title1 = re.search(r'<title>([^<]+)</title>', r1.text)
print(f'allow_redirects=False: status={r1.status_code}, title={title1.group(1) if title1 else "none"}')

r1b = s.get('https://phabricator.services.mozilla.com/tag/omc-reviewers/', allow_redirects=True)
title1b = re.search(r'<title>([^<]+)</title>', r1b.text)
print(f'allow_redirects=True:  status={r1b.status_code}, title={title1b.group(1) if title1b else "none"}')

print()
print('=== Members page /project/members/171/ ===')
r2 = s.get('https://phabricator.services.mozilla.com/project/members/171/', allow_redirects=False)
title2 = re.search(r'<title>([^<]+)</title>', r2.text)
print(f'allow_redirects=False: status={r2.status_code}, location={r2.headers.get("Location", "none")}, title={title2.group(1) if title2 else "none"}')

r2b = s.get('https://phabricator.services.mozilla.com/project/members/171/', allow_redirects=True)
title2b = re.search(r'<title>([^<]+)</title>', r2b.text)
print(f'allow_redirects=True:  status={r2b.status_code}, title={title2b.group(1) if title2b else "none"}')
print(f'Final URL: {r2b.url}')
