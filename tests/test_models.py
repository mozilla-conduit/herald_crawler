"""Tests for Herald scraper data models."""

import pytest
from pydantic import ValidationError

from herald_scraper.models import (
    Condition,
    Reviewer,
    Action,
    Rule,
    Group,
    Metadata,
    HeraldRulesOutput,
    RuleType,
    RuleStatus,
    ConditionOperator,
)


class TestCondition:
    """Tests for Condition model."""

    def test_create_condition(self):
        """Test creating a basic condition."""
        condition = Condition(
            type="differential-diff-content",
            operator="matches-regexp",
            value="^path/to/.*"
        )
        assert condition.type == "differential-diff-content"
        assert condition.operator == "matches-regexp"
        assert condition.value == "^path/to/.*"

    def test_condition_to_dict(self):
        """Test serialization to dictionary."""
        condition = Condition(
            type="differential-diff-content",
            operator="matches-regexp",
            value="^path/to/.*"
        )
        data = condition.model_dump()
        assert data == {
            "type": "differential-diff-content",
            "operator": "matches-regexp",
            "value": "^path/to/.*"
        }

    def test_condition_from_dict(self):
        """Test deserialization from dictionary."""
        data = {
            "type": "differential-diff-content",
            "operator": "matches-regexp",
            "value": "^path/to/.*"
        }
        condition = Condition(**data)
        assert condition.type == data["type"]
        assert condition.operator == data["operator"]
        assert condition.value == data["value"]

    def test_condition_with_complex_value(self):
        """Test condition with list or dict value."""
        condition = Condition(
            type="repository",
            operator="equals",
            value=["repo1", "repo2"]
        )
        assert condition.value == ["repo1", "repo2"]

    def test_condition_rejects_extra_fields(self):
        """Test that extra fields are rejected."""
        with pytest.raises(ValidationError):
            Condition(
                type="test",
                operator="equals",
                value="test",
                extra_field="not allowed"
            )


class TestReviewer:
    """Tests for Reviewer model."""

    def test_create_reviewer_blocking(self):
        """Test creating a blocking reviewer."""
        reviewer = Reviewer(target="user@example.com", blocking=True)
        assert reviewer.target == "user@example.com"
        assert reviewer.blocking is True

    def test_create_reviewer_non_blocking(self):
        """Test creating a non-blocking reviewer."""
        reviewer = Reviewer(target="group-name", blocking=False)
        assert reviewer.target == "group-name"
        assert reviewer.blocking is False

    def test_reviewer_default_blocking(self):
        """Test that blocking defaults to False."""
        reviewer = Reviewer(target="user@example.com")
        assert reviewer.blocking is False

    def test_reviewer_to_dict(self):
        """Test serialization to dictionary."""
        reviewer = Reviewer(target="user@example.com", blocking=True)
        data = reviewer.model_dump()
        assert data == {
            "target": "user@example.com",
            "blocking": True
        }


class TestAction:
    """Tests for Action model."""

    def test_create_action_with_reviewers(self):
        """Test creating an action with reviewers."""
        action = Action(
            type="add-reviewers",
            reviewers=[
                Reviewer(target="user@example.com", blocking=True),
                Reviewer(target="group-name", blocking=False)
            ]
        )
        assert action.type == "add-reviewers"
        assert len(action.reviewers) == 2
        assert action.reviewers[0].target == "user@example.com"
        assert action.reviewers[0].blocking is True

    def test_create_action_with_targets(self):
        """Test creating an action with targets."""
        action = Action(
            type="add-subscribers",
            targets=["user1@example.com", "user2@example.com"]
        )
        assert action.type == "add-subscribers"
        assert len(action.targets) == 2
        assert action.targets[0] == "user1@example.com"

    def test_action_to_dict(self):
        """Test serialization to dictionary."""
        action = Action(
            type="add-reviewers",
            reviewers=[Reviewer(target="user@example.com", blocking=True)]
        )
        data = action.model_dump()
        assert data["type"] == "add-reviewers"
        assert data["reviewers"][0]["target"] == "user@example.com"
        assert data["reviewers"][0]["blocking"] is True

    def test_action_from_dict(self):
        """Test deserialization from dictionary."""
        data = {
            "type": "add-reviewers",
            "reviewers": [
                {"target": "user@example.com", "blocking": True}
            ]
        }
        action = Action(**data)
        assert action.type == "add-reviewers"
        assert len(action.reviewers) == 1
        assert action.reviewers[0].target == "user@example.com"


