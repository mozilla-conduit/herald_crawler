"""Data models for Herald rules extraction using Pydantic."""

from datetime import datetime
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field


class Condition(BaseModel):
    """Represents a condition in a Herald rule."""
    type: str
    operator: str
    value: Any

    model_config = {"extra": "forbid"}


class Reviewer(BaseModel):
    """Represents a reviewer in an action."""
    target: str = Field(..., description="Username, email, or group name")
    blocking: bool = Field(default=False, description="Whether this is a blocking reviewer")

    model_config = {"extra": "forbid"}


class Action(BaseModel):
    """Represents an action in a Herald rule."""
    type: str
    reviewers: Optional[List[Reviewer]] = None
    targets: Optional[List[str]] = None

    model_config = {"extra": "forbid"}


class Rule(BaseModel):
    """Represents a Herald rule."""
    id: str
    name: str
    author: str
    status: str
    type: str
    conditions: List[Condition] = Field(default_factory=list)
    actions: List[Action] = Field(default_factory=list)
    repository: Optional[str] = None

    model_config = {"extra": "forbid"}


class Group(BaseModel):
    """Represents a reviewer group with its members."""
    id: str
    display_name: str
    members: List[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class Metadata(BaseModel):
    """Metadata about the extraction."""
    extracted_at: datetime
    total_rules: int
    total_groups: int
    phabricator_instance: str

    model_config = {"extra": "forbid"}


class HeraldRulesOutput(BaseModel):
    """Complete output structure for Herald rules extraction."""
    rules: List[Rule] = Field(default_factory=list)
    groups: Dict[str, Group] = Field(default_factory=dict)
    metadata: Optional[Metadata] = None

    model_config = {"extra": "forbid"}
