# PII Anonymization Workflow

This document describes how to remove Personally Identifiable Information (PII) from test fixtures and git history.

## Overview

The anonymization process consists of four steps:

1. **Update test assertions** - Make tests PII-independent (must be done FIRST)
2. **Anonymize fixtures** - Replace real usernames, GitHub IDs, etc. with fake values
3. **Verify no PII remains** - Scan fixtures to confirm all PII has been removed
4. **Rewrite git history** - Remove PII from all historical commits

## Scripts

| Script | Purpose |
|--------|---------|
| `update_test_assertions.py` | Make test files PII-independent |
| `anonymize_fixtures.py` | Anonymize PII in fixture files |
| `verify_no_pii.py` | Verify no PII remains in fixtures |
| `rewrite_git_history.py` | Generate git-filter-repo expressions |

## Step-by-Step Workflow

### Step 1: Update Test Assertions (FIRST)

Update test files to be PII-independent BEFORE anonymization:

```bash
# Dry run first
python scripts/update_test_assertions.py --dry-run

# Apply changes
python scripts/update_test_assertions.py
```

This script:
- Replaces fixture-specific tests with generic tests that work with any fixture
- Removes hardcoded username assertions
- Changes member list checks to count-only checks

### Step 2: Verify Tests Pass (Before Anonymization)

Ensure tests still pass before anonymizing:

```bash
.venv/test/bin/pytest tests/ -v
```

### Step 3: Dry Run Anonymization

Run a dry run to see what would be changed:

```bash
python scripts/anonymize_fixtures.py --dry-run
```

This shows:
- Number of usernames, GitHub IDs, and GitHub usernames found
- Files that would be modified
- Files that would be renamed

### Step 4: Run Anonymization

Run the actual anonymization and save the mapping:

```bash
python scripts/anonymize_fixtures.py --save-mapping anonymization_mapping.json
```

**Important**: The mapping file contains the real-to-fake translations. DO NOT commit this file!

### Step 5: Run Tests (After Anonymization)

Verify tests still pass with anonymized fixtures:

```bash
.venv/test/bin/pytest tests/ -v
```

### Step 6: Verify No PII Remains

Scan fixtures to verify all PII has been removed:

```bash
# Basic check
python scripts/verify_no_pii.py

# Stricter check with mapping
python scripts/verify_no_pii.py --mapping anonymization_mapping.json --verbose
```

### Step 7: Commit Changes

Commit the anonymized fixtures and updated tests:

```bash
git add tests/fixtures tests/*.py
git commit -m "Anonymize PII in test fixtures"
```

### Step 8: Rewrite Git History (Optional)

If PII exists in git history, rewrite it:

```bash
# Generate expressions file
python scripts/rewrite_git_history.py --mapping anonymization_mapping.json --expressions-only

# Review the expressions file
cat git_filter_expressions.txt

# Run git-filter-repo (requires pip install git-filter-repo)
git filter-repo --replace-text git_filter_expressions.txt --force
```

**Warning**: This rewrites git history! All collaborators will need to re-clone.

## What Gets Anonymized

### Usernames (Phabricator)
- `/p/username/` paths in HTML
- `>username</a>` link text
- `title="username"` attributes
- Filenames like `username_graphql.json`
- Replaced with `user1`, `user2`, etc.

### GitHub IDs
- `"value": "123456"` in GraphQL responses
- Replaced with sequential IDs starting at 100001

### GitHub Usernames
- `"username": "someuser"` in REST responses
- Replaced with `ghuser1`, `ghuser2`, etc.

## Mapping Consistency

The anonymization maintains consistency:
- Same real username → same fake username across all files
- Related data stays linked (e.g., user1's GitHub becomes ghuser1)

## Files to NOT Commit

- `anonymization_mapping.json` - Contains real-to-fake translations
- `git_filter_expressions.txt` - Contains real values

Add these to `.gitignore`:

```
anonymization_mapping.json
git_filter_expressions.txt
```
