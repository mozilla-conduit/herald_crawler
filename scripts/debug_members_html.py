#!/usr/bin/env python3
"""Debug script to inspect members page HTML structure."""

import os
import requests
from bs4 import BeautifulSoup

cookie = os.environ.get('PHABRICATOR_SESSION_COOKIE', '')
s = requests.Session()
s.headers['User-Agent'] = 'HeraldScraper/0.1'
if cookie:
    s.cookies.set('phsid', cookie.replace('phsid=', ''), domain='.services.mozilla.com')

r = s.get('https://phabricator.services.mozilla.com/project/members/171/')
soup = BeautifulSoup(r.text, 'lxml')

print(f'Page length: {len(r.text)}')

# Check what the parser looks for
print()
print('=== Parser looks for: <a class="phui-oi-link" href="/p/..."> ===')
links = soup.find_all('a', class_='phui-oi-link', href=lambda h: h and h.startswith('/p/'))
print(f'Found: {len(links)}')
for link in links[:5]:
    print(f'  {link.get("href")} -> {link.get_text(strip=True)}')

# Check what other structures exist
print()
print('=== Alternative patterns ===')
print(f'phui-oi-link: {len(soup.find_all("a", class_="phui-oi-link"))}')
print(f'phui-handle: {len(soup.find_all("a", class_="phui-handle"))}')
print(f'phui-link-person: {len(soup.find_all("a", class_="phui-link-person"))}')
print(f'href=/p/: {len(soup.find_all("a", href=lambda h: h and "/p/" in h))}')

# Show sample of /p/ links
print()
print('=== Sample /p/ links ===')
p_links = soup.find_all('a', href=lambda h: h and h.startswith('/p/'))
for link in p_links[:10]:
    print(f'  class={link.get("class")}, href={link.get("href")}, text={link.get_text(strip=True)[:30]}')