class TestRule:
    """Tests for Rule model."""

    def test_create_minimal_rule(self):
        """Test creating a rule with minimal fields."""
        rule = Rule(
            id="H123",
            name="Test Rule",
            author="user@example.com",
            status="active",
            type="differential-revision"
        )
        assert rule.id == "H123"
        assert rule.name == "Test Rule"
        assert rule.author == "user@example.com"
        assert rule.status == "active"
        assert rule.type == "differential-revision"
        assert rule.conditions == []
        assert rule.actions == []
        assert rule.repository is None

    def test_create_complete_rule(self):
        """Test creating a complete rule with all fields."""
        rule = Rule(
            id="H123",
            name="Test Rule",
            author="user@example.com",
            status="active",
            type="differential-revision",
            repository="mozilla-central",
            conditions=[
                Condition(
                    type="differential-diff-content",
                    operator="matches-regexp",
                    value="^path/to/.*"
                )
            ],
            actions=[
                Action(
                    type="add-reviewers",
                    reviewers=[
                        Reviewer(target="reviewer@example.com", blocking=True)
                    ]
                )
            ]
        )
        assert rule.repository == "mozilla-central"
        assert len(rule.conditions) == 1
        assert len(rule.actions) == 1

    def test_rule_to_dict(self):
        """Test serialization to dictionary."""
        rule = Rule(
            id="H123",
            name="Test Rule",
            author="user@example.com",
            status="active",
            type="differential-revision",
            conditions=[
                Condition(
                    type="differential-diff-content",
                    operator="matches-regexp",
                    value="^path/to/.*"
                )
            ],
            actions=[
                Action(
                    type="add-reviewers",
                    reviewers=[
                        Reviewer(target="reviewer@example.com", blocking=True)
                    ]
                )
            ]
        )
        data = rule.model_dump()
        assert data["id"] == "H123"
        assert data["name"] == "Test Rule"
        assert len(data["conditions"]) == 1
        assert len(data["actions"]) == 1

    def test_rule_from_dict(self):
        """Test deserialization from dictionary."""
        data = {
            "id": "H123",
            "name": "Test Rule",
            "author": "user@example.com",
            "status": "active",
            "type": "differential-revision",
            "repository": "mozilla-central",
            "conditions": [
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
                        {"target": "reviewer@example.com", "blocking": True}
                    ]
                }
            ]
        }
        rule = Rule(**data)
        assert rule.id == "H123"
        assert rule.repository == "mozilla-central"
        assert len(rule.conditions) == 1
        assert len(rule.actions) == 1


class TestGroup:
    """Tests for Group model."""

    def test_create_group(self):
        """Test creating a group."""
        group = Group(
            id="group-slug",
            display_name="Group Display Name",
            members=["user1@example.com", "user2@example.com"]
        )
        assert group.id == "group-slug"
        assert group.display_name == "Group Display Name"
        assert len(group.members) == 2

    def test_group_empty_members(self):
        """Test group with no members."""
        group = Group(
            id="empty-group",
            display_name="Empty Group"
        )
        assert group.members == []

    def test_group_to_dict(self):
        """Test serialization to dictionary."""
        group = Group(
            id="group-slug",
            display_name="Group Display Name",
            members=["user1@example.com"]
        )
        data = group.model_dump()
        assert data == {
            "id": "group-slug",
            "display_name": "Group Display Name",
            "members": ["user1@example.com"]
        }


class TestMetadata:
    """Tests for Metadata model."""

    def test_create_metadata(self):
        """Test creating metadata."""
        metadata = Metadata(
            extracted_at="2026-01-21T12:00:00Z",
            total_rules=123,
            total_groups=5,
            phabricator_instance="phabricator.services.mozilla.com"
        )
        assert metadata.extracted_at == "2026-01-21T12:00:00Z"
        assert metadata.total_rules == 123
        assert metadata.total_groups == 5
        assert metadata.phabricator_instance == "phabricator.services.mozilla.com"

    def test_metadata_to_dict(self):
        """Test serialization to dictionary."""
        metadata = Metadata(
            extracted_at="2026-01-21T12:00:00Z",
            total_rules=123,
            total_groups=5,
            phabricator_instance="phabricator.services.mozilla.com"
        )
        data = metadata.model_dump()
        assert data["extracted_at"] == "2026-01-21T12:00:00Z"
        assert data["total_rules"] == 123


