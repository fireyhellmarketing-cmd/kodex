from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import signal
import sqlite3
import subprocess
import shutil
import threading
import time
import asyncio
import secrets
from contextlib import contextmanager
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .contracts import EventEnvelope, SCHEMA_VERSION, contract_catalog
from .agent_runtime import AgentRuntime, detect_run_profiles, listening_ports
from .project_intelligence import (
    analyze_workspace,
    discover_skills,
    load_project_state,
    set_skill_trust,
    update_project_state,
)
from .platform_services import PlatformServices
from .designer import DEVICE_PROFILES, PROJECT_TEMPLATES, DesignerService
from .excellence import (
    compare_trajectories,
    read_workspace_session,
    run_benchmarks,
    runtime_adapters,
    write_workspace_session,
)


APP_VERSION = "0.1.0"
DEFAULT_DB_PATH = Path(os.environ.get("CODEX_CORE_DB", Path.home() / ".codex" / "codex-core.sqlite3"))
DEFAULT_WORKSPACE = Path(os.environ.get("CODEX_WORKSPACE", Path.cwd()))
CORE_AUTH_TOKEN = os.environ.get("CODEX_CORE_TOKEN", "")
CORE_SESSION_ID = os.environ.get("CODEX_SESSION_ID", secrets.token_hex(8))
LOG_LIMIT = 2_000
EVENT_LIMIT = 2_000

