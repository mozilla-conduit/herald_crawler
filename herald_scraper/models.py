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
    github_username: Optional[str] = Field(default=None, description="Resolved GitHub username")
    github_user_id: Optional[int] = Field(default=None, description="Resolved GitHub numeric user ID")

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
    author_github: Optional[str] = Field(default=None, description="Author's GitHub username if resolved")
    status: str
    type: str
    conditions: List[Condition] = Field(default_factory=list)
    actions: List[Action] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class Group(BaseModel):
    """Represents a reviewer group with its members."""
    id: str
    display_name: str
    members: List[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class UnresolvedUser(BaseModel):
    """Represents a user whose GitHub username couldn't be resolved."""
    phabricator_username: str
    reason: str = Field(..., description="Why resolution failed: 'not_found', 'no_github_linked', or error message")
    referenced_in: List[str] = Field(default_factory=list, description="Rule IDs that reference this user")

    model_config = {"extra": "forbid"}


class GitHubUser(BaseModel):
    """Resolved GitHub user information."""
    username: Optional[str] = Field(default=None, description="GitHub username")
    user_id: Optional[int] = Field(default=None, description="GitHub numeric user ID")

    model_config = {"extra": "forbid"}


class ScrapeStatus(BaseModel):
    """Tracks completion status of each scraping phase."""
    rules_complete: bool = Field(default=False, description="Whether all rules have been fetched")
    groups_complete: bool = Field(default=False, description="Whether all groups have been fetched")
    github_complete: bool = Field(default=False, description="Whether GitHub resolution is complete")

    model_config = {"extra": "forbid"}


class Metadata(BaseModel):
    """Metadata about the extraction."""
    extracted_at: datetime
    total_rules: int
    total_groups: int
    total_users_resolved: int = Field(default=0, description="Number of users with GitHub usernames resolved")
    total_users_unresolved: int = Field(default=0, description="Number of users without GitHub usernames")
    phabricator_instance: str
    scrape_status: Optional[ScrapeStatus] = Field(default=None, description="Completion status for resumable scraping")

    model_config = {"extra": "forbid"}


class HeraldRulesOutput(BaseModel):
    """Complete output structure for Herald rules extraction."""
    rules: List[Rule] = Field(default_factory=list)
    groups: Dict[str, Group] = Field(default_factory=dict)
    github_users: Dict[str, GitHubUser] = Field(
        default_factory=dict,
        description="Mapping of Phabricator username to GitHub user info",
    )
    unresolved_users: List[UnresolvedUser] = Field(
        default_factory=list,
        description="Users whose GitHub username couldn't be resolved",
    )
    metadata: Optional[Metadata] = None

    model_config = {"extra": "forbid"}