class TestHeraldRulesOutput:
    """Tests for HeraldRulesOutput model."""

    def test_create_empty_output(self):
        """Test creating an empty output structure."""
        output = HeraldRulesOutput()
        assert output.rules == []
        assert output.groups == {}
        assert output.metadata is None

    def test_create_complete_output(self):
        """Test creating a complete output structure."""
        output = HeraldRulesOutput(
            rules=[
                Rule(
                    id="H123",
                    name="Test Rule",
                    author="user@example.com",
                    status="active",
                    type="differential-revision",
                    repository="mozilla-central",
                    conditions=[
                        Condition(
                            type="differential-diff-content",
                            operator="matches-regexp",
                            value="^path/to/.*"
                        )
                    ],
                    actions=[
                        Action(
                            type="add-reviewers",
                            reviewers=[
                                Reviewer(target="reviewer-group", blocking=True)
                            ]
                        )
                    ]
                )
            ],
            groups={
                "reviewer-group": Group(
                    id="reviewer-group",
                    display_name="Reviewer Group",
                    members=["alice@example.com", "bob@example.com"]
                )
            },
            metadata=Metadata(
                extracted_at="2026-01-21T12:00:00Z",
                total_rules=1,
                total_groups=1,
                phabricator_instance="phabricator.services.mozilla.com"
            )
        )
        assert len(output.rules) == 1
        assert len(output.groups) == 1
        assert output.metadata.total_rules == 1

    def test_output_to_dict(self):
        """Test serialization to dictionary."""
        output = HeraldRulesOutput(
            rules=[
                Rule(
                    id="H123",
                    name="Test Rule",
                    author="user@example.com",
                    status="active",
                    type="differential-revision"
                )
            ],
            groups={
                "group1": Group(
                    id="group1",
                    display_name="Group 1",
                    members=["user@example.com"]
                )
            },
            metadata=Metadata(
                extracted_at="2026-01-21T12:00:00Z",
                total_rules=1,
                total_groups=1,
                phabricator_instance="phabricator.services.mozilla.com"
            )
        )
        data = output.model_dump()
        assert "rules" in data
        assert "groups" in data
        assert "metadata" in data
        assert len(data["rules"]) == 1
        assert "group1" in data["groups"]

    def test_output_from_dict(self):
        """Test deserialization from dictionary."""
        data = {
            "rules": [
                {
                    "id": "H123",
                    "name": "Test Rule",
                    "author": "user@example.com",
                    "status": "active",
                    "type": "differential-revision",
                    "conditions": [],
                    "actions": [],
                    "repository": None
                }
            ],
            "groups": {
                "group1": {
                    "id": "group1",
                    "display_name": "Group 1",
                    "members": ["user@example.com"]
                }
            },
            "metadata": {
                "extracted_at": "2026-01-21T12:00:00Z",
                "total_rules": 1,
                "total_groups": 1,
                "phabricator_instance": "phabricator.services.mozilla.com"
            }
        }
        output = HeraldRulesOutput(**data)
        assert len(output.rules) == 1
        assert output.rules[0].id == "H123"
        assert "group1" in output.groups
        assert output.metadata.total_rules == 1

    def test_output_json_roundtrip(self):
        """Test that output can be serialized to JSON and back."""
        import json

        output = HeraldRulesOutput(
            rules=[
                Rule(
                    id="H123",
                    name="Test Rule",
                    author="user@example.com",
                    status="active",
                    type="differential-revision",
                    conditions=[
                        Condition(
                            type="differential-diff-content",
                            operator="matches-regexp",
                            value="^path/to/.*"
                        )
                    ],
                    actions=[
                        Action(
                            type="add-reviewers",
                            reviewers=[
                                Reviewer(target="reviewer@example.com", blocking=True)
                            ]
                        )
                    ]
                )
            ],
            groups={
                "group1": Group(
                    id="group1",
                    display_name="Group 1",
                    members=["user@example.com"]
                )
            },
            metadata=Metadata(
                extracted_at="2026-01-21T12:00:00Z",
                total_rules=1,
                total_groups=1,
                phabricator_instance="phabricator.services.mozilla.com"
            )
        )

        # Serialize to JSON
        json_str = output.model_dump_json(indent=2)

        # Deserialize back
        data = json.loads(json_str)
        output2 = HeraldRulesOutput(**data)

        # Verify they match
        assert len(output2.rules) == len(output.rules)
        assert output2.rules[0].id == output.rules[0].id
        assert output2.rules[0].name == output.rules[0].name
        assert len(output2.rules[0].conditions) == len(output.rules[0].conditions)
        assert len(output2.rules[0].actions) == len(output.rules[0].actions)
        assert output2.groups.keys() == output.groups.keys()
        assert output2.metadata.total_rules == output.metadata.total_rules


class TestValidation:
    """Tests for Pydantic validation."""

    def test_rule_missing_required_field(self):
        """Test that missing required fields raise ValidationError."""
        with pytest.raises(ValidationError):
            Rule(
                id="H123",
                name="Test Rule",
                # Missing author, status, type
            )

    def test_reviewer_missing_target(self):
        """Test that missing target raises ValidationError."""
        with pytest.raises(ValidationError):
            Reviewer(blocking=True)  # Missing target

    def test_condition_missing_fields(self):
        """Test that missing condition fields raise ValidationError."""
        with pytest.raises(ValidationError):
            Condition(type="test")  # Missing operator and value

    def test_metadata_wrong_type(self):
        """Test that wrong types raise ValidationError."""
        with pytest.raises(ValidationError):
            Metadata(
                extracted_at="2026-01-21T12:00:00Z",
                total_rules="not a number",  # Should be int
                total_groups=5,
                phabricator_instance="phabricator.services.mozilla.com"
            )
