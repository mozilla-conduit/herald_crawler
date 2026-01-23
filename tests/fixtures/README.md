# Test Fixtures

This directory contains HTML snapshots from the Phabricator instance for testing the parser.

## Directory Structure

- `rules/` - Herald rule pages
  - `listing.html` - Main Herald rules listing page
  - `rule_*.html` - Individual rule detail pages
- `groups/` - Project/group pages for membership extraction
  - `group_*.html` - Group/project detail pages with member lists
- `phids/` - Example pages for PHID resolution
  - User profile pages
  - Repository pages
  - Other PHID-containing pages

## Collected Fixtures

### Rules Listing
- `listing.html` - Herald rules listing from https://phabricator.services.mozilla.com/herald/query/all/

### Individual Rules
(To be documented as collected)

### Groups
(To be documented as collected)

## Collection Method

### Automated Collection (Recommended)

Use the provided fetch script:

```bash
# Install dependencies first
pip install requests beautifulsoup4 lxml

# Get your session cookie
# 1. Log in to Phabricator in your browser
# 2. Open Developer Tools > Application > Cookies
# 3. Copy the 'phsid' cookie value
export PHABRICATOR_SESSION_COOKIE="your-phsid-cookie-value"

# Run the script (fetches default examples)
python scripts/fetch_fixtures.py

# Fetch specific rules
python scripts/fetch_fixtures.py --rules H416 H417 H420

# Fetch all rules (limited to first 10 by default)
python scripts/fetch_fixtures.py --all --max-rules 10
```

### Manual Collection

#### Listing Page (No Auth Required)
```bash
curl -o tests/fixtures/rules/listing.html "https://phabricator.services.mozilla.com/herald/query/all/"
```

#### Individual Rule Pages (Auth Required)
```bash
# Using browser cookies
curl -b "phsid=YOUR_SESSION_COOKIE" \
  -o tests/fixtures/rules/rule_H416.html \
  "https://phabricator.services.mozilla.com/H416"
```

## Notes

- All fixtures are snapshots from 2026-01-21
- **IMPORTANT**: Individual rule pages require authentication (BMO/Bugzilla OAuth)
- Listing page is publicly accessible
- PHIDs in fixtures should be documented for cross-reference testing
- Rule IDs found in listing: H416-H436 (and more with pagination)
