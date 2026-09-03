# Herald Rules Scraper

A web scraper to extract Herald rules from Phabricator in a machine-parseable format.

## Overview

This tool extracts Herald rules from a Phabricator instance (specifically https://phabricator.services.mozilla.com/) and outputs them as structured JSON data with all PHIDs resolved to human-readable names.

## Features

- Extracts all Herald rules with conditions and actions
- Resolves PHIDs to usernames, emails, and group names
- Fetches reviewer group membership from STMO's `phabricator_metrics.review_groups`
- Resolves GitHub usernames and IDs from STMO's `mozcloud.person_api_staff_members`, falling
  back to the People Directory, so unattended runs need no browser session
- Outputs structured JSON with complete metadata
- Uses Pydantic for data validation and type safety

## Installation

```bash
# Install dependencies
$ uv sync
```

## Usage

```bash
$ uv run herald-scraper \
  --url https://phabricator.services.mozilla.com \
  --stmo-api-key $REDASH_API_KEY \
  --conduit-token $CONDUIT_API_TOKEN \
  --pmo-cookie $PMO_COOKIE \
  [--phab-cookie $PHABRICATOR_SESSION_COOKIE] \
  [--max-pages P] \
  [--max-groups G] \
  [--max-rules R] \
  --output herald_rules.$(date -Iseconds).json
```

Get `$REDASH_API_KEY` from https://sql.telemetry.mozilla.org/users/me. `$STMO_DATA_SOURCE_ID` is the numeric ID of the data source hosting `phabricator_metrics.review_groups` (visible in the URL of the data source page under https://sql.telemetry.mozilla.org/data_sources).

Get `$CONDUIT_API_TOKEN` from https://phabricator.services.mozilla.com/settings/user/YOUR_USERNAME/page/apitokens/

Get `$PMO_COOKIE` by logging in to https://people.mozilla.org/ and getting the value of the `pmo-access` cookie.

Get `$PHABRICATOR_SESSION_COOKIE` by logging in to Phabricator and getting the value of the `phsid` cookie. Not needed if using STMO.


### Reviewer group membership

Group membership comes from STMO's `phabricator_metrics.review_groups` table, which holds
`group_name`, `group_usernames` and `group_emails`. It accumulates a row per group per collection
run (tens of thousands of rows for ~150 groups), so the scraper runs a single ad-hoc query that
keeps one row per group, then keeps the groups referenced by the rules it found:

```sql
SELECT group_name, group_usernames, group_emails
FROM (
  SELECT group_name, group_usernames, group_emails,
         ROW_NUMBER() OVER (PARTITION BY group_name) AS row_num
  FROM phabricator_metrics.review_groups
)
WHERE row_num = 1
```

With no `ORDER BY` in the window, the surviving row is the one inserted last, which is the current
membership. That follows from the table being append-only and scanned in insertion order rather
than being pinned down by the query.

Override the table with `--stmo-table`. Authentication follows
[`stmo-cli`](https://github.com/mozilla/stmo-cli): a Redash API key sent as
`Authorization: Key <key>`, read from `--stmo-api-key` or `REDASH_API_KEY`, against
`--stmo-url`/`REDASH_URL`.

Without STMO credentials no groups are collected, `groups` are empty and
`metadata.scrape_status.groups_complete` is `false`.

### GitHub usernames

GitHub accounts come from STMO's `mozcloud.person_api_staff_members` table, which pairs a
staff member's LDAP username and email with their GitHub username and numeric ID. One query
pulls the whole directory:

```sql
SELECT DISTINCT email, username, github_username, github_id
FROM mozcloud.person_api_staff_members
WHERE github_username IS NOT NULL OR github_id IS NOT NULL
```

Rows describing someone with no GitHub account are dropped. Override the table with
`--stmo-github-table`.

Rules and groups name people by Phabricator username, which is the LDAP `username` for most
people, so that is matched first and answers without any further lookup. Failing that, the
user is joined by email — through the `group_emails` the review groups query already returns,
or through a reviewer target that is itself an address — and finally by email local part;
local parts shared by several people with different accounts are ambiguous and never match.

This directory takes precedence over the People Directory, which only sees the users it
misses. It carries both the GitHub username and the numeric ID, so users it resolves get a
`username` and a `user_id`.
`--github-user-mapping` still wins over both.

### Unattended runs

Every credential beyond the Phabricator session cookie is optional, so a scheduled run needs no
browser session other than `phsid`:

| Credential | Needed for | Omitting it means |
| --- | --- | --- |
| `--phab-cookie` / `PHABRICATOR_SESSION_COOKIE` | scraping the rules themselves | required |
| `--stmo-api-key` / `REDASH_API_KEY` | reviewer group membership, and the bulk GitHub login map | `groups` is empty, `groups_complete` is `false`, and every GitHub username has to come from the People Directory |
| `--conduit-token` / `PHABRICATOR_CONDUIT_TOKEN` | cross-checking GitHub resolution against Phabricator's Bugzilla ID and real name | resolution runs without the cross-check |
| `--pmo-cookie` / `PEOPLE_MOZILLA_COOKIE` | resolving the users the STMO map misses | only the STMO map and `--github-user-mapping` resolve users, no People Directory requests |

The People Directory cookie is the only interactive credential, and it is now a fallback: the STMO
map resolves most users on the API key alone, and whoever it misses is reported under
`unresolved_users` rather than dropped. Pass `--no-resolve-github` to skip GitHub resolution
entirely; omitting the cookie only skips the People Directory half of it, with a warning.
`--github-user-mapping` supplies overrides from a JSON file for users neither source resolves.

## Development

### Setup

```bash
# Install package with dev dependencies
$ uv sync --group dev
```

### Running Tests

```bash
$ uv run pytest
```

### Code Formatting

```bash
$ uv run ruff check herald_scraper tests
```

### Type Checking

```bash
$ uv run mypy herald_scraper
```

## Data Model

The output JSON structure includes:

- **rules**: List of Herald rules with conditions and actions
- **groups**: Dictionary of reviewer groups with their members
- **metadata**: Information about the extraction (timestamp, counts, instance)

<details>
<summary>Example JSON output</summary>

```json
{
  "rules": [
    {
      "id": "H123",
      "name": "Rule Name",
      "author": "username",
      "status": "active",
      "type": "differential-revision",
      "conditions": [
        {
          "type": "repository",
          "operator": "is-any-of",
          "value": ["mozilla-central", "firefox-autoland"]
        },
        {
          "type": "differential-diff-content",
          "operator": "matches-regexp",
          "value": "^path/to/.*"
        }
      ],
      "actions": [
        {
          "type": "add-reviewers",
          "reviewers": [
            {
              "target": "reviewer-group-name",
              "blocking": true
            },
            {
              "target": "individual-user",
              "blocking": false
            }
          ]
        }
      ]
    }
  ],
  "groups": {
    "reviewer-group-name": {
      "id": "reviewer-group-name",
      "display_name": "Reviewer Group Name",
      "members": ["user-a", "user-b", "user-c"]
    }
  },
  "github_users": {
    "user-a": {
      "username": "github-user-a",
      "user_id": 11111111
    },
    "user-b": {
      "username": "github-user-b",
      "user_id": 22222222
    }
  },
  "unresolved_users": [
    {
      "phabricator_username": "user-c",
      "reason": "no_github_linked_or_not_found",
      "referenced_in": ["group:reviewer-group-name"]
    }
  ],
  "metadata": {
    "extracted_at": "2026-01-21T12:00:00Z",
    "total_rules": 123,
    "total_groups": 2,
    "total_users_resolved": 2,
    "total_users_unresolved": 1,
    "phabricator_instance": "phabricator.services.mozilla.com",
    "scrape_status": {
      "rules_complete": true,
      "groups_complete": true,
      "github_complete": true
    }
  }
}
```

</details>

**Notes:**
- `github_users` is a single mapping from Phabricator username to `{username, user_id}` object
- GitHub info for rule authors, reviewers, and group members is looked up via `github_users` (avoids duplication)
- `groups.members` is a simple list of usernames; GitHub info is in `github_users`
- `scrape_status` in metadata enables resumable scraping

## Scripts

The `scripts/` directory contains utility scripts for development and testing:

### Fixture Collection

- **`fetch_fixtures.py`**: Fetch Herald rule pages from Phabricator for test fixtures
  - Requires authentication via `PHABRICATOR_SESSION_COOKIE` environment variable
  - Can fetch specific rules, all rules, or recommended diverse set
  - Usage: `python scripts/fetch_fixtures.py --rules H420 H422 H425`

### Analysis Scripts

- **`analyze_listing.py`**: Analyze the Herald rules listing page (BeautifulSoup-based)
  - Extracts rule IDs and metadata from listing HTML
  - Identifies PHIDs and project references
  - Suggests diverse rules to fetch for testing
  - Usage: `python scripts/analyze_listing.py`

- **`analyze_listing_simple.py`**: Simplified listing analysis (regex-based)
  - Faster analysis using regular expressions
  - Provides recommendations for diverse test fixtures
  - Shows PHID types and project references
  - Usage: `python scripts/analyze_listing_simple.py`

- **`inspect_fixtures.py`**: Inspect structure of saved rule fixtures
  - Shows rule IDs, titles, breadcrumbs
  - Identifies rule types (Global, Personal, Object)
  - Helps understand HTML structure for parser development
  - Usage: `python scripts/inspect_fixtures.py`

- **`extract_conditions_actions.py`**: Extract conditions and actions text
  - Parses rule fixtures and extracts raw text sections
  - Shows natural language structure of conditions and actions
  - Useful for understanding parser requirements
  - Usage: `python scripts/extract_conditions_actions.py`

- **`analyze_html_structure.py`**: Detailed HTML structure analysis
  - Examines HTML elements and their relationships
  - Extracts regexp patterns and reviewer names
  - Helps with parser implementation
  - Usage: `python scripts/analyze_html_structure.py`

## License

Mozilla Public License 2.0