app = FastAPI(
    title="Kodex Core",
    version=APP_VERSION,
    openapi_tags=[
        {"name": "system", "description": "Core lifecycle, contracts, and diagnostics"},
        {"name": "workspace", "description": "Workspace-safe file operations"},
    ],
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

structured_logs: deque[dict[str, Any]] = deque(maxlen=LOG_LIMIT)
event_stream: deque[dict[str, Any]] = deque(maxlen=EVENT_LIMIT)
event_sequence = 0
event_lock = threading.Lock()


def write_log(level: str, message: str, **fields: Any) -> dict[str, Any]:
    entry = {
        "time": now_iso(),
        "level": level,
        "message": message,
        "service": "codex-core",
        **fields,
    }
    structured_logs.append(entry)
    return entry


def emit_event(event: str, data: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    global event_sequence
    with event_lock:
        event_sequence += 1
        envelope = EventEnvelope(
            sequence=event_sequence,
            event=event,
            time=now_iso(),
            data=data or {},
        ).model_dump()
        event_stream.append(envelope)
    return envelope


def supplied_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:]
    return request.headers.get("x-codex-token", "")


@app.middleware("http")
async def authenticated_local_session(request: Request, call_next: Any) -> Any:
    started = time.perf_counter()
    public = request.method == "OPTIONS" or request.url.path in {
        "/v1/health",
        "/docs",
        "/openapi.json",
    }
    if CORE_AUTH_TOKEN and not public and not secrets.compare_digest(
        supplied_token(request), CORE_AUTH_TOKEN
    ):
        return JSONResponse(status_code=401, content={"detail": "Invalid Kodex local session"})
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    write_log(
        "info" if response.status_code < 400 else "warning",
        "http.request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=duration_ms,
        session_id=CORE_SESSION_ID,
    )
    return response


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def db_path() -> Path:
    return DEFAULT_DB_PATH


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def connect() -> Generator[sqlite3.Connection, None, None]:
    ensure_parent(db_path())
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            create table if not exists memories (
                id integer primary key autoincrement,
                kind text not null,
                title text not null,
                content text not null,
                created_at text not null
            );

            create table if not exists tasks (
                id integer primary key autoincrement,
                title text not null,
                status text not null,
                details text not null,
                created_at text not null,
                updated_at text not null
            );

            create table if not exists sessions (
                id integer primary key autoincrement,
                summary text not null,
                created_at text not null
            );

            create table if not exists snapshots (
                id integer primary key autoincrement,
                label text not null,
                workspace text not null,
                file_count integer not null,
                total_bytes integer not null,
                created_at text not null
            );

            create table if not exists snapshot_files (
                snapshot_id integer not null,
                path text not null,
                content blob not null,
                mode integer not null,
                primary key (snapshot_id, path),
                foreign key (snapshot_id) references snapshots(id) on delete cascade
            );

            create table if not exists conversations (
                id integer primary key autoincrement,
                title text not null,
                created_at text not null,
                updated_at text not null
            );

            create table if not exists conversation_messages (
                id integer primary key autoincrement,
                conversation_id integer not null,
                role text not null,
                content text not null,
                created_at text not null,
                foreign key (conversation_id) references conversations(id) on delete cascade
            );

            create table if not exists agent_runs (
                id integer primary key autoincrement,
                conversation_id integer not null,
                status text not null,
                mode text not null,
                permission text not null,
                pursue_goal integer not null,
                model text not null,
                contract_json text not null,
                context_json text not null,
                summary text not null default '',
                progress_json text not null default '{}',
                failure_code text not null default '',
                failure_details_json text not null default '{}',
                changed_files_json text not null default '[]',
                validation_json text not null default '{}',
                recovery_attempts integer not null default 0,
                access_scope text not null default 'workspace',
                created_at text not null,
                updated_at text not null,
                foreign key (conversation_id) references conversations(id) on delete cascade
            );

            create table if not exists agent_steps (
                id integer primary key autoincrement,
                run_id integer not null,
                kind text not null,
                status text not null,
                title text not null,
                data_json text not null,
                created_at text not null,
                updated_at text not null,
                foreign key (run_id) references agent_runs(id) on delete cascade
            );

            create table if not exists approvals (
                id integer primary key autoincrement,
                run_id integer not null,
                kind text not null,
                status text not null,
                summary text not null,
                details_json text not null,
                created_at text not null,
                updated_at text not null,
                foreign key (run_id) references agent_runs(id) on delete cascade
            );

            create table if not exists permission_grants (
                tool text not null,
                workspace text not null,
                created_at text not null,
                primary key (tool, workspace)
            );
            create table if not exists agent_run_tasks (
                id integer primary key autoincrement,
                run_id integer not null,
                position integer not null,
                title text not null,
                status text not null,
                details_json text not null default '{}',
                created_at text not null,
                updated_at text not null
            );
            create table if not exists agent_checkpoints (
                id integer primary key autoincrement,
                run_id integer not null,
                phase text not null,
                state_json text not null,
                created_at text not null
            );
            create table if not exists agent_observations (
                id integer primary key autoincrement,
                run_id integer not null,
                kind text not null,
                data_json text not null,
                created_at text not null
            );
            create table if not exists agent_provider_attempts (
                id integer primary key autoincrement,
                run_id integer not null,
                provider text not null,
                model text not null,
                status text not null,
                error text not null default '',
                created_at text not null,
                updated_at text not null
            );
            create table if not exists agent_steering_messages (
                id integer primary key autoincrement,
                run_id integer not null,
                content text not null,
                status text not null default 'queued',
                created_at text not null,
                consumed_at text,
                attachments_json text not null default '[]'
            );
            """
        )
        run_columns = {row["name"] for row in conn.execute("pragma table_info(agent_runs)")}
        conversation_columns = {
            row["name"] for row in conn.execute("pragma table_info(conversations)")
        }
        if "workspace" not in conversation_columns:
            conn.execute(
                "alter table conversations add column workspace text not null default ''"
            )
        unscoped = conn.execute(
            "select id from conversations where workspace = ''"
        ).fetchall()
        for conversation in unscoped:
            runs = conn.execute(
                """
                select context_json from agent_runs
                where conversation_id = ?
                order by id desc
                """,
                (conversation["id"],),
            ).fetchall()
            workspace = ""
            for run in runs:
                try:
                    context = json.loads(run["context_json"] or "{}")
                except ValueError:
                    continue
                workspace = str(
                    context.get("workspace")
                    or (context.get("diagnostics") or {}).get("workspace")
                    or ""
                )
                if workspace:
                    break
            if workspace:
                conn.execute(
                    "update conversations set workspace = ? where id = ?",
                    (workspace, conversation["id"]),
                )
        migrations = {
            "progress_json": "text not null default '{}'",
            "failure_code": "text not null default ''",
            "failure_details_json": "text not null default '{}'",
            "changed_files_json": "text not null default '[]'",
            "validation_json": "text not null default '{}'",
            "recovery_attempts": "integer not null default 0",
            "access_scope": "text not null default 'workspace'",
            "phase": "text not null default 'understand'",
            "goal_json": "text not null default '{}'",
            "checkpoint_json": "text not null default '{}'",
            "completion_evidence_json": "text not null default '{}'",
            "agent_profile_id": "integer",
            "agent_profile_version": "integer",
            "requested_mode": "text not null default 'auto'",
            "resolved_intent": "text not null default 'execute'",
            "intent_confidence": "real not null default 1.0",
            "structured_plan_json": "text not null default '{}'",
            "reflection_json": "text not null default '{}'",
            "specialist_activity_json": "text not null default '[]'",
        }
        for column, definition in migrations.items():
            if column not in run_columns:
                conn.execute(f"alter table agent_runs add column {column} {definition}")
        steering_columns = {
            row["name"]
            for row in conn.execute("pragma table_info(agent_steering_messages)")
        }
        if "attachments_json" not in steering_columns:
            conn.execute(
                "alter table agent_steering_messages add column attachments_json text not null default '[]'"
            )


@app.on_event("startup")
def startup() -> None:
    init_db()
    platform_services.init_schema()
    interrupted = agent_runtime.recover_interrupted()
    intelligence = analyze_workspace(DEFAULT_WORKSPACE)
    update_project_state(DEFAULT_WORKSPACE, intelligence)
    start_workspace_watcher()
    start_lifecycle_monitor()
    write_log(
        "info",
        "core.started",
        version=APP_VERSION,
        workspace=str(DEFAULT_WORKSPACE.resolve()),
        authenticated=bool(CORE_AUTH_TOKEN),
        session_id=CORE_SESSION_ID,
    )
    emit_event("core.ready", {"version": APP_VERSION, "session_id": CORE_SESSION_ID})
    if interrupted:
        emit_event("agent.runs_interrupted", {"count": interrupted})


@app.on_event("shutdown")
def shutdown() -> None:
    emit_event("core.stopping", {"session_id": CORE_SESSION_ID})
    write_log("info", "core.stopping", session_id=CORE_SESSION_ID)
    stop_workspace_watcher()
    stop_lifecycle_monitor()


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    db_path: str


class MemoryCreate(BaseModel):
    kind: str = Field(default="project")
    title: str
    content: str


class MemoryRecord(MemoryCreate):
    id: int
    created_at: str


class TaskCreate(BaseModel):
    title: str
    details: str = ""


class TaskUpdate(BaseModel):
    status: Optional[str] = None
    details: Optional[str] = None


class TaskRecord(TaskCreate):
    id: int
    status: str
    created_at: str
    updated_at: str


class SessionCreate(BaseModel):
    summary: str = ""


class RunRequest(BaseModel):
    command: list[str]
    cwd: str = ""
    name: str = "run"


class FileWriteRequest(BaseModel):
    path: str
    content: str


class FileCreateRequest(BaseModel):
    path: str
    kind: str = Field(pattern="^(file|directory)$")
    content: str = ""


class FileMoveRequest(BaseModel):
    source: str
    destination: str


class SnapshotCreateRequest(BaseModel):
    label: str = "Manual snapshot"


class TerminalRequest(BaseModel):
    shell: str = "bash"
    cwd: str = ""


class ConversationCreateRequest(BaseModel):
    title: str = "New conversation"


class AgentMessageRequest(BaseModel):
    content: str
    mode: str = Field(default="auto", pattern="^(chat|plan|auto)$")
    pursue_goal: bool = False
    permission: str = Field(
        default="workspace",
        pattern="^(read-only|confirm-edits|workspace|full-access)$",
    )
    context: dict[str, Any] = Field(default_factory=dict)
    model: str = "auto"
    agent_profile_id: Optional[int] = None


class AgentSteeringRequest(BaseModel):
    content: str
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class ApprovalResolutionRequest(BaseModel):
    approved: bool
    always_allow: bool = False


class AgentRetryRequest(BaseModel):
    mode: str = Field(default="repair", pattern="^(repair|fresh)$")


class StructuredMemoryRequest(BaseModel):
    scope: str = "project"
    owner_id: str = ""
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0, le=1)
    status: str = "active"
    source: str = "user"
    resolve_conflicts: bool = False


class MemoryImportRequest(BaseModel):
    memories: list[dict[str, Any]] = Field(default_factory=list)


class AgentProfileRequest(BaseModel):
    agent_id: str
    name: str
    role: str = "coder"
    prompt: str
    constitution: str
    workflow: list[dict[str, Any]] = Field(default_factory=list)
    tool_policy: dict[str, Any] = Field(default_factory=dict)


class AgentPromotionRequest(BaseModel):
    status: str = Field(pattern="^(candidate|active|archived)$")


class EvaluationRunRequest(BaseModel):
    agent_id: str
    agent_version: int
    fixture: str
    score: float = Field(ge=0, le=1)
    passing_score: float = Field(default=0.7, ge=0, le=1)
    duration_ms: int = 0
    tool_calls: int = 0
    failures: list[str] = Field(default_factory=list)
    output: str = ""


class GitActionRequest(BaseModel):
    paths: list[str] = Field(default_factory=list)
    message: str = ""
    name: str = ""
    confirmed: bool = False


class GitSummaryRequest(BaseModel):
    staged: bool = False
    kind: str = Field(default="change", pattern="^(change|feat|fix|docs|test|refactor|chore)$")


class SkillTrustRequest(BaseModel):
    trust: str = Field(pattern="^(trusted|review|disabled)$")


class WorkspaceSessionRequest(BaseModel):
    open_files: list[str] = Field(default_factory=list)
    active_file: str = ""
    unfinished_task_ids: list[int] = Field(default_factory=list)


class BenchmarkRequest(BaseModel):
    adapter: str = Field(default="local", pattern="^(local|docker|podman)$")


class DiagnosticsParseRequest(BaseModel):
    output: str


class TestRunRequest(BaseModel):
    profile_id: str = ""


class McpServerRequest(BaseModel):
    name: str
    command: list[str]
    env: dict[str, str] = Field(default_factory=dict)
    permission: str = Field(
        default="read-only", pattern="^(read-only|confirm-edits|workspace)$"
    )
    enabled: bool = True


class McpInvokeRequest(BaseModel):
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class ModelDownloadRequest(BaseModel):
    model: str
    runtime: str = Field(default="ollama", pattern="^ollama$")


class DatabaseRestoreRequest(BaseModel):
    path: str


class DesignerTransactionRequest(BaseModel):
    path: str
    operation: str = Field(pattern="^(add|update|delete)$")
    node_id: str = ""
    component: str = ""
    property: str = ""
    value: str = ""
    label: str = ""
    expected_hash: str = ""


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def resolve_workspace_path(path: str, *, allow_root: bool = True) -> Path:
    workspace_root = DEFAULT_WORKSPACE.resolve()
    target = (workspace_root / path).resolve()
    if workspace_root not in target.parents and target != workspace_root:
        raise HTTPException(status_code=400, detail="Path is outside the workspace")
    if not allow_root and target == workspace_root:
        raise HTTPException(status_code=400, detail="Operation is not allowed on the workspace root")
    return target


def relative_workspace_path(path: Path) -> str:
    relative = path.relative_to(DEFAULT_WORKSPACE.resolve())
    return "." if str(relative) == "." else str(relative)


SEARCH_IGNORED_DIRECTORIES = {
    ".git",
    ".idea",
    ".next",
    ".pytest_cache",
    ".venv",
    ".vite",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "venv",
}
SEARCH_MAX_FILE_BYTES = 1_000_000
SEARCH_MAX_RESULTS = 250
WORKSPACE_EVENT_LIMIT = 2_000
SNAPSHOT_MAX_FILE_BYTES = 25_000_000
SNAPSHOT_MAX_TOTAL_BYTES = 250_000_000
SYMBOL_PATTERNS = (
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)"),
    re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_$][\w$]*)"),
    re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*="),
    re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_][\w]*)"),
    re.compile(r"^\s*class\s+([A-Za-z_][\w]*)"),
)


def workspace_text_files(root: Path) -> Generator[Path, None, None]:
    if root.is_file():
        if root.stat().st_size <= SEARCH_MAX_FILE_BYTES:
            yield root
        return
    workspace_root = DEFAULT_WORKSPACE.resolve()
    for current_root, directories, filenames in os.walk(root):
        directories[:] = [
            name for name in directories if name not in SEARCH_IGNORED_DIRECTORIES
        ]
        current = Path(current_root)
        for filename in filenames:
            candidate = current / filename
            try:
                resolved = candidate.resolve()
                if workspace_root not in resolved.parents and resolved != workspace_root:
                    continue
                if resolved.stat().st_size <= SEARCH_MAX_FILE_BYTES:
                    yield resolved
            except OSError:
                continue


def read_search_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []


def workspace_snapshot_files() -> Generator[Path, None, None]:
    workspace_root = DEFAULT_WORKSPACE.resolve()
    database = db_path().resolve()
    for current_root, directories, filenames in os.walk(workspace_root):
        directories[:] = [
            name for name in directories if name not in SEARCH_IGNORED_DIRECTORIES
        ]
        current = Path(current_root)
        for filename in filenames:
            candidate = current / filename
            try:
                resolved = candidate.resolve()
                if workspace_root not in resolved.parents or resolved == database:
                    continue
                if resolved.stat().st_size <= SNAPSHOT_MAX_FILE_BYTES:
                    yield resolved
            except OSError:
                continue


def create_workspace_snapshot(label: str) -> dict[str, Any]:
    clean_label = label.strip() or "Manual snapshot"
    files: list[tuple[str, bytes, int]] = []
    total_bytes = 0
    for file_path in workspace_snapshot_files():
        content = file_path.read_bytes()
        total_bytes += len(content)
        if total_bytes > SNAPSHOT_MAX_TOTAL_BYTES:
            raise HTTPException(status_code=413, detail="Workspace snapshot exceeds size limit")
        files.append(
            (
                relative_workspace_path(file_path),
                content,
                file_path.stat().st_mode & 0o777,
            )
        )
    with connect() as conn:
        cursor = conn.execute(
            """
            insert into snapshots(label, workspace, file_count, total_bytes, created_at)
            values (?, ?, ?, ?, ?)
            """,
            (
                clean_label,
                str(DEFAULT_WORKSPACE.resolve()),
                len(files),
                total_bytes,
                now_iso(),
            ),
        )
        snapshot_id = int(cursor.lastrowid)
        conn.executemany(
            "insert into snapshot_files(snapshot_id, path, content, mode) values (?, ?, ?, ?)",
            ((snapshot_id, path, content, mode) for path, content, mode in files),
        )
        row = conn.execute("select * from snapshots where id = ?", (snapshot_id,)).fetchone()
    return row_to_dict(row)


def snapshot_record(snapshot_id: int) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("select * from snapshots where id = ?", (snapshot_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    record = row_to_dict(row)
    if record["workspace"] != str(DEFAULT_WORKSPACE.resolve()):
        raise HTTPException(status_code=409, detail="Snapshot belongs to another workspace")
    return record


def snapshot_file_map(snapshot_id: int) -> dict[str, tuple[bytes, int]]:
    snapshot_record(snapshot_id)
    with connect() as conn:
        rows = conn.execute(
            "select path, content, mode from snapshot_files where snapshot_id = ? order by path",
            (snapshot_id,),
        ).fetchall()
    return {row["path"]: (bytes(row["content"]), row["mode"]) for row in rows}


def current_workspace_file_map() -> dict[str, tuple[bytes, int]]:
    return {
        relative_workspace_path(file_path): (
            file_path.read_bytes(),
            file_path.stat().st_mode & 0o777,
        )
        for file_path in workspace_snapshot_files()
    }


def text_content(content: bytes) -> Optional[str]:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return None


def snapshot_diff(snapshot_id: int) -> dict[str, Any]:
    record = snapshot_record(snapshot_id)
    before = snapshot_file_map(snapshot_id)
    current = current_workspace_file_map()
    paths = sorted(before.keys() | current.keys())
    files: list[dict[str, Any]] = []
    additions = 0
    deletions = 0
    for path in paths:
        old_entry = before.get(path)
        new_entry = current.get(path)
        if old_entry == new_entry:
            continue
        status = "modified"
        if old_entry is None:
            status = "added"
        elif new_entry is None:
            status = "deleted"
        old_bytes = old_entry[0] if old_entry else b""
        new_bytes = new_entry[0] if new_entry else b""
        old_text = text_content(old_bytes)
        new_text = text_content(new_bytes)
        binary = old_text is None or new_text is None
        diff = ""
        file_additions = 0
        file_deletions = 0
        if not binary:
            diff_lines = list(
                difflib.unified_diff(
                    old_text.splitlines(),
                    new_text.splitlines(),
                    fromfile=f"a/{path}",
                    tofile=f"b/{path}",
                    lineterm="",
                )
            )
            diff = "\n".join(diff_lines)
            file_additions = sum(
                1 for line in diff_lines if line.startswith("+") and not line.startswith("+++")
            )
            file_deletions = sum(
                1 for line in diff_lines if line.startswith("-") and not line.startswith("---")
            )
        additions += file_additions
        deletions += file_deletions
        files.append(
            {
                "path": path,
                "status": status,
                "binary": binary,
                "additions": file_additions,
                "deletions": file_deletions,
                "diff": diff,
                "before_hash": hashlib.sha256(old_bytes).hexdigest() if old_entry else None,
                "current_hash": hashlib.sha256(new_bytes).hexdigest() if new_entry else None,
            }
        )
    return {
        "snapshot": record,
        "files": files,
        "file_count": len(files),
        "additions": additions,
        "deletions": deletions,
    }


def apply_snapshot_file_map(files: dict[str, tuple[bytes, int]]) -> None:
    workspace_root = DEFAULT_WORKSPACE.resolve()
    current_paths = set(current_workspace_file_map())
    snapshot_paths = set(files)
    for relative_path in sorted(current_paths - snapshot_paths, reverse=True):
        target = resolve_workspace_path(relative_path, allow_root=False)
        if target.exists():
            target.unlink()
    for relative_path, (content, mode) in files.items():
        target = resolve_workspace_path(relative_path, allow_root=False)
        ensure_parent(target)
        temporary = target.with_name(f".{target.name}.codex-restore")
        temporary.write_bytes(content)
        os.chmod(temporary, mode)
        temporary.replace(target)
    for current_root, directories, filenames in os.walk(workspace_root, topdown=False):
        if filenames:
            continue
        current = Path(current_root)
        for directory in directories:
            candidate = current / directory
            if candidate.name in SEARCH_IGNORED_DIRECTORIES:
                continue
            try:
                candidate.rmdir()
            except OSError:
                pass


def restore_workspace_snapshot(snapshot_id: int) -> dict[str, Any]:
    target_snapshot = snapshot_record(snapshot_id)
    target_files = snapshot_file_map(snapshot_id)
    safety_snapshot = create_workspace_snapshot(
        f"Automatic safety snapshot before restoring #{snapshot_id}"
    )
    try:
        apply_snapshot_file_map(target_files)
    except Exception as error:
        apply_snapshot_file_map(snapshot_file_map(safety_snapshot["id"]))
        raise HTTPException(status_code=500, detail="Restore failed; safety snapshot reapplied") from error
    scan_workspace_changes()
    return {
        "restored": True,
        "snapshot": target_snapshot,
        "safety_snapshot": safety_snapshot,
    }


workspace_events: list[dict[str, Any]] = []
workspace_event_sequence = 0
workspace_snapshot: dict[str, tuple[str, int, int]] = {}
workspace_watcher_thread: Optional[threading.Thread] = None
workspace_watcher_stop = threading.Event()
workspace_watcher_lock = threading.Lock()
lifecycle_monitor_thread: Optional[threading.Thread] = None
lifecycle_monitor_stop = threading.Event()


def workspace_metadata_snapshot() -> dict[str, tuple[str, int, int]]:
    root = DEFAULT_WORKSPACE.resolve()
    snapshot: dict[str, tuple[str, int, int]] = {}
    for current_root, directories, filenames in os.walk(root):
        directories[:] = [
            name for name in directories if name not in SEARCH_IGNORED_DIRECTORIES
        ]
        current = Path(current_root)
        for directory in directories:
            candidate = current / directory
            try:
                resolved = candidate.resolve()
                if root not in resolved.parents:
                    continue
                stat = resolved.stat()
                snapshot[relative_workspace_path(resolved)] = (
                    "directory",
                    stat.st_mtime_ns,
                    0,
                )
            except OSError:
                continue
        for filename in filenames:
            candidate = current / filename
            try:
                resolved = candidate.resolve()
                if root not in resolved.parents:
                    continue
                stat = resolved.stat()
                snapshot[relative_workspace_path(resolved)] = (
                    "file",
                    stat.st_mtime_ns,
                    stat.st_size,
                )
            except OSError:
                continue
    return snapshot


def append_workspace_event(event_type: str, path: str, kind: str) -> None:
    global workspace_event_sequence
    workspace_event_sequence += 1
    workspace_events.append(
        {
            "sequence": workspace_event_sequence,
            "type": event_type,
            "path": path,
            "kind": kind,
            "time": now_iso(),
        }
    )
    if len(workspace_events) > WORKSPACE_EVENT_LIMIT:
        del workspace_events[:-WORKSPACE_EVENT_LIMIT]
    emit_event(
        f"workspace.{event_type}",
        {"type": event_type, "path": path, "kind": kind, "workspace_sequence": workspace_event_sequence},
    )


def scan_workspace_changes() -> None:
    global workspace_snapshot
    latest = workspace_metadata_snapshot()
    with workspace_watcher_lock:
        previous = workspace_snapshot
        for path in sorted(latest.keys() - previous.keys()):
            append_workspace_event("created", path, latest[path][0])
        for path in sorted(previous.keys() - latest.keys()):
            append_workspace_event("deleted", path, previous[path][0])
        for path in sorted(latest.keys() & previous.keys()):
            if latest[path] != previous[path]:
                append_workspace_event("modified", path, latest[path][0])
        workspace_snapshot = latest


def workspace_watcher_loop() -> None:
    while not workspace_watcher_stop.wait(0.75):
        try:
            scan_workspace_changes()
        except OSError:
            time.sleep(0.25)


def start_workspace_watcher() -> None:
    global workspace_snapshot, workspace_watcher_thread
    if workspace_watcher_thread and workspace_watcher_thread.is_alive():
        return
    workspace_snapshot = workspace_metadata_snapshot()
    workspace_watcher_stop.clear()
    workspace_watcher_thread = threading.Thread(
        target=workspace_watcher_loop,
        name="codex-workspace-watcher",
        daemon=True,
    )
    workspace_watcher_thread.start()


def stop_workspace_watcher() -> None:
    global workspace_watcher_thread
    workspace_watcher_stop.set()
    if workspace_watcher_thread:
        workspace_watcher_thread.join(timeout=2)
    workspace_watcher_thread = None


def lifecycle_monitor_loop() -> None:
    parent_pid = int(os.environ.get("CODEX_PARENT_PID", "0") or "0")
    lifecycle_file = os.environ.get("CODEX_LIFECYCLE_FILE", "")
    if not parent_pid or not lifecycle_file:
        return
    while not lifecycle_monitor_stop.wait(1):
        background_core = False
        try:
            payload = json.loads(Path(lifecycle_file).read_text(encoding="utf-8"))
            background_core = bool(payload.get("backgroundCore"))
        except (OSError, ValueError, TypeError):
            pass
        try:
            os.kill(parent_pid, 0)
            parent_alive = True
        except OSError:
            parent_alive = False
        if not parent_alive and not background_core:
            write_log("warning", "core.parent-exited", parent_pid=parent_pid)
            os.kill(os.getpid(), signal.SIGTERM)
            return


def start_lifecycle_monitor() -> None:
    global lifecycle_monitor_thread
    lifecycle_monitor_stop.clear()
    lifecycle_monitor_thread = threading.Thread(
        target=lifecycle_monitor_loop,
        name="codex-lifecycle-monitor",
        daemon=True,
    )
    lifecycle_monitor_thread.start()


def stop_lifecycle_monitor() -> None:
    global lifecycle_monitor_thread
    lifecycle_monitor_stop.set()
    if lifecycle_monitor_thread:
        lifecycle_monitor_thread.join(timeout=2)
    lifecycle_monitor_thread = None


processes: dict[int, dict[str, Any]] = {}
process_counter = 0
agent_runtime = AgentRuntime(
    DEFAULT_WORKSPACE,
    db_path,
    emit_event,
    create_workspace_snapshot,
)
platform_services = PlatformServices(DEFAULT_WORKSPACE, db_path, emit_event)
designer_service = DesignerService(DEFAULT_WORKSPACE, emit_event)


def add_process_log(proc_id: int, stream: str, line: str) -> None:
    entry = {"stream": stream, "line": line.rstrip("\n"), "time": now_iso()}
    processes[proc_id]["logs"].append(entry)
    emit_event("process.output", {"process_id": proc_id, **entry})


def stream_process_output(proc_id: int, pipe: Any, stream: str) -> None:
    assert pipe is not None
    for line in iter(pipe.readline, ""):
        add_process_log(proc_id, stream, line)
    pipe.close()


def register_process(command: list[str], cwd: str, name: str) -> dict[str, Any]:
    global process_counter
    process_counter += 1
    proc_id = process_counter
    child_environment = os.environ.copy()
    for inherited_debugger_variable in (
        "NODE_OPTIONS",
        "VSCODE_INSPECTOR_OPTIONS",
        "ELECTRON_RUN_AS_NODE",
    ):
        child_environment.pop(inherited_debugger_variable, None)
    proc = subprocess.Popen(
        command,
        cwd=cwd or str(DEFAULT_WORKSPACE),
        env=child_environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        text=True,
    )
    processes[proc_id] = {
        "id": proc_id,
        "name": name,
        "command": command,
        "cwd": cwd or str(DEFAULT_WORKSPACE),
        "pid": proc.pid,
        "status": "running",
        "started_at": now_iso(),
        "logs": [],
        "process": proc,
    }
    write_log("info", "process.started", process_id=proc_id, name=name, pid=proc.pid, command=command)
    emit_event("process.started", {"process_id": proc_id, "name": name, "pid": proc.pid})
    threading.Thread(target=stream_process_output, args=(proc_id, proc.stdout, "stdout"), daemon=True).start()
    threading.Thread(target=stream_process_output, args=(proc_id, proc.stderr, "stderr"), daemon=True).start()

    def wait_for_exit() -> None:
        code = proc.wait()
        if processes[proc_id]["status"] == "stopping":
            processes[proc_id]["status"] = "stopped"
        else:
            processes[proc_id]["status"] = "exited" if code == 0 else "failed"
        processes[proc_id]["exit_code"] = code
        processes[proc_id]["ended_at"] = now_iso()
        write_log(
            "info" if code == 0 else "warning",
            "process.exited",
            process_id=proc_id,
            name=name,
            exit_code=code,
        )
        emit_event(
            "process.exited",
            {"process_id": proc_id, "name": name, "exit_code": code, "status": processes[proc_id]["status"]},
        )

    threading.Thread(target=wait_for_exit, daemon=True).start()
    snapshot = {k: v for k, v in processes[proc_id].items() if k != "process"}
    return snapshot


@app.get("/v1/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="codex-core", version=APP_VERSION, db_path=str(db_path()))


@app.get("/v1/auth/session", tags=["system"])
def authenticated_session() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": CORE_SESSION_ID,
        "authenticated": True,
        "transport": "local-bearer",
    }


@app.get("/v1/contracts", tags=["system"])
def contracts() -> dict[str, Any]:
    return contract_catalog().model_dump()


@app.get("/v1/sdk", tags=["system"])
def sdk_contracts() -> dict[str, Any]:
    catalog = contract_catalog().model_dump()
    return {
        "schema_version": SCHEMA_VERSION,
        "plugin": catalog["plugin_manifest"],
        "skill": catalog["skill_manifest"],
        "mcp_transport": "json-rpc-2.0-stdio",
        "permission_profiles": [
            "read-only",
            "confirm-edits",
            "workspace",
            "full-access",
        ],
    }


@app.get("/v1/logs", tags=["system"])
def logs(limit: int = 200, level: Optional[str] = None) -> dict[str, Any]:
    safe_limit = min(max(limit, 1), LOG_LIMIT)
    entries = list(structured_logs)
    if level:
        entries = [entry for entry in entries if entry["level"] == level]
    return {"schema_version": SCHEMA_VERSION, "entries": entries[-safe_limit:]}


@app.get("/v1/diagnostics", tags=["system"])
def diagnostics() -> dict[str, Any]:
    with connect() as conn:
        tables = {
            name: conn.execute(f"select count(*) as c from {name}").fetchone()["c"]
            for name in ("memories", "tasks", "sessions", "snapshots")
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "version": APP_VERSION,
        "session_id": CORE_SESSION_ID,
        "authenticated": bool(CORE_AUTH_TOKEN),
        "workspace": str(DEFAULT_WORKSPACE.resolve()),
        "database": str(db_path()),
        "processes": {
            "total": len(processes),
            "running": sum(1 for process in processes.values() if process["status"] == "running"),
        },
        "events": {"sequence": event_sequence, "retained": len(event_stream)},
        "logs": {"retained": len(structured_logs)},
        "records": tables,
        "watcher": bool(workspace_watcher_thread and workspace_watcher_thread.is_alive()),
    }


@app.websocket("/events")
async def events(websocket: WebSocket) -> None:
    token = websocket.query_params.get("token", "")
    if CORE_AUTH_TOKEN and not secrets.compare_digest(token, CORE_AUTH_TOKEN):
        await websocket.close(code=4401, reason="Invalid Kodex local session")
        return
    await websocket.accept(subprotocol="codex.events.v1")
    cursor = int(websocket.query_params.get("since", "0") or "0")
    try:
        await websocket.send_json(
            EventEnvelope(
                sequence=event_sequence,
                event="session.connected",
                time=now_iso(),
                data={"session_id": CORE_SESSION_ID},
            ).model_dump()
        )
        while True:
            with event_lock:
                pending = [event.copy() for event in event_stream if event["sequence"] > cursor]
            for event in pending:
                await websocket.send_json(event)
                cursor = event["sequence"]
            try:
                message = await asyncio.wait_for(websocket.receive(), timeout=0.2)
                if message["type"] == "websocket.disconnect":
                    return
            except asyncio.TimeoutError:
                continue
    except (WebSocketDisconnect, RuntimeError):
        return


@app.get("/v1/state")
def state() -> dict[str, Any]:
    with connect() as conn:
        memories = conn.execute("select count(*) as c from memories").fetchone()["c"]
        tasks = conn.execute("select count(*) as c from tasks").fetchone()["c"]
    return {
        "product": "Kodex",
        "mode": "mvp",
        "schema_version": SCHEMA_VERSION,
        "features": ["local-core-api", "desktop-shell", "project-memory", "task-tracking"],
        "counts": {"memories": memories, "tasks": tasks},
    }


@app.get("/v1/providers")
def provider_list() -> list[dict[str, Any]]:
    return agent_runtime.providers()


@app.get("/v1/providers/codex/status")
def codex_provider_status() -> dict[str, Any]:
    return agent_runtime.codex_status(refresh=True)


@app.get("/v1/providers/claude/status")
def claude_provider_status() -> dict[str, Any]:
    return agent_runtime.claude_status(refresh=True)


@app.get("/v1/agents")
def agents() -> list[dict[str, Any]]:
    return agent_runtime.agent_manifests()


@app.get("/v1/tools")
def tools() -> list[dict[str, Any]]:
    return agent_runtime.tool_registry()


@app.get("/v1/models")
def model_list() -> dict[str, Any]:
    providers = agent_runtime.providers()
    return {
        "providers": providers,
        "models": [
            {**model, "provider": provider["id"], "kind": provider["kind"]}
            for provider in providers
            for model in provider["models"]
        ],
    }


@app.post("/v1/providers/test")
def provider_test() -> dict[str, Any]:
    providers = agent_runtime.providers()
    return {
        "available": [provider["id"] for provider in providers if provider["available"]],
        "providers": providers,
    }


@app.post("/v1/providers/{provider_id}/test")
def provider_test_one(provider_id: str) -> dict[str, Any]:
    try:
        return agent_runtime.test_provider(provider_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get("/v1/run-profiles")
def run_profiles() -> list[dict[str, Any]]:
    return detect_run_profiles(DEFAULT_WORKSPACE)


@app.get("/v1/project-intelligence")
def project_intelligence() -> dict[str, Any]:
    intelligence = analyze_workspace(DEFAULT_WORKSPACE)
    update_project_state(DEFAULT_WORKSPACE, intelligence)
    return intelligence


@app.get("/v1/project-state")
def project_state() -> dict[str, Any]:
    state = load_project_state(DEFAULT_WORKSPACE)
    if state:
        return state
    intelligence = analyze_workspace(DEFAULT_WORKSPACE)
    return update_project_state(DEFAULT_WORKSPACE, intelligence)


@app.get("/v1/skills")
def skills() -> list[dict[str, Any]]:
    return discover_skills(DEFAULT_WORKSPACE)


@app.patch("/v1/skills/{skill_id:path}")
def skill_trust_update(skill_id: str, payload: SkillTrustRequest) -> dict[str, Any]:
    try:
        result = set_skill_trust(DEFAULT_WORKSPACE, skill_id, payload.trust)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    emit_event("skill.trust_changed", {"skill_id": skill_id, "trust": payload.trust})
    return result


@app.get("/v1/workspace/session")
def workspace_session_get() -> dict[str, Any]:
    session = read_workspace_session(DEFAULT_WORKSPACE)
    with connect() as conn:
        unfinished = conn.execute(
            "select * from tasks where status not in ('completed', 'cancelled') order by id"
        ).fetchall()
    return {**session, "unfinished_tasks": [dict(row) for row in unfinished]}


@app.put("/v1/workspace/session")
def workspace_session_put(payload: WorkspaceSessionRequest) -> dict[str, Any]:
    return write_workspace_session(DEFAULT_WORKSPACE, payload.model_dump())


@app.get("/v1/evaluations")
def evaluations() -> dict[str, Any]:
    intelligence = analyze_workspace(DEFAULT_WORKSPACE)
    profiles = detect_run_profiles(DEFAULT_WORKSPACE)
    fixtures = [
        {
            "id": "workspace-readable",
            "name": "Workspace can be analyzed",
            "passed": intelligence["file_count"] >= 0,
            "detail": f"{intelligence['file_count']} supported files indexed",
        },
        {
            "id": "run-profile",
            "name": "Runnable entry point is detected",
            "passed": any(profile["kind"] == "run" for profile in profiles),
            "detail": ", ".join(profile["name"] for profile in profiles) or "No run profile",
        },
        {
            "id": "continuity-state",
            "name": "Project continuity state exists",
            "passed": bool(load_project_state(DEFAULT_WORKSPACE)),
            "detail": ".kodex-agent/project-state.json",
        },
    ]
    return {
        "schema_version": "1.0",
        "passed": all(fixture["passed"] for fixture in fixtures),
        "fixtures": fixtures,
    }


@app.get("/v1/runtime-adapters")
def runtime_adapter_list() -> list[dict[str, Any]]:
    return runtime_adapters()


@app.post("/v1/benchmarks/run")
def benchmark_run(payload: BenchmarkRequest) -> dict[str, Any]:
    available = {item["id"]: item["available"] for item in runtime_adapters()}
    if not available.get(payload.adapter, False):
        raise HTTPException(status_code=409, detail=f"{payload.adapter} runtime is unavailable")
    return run_benchmarks(DEFAULT_WORKSPACE, payload.adapter)


@app.get("/v1/ports")
def ports() -> list[dict[str, Any]]:
    managed_by_pid = {
        record["pid"]: record["id"]
        for record in processes.values()
        if record["status"] in {"running", "stopping"}
    }
    return [
        {**record, "managed_process_id": managed_by_pid.get(record["pid"])}
        for record in listening_ports()
    ]


@app.get("/v1/conversations")
def list_conversations() -> list[dict[str, Any]]:
    return agent_runtime.conversations()


@app.post("/v1/conversations")
def create_conversation(payload: ConversationCreateRequest) -> dict[str, Any]:
    return agent_runtime.create_conversation(payload.title)


@app.delete("/v1/conversations")
def clear_conversations() -> dict[str, Any]:
    return agent_runtime.clear_conversations()


@app.get("/v1/conversations/{conversation_id}")
def get_conversation(conversation_id: int) -> dict[str, Any]:
    try:
        return agent_runtime.conversation(conversation_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.delete("/v1/conversations/{conversation_id}")
def delete_conversation(conversation_id: int) -> dict[str, Any]:
    try:
        return agent_runtime.delete_conversation(conversation_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/v1/conversations/{conversation_id}/messages")
def send_agent_message(
    conversation_id: int, payload: AgentMessageRequest
) -> dict[str, Any]:
    try:
        return agent_runtime.start(
            conversation_id,
            payload.content,
            payload.mode,
            payload.pursue_goal,
            payload.permission,
            payload.context,
            payload.model,
            agent_profile_id=payload.agent_profile_id,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get("/v1/agent-runs/{run_id}")
def get_agent_run(run_id: int) -> dict[str, Any]:
    try:
        return agent_runtime.get_run(run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get("/v1/conversations/{conversation_id}/active-run")
def active_conversation_run(conversation_id: int) -> dict[str, Any]:
    try:
        run = agent_runtime.active_run_for_conversation(conversation_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return run or {}


@app.post("/v1/agent-runs/{run_id}/steering")
def steer_agent_run(run_id: int, payload: AgentSteeringRequest) -> dict[str, Any]:
    try:
        return agent_runtime.queue_steering(
            run_id, payload.content, payload.attachments
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/v1/agent-runs/{run_id}/trajectory")
def get_agent_trajectory(run_id: int) -> dict[str, Any]:
    try:
        return agent_runtime.trajectory(run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/v1/agent-runs/{run_id}/replay")
def replay_agent_run(run_id: int) -> dict[str, Any]:
    try:
        return agent_runtime.replay(run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/v1/trajectory-comparison")
def compare_agent_runs(first: int, second: int) -> dict[str, Any]:
    try:
        return compare_trajectories(
            agent_runtime.trajectory(first),
            agent_runtime.trajectory(second),
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/v1/agent-runs/{run_id}/cancel")
def cancel_agent_run(run_id: int) -> dict[str, Any]:
    try:
        return agent_runtime.cancel(run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/v1/agent-runs/{run_id}/pause")
def pause_agent_run(run_id: int) -> dict[str, Any]:
    try:
        return agent_runtime.pause(run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/v1/agent-runs/{run_id}/resume")
def resume_agent_run(run_id: int) -> dict[str, Any]:
    try:
        return agent_runtime.resume(run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/v1/agent-runs/{run_id}/continue")
def continue_agent_run(run_id: int) -> dict[str, Any]:
    try:
        return agent_runtime.continue_interrupted(run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/v1/agent-runs/{run_id}/execute-plan")
def execute_agent_plan(run_id: int) -> dict[str, Any]:
    try:
        return agent_runtime.execute_approved_plan(run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/v1/agent-runs/{run_id}/retry")
def retry_agent_run(run_id: int, payload: AgentRetryRequest) -> dict[str, Any]:
    try:
        return agent_runtime.retry_with_repair(run_id, payload.mode)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/v1/approvals/{approval_id}")
def resolve_approval(
    approval_id: int, payload: ApprovalResolutionRequest
) -> dict[str, Any]:
    try:
        return agent_runtime.resolve_approval(
            approval_id, payload.approved, payload.always_allow
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get("/v1/memory")
def structured_memory_list(
    scope: Optional[str] = None, owner_id: Optional[str] = None
) -> list[dict[str, Any]]:
    return platform_services.list_memories(scope, owner_id)


@app.post("/v1/memory")
def structured_memory_create(payload: StructuredMemoryRequest) -> dict[str, Any]:
    data = payload.model_dump()
    if not data["owner_id"]:
        data["owner_id"] = str(DEFAULT_WORKSPACE.resolve())
    try:
        return platform_services.create_memory(data)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/v1/memory/suggestions")
def memory_suggestions() -> list[dict[str, Any]]:
    return platform_services.suggest_memories()


@app.get("/v1/memory/export")
def memory_export() -> dict[str, Any]:
    return platform_services.export_memories()


@app.post("/v1/memory/import")
def memory_import(payload: MemoryImportRequest) -> dict[str, Any]:
    return platform_services.import_memories(payload.memories)


@app.delete("/v1/memory")
def memory_reset(scope: Optional[str] = None) -> dict[str, Any]:
    return platform_services.reset_memories(scope)


@app.post("/v1/memory/compress")
def memory_compress() -> dict[str, Any]:
    return platform_services.compress_memories()


@app.get("/v1/agent-studio")
def agent_studio_list() -> list[dict[str, Any]]:
    return platform_services.list_agent_profiles()


@app.post("/v1/agent-studio")
def agent_studio_save(payload: AgentProfileRequest) -> dict[str, Any]:
    try:
        return platform_services.save_agent_profile(payload.model_dump())
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/v1/agent-studio/{profile_id}/validate")
def agent_studio_validate(profile_id: int) -> dict[str, Any]:
    try:
        return platform_services.validate_agent_profile(profile_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/v1/agent-studio/{profile_id}/test")
def agent_studio_test(profile_id: int) -> dict[str, Any]:
    try:
        validation = platform_services.validate_agent_profile(profile_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    profile = validation["profile"]
    checks = [
        validation["valid"],
        bool(profile["workflow"]),
        bool(profile["tool_policy"].get("allow")),
        len(profile["constitution"]) >= 20,
    ]
    score = sum(checks) / len(checks)
    evaluation = platform_services.record_evaluation(
        {
            "agent_id": profile["agent_id"],
            "agent_version": profile["version"],
            "fixture": "agent-contract-smoke",
            "score": score,
            "duration_ms": 0,
            "tool_calls": 0,
            "failures": validation["errors"],
            "output": "Agent contract and policy validation",
        }
    )
    return {"validation": validation, "evaluation": evaluation}


@app.get("/v1/agent-studio/compare")
def agent_studio_compare(first: int, second: int) -> dict[str, Any]:
    try:
        return platform_services.compare_agent_profiles(first, second)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/v1/agent-studio/{profile_id}/promote")
def agent_studio_promote(
    profile_id: int, payload: AgentPromotionRequest
) -> dict[str, Any]:
    try:
        return platform_services.promote_agent_profile(profile_id, payload.status)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/v1/evaluation-runs")
def evaluation_run_create(payload: EvaluationRunRequest) -> dict[str, Any]:
    try:
        return platform_services.record_evaluation(payload.model_dump())
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/v1/evaluation-runs")
def evaluation_run_report(agent_id: Optional[str] = None) -> dict[str, Any]:
    return platform_services.evaluation_report(agent_id)


@app.get("/v1/git")
def git_state() -> dict[str, Any]:
    return platform_services.git_state()


@app.get("/v1/git/diff")
def git_diff(path: Optional[str] = None, staged: bool = False) -> dict[str, Any]:
    try:
        return platform_services.git_diff(path, staged)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/v1/git/summary")
def git_summary_generate(payload: GitSummaryRequest) -> dict[str, Any]:
    diff = platform_services.git_diff(staged=payload.staged)["diff"]
    return agent_runtime.generate_git_summary(diff, payload.kind)


@app.post("/v1/git/{action}")
def git_action(action: str, payload: GitActionRequest) -> dict[str, Any]:
    if action in {"push", "pull", "commit"} and not payload.confirmed:
        raise HTTPException(
            status_code=428,
            detail=f"Git {action} requires explicit confirmation",
        )
    if action in {"push", "pull", "commit", "branch", "switch"}:
        emit_event(
            "approval.audit",
            {
                "kind": "git",
                "action": action,
                "decision": "explicit-api-request",
                "workspace": str(DEFAULT_WORKSPACE.resolve()),
            },
        )
    try:
        return platform_services.git_mutation(action, payload.model_dump())
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (OSError, subprocess.TimeoutExpired) as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@app.get("/v1/hardware")
def hardware_info() -> dict[str, Any]:
    return platform_services.hardware()


@app.get("/v1/model-advisor")
def model_advisor() -> dict[str, Any]:
    return platform_services.model_advice(agent_runtime.providers())


@app.post("/v1/diagnostics/parse")
def diagnostics_parse(payload: DiagnosticsParseRequest) -> dict[str, Any]:
    return {"diagnostics": platform_services.parse_diagnostics(payload.output)}


@app.post("/v1/tests/run")
def test_run(payload: TestRunRequest) -> dict[str, Any]:
    profiles = [
        profile for profile in detect_run_profiles(DEFAULT_WORKSPACE)
        if profile["kind"] in {"test", "lint", "build"}
    ]
    selected = next(
        (profile for profile in profiles if profile["id"] == payload.profile_id),
        profiles[0] if profiles else None,
    )
    if selected is None:
        raise HTTPException(status_code=404, detail="No test, lint, or build profile detected")
    cwd = resolve_workspace_path(selected.get("cwd", "."))
    try:
        result = subprocess.run(
            ["bash", "-lc", selected["command"]],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise HTTPException(status_code=408, detail="Validation timed out") from error
    output = (result.stdout + "\n" + result.stderr).strip()
    response = {
        "profile": selected,
        "passed": result.returncode == 0,
        "exit_code": result.returncode,
        "output": output[-100_000:],
        "diagnostics": platform_services.parse_diagnostics(output),
    }
    emit_event("test.completed", response)
    return response


@app.get("/v1/mcp/servers")
def mcp_server_list() -> list[dict[str, Any]]:
    return platform_services.list_mcp_servers()


@app.post("/v1/mcp/servers")
def mcp_server_configure(payload: McpServerRequest) -> dict[str, Any]:
    try:
        return platform_services.configure_mcp(payload.model_dump())
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/v1/mcp/servers/{server_id}/test")
def mcp_server_test(server_id: int) -> dict[str, Any]:
    try:
        return platform_services.test_mcp(server_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/v1/mcp/servers/{server_id}/invoke")
def mcp_server_invoke(server_id: int, payload: McpInvokeRequest) -> dict[str, Any]:
    try:
        return platform_services.invoke_mcp(server_id, payload.method, payload.params)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.post("/v1/models/download")
def model_download(payload: ModelDownloadRequest) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9._:/-]+", payload.model):
        raise HTTPException(status_code=400, detail="Invalid model name")
    if shutil.which("ollama") is None:
        raise HTTPException(status_code=404, detail="Ollama is not installed")
    return register_process(
        ["ollama", "pull", payload.model],
        str(DEFAULT_WORKSPACE),
        f"Download {payload.model}",
    )


@app.get("/v1/database/backups")
def database_backups() -> list[dict[str, Any]]:
    return platform_services.list_backups()


@app.post("/v1/database/backups")
def database_backup_create() -> dict[str, Any]:
    return platform_services.backup_database()


@app.post("/v1/database/restore")
def database_backup_restore(payload: DatabaseRestoreRequest) -> dict[str, Any]:
    try:
        return platform_services.restore_database(payload.path)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/v1/runtime-validation")
def runtime_validation() -> dict[str, Any]:
    return platform_services.runtime_validation()


@app.get("/v1/memories", response_model=list[MemoryRecord])
def list_memories() -> list[MemoryRecord]:
    with connect() as conn:
        rows = conn.execute("select * from memories order by id desc").fetchall()
    return [MemoryRecord(**row_to_dict(row)) for row in rows]


@app.post("/v1/memories", response_model=MemoryRecord)
def create_memory(payload: MemoryCreate) -> MemoryRecord:
    with connect() as conn:
        cur = conn.execute(
            "insert into memories(kind, title, content, created_at) values (?, ?, ?, ?)",
            (payload.kind, payload.title, payload.content, now_iso()),
        )
        row = conn.execute("select * from memories where id = ?", (cur.lastrowid,)).fetchone()
    record = MemoryRecord(**row_to_dict(row))
    emit_event("memory.created", record.model_dump())
    return record


@app.get("/v1/tasks", response_model=list[TaskRecord])
def list_tasks() -> list[TaskRecord]:
    with connect() as conn:
        rows = conn.execute("select * from tasks order by id desc").fetchall()
    return [TaskRecord(**row_to_dict(row)) for row in rows]


@app.post("/v1/tasks", response_model=TaskRecord)
def create_task(payload: TaskCreate) -> TaskRecord:
    with connect() as conn:
        cur = conn.execute(
            "insert into tasks(title, status, details, created_at, updated_at) values (?, ?, ?, ?, ?)",
            (payload.title, "open", payload.details, now_iso(), now_iso()),
        )
        row = conn.execute("select * from tasks where id = ?", (cur.lastrowid,)).fetchone()
    record = TaskRecord(**row_to_dict(row))
    emit_event("task.created", record.model_dump())
    return record


@app.patch("/v1/tasks/{task_id}", response_model=TaskRecord)
def update_task(task_id: int, payload: TaskUpdate) -> TaskRecord:
    with connect() as conn:
        row = conn.execute("select * from tasks where id = ?", (task_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Task not found")
        status = payload.status or row["status"]
        details = payload.details if payload.details is not None else row["details"]
        conn.execute(
            "update tasks set status = ?, details = ?, updated_at = ? where id = ?",
            (status, details, now_iso(), task_id),
        )
        updated = conn.execute("select * from tasks where id = ?", (task_id,)).fetchone()
    record = TaskRecord(**row_to_dict(updated))
    emit_event("task.updated", record.model_dump())
    return record


@app.get("/v1/sessions")
def list_sessions() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("select * from sessions order by id desc").fetchall()
    return [row_to_dict(row) for row in rows]


@app.post("/v1/sessions")
def create_session(payload: SessionCreate) -> dict[str, Any]:
    with connect() as conn:
        cur = conn.execute(
            "insert into sessions(summary, created_at) values (?, ?)",
            (payload.summary, now_iso()),
        )
        row = conn.execute("select * from sessions where id = ?", (cur.lastrowid,)).fetchone()
    record = row_to_dict(row)
    emit_event("session.created", record)
    return record


@app.get("/v1/processes")
def list_processes() -> list[dict[str, Any]]:
    return [{k: v for k, v in proc.items() if k != "process"} for proc in processes.values()]


@app.post("/v1/run")
def run_command(payload: RunRequest) -> dict[str, Any]:
    for proc in processes.values():
        if proc["name"] == payload.name and proc["status"] == "running":
            os.killpg(os.getpgid(proc["pid"]), signal.SIGTERM)
    cwd = resolve_workspace_path(payload.cwd or ".")
    if not cwd.is_dir():
        raise HTTPException(status_code=400, detail="Run profile directory does not exist")
    return register_process(payload.command, str(cwd), payload.name)


@app.delete("/v1/processes/{process_id}")
def stop_process(process_id: int) -> dict[str, Any]:
    record = processes.get(process_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Process not found")
    if record["status"] == "running":
        os.killpg(os.getpgid(record["pid"]), signal.SIGTERM)
        record["status"] = "stopping"
        emit_event("process.stopping", {"process_id": process_id, "pid": record["pid"]})
    return {k: v for k, v in record.items() if k != "process"}


@app.get("/v1/bootstrap")
def bootstrap() -> dict[str, Any]:
    return {
        "project": {
            "name": "Kodex",
            "tagline": "Your machine. Your models. Your agent. Your rules.",
        },
        "tabs": ["Workspace", "Memory", "Tasks", "Models", "Agent Studio"],
        "core": {"url": "http://127.0.0.1:7799"},
        "workspace": str(DEFAULT_WORKSPACE),
    }


@app.get("/v1/files")
def list_files(path: str = ".") -> dict[str, Any]:
    root = resolve_workspace_path(path)
    if not root.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if root.is_file():
        return {"path": relative_workspace_path(root), "kind": "file", "entries": []}
    entries = [
        {
            "name": item.name,
            "kind": "directory" if item.is_dir() else "file",
            "path": relative_workspace_path(item),
        }
        for item in sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    ]
    return {"path": relative_workspace_path(root), "kind": "directory", "entries": entries}


@app.get("/v1/file")
def read_file(path: str) -> dict[str, Any]:
    root = resolve_workspace_path(path, allow_root=False)
    if not root.exists() or not root.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        content = root.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise HTTPException(status_code=415, detail="File is not UTF-8 text") from error
    return {"path": relative_workspace_path(root), "content": content}


@app.get("/v1/designer/project")
def designer_project() -> dict[str, Any]:
    return designer_service.detect_project()


@app.get("/v1/designer/document")
def designer_document(path: str) -> dict[str, Any]:
    try:
        return designer_service.parse_document(path)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Designer source file not found") from error
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/v1/designer/toolbox")
def designer_toolbox(adapter: str = "") -> list[dict[str, Any]]:
    return designer_service.toolbox(adapter)


@app.get("/v1/designer/device-profiles")
def designer_device_profiles() -> list[dict[str, Any]]:
    return DEVICE_PROFILES


@app.get("/v1/designer/history")
def designer_history() -> dict[str, Any]:
    return designer_service.history()


@app.post("/v1/designer/transactions")
def designer_transaction(payload: DesignerTransactionRequest) -> dict[str, Any]:
    try:
        return designer_service.apply(payload.model_dump())
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Designer source file not found") from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/v1/designer/transactions/undo")
def designer_undo() -> dict[str, Any]:
    try:
        return designer_service.undo()
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/v1/designer/transactions/redo")
def designer_redo() -> dict[str, Any]:
    try:
        return designer_service.redo()
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/v1/project-templates")
def project_templates() -> list[dict[str, Any]]:
    return PROJECT_TEMPLATES


@app.put("/v1/file")
def write_file(payload: FileWriteRequest) -> dict[str, Any]:
    root = resolve_workspace_path(payload.path, allow_root=False)
    if root.exists() and not root.is_file():
        raise HTTPException(status_code=409, detail="Path is not a file")
    ensure_parent(root)
    root.write_text(payload.content, encoding="utf-8")
    emit_event("file.written", {"path": relative_workspace_path(root)})
    return {"path": relative_workspace_path(root), "written": True}


@app.post("/v1/files")
def create_file(payload: FileCreateRequest) -> dict[str, Any]:
    target = resolve_workspace_path(payload.path, allow_root=False)
    if target.exists():
        raise HTTPException(status_code=409, detail="Path already exists")
    if payload.kind == "directory":
        target.mkdir(parents=True)
    else:
        ensure_parent(target)
        target.write_text(payload.content, encoding="utf-8")
    result = {
        "path": relative_workspace_path(target),
        "kind": payload.kind,
        "created": True,
    }
    emit_event("file.created", result)
    return result


@app.patch("/v1/files")
def move_file(payload: FileMoveRequest) -> dict[str, Any]:
    source = resolve_workspace_path(payload.source, allow_root=False)
    destination = resolve_workspace_path(payload.destination, allow_root=False)
    if not source.exists():
        raise HTTPException(status_code=404, detail="Source path not found")
    if destination.exists():
        raise HTTPException(status_code=409, detail="Destination already exists")
    ensure_parent(destination)
    source.rename(destination)
    result = {
        "source": payload.source,
        "path": relative_workspace_path(destination),
        "kind": "directory" if destination.is_dir() else "file",
        "moved": True,
    }
    emit_event("file.moved", result)
    return result


@app.delete("/v1/files")
def delete_file(path: str) -> dict[str, Any]:
    target = resolve_workspace_path(path, allow_root=False)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    kind = "directory" if target.is_dir() else "file"
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    result = {"path": path, "kind": kind, "deleted": True}
    emit_event("file.deleted", result)
    return result


@app.get("/v1/search")
def search_workspace(q: str, mode: str = "text", path: str = ".") -> dict[str, Any]:
    query = q.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Search query is required")
    if mode not in {"text", "symbols"}:
        raise HTTPException(status_code=400, detail="Unsupported search mode")
    root = resolve_workspace_path(path)
    if not root.exists():
        raise HTTPException(status_code=404, detail="Search path not found")

    query_lower = query.lower()
    results: list[dict[str, Any]] = []
    scanned_files = 0
    for candidate in workspace_text_files(root):
        scanned_files += 1
        lines = read_search_lines(candidate)
        for line_number, line in enumerate(lines, start=1):
            if mode == "text":
                if query_lower not in line.lower():
                    continue
                results.append(
                    {
                        "path": relative_workspace_path(candidate),
                        "line": line_number,
                        "column": line.lower().find(query_lower) + 1,
                        "preview": line.strip()[:300],
                        "kind": "text",
                    }
                )
            else:
                symbol = next(
                    (
                        match.group(1)
                        for pattern in SYMBOL_PATTERNS
                        if (match := pattern.search(line))
                    ),
                    None,
                )
                if symbol is None or query_lower not in symbol.lower():
                    continue
                results.append(
                    {
                        "path": relative_workspace_path(candidate),
                        "line": line_number,
                        "column": max(line.find(symbol), 0) + 1,
                        "preview": line.strip()[:300],
                        "kind": "symbol",
                        "symbol": symbol,
                    }
                )
            if len(results) >= SEARCH_MAX_RESULTS:
                return {
                    "query": query,
                    "mode": mode,
                    "results": results,
                    "scanned_files": scanned_files,
                    "truncated": True,
                }
    return {
        "query": query,
        "mode": mode,
        "results": results,
        "scanned_files": scanned_files,
        "truncated": False,
    }


@app.get("/v1/workspace/changes")
def workspace_changes(since: int = 0) -> dict[str, Any]:
    if since < 0:
        raise HTTPException(status_code=400, detail="Change cursor cannot be negative")
    with workspace_watcher_lock:
        earliest = workspace_events[0]["sequence"] if workspace_events else workspace_event_sequence + 1
        reset = since > 0 and since < earliest - 1
        events = [] if reset else [event.copy() for event in workspace_events if event["sequence"] > since]
        cursor = workspace_event_sequence
    return {"cursor": cursor, "events": events, "reset": reset}


@app.get("/v1/snapshots")
def list_snapshots() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "select * from snapshots where workspace = ? order by id desc limit 100",
            (str(DEFAULT_WORKSPACE.resolve()),),
        ).fetchall()
    return [row_to_dict(row) for row in rows]


@app.post("/v1/snapshots")
def create_snapshot(payload: SnapshotCreateRequest) -> dict[str, Any]:
    snapshot = create_workspace_snapshot(payload.label)
    emit_event("snapshot.created", snapshot)
    return snapshot


@app.get("/v1/snapshots/{snapshot_id}/diff")
def get_snapshot_diff(snapshot_id: int) -> dict[str, Any]:
    return snapshot_diff(snapshot_id)


@app.post("/v1/snapshots/{snapshot_id}/restore")
def restore_snapshot(snapshot_id: int) -> dict[str, Any]:
    result = restore_workspace_snapshot(snapshot_id)
    emit_event("snapshot.restored", {"snapshot_id": snapshot_id})
    return result


@app.delete("/v1/snapshots/{snapshot_id}")
def delete_snapshot(snapshot_id: int) -> dict[str, Any]:
    snapshot = snapshot_record(snapshot_id)
    with connect() as conn:
        conn.execute("delete from snapshot_files where snapshot_id = ?", (snapshot_id,))
        conn.execute("delete from snapshots where id = ?", (snapshot_id,))
        conn.commit()
    emit_event("snapshot.deleted", {"snapshot_id": snapshot_id})
    return {"deleted": snapshot}


@app.post("/v1/terminal")
def start_terminal(payload: TerminalRequest) -> dict[str, Any]:
    shell = payload.shell or "bash"
    command = [shell]
    return register_process(command, payload.cwd or str(DEFAULT_WORKSPACE), "terminal")


def app_cli() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ.get("CODEX_CORE_PORT", "7799")),
        reload=False,
    )
