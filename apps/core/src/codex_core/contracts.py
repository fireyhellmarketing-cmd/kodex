from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


SCHEMA_VERSION = "1.0"


class MemoryScope(str, Enum):
    global_user = "global"
    project = "project"
    session = "session"
    task = "task"


class MemoryContract(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    scope: MemoryScope
    owner_id: str
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0, le=1)


class PermissionProfile(str, Enum):
    read_only = "read-only"
    confirm_edits = "confirm-edits"
    workspace = "workspace"
    full_access = "full-access"


class ApprovalKind(str, Enum):
    file_write = "file-write"
    process_launch = "process-launch"
    network_access = "network-access"
    destructive_action = "destructive-action"


class ApprovalContract(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    request_id: str
    kind: ApprovalKind
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    required_profile: PermissionProfile
    status: Literal["pending", "approved", "denied", "expired"] = "pending"


class AgentToolPolicy(BaseModel):
    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    approval_required: list[str] = Field(default_factory=list)


class AgentManifest(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str
    version: str
    role: str
    instructions: str
    permission_profile: PermissionProfile = PermissionProfile.workspace
    tools: AgentToolPolicy = Field(default_factory=AgentToolPolicy)


class WorkflowStep(BaseModel):
    id: str
    agent_id: str
    action: str
    depends_on: list[str] = Field(default_factory=list)
    continue_on_error: bool = False


class WorkflowManifest(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str
    version: str
    steps: list[WorkflowStep]


class SkillManifest(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str
    version: str = "1.0.0"
    description: str
    instructions_path: str
    trust: Literal["unreviewed", "reviewed", "trusted"] = "unreviewed"
    tools: list[str] = Field(default_factory=list)


class PluginManifest(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str
    version: str
    entrypoint: str
    permissions: list[str] = Field(default_factory=list)
    contributes: dict[str, list[str]] = Field(default_factory=dict)


class EventEnvelope(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    sequence: int
    event: str
    time: str
    data: dict[str, Any] = Field(default_factory=dict)


class ApiContract(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    api_version: Literal["v1"] = "v1"
    websocket_protocol: Literal["codex.events.v1"] = "codex.events.v1"
    agent_manifest: dict[str, Any]
    workflow_manifest: dict[str, Any]
    skill_manifest: dict[str, Any]
    plugin_manifest: dict[str, Any]
    memory: dict[str, Any]
    approval: dict[str, Any]


def contract_catalog() -> ApiContract:
    return ApiContract(
        agent_manifest=AgentManifest.model_json_schema(),
        workflow_manifest=WorkflowManifest.model_json_schema(),
        skill_manifest=SkillManifest.model_json_schema(),
        plugin_manifest=PluginManifest.model_json_schema(),
        memory=MemoryContract.model_json_schema(),
        approval=ApprovalContract.model_json_schema(),
    )
