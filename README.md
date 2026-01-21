# Herald Rules Scraper

A web scraper to extract Herald rules from Phabricator in a machine-parseable format.

## Overview

This tool extracts Herald rules from a Phabricator instance (specifically https://phabricator.services.mozilla.com/) and outputs them as structured JSON data with all PHIDs resolved to human-readable names.

## Features

- Extracts all Herald rules with conditions and actions
- Resolves PHIDs to usernames, emails, and group names
- Extracts group membership for reviewer groups
- Outputs structured JSON with complete metadata
- Uses Pydantic for data validation and type safety

## Installation

```bash
# Install dependencies
pip install -r requirements.txt

# For development
pip install -r requirements-dev.txt

# Or install as a package
pip install -e .
```

## Usage

```bash
herald-scraper \
  --instance https://phabricator.services.mozilla.com \
  --output rules.json \
  [--token API_TOKEN]
```

## Development

### Running Tests

```bash
pytest
```

### Code Formatting

```bash
black herald_scraper tests
ruff check herald_scraper tests
```

### Type Checking

```bash
mypy herald_scraper
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

## License

Mozilla Public License 2.0
