from __future__ import annotations

import json
import hashlib
import base64
import ipaddress
import os
import re
import shlex
import signal
import select
import shutil
import socket
import sqlite3
import subprocess
import threading
import time
import tempfile
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import difflib
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from .project_intelligence import (
    analyze_workspace,
    discover_skills,
    load_project_state,
    update_project_state,
)


def utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


@dataclass
class ProviderChoice:
    provider: str
    model: str
    base_url: str
    api_key: str = ""
    capabilities: tuple[str, ...] = ()
    context_length: int = 8192


VISION_MODEL_HINTS = (
    "vision", "llava", "qwen2-vl", "qwen2.5-vl", "qwen3-vl",
    "minicpm-v", "gemma3", "pixtral", "moondream",
)

LOCAL_CONTEXT_DEFAULT = 8192
CODEX_CONTEXT_TARGET = 14_000
CODEX_MODEL_TIMEOUT_SECONDS = 180
CODEX_MODEL_IDLE_TIMEOUT_SECONDS = 90
WORKSPACE_IGNORED_DIRECTORIES = {
    ".git", "node_modules", "dist", "build", "release", "coverage",
    "__pycache__", ".venv", ".cache", ".next", "target",
}


def model_capabilities(model_id: str, *, local: bool = False) -> list[str]:
    capabilities = ["chat", "streaming", "structured-output"]
    if not local or any(hint in model_id.lower() for hint in VISION_MODEL_HINTS):
        capabilities.append("vision")
    return capabilities


def claude_executable() -> str | None:
    configured = os.environ.get("CLAUDE_CLI_PATH")
    if configured:
        return configured
    discovered = shutil.which("claude")
    if discovered:
        return discovered
    executable_name = "claude.exe" if os.name == "nt" else "claude"
    extension_roots = (
        Path.home() / ".vscode" / "extensions",
        Path.home() / ".vscode-insiders" / "extensions",
    )
    candidates: list[Path] = []
    for root in extension_roots:
        if not root.exists():
            continue
        candidates.extend(
            root.glob(
                f"anthropic.claude-code-*/resources/native-binary/{executable_name}"
            )
        )
    for candidate in sorted(candidates, reverse=True):
        if candidate.is_file():
            return str(candidate)
    return None


class ReadableHTMLParser(HTMLParser):
    BLOCKED = {"script", "style", "svg", "noscript"}

    def __init__(self) -> None:
        super().__init__()
        self.blocked_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.BLOCKED:
            self.blocked_depth += 1
        elif tag in {"p", "br", "li", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.BLOCKED and self.blocked_depth:
            self.blocked_depth -= 1
        elif tag in {"p", "li", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.blocked_depth and data.strip():
            self.parts.append(data.strip())

    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", " ".join(self.parts)).strip()


class ToolInputError(ValueError):
    def __init__(
        self,
        code: str,
        tool: str,
        message: str,
        *,
        field: str = "",
        suggested_recovery: str = "",
    ) -> None:
        super().__init__(message)
        self.details = {
            "code": code,
            "tool": tool,
            "field": field,
            "message": message,
            "suggested_recovery": suggested_recovery,
        }


class ProviderResponseError(RuntimeError):
    def __init__(
        self,
        provider: str,
        model: str,
        code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.details = {
            "provider": provider,
            "model": model,
            "code": code,
            "message": message,
            "retryable": retryable,
        }


class AgentRuntime:
    def __init__(
        self,
        workspace: Path,
        db_path: Callable[[], Path],
        emit: Callable[[str, Optional[dict[str, Any]]], dict[str, Any]],
        create_snapshot: Callable[[str], dict[str, Any]],
    ) -> None:
        self.workspace = workspace.resolve()
        self.db_path = db_path
        self.emit = emit
        self.create_snapshot = create_snapshot
        self.cancel_events: dict[int, threading.Event] = {}
        self.pause_events: dict[int, threading.Event] = {}
        self.run_threads: dict[int, threading.Thread] = {}
        self.read_versions: dict[int, dict[str, tuple[int, int]]] = {}
        self.active_operations: dict[int, dict[int, tuple[str, Callable[[], None]]]] = {}
        self.cancellation_claims: set[int] = set()
        self._operation_sequence = 0
        self._codex_status_cache: tuple[float, dict[str, Any]] | None = None
        self._claude_status_cache: tuple[float, dict[str, Any]] | None = None
        self._lock = threading.Lock()
        self.ensure_runtime_schema()

    TERMINAL_STATUSES = {
        "completed", "failed", "cancelled", "rejected", "needs_review", "interrupted",
    }

    TOOL_ALIASES = {
        "write_file": "create_file",
        "apply_patch": "edit_file",
        "read_files": "read_file",
    }
    PLACEHOLDER_VALUES = {"", "path", "<path>", "file", "<file>", "filename", "todo"}
    TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
        "list_files": {"required": [], "properties": {"path": "string"}},
        "tree": {"required": [], "properties": {"path": "string", "depth": "integer"}},
        "glob": {"required": ["pattern"], "properties": {"pattern": "string", "path": "string"}},
        "read_file": {
            "required": ["path"],
            "properties": {
                "path": "string", "start_line": "integer", "end_line": "integer",
            },
        },
        "read_files": {"required": ["paths"], "properties": {"paths": "array"}},
        "search": {"required": ["query"], "properties": {"query": "string", "path": "string"}},
        "fetch_url": {
            "required": ["url"],
            "properties": {"url": "string", "max_bytes": "integer"},
        },
        "create_file": {
            "required": ["path", "content"],
            "properties": {"path": "string", "content": "string"},
        },
        "edit_file": {
            "required": ["path", "edits"],
            "properties": {"path": "string", "edits": "array"},
        },
        "move_file": {
            "required": ["path", "destination"],
            "properties": {"path": "string", "destination": "string"},
        },
        "delete_file": {"required": ["path"], "properties": {"path": "string"}},
        "git_status": {"required": [], "properties": {}},
        "git_diff": {"required": [], "properties": {"path": "string", "staged": "boolean"}},
        "workspace_diagnostics": {"required": [], "properties": {}},
        "list_ports": {"required": [], "properties": {}},
        "stop_process": {"required": ["pid"], "properties": {"pid": "integer"}},
        "run_command": {
            "required": ["command"],
            "properties": {"command": "string", "cwd": "string", "timeout": "integer"},
        },
    }

    def ensure_runtime_schema(self) -> None:
        try:
            with self.connect() as connection:
                tables = {
                    row["name"]
                    for row in connection.execute(
                        "select name from sqlite_master where type = 'table'"
                    )
                }
                if "agent_runs" not in tables:
                    return
                conversation_columns = {
                    row["name"]
                    for row in connection.execute("pragma table_info(conversations)")
                }
                if "workspace" not in conversation_columns:
                    connection.execute(
                        "alter table conversations add column workspace text not null default ''"
                    )
                columns = {
                    row["name"]
                    for row in connection.execute("pragma table_info(agent_runs)")
                }
                migrations = {
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
                    if column not in columns:
                        connection.execute(
                            f"alter table agent_runs add column {column} {definition}"
                        )
                connection.executescript(
                    """
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
                    create table if not exists provider_diagnostics (
                        provider text primary key,
                        health text not null,
                        available integer not null,
                        detail text not null default '',
                        latency_ms real,
                        model_count integer not null default 0,
                        checked_at text not null
                    );
                    create table if not exists agent_steering_messages (
                        id integer primary key autoincrement,
                        run_id integer not null,
                        content text not null,
                        status text not null default 'queued',
                        created_at text not null,
                        consumed_at text
                    );
                    """
                )
                steering_columns = {
                    row["name"]
                    for row in connection.execute(
                        "pragma table_info(agent_steering_messages)"
                    )
                }
                if "attachments_json" not in steering_columns:
                    connection.execute(
                        "alter table agent_steering_messages add column attachments_json text not null default '[]'"
                    )
                connection.commit()
        except sqlite3.Error:
            # The main application creates the base schema during startup.
            return

    def recover_interrupted(self) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                update agent_runs
                set status = 'interrupted',
                    summary = 'Core restarted before this run finished. Continue the run to resume safely.',
                    updated_at = ?
                where status in (
                    'queued',
                    'understanding',
                    'planning',
                    'working',
                    'testing',
                    'waiting_for_approval',
                    'paused',
                    'stopping'
                )
                """,
                (utc_now(),),
            )
            connection.commit()
        return cursor.rowcount

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path())
        connection.row_factory = sqlite3.Row
        return connection

    def providers(self) -> list[dict[str, Any]]:
        providers: list[dict[str, Any]] = []
        codex_status = self.codex_status()
        providers.append(
            {
                "id": "openai-codex",
                "name": "OpenAI Codex",
                "kind": "chatgpt",
                "available": codex_status["authenticated"],
                "health": "ready" if codex_status["authenticated"] else "authentication-required",
                "base_url": "codex://local-cli",
                "detail": codex_status["message"],
                "latency_ms": None,
                "capabilities": ["chat", "streaming", "structured-output", "vision", "tools"],
                "models": [
                    {
                        "id": "default",
                        "name": "Codex account default",
                        "context_length": 200000,
                        "supports_tools": True,
                        "capabilities": ["chat", "streaming", "structured-output", "vision", "tools"],
                    }
                ]
                if codex_status["authenticated"]
                else [],
            }
        )
        claude_status = self.claude_status()
        providers.append(
            {
                "id": "anthropic-claude-code",
                "name": "Claude Code",
                "kind": "claude",
                "available": claude_status["authenticated"],
                "health": "ready" if claude_status["authenticated"] else "authentication-required",
                "base_url": "claude-code://local-cli",
                "detail": claude_status["message"],
                "latency_ms": None,
                "capabilities": ["chat", "structured-output", "vision", "tools"],
                "models": [
                    {
                        "id": model,
                        "name": f"Claude {model.title()}",
                        "context_length": 200000,
                        "supports_tools": True,
                        "capabilities": ["chat", "structured-output", "vision", "tools"],
                    }
                    for model in ("sonnet", "opus", "haiku")
                ]
                if claude_status["authenticated"]
                else [],
            }
        )
        started = time.monotonic()
        try:
            response = httpx.get("http://127.0.0.1:11434/api/tags", timeout=1.5)
            response.raise_for_status()
            models = [
                {
                    "id": item["name"],
                    "name": item["name"],
                    "context_length": 8192,
                    "supports_tools": True,
                    "capabilities": model_capabilities(item["name"], local=True),
                }
                for item in response.json().get("models", [])
            ]
            providers.append(
                {
                    "id": "ollama",
                    "name": "Ollama",
                    "kind": "local",
                    "available": True,
                    "health": "ready" if models else "no-models",
                    "base_url": "http://127.0.0.1:11434",
                    "detail": "Ollama is ready." if models else "Ollama is running without a loaded model.",
                    "latency_ms": round((time.monotonic() - started) * 1000),
                    "capabilities": ["chat", "streaming", "structured-output", "tools"],
                    "models": models,
                }
            )
        except (httpx.HTTPError, ValueError, KeyError):
            providers.append(
                {
                    "id": "ollama",
                    "name": "Ollama",
                    "kind": "local",
                    "available": False,
                    "health": "offline",
                    "base_url": "http://127.0.0.1:11434",
                    "detail": "Start Ollama to use local models.",
                    "latency_ms": None,
                    "capabilities": ["chat", "streaming", "structured-output", "tools"],
                    "models": [],
                }
            )
        lm_studio_url = os.environ.get(
            "CODEX_LM_STUDIO_BASE_URL", "http://127.0.0.1:1234/v1"
        ).rstrip("/")
        started = time.monotonic()
        try:
            response = httpx.get(f"{lm_studio_url}/models", timeout=1.5)
            response.raise_for_status()
            models = [
                {
                    "id": item["id"],
                    "name": item.get("name") or item["id"],
                    "context_length": int(
                        item.get("context_length")
                        or item.get("max_context_length")
                        or LOCAL_CONTEXT_DEFAULT
                    ),
                    "supports_tools": True,
                    "capabilities": model_capabilities(item["id"], local=True),
                }
                for item in response.json().get("data", [])
                if isinstance(item, dict) and item.get("id")
            ]
            providers.append(
                {
                    "id": "lm-studio",
                    "name": "LM Studio",
                    "kind": "local",
                    "available": True,
                    "health": "ready" if models else "no-models",
                    "base_url": lm_studio_url,
                    "detail": (
                        f"LM Studio is ready with {len(models)} loaded model(s)."
                        if models
                        else "LM Studio is running. Load a model to use it in Kodex."
                    ),
                    "latency_ms": round((time.monotonic() - started) * 1000),
                    "capabilities": ["chat", "streaming", "structured-output", "tools"],
                    "models": models,
                }
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            providers.append(
                {
                    "id": "lm-studio",
                    "name": "LM Studio",
                    "kind": "local",
                    "available": False,
                    "health": "offline",
                    "base_url": lm_studio_url,
                    "detail": (
                        f"LM Studio is not responding at {lm_studio_url}. "
                        "Start its local server and retry detection."
                    ),
                    "latency_ms": None,
                    "capabilities": ["chat", "streaming", "structured-output", "tools"],
                    "models": [],
                }
            )
        cloud_url = os.environ.get("CODEX_OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        cloud_key = os.environ.get("CODEX_OPENAI_API_KEY", "")
        providers.append(
            {
                "id": "openai-compatible",
                "name": "OpenAI Compatible",
                "kind": "cloud",
                "available": bool(cloud_key),
                "health": "ready" if cloud_key else "configuration-required",
                "base_url": cloud_url,
                "detail": "Cloud provider is configured." if cloud_key else "Add an API key to enable this provider.",
                "latency_ms": None,
                "capabilities": ["chat", "streaming", "structured-output", "vision", "tools"],
                "models": [
                    {
                        "id": os.environ.get("CODEX_OPENAI_MODEL", "gpt-4.1-mini"),
                        "name": os.environ.get("CODEX_OPENAI_MODEL", "gpt-4.1-mini"),
                        "context_length": 128000,
                        "supports_tools": True,
                        "capabilities": ["chat", "streaming", "structured-output", "vision", "tools"],
                    }
                ]
                if cloud_key
                else [],
            }
        )
        self.record_provider_diagnostics(providers)
        return providers

    def record_provider_diagnostics(self, providers: list[dict[str, Any]]) -> None:
        try:
            with self.connect() as connection:
                for provider in providers:
                    connection.execute(
                        """
                        insert into provider_diagnostics(
                            provider, health, available, detail, latency_ms,
                            model_count, checked_at
                        ) values (?, ?, ?, ?, ?, ?, ?)
                        on conflict(provider) do update set
                            health = excluded.health,
                            available = excluded.available,
                            detail = excluded.detail,
                            latency_ms = excluded.latency_ms,
                            model_count = excluded.model_count,
                            checked_at = excluded.checked_at
                        """,
                        (
                            provider["id"],
                            provider.get("health", "unknown"),
                            int(bool(provider.get("available"))),
                            provider.get("detail", ""),
                            provider.get("latency_ms"),
                            len(provider.get("models", [])),
                            utc_now(),
                        ),
                    )
                connection.commit()
        except sqlite3.Error:
            return

    def select_provider(self, requested: str = "auto") -> ProviderChoice:
        providers = self.providers()
        if requested != "auto":
            provider_id, _, requested_model = requested.partition(":")
            match = next(
                (
                    provider
                    for provider in providers
                    if provider["id"] == provider_id and provider["available"]
                ),
                None,
            )
            if match and match["models"]:
                model = requested_model or match["models"][0]["id"]
                if not any(item["id"] == model for item in match["models"]):
                    raise RuntimeError(
                        f"{model} is not loaded or available in {match['name']}. Refresh models and choose another model."
                    )
                return ProviderChoice(
                    provider=provider_id,
                    model=model,
                    base_url=match["base_url"],
                    api_key=os.environ.get("CODEX_OPENAI_API_KEY", ""),
                    capabilities=tuple(match.get("capabilities", [])),
                    context_length=int(
                        next(
                            item.get("context_length", LOCAL_CONTEXT_DEFAULT)
                            for item in match["models"]
                            if item["id"] == model
                        )
                    ),
                )
            raise RuntimeError(
                f"{provider_id} is unavailable or has no loaded models. Refresh provider detection and try again."
            )
        for provider_id in (
            "openai-codex",
            "anthropic-claude-code",
            "ollama",
            "lm-studio",
            "openai-compatible",
        ):
            match = next(
                (
                    provider
                    for provider in providers
                    if provider["id"] == provider_id
                    and provider["available"]
                    and provider["models"]
                ),
                None,
            )
            if match:
                return ProviderChoice(
                    provider=provider_id,
                    model=match["models"][0]["id"],
                    base_url=match["base_url"],
                    api_key=os.environ.get("CODEX_OPENAI_API_KEY", ""),
                    capabilities=tuple(match.get("capabilities", [])),
                    context_length=int(
                        match["models"][0].get(
                            "context_length", LOCAL_CONTEXT_DEFAULT
                        )
                    ),
                )
        raise RuntimeError("No model provider is available. Sign in to Codex, start Ollama or LM Studio, or configure a cloud provider.")

    def provider_candidates(
        self, requested: str = "auto", excluded: Optional[set[str]] = None
    ) -> list[ProviderChoice]:
        excluded = excluded or set()
        if type(self).select_provider is not AgentRuntime.select_provider:
            return [self.select_provider(requested)]
        if requested != "auto":
            choice = self.select_provider(requested)
            return [] if choice.provider in excluded else [choice]
        candidates: list[ProviderChoice] = []
        for provider in self.providers():
            if (
                provider["id"] in excluded
                or not provider.get("available")
                or not provider.get("models")
            ):
                continue
            candidates.append(
                ProviderChoice(
                    provider=provider["id"],
                    model=provider["models"][0]["id"],
                    base_url=provider["base_url"],
                    api_key=os.environ.get("CODEX_OPENAI_API_KEY", ""),
                    capabilities=tuple(provider.get("capabilities", [])),
                    context_length=int(
                        provider["models"][0].get(
                            "context_length", LOCAL_CONTEXT_DEFAULT
                        )
                    ),
                )
            )
        return candidates

    def test_provider(self, provider_id: str) -> dict[str, Any]:
        started = time.monotonic()
        provider = next(
            (item for item in self.providers() if item["id"] == provider_id),
            None,
        )
        if provider is None:
            raise KeyError("Provider not found")
        return {
            "id": provider["id"],
            "available": provider["available"],
            "health": provider.get("health", "unknown"),
            "detail": provider.get("detail", ""),
            "latency_ms": provider.get("latency_ms")
            if provider.get("latency_ms") is not None
            else round((time.monotonic() - started) * 1000),
            "models": provider["models"],
            "capabilities": provider.get("capabilities", []),
        }

    def record_provider_attempt(
        self,
        run_id: int,
        choice: Any,
        status: str,
        error: str = "",
    ) -> None:
        provider = str(getattr(choice, "provider", "scripted"))
        model = str(getattr(choice, "model", "default"))
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                insert into agent_provider_attempts(
                    run_id, provider, model, status, error, created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, provider, model, status, error[-4000:], now, now),
            )
            connection.commit()
        self.emit(
            "agent.provider_attempt",
            {
                "run_id": run_id,
                "provider": provider,
                "model": model,
                "status": status,
                "error": error[-500:],
            },
        )

    def register_operation(
        self, run_id: int, kind: str, cancel_operation: Callable[[], None]
    ) -> int:
        with self._lock:
            self._operation_sequence += 1
            operation_id = self._operation_sequence
            self.active_operations.setdefault(run_id, {})[operation_id] = (
                kind,
                cancel_operation,
            )
        return operation_id

    def unregister_operation(self, run_id: int, operation_id: int) -> None:
        with self._lock:
            operations = self.active_operations.get(run_id)
            if not operations:
                return
            operations.pop(operation_id, None)
            if not operations:
                self.active_operations.pop(run_id, None)

    @staticmethod
    def terminate_process(process: subprocess.Popen[Any]) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            process.wait(timeout=0.15)
        except (OSError, ProcessLookupError):
            return
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass

    def terminate_registered_operations(self, run_id: int) -> list[str]:
        with self._lock:
            operations = list(self.active_operations.pop(run_id, {}).values())
        terminated: list[str] = []
        for kind, cancel_operation in operations:
            try:
                cancel_operation()
            except Exception:
                pass
            if kind not in terminated:
                terminated.append(kind)
        return terminated

    @staticmethod
    def provider_error_code(message: str) -> tuple[str, bool]:
        lowered = message.lower()
        if any(token in lowered for token in ("not loaded", "no model loaded", "model unloaded")):
            return "model_unloaded", True
        if any(token in lowered for token in ("busy", "loading", "warming", "temporarily unavailable")):
            return "server_busy", True
        if any(token in lowered for token in ("context length", "context window", "too many tokens")):
            return "context_overflow", False
        if "cancel" in lowered or "abort" in lowered:
            return "cancelled", False
        return "invalid_response", True

    @classmethod
    def provider_error_from_payload(
        cls, choice: ProviderChoice, payload: Any
    ) -> ProviderResponseError:
        if isinstance(payload, dict):
            raw_error = payload.get("error")
            if isinstance(raw_error, dict):
                message = str(
                    raw_error.get("message")
                    or raw_error.get("detail")
                    or raw_error.get("type")
                    or raw_error
                )
            else:
                message = str(
                    raw_error
                    or payload.get("message")
                    or payload.get("detail")
                    or "Provider returned no choices."
                )
        else:
            message = "Provider returned an unreadable response."
        code, retryable = cls.provider_error_code(message)
        if code == "context_overflow":
            message = (
                "This request is larger than the context window configured for the loaded model. "
                "Retry with reduced context, or increase the model context length in LM Studio."
            )
        return ProviderResponseError(
            choice.provider,
            choice.model,
            code,
            message,
            retryable=retryable,
        )

    @classmethod
    def openai_response_content(
        cls, choice: ProviderChoice, payload: Any, *, streaming: bool
    ) -> str:
        if not isinstance(payload, dict):
            raise cls.provider_error_from_payload(choice, payload)
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            if payload.get("usage") and not payload.get("error"):
                return ""
            raise cls.provider_error_from_payload(choice, payload)
        first = choices[0]
        if not isinstance(first, dict):
            raise cls.provider_error_from_payload(choice, payload)
        container = first.get("delta") if streaming else first.get("message")
        if not isinstance(container, dict):
            container = first.get("message") or first.get("delta") or {}
        content = container.get("content") if isinstance(container, dict) else ""
        if isinstance(content, list):
            return "".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict)
            )
        return str(content or "")

    def call_with_fallback(
        self,
        run_id: int,
        requested: str,
        messages: list[dict[str, str]],
        cancel: threading.Event,
        excluded: Optional[set[str]] = None,
    ) -> tuple[str, Any]:
        excluded = excluded or set()
        candidates = self.provider_candidates(requested, excluded)
        if self.image_attachments_for_run(run_id):
            candidates = [
                choice
                for choice in candidates
                if choice.provider not in {"ollama", "lm-studio"}
                or any(hint in choice.model.lower() for hint in VISION_MODEL_HINTS)
            ]
        if not candidates:
            choice = self.select_provider(requested)
            if (
                self.image_attachments_for_run(run_id)
                and choice.provider in {"ollama", "lm-studio"}
                and not any(hint in choice.model.lower() for hint in VISION_MODEL_HINTS)
            ):
                raise RuntimeError(
                    f"{choice.model} is not a detected vision model. Select Codex or a vision-capable local model."
                )
            candidates = [choice]
        failures: list[str] = []
        last_provider_error: ProviderResponseError | None = None
        for choice in candidates:
            attempts = 2 if getattr(choice, "provider", "") == "openai-codex" else 1
            for attempt in range(attempts):
                try:
                    self.record_provider_attempt(run_id, choice, "running")
                    content = self.model_call(run_id, choice, messages, cancel)
                    self.record_provider_attempt(run_id, choice, "completed")
                    return content, choice
                except InterruptedError:
                    raise
                except Exception as error:
                    if cancel.is_set():
                        raise InterruptedError("Agent run cancelled") from error
                    message = str(error)
                    if isinstance(error, ProviderResponseError):
                        last_provider_error = error
                    self.record_provider_attempt(run_id, choice, "failed", message)
                    if attempt + 1 < attempts:
                        self.emit(
                            "agent.provider_retry",
                            {
                                "run_id": run_id,
                                "provider": getattr(choice, "provider", "provider"),
                                "attempt": attempt + 2,
                                "error": message[-1000:],
                            },
                        )
                        if cancel.wait(0.25):
                            raise InterruptedError("Agent run cancelled")
                        continue
                    failures.append(
                        f"{getattr(choice, 'provider', 'provider')}: {message}"
                    )
                    self.emit(
                        "agent.provider_fallback",
                        {
                            "run_id": run_id,
                            "provider": getattr(choice, "provider", "provider"),
                            "error": message[-1000:],
                        },
                    )
                    break
            if requested != "auto":
                break
        if last_provider_error is not None:
            details = last_provider_error.details
            raise ProviderResponseError(
                str(details["provider"]),
                str(details["model"]),
                str(details["code"]),
                "All eligible model providers failed. " + " | ".join(failures),
                retryable=bool(details["retryable"]),
            )
        raise RuntimeError("All eligible model providers failed. " + " | ".join(failures))

    def codex_status(self, refresh: bool = False) -> dict[str, Any]:
        if (
            not refresh
            and self._codex_status_cache
            and time.monotonic() - self._codex_status_cache[0] < 5
        ):
            return self._codex_status_cache[1]
        executable = os.environ.get("CODEX_CLI_PATH") or shutil.which("codex")
        if not executable:
            status = {
                "installed": False,
                "authenticated": False,
                "message": "Codex CLI is not installed or could not be found.",
            }
        else:
            try:
                result = subprocess.run(
                    [executable, "login", "status"],
                    cwd=self.workspace,
                    capture_output=True,
                    text=True,
                    timeout=8,
                )
                output = (result.stdout or result.stderr).strip()
                status = {
                    "installed": True,
                    "authenticated": result.returncode == 0 and "logged in" in output.lower(),
                    "message": output or (
                        "Signed in to Codex."
                        if result.returncode == 0
                        else "Codex is installed but not signed in."
                    ),
                }
            except (OSError, subprocess.TimeoutExpired) as error:
                status = {
                    "installed": True,
                    "authenticated": False,
                    "message": f"Could not check Codex authentication: {error}",
                }
        self._codex_status_cache = (time.monotonic(), status)
        return status

    def claude_status(self, refresh: bool = False) -> dict[str, Any]:
        if (
            not refresh
            and self._claude_status_cache
            and time.monotonic() - self._claude_status_cache[0] < 5
        ):
            return self._claude_status_cache[1]
        executable = claude_executable()
        if not executable:
            status = {
                "installed": False,
                "authenticated": False,
                "message": "Claude Code is not installed or could not be found.",
            }
        else:
            try:
                result = subprocess.run(
                    [executable, "auth", "status", "--text"],
                    cwd=self.workspace,
                    capture_output=True,
                    text=True,
                    timeout=8,
                )
                output = (result.stdout or result.stderr).strip()
                status = {
                    "installed": True,
                    "authenticated": result.returncode == 0,
                    "message": output or (
                        "Signed in to Claude Code."
                        if result.returncode == 0
                        else "Claude Code is installed but not signed in."
                    ),
                }
            except (OSError, subprocess.TimeoutExpired) as error:
                status = {
                    "installed": True,
                    "authenticated": False,
                    "message": f"Could not check Claude Code authentication: {error}",
                }
        self._claude_status_cache = (time.monotonic(), status)
        return status

    @staticmethod
    def resolve_request_intent(content: str, mode: str) -> dict[str, Any]:
        if mode == "plan":
            return {
                "requested_mode": mode,
                "resolved_intent": "plan_only",
                "intent_confidence": 1.0,
                "intent_reason": "Plan mode was selected explicitly.",
            }
        if mode == "chat":
            return {
                "requested_mode": mode,
                "resolved_intent": "chat",
                "intent_confidence": 1.0,
                "intent_reason": "Chat mode was selected explicitly.",
            }
        normalized = re.sub(r"\s+", " ", content.lower()).strip()
        asks_for_plan = bool(
            re.search(
                r"\b(make|create|write|give|draft|propose|prepare)\b.{0,24}\bplan\b"
                r"|\bplan\b.{0,18}\b(for|to|how)\b",
                normalized,
            )
        )
        asks_to_execute = bool(
            re.search(
                r"\b(and then|then|and)\s+(do|implement|execute|build|fix|apply|start|proceed)\b"
                r"|\b(plan|planning)\s+(and|then)\s+(do|implement|execute|build|fix|apply)\b"
                r"|\bdo it\b|\bgo ahead\b",
                normalized,
            )
        )
        if asks_for_plan and asks_to_execute:
            return {
                "requested_mode": mode,
                "resolved_intent": "plan_then_execute",
                "intent_confidence": 0.96,
                "intent_reason": "The request asks for a plan and explicitly asks Kodex to carry it out.",
            }
        if asks_for_plan:
            return {
                "requested_mode": mode,
                "resolved_intent": "plan_only",
                "intent_confidence": 0.93,
                "intent_reason": "The request asks for a plan without an execution instruction.",
            }
        return {
            "requested_mode": mode,
            "resolved_intent": "execute",
            "intent_confidence": 0.9,
            "intent_reason": "The request asks for workspace work and does not limit Kodex to planning.",
        }

    def normalize_intent(self, content: str, mode: str) -> dict[str, Any]:
        lower = content.lower()
        request_intent = self.resolve_request_intent(content, mode)
        intent = "feature_add"
        if any(word in lower for word in ("fix", "broken", "error", "bug")):
            intent = "bug_fix"
        elif any(word in lower for word in ("explain", "what is", "how does")):
            intent = "explain"
        elif any(word in lower for word in ("test", "verify")):
            intent = "test"
        elif any(word in lower for word in ("refactor", "cleanup")):
            intent = "refactor"
        risk = "high" if any(word in lower for word in ("delete", "remove", "install", "credential", "secret", "push", "force")) else "medium"
        if request_intent["resolved_intent"] in {"chat", "plan_only"}:
            risk = "low"
        return {
            "objective": content.strip(),
            "intent_types": [intent],
            "risk_level": risk,
            "acceptance_criteria": [
                "Requested behavior is implemented or explained",
                "Relevant validation completes successfully",
                "No unresolved tool failures remain",
            ],
            "mode": mode,
            **request_intent,
            "agents": (
                ["planner"]
                if request_intent["resolved_intent"] == "plan_only"
                else ["debugger", "coder", "tester"]
                if intent == "bug_fix"
                else ["coder", "tester", "reviewer"]
            ),
        }

    def create_conversation(self, title: str = "New conversation") -> dict[str, Any]:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                insert into conversations(title, created_at, updated_at, workspace)
                values (?, ?, ?, ?)
                """,
                (title, utc_now(), utc_now(), str(self.workspace)),
            )
            connection.commit()
            row = connection.execute(
                "select * from conversations where id = ?", (cursor.lastrowid,)
            ).fetchone()
        return dict(row)

    def conversations(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                select * from conversations
                where workspace = ?
                order by updated_at desc
                """,
                (str(self.workspace),),
            ).fetchall()
            if not rows:
                rows = connection.execute(
                    """
                    select * from conversations
                    where workspace = ''
                    order by updated_at desc
                    """
                ).fetchall()
        return [dict(row) for row in rows]

    def delete_conversation(self, conversation_id: int) -> dict[str, Any]:
        with self.connect() as connection:
            conversation = connection.execute(
                """
                select * from conversations
                where id = ? and workspace in (?, '')
                """,
                (conversation_id, str(self.workspace)),
            ).fetchone()
            if conversation is None:
                raise KeyError("Conversation not found")
            active = connection.execute(
                """
                select id from agent_runs
                where conversation_id = ?
                  and status in (
                    'queued', 'working', 'testing', 'planning', 'running',
                    'waiting_for_approval', 'paused', 'stopping'
                  )
                limit 1
                """,
                (conversation_id,),
            ).fetchone()
            if active is not None:
                raise ValueError("Stop the active agent run before deleting this session.")
            run_ids = [
                int(row["id"])
                for row in connection.execute(
                    "select id from agent_runs where conversation_id = ?",
                    (conversation_id,),
                ).fetchall()
            ]
            tables = {
                row["name"]
                for row in connection.execute(
                    "select name from sqlite_master where type = 'table'"
                )
            }
            for run_id in run_ids:
                for table in (
                    "agent_checkpoints",
                    "agent_observations",
                    "agent_provider_attempts",
                    "agent_run_tasks",
                    "agent_steering_messages",
                    "agent_steps",
                    "approvals",
                ):
                    if table in tables:
                        connection.execute(
                            f"delete from {table} where run_id = ?", (run_id,)
                        )
            connection.execute(
                "delete from conversation_messages where conversation_id = ?",
                (conversation_id,),
            )
            connection.execute(
                "delete from agent_runs where conversation_id = ?",
                (conversation_id,),
            )
            connection.execute(
                "delete from conversations where id = ?", (conversation_id,)
            )
            connection.commit()
        return {"deleted": True, "id": conversation_id}

    def clear_conversations(self) -> dict[str, Any]:
        conversations = self.conversations()
        deleted = 0
        blocked: list[int] = []
        for conversation in conversations:
            try:
                self.delete_conversation(int(conversation["id"]))
                deleted += 1
            except ValueError:
                blocked.append(int(conversation["id"]))
        return {"deleted": deleted, "blocked": blocked}

    def conversation(self, conversation_id: int) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "select * from conversations where id = ?", (conversation_id,)
            ).fetchone()
            messages = connection.execute(
                "select * from conversation_messages where conversation_id = ? order by id",
                (conversation_id,),
            ).fetchall()
            runs = connection.execute(
                "select * from agent_runs where conversation_id = ? order by id desc",
                (conversation_id,),
            ).fetchall()
        if row is None:
            raise KeyError("Conversation not found")
        return {
            **dict(row),
            "messages": [dict(message) for message in messages],
            "runs": [self.run_record(dict(run)) for run in runs],
        }

    def run_record(self, run: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as connection:
            steps = connection.execute(
                "select * from agent_steps where run_id = ? order by id", (run["id"],)
            ).fetchall()
            approvals = connection.execute(
                "select * from approvals where run_id = ? order by id", (run["id"],)
            ).fetchall()
            tasks = connection.execute(
                "select * from agent_run_tasks where run_id = ? order by position, id",
                (run["id"],),
            ).fetchall()
            steering = connection.execute(
                "select * from agent_steering_messages where run_id = ? order by id",
                (run["id"],),
            ).fetchall()
            provider_attempts = connection.execute(
                "select * from agent_provider_attempts where run_id = ? order by id",
                (run["id"],),
            ).fetchall()
        run["contract"] = json.loads(run.pop("contract_json") or "{}")
        run["context"] = json.loads(run.pop("context_json") or "{}")
        run["progress"] = json.loads(run.pop("progress_json", "{}") or "{}")
        run["goal"] = json.loads(run.pop("goal_json", "{}") or "{}")
        run["checkpoint"] = json.loads(run.pop("checkpoint_json", "{}") or "{}")
        run["completion_evidence"] = json.loads(
            run.pop("completion_evidence_json", "{}") or "{}"
        )
        run["plan"] = json.loads(run.pop("structured_plan_json", "{}") or "{}")
        run["reflection"] = json.loads(run.pop("reflection_json", "{}") or "{}")
        run["specialist_activity"] = json.loads(
            run.pop("specialist_activity_json", "[]") or "[]"
        )
        run["failure_details"] = json.loads(run.pop("failure_details_json", "{}") or "{}")
        run["changed_files"] = json.loads(run.pop("changed_files_json", "[]") or "[]")
        run["validation"] = json.loads(run.pop("validation_json", "{}") or "{}")
        run["steps"] = [
            {**dict(step), "data": json.loads(step["data_json"] or "{}")}
            for step in steps
        ]
        run["approvals"] = [
            {**dict(approval), "details": json.loads(approval["details_json"] or "{}")}
            for approval in approvals
        ]
        run["tasks"] = [
            {**dict(task), "details": json.loads(task["details_json"] or "{}")}
            for task in tasks
        ]
        run["steering_messages"] = [dict(message) for message in steering]
        run["provider_attempts"] = [dict(attempt) for attempt in provider_attempts]
        return run

    def get_run(self, run_id: int) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "select * from agent_runs where id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError("Agent run not found")
        return self.run_record(dict(row))

    def active_run_for_conversation(
        self, conversation_id: int
    ) -> Optional[dict[str, Any]]:
        with self.connect() as connection:
            conversation = connection.execute(
                "select 1 from conversations where id = ?", (conversation_id,)
            ).fetchone()
            if conversation is None:
                raise KeyError("Conversation not found")
            row = connection.execute(
                """
                select * from agent_runs
                where conversation_id = ?
                order by id desc limit 1
                """,
                (conversation_id,),
            ).fetchone()
        return self.run_record(dict(row)) if row is not None else None

    def start(
        self,
        conversation_id: int,
        content: str,
        mode: str,
        pursue_goal: bool,
        permission: str,
        context: dict[str, Any],
        model: str,
        record_message: bool = True,
        agent_profile_id: Optional[int] = None,
        resume_state: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        contract = self.normalize_intent(content, mode)
        effective_mode = (
            "plan"
            if contract["resolved_intent"] == "plan_only"
            else "chat"
            if contract["resolved_intent"] == "chat"
            else "auto"
        )
        profile = self.resolve_agent_profile(agent_profile_id)
        goal = {
            "objective": contract["objective"],
            "acceptance_criteria": contract["acceptance_criteria"],
            "mode": effective_mode,
        }
        now = utc_now()
        with self.connect() as connection:
            conversation = connection.execute(
                "select * from conversations where id = ?", (conversation_id,)
            ).fetchone()
            if conversation is None:
                raise KeyError("Conversation not found")
            if record_message:
                connection.execute(
                    """
                    insert into conversation_messages(conversation_id, role, content, created_at)
                    values (?, 'user', ?, ?)
                    """,
                    (conversation_id, content, now),
                )
            cursor = connection.execute(
                """
                insert into agent_runs(
                    conversation_id, status, mode, permission, pursue_goal, model,
                    contract_json, context_json, access_scope, phase, goal_json,
                    checkpoint_json, agent_profile_id, agent_profile_version,
                    requested_mode, resolved_intent, intent_confidence,
                    created_at, updated_at
                ) values (
                    :conversation_id, 'queued', :mode, :permission, :pursue_goal,
                    :model, :contract, :context, :access_scope, 'understand',
                    :goal, :checkpoint, :profile_id, :profile_version,
                    :requested_mode, :resolved_intent, :intent_confidence,
                    :created_at, :updated_at
                )
                """,
                {
                    "conversation_id": conversation_id,
                    "mode": effective_mode,
                    "permission": permission,
                    "pursue_goal": int(pursue_goal),
                    "model": model,
                    "contract": json.dumps(contract),
                    "context": json.dumps(context),
                    "access_scope": permission,
                    "goal": json.dumps(goal),
                    "checkpoint": json.dumps(resume_state or {}),
                    "profile_id": profile.get("id"),
                    "profile_version": profile.get("version"),
                    "requested_mode": mode,
                    "resolved_intent": contract["resolved_intent"],
                    "intent_confidence": contract["intent_confidence"],
                    "created_at": now,
                    "updated_at": now,
                },
            )
            run_id = int(cursor.lastrowid)
            for position, title in enumerate(
                self.initial_tasks(content, effective_mode, profile), start=1
            ):
                connection.execute(
                    """
                    insert into agent_run_tasks(
                        run_id, position, title, status, details_json, created_at, updated_at
                    ) values (?, ?, ?, 'pending', '{}', ?, ?)
                    """,
                    (run_id, position, title, now, now),
                )
            connection.execute(
                "update conversations set title = case when title = 'New conversation' then ? else title end, updated_at = ? where id = ?",
                (content[:70], now, conversation_id),
            )
            connection.commit()
        cancel = threading.Event()
        pause = threading.Event()
        thread = threading.Thread(
            target=self.execute_run,
            args=(run_id, content, cancel),
            name=f"kodex-agent-{run_id}",
            daemon=True,
        )
        with self._lock:
            self.cancel_events[run_id] = cancel
            self.pause_events[run_id] = pause
            self.run_threads[run_id] = thread
        thread.start()
        return self.get_run(run_id)

    @staticmethod
    def initial_tasks(
        content: str, mode: str, profile: dict[str, Any]
    ) -> list[str]:
        if mode == "plan":
            return ["Inspect project context", "Produce an implementation plan"]
        workflow = [
            str(item.get("action", "")).strip().replace("_", " ").title()
            for item in profile.get("workflow", [])
            if isinstance(item, dict) and str(item.get("action", "")).strip()
        ]
        return workflow or [
            "Understand the request",
            "Inspect relevant project files",
            "Implement the required changes",
            "Run relevant validation",
            "Review the result against the request",
        ]

    def resolve_agent_profile(self, profile_id: Optional[int]) -> dict[str, Any]:
        try:
            with self.connect() as connection:
                if profile_id is not None:
                    row = connection.execute(
                        "select * from agent_profiles where id = ?", (profile_id,)
                    ).fetchone()
                else:
                    row = connection.execute(
                        """
                        select * from agent_profiles
                        where status = 'active'
                        order by updated_at desc limit 1
                        """
                    ).fetchone()
        except sqlite3.Error:
            row = None
        if row is None:
            return {
                "id": None,
                "version": None,
                "name": "Kodex Workspace Engineer",
                "role": "coder",
                "prompt": "Inspect the workspace, implement complete changes, and validate the result.",
                "constitution": "Preserve user work and never claim success without evidence.",
                "workflow": [],
                "tool_policy": {"allow": list(self.TOOL_SCHEMAS)},
            }
        profile = dict(row)
        profile["workflow"] = json.loads(profile.pop("workflow_json") or "[]")
        profile["tool_policy"] = json.loads(profile.pop("tool_policy_json") or "{}")
        return profile

    def set_phase(self, run_id: int, phase: str, status: Optional[str] = None) -> None:
        with self.connect() as connection:
            if status:
                connection.execute(
                    "update agent_runs set phase = ?, status = ?, updated_at = ? where id = ?",
                    (phase, status, utc_now(), run_id),
                )
            else:
                connection.execute(
                    "update agent_runs set phase = ?, updated_at = ? where id = ?",
                    (phase, utc_now(), run_id),
                )
            connection.commit()
        self.emit("agent.phase_changed", {"run_id": run_id, "phase": phase})

    def update_task(
        self, run_id: int, position: int, status: str, details: Optional[dict[str, Any]] = None
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                update agent_run_tasks
                set status = ?, details_json = ?, updated_at = ?
                where run_id = ? and position = ?
                """,
                (status, json.dumps(details or {}), utc_now(), run_id, position),
            )
            connection.commit()
        self.emit(
            "agent.task_changed",
            {"run_id": run_id, "position": position, "status": status},
        )

    def save_observation(self, run_id: int, kind: str, data: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                insert into agent_observations(run_id, kind, data_json, created_at)
                values (?, ?, ?, ?)
                """,
                (run_id, kind, json.dumps(data, default=str), utc_now()),
            )
            connection.commit()

    def save_checkpoint(self, run_id: int, phase: str, state: dict[str, Any]) -> None:
        compact = json.loads(json.dumps(state, default=str))
        with self.connect() as connection:
            connection.execute(
                """
                insert into agent_checkpoints(run_id, phase, state_json, created_at)
                values (?, ?, ?, ?)
                """,
                (run_id, phase, json.dumps(compact), utc_now()),
            )
            connection.execute(
                """
                update agent_runs set phase = ?, checkpoint_json = ?, progress_json = ?,
                    updated_at = ? where id = ?
                """,
                (phase, json.dumps(compact), json.dumps(compact), utc_now(), run_id),
            )
            connection.commit()
        self.emit("agent.checkpoint", {"run_id": run_id, "phase": phase})

    def queue_steering(
        self,
        run_id: int,
        content: str,
        attachments: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        content = content.strip()
        if not content:
            raise ValueError("Steering instruction is required")
        safe_attachments = [
            item
            for item in (attachments or [])
            if isinstance(item, dict)
        ][:10]
        with self.connect() as connection:
            run = connection.execute(
                "select status from agent_runs where id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise KeyError("Agent run not found")
            if run["status"] not in {
                "queued", "understanding", "planning", "working", "testing",
                "waiting_for_approval", "paused", "stopping",
            }:
                raise ValueError("Only an active run can receive steering")
            cursor = connection.execute(
                """
                insert into agent_steering_messages(
                    run_id, content, status, created_at, attachments_json
                )
                values (?, ?, 'queued', ?, ?)
                """,
                (run_id, content, utc_now(), json.dumps(safe_attachments)),
            )
            connection.commit()
        message = {
            "id": int(cursor.lastrowid),
            "run_id": run_id,
            "content": content,
            "status": "queued",
            "attachments": safe_attachments,
        }
        self.emit("agent.steering_queued", message)
        return message

    def consume_steering(self, run_id: int) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                select * from agent_steering_messages
                where run_id = ? and status = 'queued' order by id
                """,
                (run_id,),
            ).fetchall()
            if rows:
                attachments = [
                    item
                    for row in rows
                    for item in json.loads(row["attachments_json"] or "[]")
                    if isinstance(item, dict)
                ][:10]
                if attachments:
                    run = connection.execute(
                        "select context_json from agent_runs where id = ?", (run_id,)
                    ).fetchone()
                    context = json.loads(run["context_json"] or "{}") if run else {}
                    existing = [
                        item
                        for item in context.get("attachments", [])
                        if isinstance(item, dict) and item.get("kind") != "image"
                    ]
                    context["attachments"] = [*existing, *attachments]
                    connection.execute(
                        "update agent_runs set context_json = ?, updated_at = ? where id = ?",
                        (json.dumps(context), utc_now(), run_id),
                    )
                connection.executemany(
                    """
                    update agent_steering_messages
                    set status = 'consumed', consumed_at = ?
                    where id = ?
                    """,
                    [(utc_now(), row["id"]) for row in rows],
                )
                connection.commit()
        return [str(row["content"]) for row in rows]

    def update_run(self, run_id: int, status: str, summary: Optional[str] = None) -> None:
        with self.connect() as connection:
            if summary is None:
                connection.execute(
                    "update agent_runs set status = ?, updated_at = ? where id = ?",
                    (status, utc_now(), run_id),
                )
            else:
                connection.execute(
                    "update agent_runs set status = ?, summary = ?, updated_at = ? where id = ?",
                    (status, summary, utc_now(), run_id),
                )
            connection.commit()
        self.emit("agent.status_changed", {"run_id": run_id, "status": status})

    def add_step(
        self, run_id: int, kind: str, status: str, title: str, data: Optional[dict[str, Any]] = None
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                insert into agent_steps(run_id, kind, status, title, data_json, created_at, updated_at)
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, kind, status, title, json.dumps(data or {}), utc_now(), utc_now()),
            )
            connection.commit()
        self.emit(
            f"agent.{kind}",
            {"run_id": run_id, "step_id": cursor.lastrowid, "status": status, "title": title, "data": data or {}},
        )
        return int(cursor.lastrowid)

    def finish_step(self, step_id: int, status: str, data: Optional[dict[str, Any]] = None) -> None:
        with self.connect() as connection:
            connection.execute(
                "update agent_steps set status = ?, data_json = ?, updated_at = ? where id = ?",
                (status, json.dumps(data or {}), utc_now(), step_id),
            )
            connection.commit()

    def conversation_context(self, conversation_id: int, limit: int = 16) -> str:
        with self.connect() as connection:
            rows = connection.execute(
                """
                select role, content from conversation_messages
                where conversation_id = ? order by id desc limit ?
                """,
                (conversation_id, limit),
            ).fetchall()
        messages = [dict(row) for row in reversed(rows)]
        return "\n".join(
            f"{message['role'].upper()}: {str(message['content'])[:6000]}"
            for message in messages
        )[-30_000:]

    @staticmethod
    def relevance_terms(text: str) -> set[str]:
        return {
            term.lower()
            for term in re.findall(r"[A-Za-z_][A-Za-z0-9_.-]{2,}", text)
            if term.lower()
            not in {
                "the", "and", "that", "this", "with", "from", "make", "please",
                "into", "have", "will", "should", "your", "agent",
            }
        }

    def relevant_structured_memories(self, request: str, limit: int = 12) -> list[dict[str, Any]]:
        try:
            with self.connect() as connection:
                rows = connection.execute(
                    """
                    select * from structured_memories
                    where status = 'active'
                    order by updated_at desc limit 200
                    """
                ).fetchall()
        except sqlite3.Error:
            return []
        terms = self.relevance_terms(request)
        ranked: list[tuple[int, dict[str, Any]]] = []
        for row in rows:
            memory = dict(row)
            haystack = " ".join(
                [
                    str(memory.get("title", "")),
                    str(memory.get("content", "")),
                    str(memory.get("tags_json", "")),
                ]
            ).lower()
            score = sum(3 if term in str(memory.get("title", "")).lower() else 1 for term in terms if term in haystack)
            if memory.get("scope") == "project":
                score += 1
            ranked.append((score, memory))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [item for score, item in ranked[:limit] if score > 0] or [
            item for _score, item in ranked[: min(4, limit)]
        ]

    def enabled_mcp_servers(self) -> list[dict[str, Any]]:
        try:
            with self.connect() as connection:
                rows = connection.execute(
                    """
                    select id, name, command_json, env_json, permission, status
                    from mcp_servers where enabled = 1
                    order by name
                    """
                ).fetchall()
        except sqlite3.Error:
            return []
        return [
            {
                **dict(row),
                "command": json.loads(row["command_json"] or "[]"),
                "env": json.loads(row["env_json"] or "{}"),
            }
            for row in rows
        ]

    def workspace_overview(
        self,
        context: dict[str, Any],
        request: str,
        conversation_id: Optional[int] = None,
        profile: Optional[dict[str, Any]] = None,
    ) -> str:
        files = self.workspace_file_index()
        attachments = context.get("attachments", [])
        attachment_text = "\n".join(
            (
                f"IMAGE ATTACHMENT {item.get('name', item.get('path', 'image'))} "
                f"({item.get('mimeType', 'image')}). The image is supplied directly to the vision model."
                if item.get("kind") == "image"
                else f"ATTACHMENT {item.get('path', item.get('name', 'context'))}:\n{str(item.get('content', ''))[:12000]}"
            )
            for item in attachments
            if not self.secret_like_path(str(item.get("path", item.get("name", ""))))
        )
        terms = [
            term.lower()
            for term in re.findall(r"[A-Za-z_][A-Za-z0-9_-]{2,}", request)
            if term.lower()
            not in {"the", "and", "that", "this", "with", "from", "make", "please", "into"}
        ][:12]
        snippets = self.relevant_workspace_snippets(files, terms)
        try:
            git = subprocess.run(
                ["git", "status", "--short"],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            ).stdout[:6000]
        except (OSError, subprocess.TimeoutExpired):
            git = ""
        memories = self.relevant_structured_memories(request)
        memory_text = "\n".join(
            f"- [{row.get('scope', 'project')}] {row.get('title', '')}: {str(row.get('content', ''))[:1200]}"
            for row in memories
        )
        project_state = load_project_state(self.workspace)
        skills = discover_skills(self.workspace)
        skill_text = "\n\n".join(
            f"SKILL {skill['name']} [{skill['trust']}]: {skill['description']}\n{skill['content'][:4000]}"
            for skill in skills[:6]
            if skill["trust"] != "disabled"
        )
        package = "\n\n".join(
            part
            for part in (
                f"CURRENT REQUEST:\n{request}",
                (
                    "RECENT CONVERSATION:\n"
                    + self.conversation_context(conversation_id)
                    if conversation_id
                    else ""
                ),
                (
                    "ACTIVE AGENT PROFILE:\n"
                    + json.dumps(profile, default=str)[:12_000]
                    if profile
                    else ""
                ),
                f"ATTACHED AND OPEN FILE CONTEXT:\n{attachment_text}" if attachment_text else "",
                "RELEVANT CODE SEARCH:\n" + "\n\n".join(snippets) if snippets else "",
                f"PROJECT MEMORY:\n{memory_text}" if memory_text else "",
                f"PROJECT CONTINUITY:\n{json.dumps(project_state, default=str)[:8000]}"
                if project_state
                else "",
                f"ACTIVE PROJECT SKILLS:\n{skill_text}" if skill_text else "",
                (
                    "ENABLED MCP SERVERS:\n"
                    + json.dumps(
                        [
                            {
                                "id": item["id"],
                                "name": item["name"],
                                "permission": item["permission"],
                                "status": item["status"],
                            }
                            for item in self.enabled_mcp_servers()
                        ]
                    )
                    if self.enabled_mcp_servers()
                    else ""
                ),
                f"GIT STATUS:\n{git}" if git else "",
                (
                    "IDE CONTEXT:\n"
                    + json.dumps(
                        {
                            "enabled": context.get("ide_context_enabled", True),
                            "workspace": context.get("workspace"),
                            "current_directory": context.get("current_directory"),
                            "active_file": context.get("active_file"),
                            "open_files": context.get("open_files", []),
                            "selected_entry": context.get("selected_entry"),
                            "unsaved_files": context.get("unsaved_files", []),
                            "git": context.get("git"),
                            "project_intelligence": context.get("project_intelligence"),
                            "run_profiles": context.get("run_profiles", []),
                        },
                        default=str,
                    )[:16_000]
                ),
                f"DIAGNOSTICS AND PROCESSES:\n{json.dumps({'diagnostics': context.get('diagnostics'), 'processes': context.get('processes', [])}, default=str)[:12000]}",
                "WORKSPACE FILE INDEX:\n" + "\n".join(files),
            )
            if part
        )
        return package[:60_000]

    def workspace_file_index(self, limit: int = 180) -> list[str]:
        ripgrep = shutil.which("rg")
        if ripgrep:
            command = [ripgrep, "--files"]
            for directory in sorted(WORKSPACE_IGNORED_DIRECTORIES):
                command.extend(["-g", f"!{directory}/**"])
            try:
                result = subprocess.run(
                    command,
                    cwd=self.workspace,
                    capture_output=True,
                    text=True,
                    timeout=2,
                    check=False,
                )
                if result.returncode in {0, 1}:
                    return [
                        path
                        for path in result.stdout.splitlines()
                        if path and not self.secret_like_path(path)
                    ][:limit]
            except (OSError, subprocess.TimeoutExpired):
                pass
        files: list[str] = []
        for current_root, directories, filenames in os.walk(self.workspace):
            directories[:] = [
                item for item in directories
                if item not in WORKSPACE_IGNORED_DIRECTORIES
            ]
            for filename in filenames:
                path = Path(current_root) / filename
                try:
                    if path.stat().st_size > 1_000_000:
                        continue
                except OSError:
                    continue
                files.append(str(path.relative_to(self.workspace)))
                if len(files) >= limit:
                    return files
        return files

    def relevant_workspace_snippets(
        self, files: list[str], terms: list[str], limit: int = 12
    ) -> list[str]:
        if not terms or not files:
            return []
        ripgrep = shutil.which("rg")
        if ripgrep:
            pattern = "|".join(re.escape(term) for term in terms)
            command = [
                ripgrep, "-n", "-i", "-H", "--no-heading", "--color", "never",
                "-m", "10", pattern, "--", *files,
            ]
            try:
                result = subprocess.run(
                    command,
                    cwd=self.workspace,
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=False,
                )
                grouped: dict[str, list[str]] = {}
                for line in result.stdout.splitlines():
                    path, separator, excerpt = line.partition(":")
                    if not separator or self.secret_like_path(path):
                        continue
                    grouped.setdefault(path, []).append(excerpt[:300])
                    if len(grouped) >= limit and all(
                        len(items) >= 2 for items in grouped.values()
                    ):
                        break
                return [
                    f"RELEVANT {path}:\n" + "\n".join(excerpts[:10])
                    for path, excerpts in list(grouped.items())[:limit]
                ]
            except (OSError, subprocess.TimeoutExpired):
                pass
        snippets: list[str] = []
        for relative in files:
            if len(snippets) >= limit or self.secret_like_path(relative):
                continue
            try:
                content = (self.workspace / relative).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            excerpts = [
                line[:300]
                for line in content.splitlines()
                if any(term in line.lower() for term in terms)
            ][:10]
            if excerpts:
                snippets.append(f"RELEVANT {relative}:\n" + "\n".join(excerpts))
        return snippets

    @staticmethod
    def secret_like_path(path: str) -> bool:
        lowered = path.lower()
        name = Path(lowered).name
        return (
            name.startswith(".env")
            or any(token in name for token in ("credential", "secret", "private-key", "id_rsa"))
            or lowered.endswith((".pem", ".key", ".p12"))
        )

    def image_attachments_for_run(self, run_id: int) -> list[dict[str, str]]:
        if run_id <= 0:
            return []
        try:
            with self.connect() as connection:
                row = connection.execute(
                    "select context_json from agent_runs where id = ?", (run_id,)
                ).fetchone()
            context = json.loads(row["context_json"] or "{}") if row else {}
        except (sqlite3.Error, ValueError, TypeError):
            return []
        images: list[dict[str, str]] = []
        for item in context.get("attachments", []):
            if not isinstance(item, dict) or item.get("kind") != "image":
                continue
            mime_type = str(item.get("mimeType", ""))
            data_url = str(item.get("dataUrl", ""))
            if (
                mime_type not in {"image/png", "image/jpeg", "image/webp", "image/gif"}
                or not data_url.startswith(f"data:{mime_type};base64,")
            ):
                continue
            images.append(
                {
                    "name": str(item.get("name", f"image-{len(images) + 1}")),
                    "mime_type": mime_type,
                    "data_url": data_url,
                    "base64": data_url.split(",", 1)[1],
                }
            )
            if len(images) >= 10:
                break
        return images

    @staticmethod
    def messages_with_images(
        messages: list[dict[str, Any]],
        images: list[dict[str, str]],
        provider: str,
    ) -> list[dict[str, Any]]:
        prepared = [dict(message) for message in messages]
        if not images:
            return prepared
        user_index = next(
            (
                index
                for index in range(len(prepared) - 1, -1, -1)
                if prepared[index].get("role") == "user"
            ),
            len(prepared) - 1,
        )
        if provider == "ollama":
            prepared[user_index]["images"] = [image["base64"] for image in images]
            return prepared
        text = prepared[user_index].get("content", "")
        prepared[user_index]["content"] = [
            {"type": "text", "text": str(text)},
            *[
                {
                    "type": "image_url",
                    "image_url": {"url": image["data_url"]},
                }
                for image in images
            ],
        ]
        return prepared

    @staticmethod
    def trim_message_content(content: str, limit: int) -> str:
        if len(content) <= limit:
            return content
        marker = "\n\n[older context removed to fit the model window]\n\n"
        usable = max(0, limit - len(marker))
        head = usable * 2 // 3
        return content[:head] + marker + content[-(usable - head):]

    def fit_messages_to_context(
        self,
        run_id: int,
        choice: ProviderChoice,
        messages: list[dict[str, Any]],
        *,
        emergency: bool = False,
    ) -> list[dict[str, Any]]:
        context_length = max(2048, int(getattr(choice, "context_length", 8192)))
        reserved_output = max(1024, min(4096, context_length // 4))
        input_tokens = max(1024, context_length - reserved_output)
        if emergency:
            # LM Studio can report the model's architectural maximum rather than
            # the smaller context configured for the currently loaded instance.
            input_tokens = min(input_tokens, max(1536, min(3072, context_length // 2)))
        character_budget = min(90_000, input_tokens * 3)
        prepared = [dict(message) for message in messages]
        text_indices = [
            index
            for index, message in enumerate(prepared)
            if isinstance(message.get("content"), str)
        ]
        total = sum(len(str(prepared[index]["content"])) for index in text_indices)
        if total <= character_budget:
            return prepared

        system_indices = [
            index for index in text_indices if prepared[index].get("role") == "system"
        ]
        latest_user = next(
            (
                index
                for index in reversed(text_indices)
                if prepared[index].get("role") == "user"
            ),
            text_indices[-1],
        )
        primary_system = system_indices[0] if system_indices else None
        protected = {latest_user}
        if primary_system is not None:
            protected.add(primary_system)
        remaining_indices = [index for index in text_indices if index not in protected]
        auxiliary_budget = min(
            character_budget // 10,
            1200 * len(remaining_indices),
        )
        primary_budget = character_budget - auxiliary_budget
        system_budget = primary_budget * 45 // 100 if primary_system is not None else 0
        user_budget = primary_budget - system_budget

        if primary_system is not None:
            prepared[primary_system]["content"] = self.trim_message_content(
                str(prepared[primary_system]["content"]), system_budget
            )
        prepared[latest_user]["content"] = self.trim_message_content(
            str(prepared[latest_user]["content"]), user_budget
        )
        per_auxiliary = (
            auxiliary_budget // len(remaining_indices) if remaining_indices else 0
        )
        for index in remaining_indices:
            prepared[index]["content"] = self.trim_message_content(
                str(prepared[index]["content"]), per_auxiliary
            )
        self.emit(
            "agent.context_compacted",
            {
                "run_id": run_id,
                "provider": choice.provider,
                "model": choice.model,
                "context_length": context_length,
                "emergency": emergency,
                "before_characters": total,
                "after_characters": sum(
                    len(str(prepared[index]["content"])) for index in text_indices
                ),
            },
        )
        return prepared

    def model_call(self, run_id: int, choice: ProviderChoice, messages: list[dict[str, Any]], cancel: threading.Event) -> str:
        if cancel.is_set():
            raise InterruptedError("Agent run cancelled")
        self.emit("agent.model_started", {"provider": choice.provider, "model": choice.model})
        images = self.image_attachments_for_run(run_id)
        if choice.provider == "openai-codex":
            executable = os.environ.get("CODEX_CLI_PATH") or shutil.which("codex")
            if not executable:
                raise RuntimeError("Codex CLI is not installed or could not be found.")
            prompt = (
                "Act only as the planning and decision model for the Kodex desktop agent. "
                "Do not edit files or run commands yourself. Return only the JSON object requested "
                "by the system instructions below.\n\n"
                + "\n\n".join(
                    f"{message.get('role', 'user').upper()}:\n{message.get('content', '')}"
                    for message in messages
                )
            )
            command = [
                executable,
                "exec",
                "--json",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "--ignore-user-config",
                "--ignore-rules",
                "--disable",
                "plugins",
                "-c",
                'model_reasoning_effort="low"',
                "-c",
                'model_reasoning_summary="none"',
                "-c",
                'model_verbosity="low"',
                "-c",
                'approval_policy="never"',
                "-c",
                'web_search="disabled"',
                "-c",
                "project_doc_max_bytes=0",
                "--cd",
                str(self.workspace),
            ]
            artifact_directory = tempfile.TemporaryDirectory(prefix="kodex-codex-")
            if images:
                for index, image in enumerate(images, 1):
                    extension = {
                        "image/png": ".png",
                        "image/jpeg": ".jpg",
                        "image/webp": ".webp",
                        "image/gif": ".gif",
                    }[image["mime_type"]]
                    image_path = Path(artifact_directory.name) / f"image-{index}{extension}"
                    image_path.write_bytes(base64.b64decode(image["base64"], validate=True))
                    command.extend(["--image", str(image_path)])
            if choice.model and choice.model != "default":
                command.extend(["--model", choice.model])
            command.extend(["--", "-"])
            try:
                process = subprocess.Popen(
                    command,
                    cwd=self.workspace,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
            except Exception:
                artifact_directory.cleanup()
                raise
            operation_id = self.register_operation(
                run_id,
                "model_process",
                lambda: self.terminate_process(process),
            )
            if process.stdin:
                process.stdin.write(prompt)
                process.stdin.close()
            messages_out: list[str] = []
            diagnostics: list[str] = []
            started = time.monotonic()
            last_activity = started
            try:
                while process.poll() is None:
                    self.wait_if_paused(run_id, cancel)
                    if cancel.is_set():
                        raise InterruptedError("Agent run cancelled")
                    now = time.monotonic()
                    if now - started > CODEX_MODEL_TIMEOUT_SECONDS:
                        raise RuntimeError("Codex decision timed out after 180 seconds.")
                    if now - last_activity > CODEX_MODEL_IDLE_TIMEOUT_SECONDS:
                        raise RuntimeError("Codex stopped producing events for 90 seconds.")
                    if not process.stdout:
                        time.sleep(0.1)
                        continue
                    readable, _, _ = select.select([process.stdout], [], [], 0.2)
                    if not readable:
                        continue
                    line = process.stdout.readline()
                    if not line:
                        continue
                    last_activity = time.monotonic()
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        diagnostics.append(line.strip())
                        continue
                    self.emit("agent.codex_event", {"run_id": run_id, "event": event})
                    item = event.get("item") or {}
                    if (
                        event.get("type") == "item.completed"
                        and item.get("type") == "agent_message"
                        and item.get("text")
                    ):
                        text = str(item["text"])
                        messages_out.append(text)
                        self.emit("agent.token", {"run_id": run_id, "content": text})
                    if event.get("type") in {"turn.failed", "error"}:
                        diagnostics.append(
                            str(event.get("error") or event.get("message") or event)
                        )
            except Exception:
                self.terminate_process(process)
                raise
            finally:
                self.unregister_operation(run_id, operation_id)
                if process.stdout:
                    remainder = process.stdout.read()
                    if remainder:
                        diagnostics.append(remainder)
                    process.stdout.close()
                artifact_directory.cleanup()
            if process.returncode != 0:
                raise RuntimeError(
                    "Codex execution failed. "
                    + ("\n".join(diagnostics)[-4000:] or f"Exit code {process.returncode}")
                )
            if not messages_out:
                raise RuntimeError(
                    "Codex completed without returning an agent decision. "
                    + "\n".join(diagnostics)[-2000:]
                )
            return messages_out[-1]
        if choice.provider == "anthropic-claude-code":
            executable = claude_executable()
            if not executable:
                raise RuntimeError("Claude Code is not installed or could not be found.")
            prompt = (
                "Act only as the planning and decision model for the Kodex desktop agent. "
                "Do not edit files or run commands yourself. Return only the JSON object requested "
                "by the system instructions below.\n\n"
                + "\n\n".join(
                    f"{message.get('role', 'user').upper()}:\n{message.get('content', '')}"
                    for message in messages
                )
            )
            command = [
                executable,
                "--print",
                "--output-format",
                "json",
                "--permission-mode",
                "plan",
                "--tools",
                "",
                "--bare",
                "--no-session-persistence",
            ]
            if choice.model and choice.model != "default":
                command.extend(["--model", choice.model])
            command.append(prompt)
            try:
                process = subprocess.Popen(
                    command,
                    cwd=self.workspace,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=True,
                )
            except OSError as error:
                raise RuntimeError(f"Claude Code execution failed: {error}") from error
            operation_id = self.register_operation(
                run_id,
                "model_process",
                lambda: self.terminate_process(process),
            )
            try:
                started = time.monotonic()
                while process.poll() is None:
                    self.wait_if_paused(run_id, cancel)
                    if cancel.is_set():
                        raise InterruptedError("Agent run cancelled")
                    if time.monotonic() - started > CODEX_MODEL_TIMEOUT_SECONDS:
                        raise RuntimeError("Claude Code decision timed out after 180 seconds.")
                    time.sleep(0.1)
                output, diagnostics = process.communicate()
            except Exception:
                self.terminate_process(process)
                raise
            finally:
                self.unregister_operation(run_id, operation_id)
            output = (output or "").strip()
            diagnostics = (diagnostics or "").strip()
            if process.returncode != 0:
                raise RuntimeError(
                    "Claude Code execution failed. "
                    + (diagnostics[-4000:] or output[-4000:] or f"Exit code {process.returncode}")
                )
            try:
                payload = json.loads(output)
            except json.JSONDecodeError as error:
                raise RuntimeError(
                    "Claude Code returned an unreadable response. " + output[-2000:]
                ) from error
            content = str(payload.get("result") or "").strip()
            if not content:
                raise RuntimeError(
                    "Claude Code completed without returning an agent decision. "
                    + diagnostics[-2000:]
                )
            self.emit("agent.token", {"run_id": run_id, "content": content})
            return content
        if choice.provider == "ollama":
            content = ""
            provider_messages = self.fit_messages_to_context(
                run_id,
                choice,
                self.messages_with_images(messages, images, "ollama"),
            )
            with httpx.stream(
                "POST",
                f"{choice.base_url}/api/chat",
                json={"model": choice.model, "messages": provider_messages, "stream": True, "format": "json"},
                timeout=180,
            ) as response:
                operation_id = self.register_operation(
                    run_id,
                    "model_stream",
                    lambda: getattr(response, "close", lambda: None)(),
                )
                try:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        self.wait_if_paused(run_id, cancel)
                        if cancel.is_set():
                            raise InterruptedError("Agent run cancelled")
                        if not line:
                            continue
                        chunk = json.loads(line).get("message", {}).get("content", "")
                        content += chunk
                        if chunk:
                            self.emit("agent.token", {"run_id": run_id, "content": chunk})
                finally:
                    self.unregister_operation(run_id, operation_id)
            if not content.strip():
                raise ProviderResponseError(
                    choice.provider,
                    choice.model,
                    "invalid_response",
                    "Ollama completed without returning content.",
                    retryable=True,
                )
            return content
        headers = {"Content-Type": "application/json"}
        if choice.api_key:
            headers["Authorization"] = f"Bearer {choice.api_key}"
        provider_messages = self.fit_messages_to_context(
            run_id,
            choice,
            self.messages_with_images(messages, images, "openai"),
        )
        request_payload = {
            "model": choice.model,
            "messages": provider_messages,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "stream": True,
            "max_tokens": max(
                512,
                min(4096, int(getattr(choice, "context_length", 8192)) // 4),
            ),
        }
        context_retried = False
        format_retried = False
        for _attempt in range(3):
            try:
                with httpx.stream(
                    "POST",
                    f"{choice.base_url}/chat/completions",
                    headers=headers,
                    json=request_payload,
                    timeout=180,
                ) as response:
                    operation_id = self.register_operation(
                        run_id,
                        "model_stream",
                        lambda: getattr(response, "close", lambda: None)(),
                    )
                    try:
                        response.raise_for_status()
                        if "text/event-stream" not in response.headers.get("content-type", ""):
                            try:
                                payload = json.loads(response.read())
                            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                                raise ProviderResponseError(
                                    choice.provider,
                                    choice.model,
                                    "invalid_response",
                                    "Provider returned an unreadable JSON response.",
                                    retryable=True,
                                ) from error
                            content = self.openai_response_content(
                                choice, payload, streaming=False
                            )
                            if not content.strip():
                                raise ProviderResponseError(
                                    choice.provider,
                                    choice.model,
                                    "invalid_response",
                                    "Provider completed without returning content.",
                                    retryable=True,
                                )
                            self.emit("agent.token", {"run_id": run_id, "content": content})
                            return content
                        content = ""
                        for line in response.iter_lines():
                            self.wait_if_paused(run_id, cancel)
                            if cancel.is_set():
                                raise InterruptedError("Agent run cancelled")
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                payload = json.loads(data)
                            except json.JSONDecodeError as error:
                                raise ProviderResponseError(
                                    choice.provider,
                                    choice.model,
                                    "invalid_response",
                                    "Provider returned malformed streaming JSON.",
                                    retryable=True,
                                ) from error
                            chunk = self.openai_response_content(
                                choice, payload, streaming=True
                            )
                            content += chunk
                            if chunk:
                                self.emit("agent.token", {"run_id": run_id, "content": chunk})
                        if not content.strip():
                            raise ProviderResponseError(
                                choice.provider,
                                choice.model,
                                "invalid_response",
                                "Provider stream ended without returning content.",
                                retryable=True,
                            )
                        return content
                    finally:
                        self.unregister_operation(run_id, operation_id)
            except httpx.HTTPStatusError as error:
                try:
                    error.response.read()
                except (httpx.StreamConsumed, httpx.ResponseNotRead):
                    pass
                try:
                    payload = error.response.json()
                except (ValueError, json.JSONDecodeError):
                    payload = {
                        "error": {
                            "message": (
                                error.response.text[-2000:]
                                or f"Provider returned HTTP {error.response.status_code}."
                            )
                        }
                    }
                provider_error = self.provider_error_from_payload(choice, payload)
                if provider_error.details["code"] == "context_overflow" and not context_retried:
                    request_payload = {
                        **request_payload,
                        "messages": self.fit_messages_to_context(
                            run_id,
                            choice,
                            self.messages_with_images(messages, images, "openai"),
                            emergency=True,
                        ),
                    }
                    context_retried = True
                    self.emit(
                        "agent.provider_compatibility",
                        {
                            "run_id": run_id,
                            "provider": choice.provider,
                            "model": choice.model,
                            "fallback": "context_compacted",
                        },
                    )
                    continue
                if (
                    not format_retried
                    and error.response.status_code in {400, 404, 422}
                    and "response_format" in request_payload
                ):
                    request_payload = {
                        key: value
                        for key, value in request_payload.items()
                        if key != "response_format"
                    }
                    format_retried = True
                    self.emit(
                        "agent.provider_compatibility",
                        {
                            "run_id": run_id,
                            "provider": choice.provider,
                            "model": choice.model,
                            "fallback": "response_format_disabled",
                        },
                    )
                    continue
                raise provider_error from error
        raise RuntimeError("OpenAI-compatible provider did not return a response")

    def compress_context(
        self,
        run_id: int,
        choice: ProviderChoice,
        context: str,
        cancel: threading.Event,
    ) -> str:
        provider = str(getattr(choice, "provider", ""))
        target = CODEX_CONTEXT_TARGET if provider == "openai-codex" else 45_000
        if len(context) <= target:
            return context
        if provider == "openai-codex":
            head = target * 2 // 3
            tail = target - head
            return (
                context[:head]
                + "\n\n[large workspace context trimmed locally]\n\n"
                + context[-tail:]
            )
        prompt = {
            "instruction": (
                "Compress this software-project context while preserving file paths, interfaces, "
                "errors, constraints, current work, and acceptance criteria. Return JSON with a "
                "single summary string."
            ),
            "context": context,
        }
        step = self.add_step(run_id, "thinking", "running", "Compressing project context")
        try:
            response = self.model_call(
                run_id,
                choice,
                [
                    {"role": "system", "content": "Return only JSON: {\"summary\":\"...\"}."},
                    {"role": "user", "content": json.dumps(prompt)},
                ],
                cancel,
            )
            summary = str(self.parse_model_json(response).get("summary", "")).strip()
            if summary:
                self.finish_step(step, "completed", {"before": len(context), "after": len(summary)})
                return summary[:45_000]
        except Exception as error:
            self.finish_step(step, "failed", {"error": str(error)})
        return context[:22_500] + "\n\n[context condensed]\n\n" + context[-22_000:]

    def parallel_review(
        self,
        run_id: int,
        choice: ProviderChoice,
        request: str,
        observations: list[dict[str, Any]],
        cancel: threading.Event,
    ) -> dict[str, Any]:
        roles = (
            {
                "reviewer": (
                    "Perform one concise combined review for behavioral regressions, unsafe edits, "
                    "unmet requirements, and missing validation."
                )
            }
            if getattr(choice, "provider", "") == "openai-codex"
            else {
                "reviewer": "Review for behavioral regressions, unsafe edits, and unmet requirements.",
                "tester": "Review validation evidence and identify missing or weak tests.",
            }
        )

        def review(role: str, instruction: str) -> tuple[str, dict[str, Any]]:
            response = self.model_call(
                run_id,
                choice,
                [
                    {
                        "role": "system",
                        "content": (
                            f"You are an independent {role}. {instruction} "
                            "Return only JSON: {\"summary\":\"...\",\"done\":true,\"actions\":[]}."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"request": request, "observations": observations[-12:]}
                        ),
                    },
                ],
                cancel,
            )
            return role, self.parse_model_json(response)

        results: dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=len(roles), thread_name_prefix="kodex-review") as executor:
            futures = {
                executor.submit(review, role, instruction): role
                for role, instruction in roles.items()
            }
            for future in as_completed(futures):
                role = futures[future]
                try:
                    _, decision = future.result()
                    results[role] = {
                        "passed": True,
                        "approved": bool(decision.get("done", True)),
                        "summary": decision.get("summary", ""),
                        "actions": decision.get("actions", []),
                    }
                except Exception as error:
                    results[role] = {
                        "passed": False,
                        "approved": True,
                        "summary": str(error),
                        "actions": [],
                    }
        self.add_step(run_id, "review", "completed", "Independent review and test agents", results)
        return results

    @staticmethod
    def repair_plan(content: str, request: str) -> dict[str, Any]:
        cleaned = re.sub(r"```(?:json)?|```", "", content).strip()
        lines = [
            re.sub(r"^(?:[-*]|\d+[.)])\s*", "", line).strip()
            for line in cleaned.splitlines()
            if line.strip()
        ]
        useful = [line for line in lines if len(line) > 5][:12]
        if not useful:
            useful = [
                "Inspect the relevant project structure and current implementation.",
                "Implement the requested behavior while preserving unrelated work.",
                "Run focused validation and repair any failures.",
                "Review the result against the original request.",
            ]
        return {
            "objective": request.strip(),
            "tasks": useful,
            "acceptance_criteria": [
                "The requested behavior is implemented",
                "Relevant validation passes",
                "No unresolved blocker remains",
            ],
            "affected_areas": [],
            "risks": [],
            "validation_strategy": "Run the nearest relevant tests, lint, build, or syntax checks.",
            "summary": "\n".join(
                f"{index}. {line}" for index, line in enumerate(useful, 1)
            ),
            "actions": [],
            "done": True,
        }

    def active_profile_for_run(self, run: dict[str, Any]) -> dict[str, Any]:
        return self.resolve_agent_profile(run.get("agent_profile_id"))

    def allowed_tools_for_profile(self, profile: dict[str, Any]) -> set[str]:
        configured = profile.get("tool_policy", {}).get("allow", [])
        if not configured:
            return {tool["id"] for tool in self.tool_registry()}
        allowed = {str(tool) for tool in configured}
        if "mcp" in allowed:
            allowed.update(
                tool["id"]
                for tool in self.tool_registry()
                if tool["id"].startswith("mcp__")
            )
        return allowed

    def generate_git_summary(self, diff: str, kind: str = "change") -> dict[str, Any]:
        trimmed = diff[-80_000:]
        fallback = self.fallback_git_summary(trimmed, kind)
        if not trimmed.strip():
            return {"source": "deterministic", **fallback}
        try:
            choice = self.select_provider("auto")
            response = self.model_call(
                0,
                choice,
                [
                    {
                        "role": "system",
                        "content": (
                            "Summarize a Git diff. Return only JSON with summary, commit_message, "
                            "and bullets (an array of concise strings)."
                        ),
                    },
                    {"role": "user", "content": trimmed},
                ],
                threading.Event(),
            )
            parsed = self.parse_model_json(response)
            summary = str(parsed.get("summary", "")).strip()
            if summary:
                return {
                    "source": f"{choice.provider}:{choice.model}",
                    "summary": summary,
                    "commit_message": str(parsed.get("commit_message", "")).strip()
                    or fallback["commit_message"],
                    "bullets": [str(item) for item in parsed.get("bullets", [])][:8]
                    or fallback["bullets"],
                }
        except Exception:
            pass
        return {"source": "deterministic", **fallback}

    @staticmethod
    def fallback_git_summary(diff: str, kind: str) -> dict[str, Any]:
        files = re.findall(r"^\+\+\+ b/(.+)$", diff, re.MULTILINE)
        additions = len(re.findall(r"^\+(?!\+\+)", diff, re.MULTILINE))
        deletions = len(re.findall(r"^-(?!--)", diff, re.MULTILINE))
        names = ", ".join(files[:4]) or "working tree"
        summary = f"Update {names} with {additions} additions and {deletions} deletions."
        return {
            "summary": summary,
            "commit_message": f"{kind}: update {files[0] if len(files) == 1 else 'project changes'}",
            "bullets": [
                f"Changed {len(set(files))} files",
                f"Added {additions} lines and removed {deletions} lines",
            ],
        }

    @staticmethod
    def parse_model_json(content: str) -> dict[str, Any]:
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if not match:
                raise ValueError("Model did not return the required JSON decision")
            parsed = json.loads(match.group(0))
        if not isinstance(parsed, dict):
            raise ValueError("Model decision must be a JSON object")
        actions = parsed.get("actions", [])
        if not isinstance(actions, list):
            actions = []
        allowed_tools = set(AgentRuntime.TOOL_SCHEMAS)
        parsed["actions"] = [
            action
            for action in actions[:16]
            if isinstance(action, dict)
            and (
                action.get("tool") in allowed_tools
                or str(action.get("tool", "")).startswith("mcp__")
                or action.get("tool") in AgentRuntime.TOOL_ALIASES
            )
            and isinstance(action.get("arguments", {}), dict)
        ]
        parsed["done"] = bool(parsed.get("done", False))
        parsed["summary"] = str(parsed.get("summary", "")).strip()
        evidence = parsed.get("evidence", {})
        parsed["evidence"] = evidence if isinstance(evidence, dict) else {}
        validation = parsed.get("validation", "")
        parsed["validation"] = validation if isinstance(validation, str) else ""
        phase = str(parsed.get("phase", "")).strip()
        parsed["phase"] = phase if phase in {
            "investigate", "plan", "execute", "validate", "review", "repair", "complete",
        } else ""
        return parsed

    def repair_decision_response(
        self,
        run_id: int,
        choice: ProviderChoice,
        content: str,
        cancel: threading.Event,
    ) -> dict[str, Any]:
        repaired = self.model_call(
            run_id,
            choice,
            [
                {
                    "role": "system",
                    "content": (
                        "Repair the following model output into one valid JSON object. "
                        "Preserve only summary, phase, task_updates, actions, done, validation, and evidence. "
                        "Do not add prose or Markdown. Use actions=[] when an action cannot be recovered safely."
                    ),
                },
                {"role": "user", "content": content[-12000:]},
            ],
            cancel,
        )
        return self.parse_model_json(repaired)

    @staticmethod
    def safe_workspace_install(command: str) -> bool:
        if re.search(r"[;&|`$<>]", command):
            return False
        try:
            parts = shlex.split(command)
        except ValueError:
            return False
        if len(parts) < 2 or parts[0] != "npm" or parts[1] not in {"install", "i", "ci"}:
            return False
        return not any(part in {"-g", "--global", "--prefix"} for part in parts[2:])

    @classmethod
    def safe_workspace_command(cls, command: str) -> bool:
        segments = [segment.strip() for segment in command.split("&&")]
        if not segments or any(not segment for segment in segments):
            return False
        for segment in segments:
            if cls.safe_workspace_install(segment):
                continue
            if re.fullmatch(r"npm run (build|test|lint|check)(?: -- [\w .:=/-]+)?", segment):
                continue
            return False
        return True

    @classmethod
    def normalize_tool_arguments(cls, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(arguments)
        if tool in {"read_file", "create_file", "edit_file", "write_file", "apply_patch", "delete_file"} and "path" not in normalized:
            for alias in ("filename", "file", "filepath", "name"):
                if alias in normalized:
                    normalized["path"] = normalized.pop(alias)
                    break
        if tool == "edit_file" and "edits" not in normalized:
            if "old_text" in normalized or "new_text" in normalized:
                normalized["edits"] = [{
                    "old_text": normalized.pop("old_text", ""),
                    "new_text": normalized.pop("new_text", ""),
                    "replace_all": normalized.pop("replace_all", False),
                }]
            elif "start_line" in normalized or "end_line" in normalized:
                normalized["edits"] = [{
                    "start_line": normalized.pop("start_line", 1),
                    "end_line": normalized.pop("end_line", normalized.get("start_line", 1)),
                    "new_text": normalized.pop("new_text", normalized.pop("content", "")),
                }]
        if tool == "list_files" and "path" not in normalized:
            normalized["path"] = normalized.pop("directory", ".")
        if tool == "search" and "query" not in normalized:
            normalized["query"] = normalized.pop("term", normalized.pop("text", ""))
        if tool == "run_command" and "command" not in normalized:
            normalized["command"] = normalized.pop("cmd", normalized.pop("script", ""))
        return normalized

    @classmethod
    def normalize_actions(cls, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized_actions: list[dict[str, Any]] = []
        for action in actions:
            source_tool = str(action.get("tool", ""))
            tool = cls.TOOL_ALIASES.get(source_tool, source_tool)
            arguments = dict(action.get("arguments") or {})
            plural = arguments.pop("paths", arguments.pop("files", None))
            if tool in {"read_file", "read_files"} and plural is not None:
                paths = plural if isinstance(plural, list) else [plural]
                for path in paths:
                    if not isinstance(path, str) or not path.strip():
                        continue
                    normalized_actions.append(
                        {"tool": "read_file", "arguments": {"path": path}}
                    )
                continue
            if tool == "read_files" and "path" in arguments:
                path = arguments.pop("path")
                if isinstance(path, str) and path.strip():
                    normalized_actions.append(
                        {"tool": "read_file", "arguments": {"path": path}}
                    )
                continue
            if plural is not None and "path" not in arguments:
                arguments["path"] = plural[0] if isinstance(plural, list) and plural else plural
            normalized_actions.append({
                "tool": tool,
                "arguments": cls.normalize_tool_arguments(tool, arguments),
            })
        return normalized_actions

    def inspection_paths(self, limit: int = 4) -> list[str]:
        preferred = (
            "package.json",
            "pyproject.toml",
            "Cargo.toml",
            "README.md",
            "main.js",
            "main.ts",
            "src/main.ts",
            "src/main.tsx",
            "src/App.tsx",
            "index.html",
            "app.js",
            "styles.css",
        )
        selected = [
            path for path in preferred
            if (self.workspace / path).is_file()
        ]
        if len(selected) < limit:
            ignored = {
                "package-lock.json", "npm-shrinkwrap.json", "yarn.lock",
                "pnpm-lock.yaml", "poetry.lock", "Cargo.lock",
            }
            for path in sorted(self.workspace.rglob("*")):
                if len(selected) >= limit:
                    break
                if (
                    not path.is_file()
                    or path.name in ignored
                    or path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}
                    or any(part in {".git", "node_modules", "dist", "build"} for part in path.parts)
                ):
                    continue
                relative = str(path.relative_to(self.workspace))
                if relative not in selected:
                    selected.append(relative)
        return selected[:limit]

    def prepare_actions(self, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        prepared = self.normalize_actions(actions)
        repaired: list[dict[str, Any]] = []
        for action in prepared:
            tool = str(action.get("tool", ""))
            arguments = dict(action.get("arguments") or {})
            if tool == "read_file" and not arguments.get("path"):
                repaired.extend(
                    {"tool": "read_file", "arguments": {"path": path}}
                    for path in self.inspection_paths()
                )
                continue
            repaired.append(action)
        return repaired

    @classmethod
    def validate_tool_arguments(cls, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool.startswith("mcp__"):
            method = arguments.get("method", "tools/list")
            params = arguments.get("params", {})
            if not isinstance(method, str) or not isinstance(params, dict):
                raise ToolInputError(
                    "invalid_mcp_arguments",
                    tool,
                    "MCP tools require a string method and object params.",
                )
            return {"method": method, "params": params}
        schema = cls.TOOL_SCHEMAS.get(tool)
        if schema is None:
            raise ToolInputError("unsupported_tool", tool, f"Unsupported tool: {tool}")
        normalized = cls.normalize_tool_arguments(tool, arguments)
        for field in schema["required"]:
            if field not in normalized:
                raise ToolInputError(
                    "missing_field",
                    tool,
                    f"{tool} requires {field}.",
                    field=field,
                    suggested_recovery=f"Provide a concrete {field} and retry the action.",
                )
        for field, expected in schema["properties"].items():
            if field not in normalized:
                continue
            value = normalized[field]
            valid = (
                expected == "string" and isinstance(value, str)
                or expected == "array" and isinstance(value, list)
                or expected == "integer" and isinstance(value, int)
                or expected == "boolean" and isinstance(value, bool)
            )
            if not valid:
                raise ToolInputError(
                    "invalid_type",
                    tool,
                    f"{field} must be {expected}.",
                    field=field,
                    suggested_recovery=f"Correct the {field} value and retry.",
                )
            if expected == "string" and field in {"path", "query", "command", "url"}:
                if value.strip().lower() in cls.PLACEHOLDER_VALUES:
                    raise ToolInputError(
                        "placeholder_value",
                        tool,
                        f"{field} must be a concrete value.",
                        field=field,
                        suggested_recovery=f"Inspect the workspace and provide the exact {field}.",
                    )
        if tool == "edit_file" and not normalized["edits"]:
            raise ToolInputError(
                "empty_edits", tool, "edit_file requires at least one edit.",
                field="edits", suggested_recovery="Read the target file and provide an anchored or line-range edit.",
            )
        if tool == "run_command":
            command = normalized["command"].strip()
            prose = re.match(r"^(?:run|execute|please run)\s+['\"`](.+)['\"`]\s*$", command, re.I)
            if prose:
                normalized["command"] = prose.group(1)
            elif re.match(r"^(?:run|execute|please)\b", command, re.I):
                raise ToolInputError(
                    "prose_command", tool, "The command contains prose instead of shell syntax.",
                    field="command", suggested_recovery="Return only the executable shell command.",
                )
        if tool == "fetch_url":
            parsed = urllib.parse.urlparse(normalized["url"])
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ToolInputError(
                    "invalid_url",
                    tool,
                    "fetch_url requires a public HTTP or HTTPS URL.",
                    field="url",
                    suggested_recovery="Provide the exact public web page URL.",
                )
        return normalized

    def requires_approval(self, permission: str, tool: str, arguments: dict[str, Any]) -> bool:
        tool = self.TOOL_ALIASES.get(tool, tool)
        with self.connect() as connection:
            grant = connection.execute(
                "select 1 from permission_grants where tool = ? and workspace = ?",
                (tool, str(self.workspace)),
            ).fetchone()
        if grant:
            return False
        if permission == "read-only":
            return tool in {
                "create_file", "edit_file", "move_file", "delete_file", "run_command",
                "stop_process",
            } or tool.startswith("mcp__")
        if permission == "confirm-edits":
            return tool in {
                "create_file", "edit_file", "move_file", "delete_file", "run_command",
                "stop_process",
            } or tool.startswith("mcp__")
        if tool in {"delete_file", "stop_process"}:
            return True
        if tool == "fetch_url":
            return True
        if tool in {"create_file", "edit_file", "move_file"}:
            path = str(arguments.get("path", "")).lower()
            destination = str(arguments.get("destination", "")).lower()
            if permission == "full-access" and (
                Path(str(arguments.get("path", ""))).expanduser().is_absolute()
                or (
                    destination
                    and Path(str(arguments.get("destination", ""))).expanduser().is_absolute()
                )
            ):
                return True
            if any(part in path for part in (".env", "credential", "secret", "token")):
                return True
            if len(str(arguments.get("content", arguments.get("edits", "")))) > 150_000:
                return True
        if tool == "run_command":
            command = str(arguments.get("command", ""))
            classification = self.classify_command(command)
            if permission in {"workspace", "full-access"} and classification == "workspace-safe":
                return False
            return classification != "read-only"
        return False

    @classmethod
    def classify_command(cls, command: str) -> str:
        segments: list[str] = []
        current: list[str] = []
        quote = ""
        escaped = False
        index = 0
        while index < len(command):
            character = command[index]
            if escaped:
                current.append(character)
                escaped = False
                index += 1
                continue
            if character == "\\" and quote != "'":
                current.append(character)
                escaped = True
                index += 1
                continue
            if character in {"'", '"'}:
                current.append(character)
                if quote == character:
                    quote = ""
                elif not quote:
                    quote = character
                index += 1
                continue
            operator = ""
            if not quote:
                if command.startswith("&&", index) or command.startswith("||", index):
                    operator = command[index:index + 2]
                elif character == ";":
                    operator = character
            if operator:
                segments.append("".join(current).strip())
                current = []
                index += len(operator)
                continue
            current.append(character)
            index += 1
        if quote or escaped:
            return "sensitive"
        segments.append("".join(current).strip())
        if not segments or any(not segment for segment in segments):
            return "sensitive"
        read_only = {
            "pwd", "ls", "find", "rg", "grep", "sed", "head", "tail", "wc",
            "git status", "git diff", "git log", "git show",
        }
        safe_prefixes = {
            "npm run", "npm test", "npm install", "npm ci", "npx tsc",
            "python -m pytest", "python3 -m pytest", "python -m unittest",
            "python3 -m unittest", "python -m compileall", "python3 -m compileall",
            "node --check", "cargo test", "cargo check", "go test",
        }
        sensitive_tokens = {
            "sudo", "rm", "rmdir", "chmod", "chown", "git push", "git clean",
            "git reset", "git checkout", "git restore", "brew install",
            "apt install", "pip install", "pip3 install", "curl", "wget", "ssh",
        }
        classifications: list[str] = []
        for segment in segments:
            lowered = segment.lower()
            try:
                parts = shlex.split(segment)
            except ValueError:
                return "sensitive"
            if not parts:
                return "sensitive"
            if any(
                lowered == token or lowered.startswith(token + " ")
                for token in sensitive_tokens
            ):
                classifications.append("sensitive")
                continue
            if lowered.startswith(("npm install -g", "npm i -g", "npm install --global")):
                classifications.append("sensitive")
                continue
            if any(lowered == prefix or lowered.startswith(prefix + " ") for prefix in safe_prefixes):
                classifications.append("workspace-safe")
                continue
            if any(lowered == prefix or lowered.startswith(prefix + " ") for prefix in read_only):
                classifications.append("read-only")
                continue
            classifications.append("workspace-safe")
        if "sensitive" in classifications:
            return "sensitive"
        if "workspace-safe" in classifications:
            return "workspace-safe"
        return "read-only"

    def create_approval(
        self, run_id: int, tool: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        if tool == "fetch_url":
            host = urllib.parse.urlparse(str(arguments.get("url", ""))).hostname or "this website"
            summary = f"Allow Kodex to visit {host} and gather data?"
        else:
            summary = f"Allow agent to {tool.replace('_', ' ')}"
        with self.connect() as connection:
            cursor = connection.execute(
                """
                insert into approvals(run_id, kind, status, summary, details_json, created_at, updated_at)
                values (?, ?, 'pending', ?, ?, ?, ?)
                """,
                (
                    run_id,
                    tool,
                    summary,
                    json.dumps(arguments),
                    utc_now(),
                    utc_now(),
                ),
            )
            connection.commit()
            approval_id = int(cursor.lastrowid)
        approval = {
            "id": approval_id,
            "run_id": run_id,
            "kind": tool,
            "status": "pending",
            "summary": summary,
            "details": arguments,
        }
        self.emit("agent.waiting_for_approval", approval)
        return approval

    def wait_for_approval(
        self, run_id: int, approval_id: int, cancel: threading.Event
    ) -> bool:
        self.update_run(run_id, "waiting_for_approval")
        while not cancel.wait(0.3):
            with self.connect() as connection:
                row = connection.execute(
                    "select status from approvals where id = ?", (approval_id,)
                ).fetchone()
            if row and row["status"] != "pending":
                return row["status"] == "approved"
        return False

    def wait_if_paused(self, run_id: int, cancel: threading.Event) -> None:
        pause = self.pause_events.get(run_id)
        while pause and pause.is_set() and not cancel.wait(0.2):
            pass
        if cancel.is_set():
            raise InterruptedError("Agent run cancelled")

    def safe_path(self, relative: str, permission: str = "workspace") -> Path:
        raw = str(relative).replace("\\", "/")
        candidate = Path(raw).expanduser()
        path = (
            candidate.resolve()
            if candidate.is_absolute() and permission == "full-access"
            else (self.workspace / raw.lstrip("/")).resolve()
        )
        if permission != "full-access" and path != self.workspace and self.workspace not in path.parents:
            raise ValueError("Path is outside the workspace")
        return path

    @staticmethod
    def file_hash(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def display_path(self, path: Path) -> str:
        return (
            str(path.relative_to(self.workspace)) or "."
            if path == self.workspace or self.workspace in path.parents
            else str(path)
        )

    def execute_tool(
        self,
        run_id: int,
        tool: str,
        arguments: dict[str, Any],
        cancel: threading.Event,
        permission: str = "workspace",
    ) -> dict[str, Any]:
        tool = self.TOOL_ALIASES.get(tool, tool)
        arguments = self.validate_tool_arguments(tool, arguments)
        self.emit("agent.tool_started", {
            "run_id": run_id,
            "tool": tool,
            "arguments": arguments,
            "access_scope": permission,
        })
        if tool == "list_files":
            path = self.safe_path(arguments.get("path", "."), permission)
            result = [
                {"path": str(item.relative_to(self.workspace)), "kind": "directory" if item.is_dir() else "file"}
                for item in sorted(path.iterdir())[:300]
            ]
        elif tool == "tree":
            root = self.safe_path(arguments.get("path", "."), permission)
            depth = min(max(int(arguments.get("depth", 3)), 1), 8)
            entries: list[dict[str, Any]] = []
            for candidate in root.rglob("*"):
                relative_root = candidate.relative_to(root)
                if len(relative_root.parts) > depth:
                    continue
                if any(
                    part in {
                        ".git", "node_modules", "dist", "build", "release",
                        "__pycache__", ".venv", ".next", "target",
                    }
                    for part in candidate.parts
                ):
                    continue
                entries.append(
                    {
                        "path": self.display_path(candidate),
                        "kind": "directory" if candidate.is_dir() else "file",
                    }
                )
                if len(entries) >= 800:
                    break
            result = entries
        elif tool == "fetch_url":
            url = str(arguments["url"])
            parsed = urllib.parse.urlparse(url)
            host = parsed.hostname or ""
            if host.lower() == "localhost" or host.lower().endswith(".local"):
                raise ToolInputError(
                    "private_url",
                    tool,
                    "Local and private network addresses cannot be crawled.",
                    field="url",
                )
            try:
                addresses = {
                    item[4][0]
                    for item in socket.getaddrinfo(
                        host,
                        parsed.port or (443 if parsed.scheme == "https" else 80),
                        type=socket.SOCK_STREAM,
                    )
                }
            except socket.gaierror as error:
                raise ToolInputError(
                    "unreachable_url",
                    tool,
                    f"Could not resolve {host}.",
                    field="url",
                ) from error
            if any(
                ipaddress.ip_address(address).is_private
                or ipaddress.ip_address(address).is_loopback
                or ipaddress.ip_address(address).is_link_local
                for address in addresses
            ):
                raise ToolInputError(
                    "private_url",
                    tool,
                    "Local and private network addresses cannot be crawled.",
                    field="url",
                )
            max_bytes = min(
                max(int(arguments.get("max_bytes", 1_000_000)), 10_000),
                2_000_000,
            )
            chunks: list[bytes] = []
            total = 0
            response_truncated = False
            with httpx.stream(
                "GET",
                url,
                follow_redirects=True,
                timeout=20,
                headers={"User-Agent": "Kodex/0.1 web research"},
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if not any(
                    allowed in content_type
                    for allowed in (
                        "text/",
                        "application/json",
                        "application/xml",
                        "application/xhtml+xml",
                    )
                ):
                    raise ToolInputError(
                        "unsupported_content",
                        tool,
                        f"Kodex can only gather text data, not {content_type or 'this file type'}.",
                        field="url",
                    )
                for chunk in response.iter_bytes():
                    remaining = max_bytes - total
                    if remaining <= 0:
                        response_truncated = True
                        break
                    if len(chunk) > remaining:
                        chunks.append(chunk[:remaining])
                        total += remaining
                        response_truncated = True
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                final_url = str(response.url)
                encoding = response.encoding or "utf-8"
            raw = b"".join(chunks).decode(encoding, errors="replace")
            if "html" in content_type:
                parser = ReadableHTMLParser()
                parser.feed(raw)
                text = parser.text()
            else:
                text = raw.strip()
            result = {
                "url": final_url,
                "source_host": urllib.parse.urlparse(final_url).hostname or host,
                "content_type": content_type.split(";", 1)[0],
                "bytes": total,
                "text": text[:120_000],
                "truncated": response_truncated or len(text) > 120_000,
                "truncation_reason": (
                    f"Page content was limited to the first {max_bytes:,} bytes."
                    if response_truncated
                    else ""
                ),
            }
        elif tool == "glob":
            root = self.safe_path(arguments.get("path", "."), permission)
            pattern = str(arguments["pattern"])
            result = [
                {
                    "path": self.display_path(candidate),
                    "kind": "directory" if candidate.is_dir() else "file",
                }
                for candidate in root.glob(pattern)
                if not any(
                    part in {".git", "node_modules", "dist", "build", "release"}
                    for part in candidate.parts
                )
            ][:500]
        elif tool == "read_file":
            path = self.safe_path(arguments["path"], permission)
            start_line = max(1, int(arguments.get("start_line", 1)))
            requested_end = max(
                start_line,
                int(arguments.get("end_line", start_line + 999)),
            )
            selected_lines: list[str] = []
            selected_characters = 0
            observed_lines = 0
            reached_eof = True
            with path.open("r", encoding="utf-8") as source:
                for line_number, line in enumerate(source, 1):
                    observed_lines = line_number
                    if line_number >= start_line and selected_characters < 100_000:
                        selected_lines.append(line)
                        selected_characters += len(line)
                    if line_number >= requested_end:
                        reached_eof = source.readline() == ""
                        break
            selected = "".join(selected_lines)[:100_000]
            end_line = min(requested_end, observed_lines)
            stat = path.stat()
            relative = self.display_path(path)
            self.read_versions.setdefault(run_id, {})[relative] = (
                stat.st_mtime_ns,
                stat.st_size,
            )
            result = {
                "path": relative,
                "content": selected[:100_000],
                "start_line": start_line,
                "end_line": end_line,
                "total_lines": observed_lines if reached_eof else None,
                "has_more": not reached_eof,
                "version": {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size},
            }
        elif tool == "read_files":
            items: list[dict[str, Any]] = []
            for raw_path in arguments["paths"][:20]:
                try:
                    items.append(
                        self.execute_tool(
                            run_id,
                            "read_file",
                            {"path": str(raw_path)},
                            cancel,
                            permission,
                        )
                    )
                except Exception as error:
                    items.append({"path": str(raw_path), "error": str(error)})
            result = {"files": items}
        elif tool == "search":
            query = str(arguments["query"])
            matches: list[dict[str, Any]] = []
            search_root = self.safe_path(str(arguments.get("path", ".")), permission)
            for candidate in search_root.rglob("*"):
                if len(matches) >= 100 or not candidate.is_file():
                    continue
                if any(part in {".git", "node_modules", "dist"} for part in candidate.parts):
                    continue
                try:
                    for line_number, line in enumerate(candidate.read_text(encoding="utf-8").splitlines(), 1):
                        if query.lower() in line.lower():
                            display = self.display_path(candidate)
                            matches.append({"path": display, "line": line_number, "preview": line[:240]})
                except (OSError, UnicodeDecodeError):
                    continue
            result = matches
        elif tool == "create_file":
            path = self.safe_path(arguments["path"], permission)
            if path.exists():
                existing = path.read_text(encoding="utf-8")
                return self.execute_tool(
                    run_id,
                    "edit_file",
                    {
                        "path": arguments["path"],
                        "edits": [{
                            "start_line": 1,
                            "end_line": max(1, len(existing.splitlines())),
                            "new_text": str(arguments.get("content", "")),
                        }],
                    },
                    cancel,
                    permission,
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(arguments.get("content", "")), encoding="utf-8")
            display = self.display_path(path)
            result = {
                "path": display,
                "written": True,
                "created": True,
                "after_hash": self.file_hash(str(arguments.get("content", ""))),
                "changed_ranges": [{"start_line": 1, "end_line": max(1, len(str(arguments.get("content", "")).splitlines()))}],
            }
            self.emit("file.patch_applied", {"run_id": run_id, **result})
        elif tool == "edit_file":
            path = self.safe_path(arguments["path"], permission)
            if not path.is_file():
                raise ToolInputError(
                    "missing_file", tool, "edit_file requires an existing file.",
                    field="path", suggested_recovery="Inspect the parent directory and create the file if needed.",
                )
            relative = self.display_path(path)
            expected = self.read_versions.get(run_id, {}).get(relative)
            if expected is None:
                self.execute_tool(run_id, "read_file", {"path": arguments["path"]}, cancel, permission)
                expected = self.read_versions.get(run_id, {}).get(relative)
            stat = path.stat()
            if expected != (stat.st_mtime_ns, stat.st_size):
                self.execute_tool(run_id, "read_file", {"path": arguments["path"]}, cancel, permission)
            content = path.read_text(encoding="utf-8")
            before_hash = self.file_hash(content)
            updated = content
            changed_ranges: list[dict[str, int]] = []
            line_edits: list[dict[str, Any]] = []
            anchor_edits: list[dict[str, Any]] = []
            for edit in arguments["edits"]:
                if not isinstance(edit, dict):
                    raise ToolInputError("invalid_edit", tool, "Each edit must be an object.", field="edits")
                if "start_line" in edit or "end_line" in edit:
                    line_edits.append(edit)
                else:
                    anchor_edits.append(edit)
            for edit in anchor_edits:
                old_text = str(edit.get("old_text", ""))
                new_text = str(edit.get("new_text", ""))
                if not old_text:
                    raise ToolInputError(
                        "empty_anchor", tool, "Anchored edits require non-empty old_text.",
                        field="edits", suggested_recovery="Use exact text from the latest read or a line-range edit.",
                    )
                occurrences = updated.count(old_text)
                if occurrences == 0:
                    raise ToolInputError(
                        "anchor_not_found", tool, "The edit anchor was not found in the latest file.",
                        field="edits", suggested_recovery="Read the latest file and regenerate only this edit.",
                    )
                replace_all = bool(edit.get("replace_all", False))
                if occurrences > 1 and not replace_all:
                    raise ToolInputError(
                        "ambiguous_anchor", tool, f"The edit anchor matches {occurrences} locations.",
                        field="edits", suggested_recovery="Use a more specific anchor or an exact line range.",
                    )
                start_line = updated[:updated.index(old_text)].count("\n") + 1
                changed_ranges.append({"start_line": start_line, "end_line": start_line + old_text.count("\n")})
                updated = updated.replace(old_text, new_text) if replace_all else updated.replace(old_text, new_text, 1)
            if line_edits:
                lines = updated.splitlines(keepends=True)
                normalized_ranges: list[tuple[int, int, str]] = []
                for edit in line_edits:
                    start = int(edit.get("start_line", 0))
                    end = int(edit.get("end_line", start))
                    if start < 1 or end < start or end > max(1, len(lines)):
                        raise ToolInputError(
                            "invalid_line_range", tool, f"Invalid line range {start}-{end}.",
                            field="edits", suggested_recovery="Read the latest file and use valid 1-based line numbers.",
                        )
                    normalized_ranges.append((start, end, str(edit.get("new_text", ""))))
                ordered = sorted(normalized_ranges, reverse=True)
                for index, (start, end, new_text) in enumerate(ordered):
                    if index and end >= ordered[index - 1][0]:
                        raise ToolInputError("overlapping_edits", tool, "Line-range edits must not overlap.", field="edits")
                    replacement = new_text.splitlines(keepends=True)
                    if new_text and not new_text.endswith("\n") and end < len(lines):
                        replacement[-1:] = [replacement[-1] + "\n"]
                    lines[start - 1:end] = replacement
                    changed_ranges.append({"start_line": start, "end_line": end})
                updated = "".join(lines)
            path.write_text(updated, encoding="utf-8")
            updated_stat = path.stat()
            self.read_versions.setdefault(run_id, {})[relative] = (
                updated_stat.st_mtime_ns,
                updated_stat.st_size,
            )
            result = {
                "path": relative,
                "patched": True,
                "replacements": len(arguments["edits"]),
                "before_bytes": len(content.encode("utf-8")),
                "after_bytes": len(updated.encode("utf-8")),
                "before_hash": before_hash,
                "after_hash": self.file_hash(updated),
                "changed": updated != content,
                "changed_ranges": sorted(changed_ranges, key=lambda item: item["start_line"]),
                "diff": "\n".join(difflib.unified_diff(
                    content.splitlines(), updated.splitlines(), fromfile=relative, tofile=relative, lineterm=""
                ))[-12000:],
            }
            self.emit("file.patch_applied", {"run_id": run_id, **result})
        elif tool == "delete_file":
            path = self.safe_path(arguments["path"], permission)
            if path.is_dir():
                import shutil

                shutil.rmtree(path)
            else:
                path.unlink()
            result = {"path": arguments["path"], "deleted": True}
        elif tool == "move_file":
            source = self.safe_path(arguments["path"], permission)
            destination = self.safe_path(arguments["destination"], permission)
            if not source.exists():
                raise ToolInputError(
                    "missing_file",
                    tool,
                    "The source path does not exist.",
                    field="path",
                    suggested_recovery="Inspect the workspace and use the current source path.",
                )
            if destination.exists():
                raise ToolInputError(
                    "destination_exists",
                    tool,
                    "The destination path already exists.",
                    field="destination",
                    suggested_recovery="Choose a new destination or edit the existing target.",
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.rename(destination)
            result = {
                "path": self.display_path(source),
                "destination": self.display_path(destination),
                "moved": True,
            }
        elif tool == "git_status":
            process = subprocess.run(
                ["git", "status", "--short", "--branch"],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            result = {
                "repository": process.returncode == 0,
                "exit_code": process.returncode,
                "output": (process.stdout or process.stderr)[-40_000:],
            }
        elif tool == "git_diff":
            command = ["git", "diff"]
            if arguments.get("staged"):
                command.append("--staged")
            if arguments.get("path"):
                command.extend(["--", str(arguments["path"])])
            process = subprocess.run(
                command,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            result = {
                "exit_code": process.returncode,
                "diff": process.stdout[-100_000:],
                "error": process.stderr[-4000:],
            }
        elif tool == "workspace_diagnostics":
            intelligence = analyze_workspace(self.workspace)
            result = {
                "summary": intelligence.get("summary", ""),
                "frameworks": intelligence.get("frameworks", []),
                "entry_points": intelligence.get("entry_points", []),
                "validation_candidates": [
                    {"command": command, "cwd": cwd}
                    for command, cwd in self.inferred_validations()
                ],
            }
        elif tool == "list_ports":
            result = {"ports": listening_ports()}
        elif tool == "stop_process":
            pid = int(arguments["pid"])
            if pid <= 1 or pid == os.getpid():
                raise ToolInputError(
                    "invalid_pid", tool, "Refusing to stop this process."
                )
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                os.kill(pid, signal.SIGTERM)
            result = {"pid": pid, "stopped": True}
        elif tool.startswith("mcp__"):
            try:
                server_id = int(tool.split("__", 1)[1])
            except (ValueError, IndexError) as error:
                raise ToolInputError(
                    "invalid_mcp_tool", tool, "Invalid MCP tool identifier."
                ) from error
            from .platform_services import PlatformServices

            service = PlatformServices(self.workspace, self.db_path, self.emit)
            result = service.invoke_mcp(
                server_id,
                str(arguments.get("method", "tools/list")),
                dict(arguments.get("params") or {}),
            )
        elif tool == "run_command":
            command = str(arguments["command"])
            cwd = self.safe_path(str(arguments.get("cwd", ".")), permission)
            timeout = min(max(int(arguments.get("timeout", 120)), 5), 300)
            process = subprocess.Popen(
                ["bash", "-lc", command],
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
            operation_id = self.register_operation(
                run_id,
                "command_process",
                lambda: self.terminate_process(process),
            )
            output: list[str] = []
            started = time.monotonic()
            timed_out = False
            try:
                while process.poll() is None:
                    self.wait_if_paused(run_id, cancel)
                    if cancel.is_set():
                        raise InterruptedError("Agent run cancelled")
                    if time.monotonic() - started > timeout:
                        timed_out = True
                        self.terminate_process(process)
                        break
                    if process.stdout:
                        readable, _, _ = select.select([process.stdout], [], [], 0.2)
                        if readable:
                            line = process.stdout.readline()
                            if line:
                                output.append(line)
                                self.emit("agent.tool_output", {"run_id": run_id, "tool": tool, "line": line.rstrip()})
                remainder = process.stdout.read() if process.stdout else ""
                if remainder:
                    output.append(remainder)
            except Exception:
                self.terminate_process(process)
                raise
            finally:
                self.unregister_operation(run_id, operation_id)
                if process.stdout:
                    process.stdout.close()
            result = {
                "command": command,
                "cwd": self.display_path(cwd),
                "exit_code": 124 if timed_out else process.returncode,
                "timed_out": timed_out,
                "output": "".join(output)[-40_000:],
            }
            result["diagnostics"] = self.parse_command_diagnostics(result["output"])
        else:
            raise ValueError(f"Unsupported tool: {tool}")
        self.emit("agent.tool_completed", {
            "run_id": run_id, "tool": tool, "result": result, "access_scope": permission,
        })
        return result

    @staticmethod
    def parse_command_diagnostics(output: str) -> list[dict[str, Any]]:
        pattern = re.compile(
            r"(?P<path>[A-Za-z0-9_./-]+\.[A-Za-z0-9]+)"
            r"(?::(?P<line>\d+))?(?::(?P<column>\d+))?"
            r"(?::|\s+-\s+)\s*(?P<message>[^\n]+)"
        )
        return [
            {
                "path": match.group("path"),
                "line": int(match.group("line") or 1),
                "column": int(match.group("column") or 1),
                "message": match.group("message").strip(),
            }
            for match in pattern.finditer(output)
        ][:200]

    @staticmethod
    def compact_observation(
        tool: str, arguments: dict[str, Any], result: Optional[dict[str, Any]] = None, error: str = ""
    ) -> dict[str, Any]:
        compact_arguments = {
            key: (f"<{len(str(value))} characters>" if key == "content" else value)
            for key, value in arguments.items()
        }
        observation: dict[str, Any] = {"tool": tool, "arguments": compact_arguments}
        if error:
            observation["error"] = error[:4000]
        elif result is not None:
            compact_result = dict(result)
            if "content" in compact_result:
                content = str(compact_result["content"])
                compact_result["content"] = content[:12_000]
                compact_result["truncated"] = len(content) > 12_000
            if "output" in compact_result:
                compact_result["output"] = str(compact_result["output"])[-12_000:]
            if "text" in compact_result:
                text = str(compact_result["text"])
                compact_result["text"] = text[:12_000]
                compact_result["truncated"] = len(text) > 12_000
            observation["result"] = compact_result
        return observation

    @staticmethod
    def tool_activity_data(arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            key: (
                f"<{len(str(value))} characters>"
                if key in {"content", "old_text", "new_text"}
                else value
            )
            for key, value in arguments.items()
        }

    def inferred_validation_commands(self) -> list[str]:
        return [command for command, _cwd in self.inferred_validations()]

    def inferred_validations(
        self, changed_paths: Optional[list[str]] = None
    ) -> list[tuple[str, str]]:
        changed_paths = changed_paths or []
        project_root = self.workspace
        for changed in changed_paths:
            candidate = self.safe_path(changed)
            current = candidate.parent if candidate.suffix else candidate
            while current != self.workspace and self.workspace in current.parents:
                if (current / "package.json").exists() or (current / "pyproject.toml").exists():
                    project_root = current
                    break
                current = current.parent
            if project_root != self.workspace:
                break
        commands: list[str] = []
        package_json = project_root / "package.json"
        if package_json.exists():
            try:
                package = json.loads(package_json.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                validation = (
                    "import json; json.load(open('package.json')); "
                    "print('package.json valid')"
                )
                return [("python3 -c " + shlex.quote(validation), self.display_path(project_root))]
            dependencies = {
                **package.get("dependencies", {}),
                **package.get("devDependencies", {}),
            }
            if dependencies and not (project_root / "node_modules").exists():
                commands.append("npm install")
            scripts = package.get("scripts", {})
            for script in ("build", "test", "lint"):
                value = str(scripts.get(script, ""))
                if value and "no test specified" not in value:
                    commands.append(f"npm run {script}")
                    break
        if not commands:
            javascript = [
                str(path.relative_to(project_root))
                for path in project_root.rglob("*.js")
                if not any(part in {"node_modules", "dist"} for part in path.parts)
            ][:30]
            commands.extend(f"node --check {shlex.quote(path)}" for path in javascript)
        if not commands and any(project_root.rglob("*.py")):
            commands.append("python3 -m compileall -q .")
        if not commands and (project_root / "index.html").exists():
            commands.append(
                "python3 -c "
                + shlex.quote(
                    "from pathlib import Path; p=Path('index.html'); "
                    "assert p.stat().st_size > 100; print('index.html valid')"
                )
            )
        return [(command, self.display_path(project_root)) for command in commands]

    def run_validation(
        self,
        run_id: int,
        requested_command: str,
        permission: str,
        cancel: threading.Event,
        changed_paths: Optional[list[str]] = None,
        successful_commands: Optional[set[str]] = None,
    ) -> tuple[bool, list[dict[str, Any]]]:
        requested = requested_command.strip()
        if requested:
            try:
                requested = self.validate_tool_arguments(
                    "run_command", {"command": requested}
                )["command"]
            except ToolInputError:
                requested = ""
        valid_requested = bool(
            requested
            and not re.search(r"\b(dev|serve|start|preview)\b", requested)
            and not re.search(r"\bcd\s+/", requested)
            and re.search(
                r"\b(build|test|lint|check|compile|pytest|unittest|tsc|vitest|jest)\b",
                requested,
            )
        )
        inferred = self.inferred_validations(changed_paths)
        default_cwd = inferred[0][1] if inferred else "."
        completed = successful_commands or set()
        commands: list[tuple[str, str]] = (
            [(requested, default_cwd)]
            if valid_requested and requested not in completed
            else []
        )
        commands.extend(
            item
            for item in inferred
            if item not in commands and item[0] not in completed
        )
        if not commands:
            return False, [{"error": "No applicable validation command was found"}]
        results: list[dict[str, Any]] = []
        for command, cwd in commands[:3]:
            arguments = {"command": command, "cwd": cwd, "timeout": 180}
            if self.requires_approval(permission, "run_command", arguments):
                approval = self.create_approval(run_id, "run_command", arguments)
                if not self.wait_for_approval(run_id, approval["id"], cancel):
                    return False, [{"command": command, "error": "Validation rejected"}]
            step = self.add_step(run_id, "validation", "running", command)
            result = self.execute_tool(run_id, "run_command", arguments, cancel, permission)
            self.finish_step(step, "completed" if result["exit_code"] == 0 else "failed", result)
            results.append(result)
            if result["exit_code"] != 0:
                return False, results
        return True, results

    def execute_builtin_notepad(
        self, run_id: int, content: str, cancel: threading.Event
    ) -> bool:
        if not re.search(r"\bnotepad\b|\bnotes?\s+app\b", content, re.I):
            return False
        target = Path(".")
        generated_names = ("index.html", "styles.css", "app.js", "package.json")
        if any((self.workspace / name).exists() for name in generated_names):
            target = Path("notepad-app")
            suffix = 2
            while (self.workspace / target).exists():
                target = Path(f"notepad-app-{suffix}")
                suffix += 1
        files = {
            target / "index.html": """<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Kodex Notepad</title>
  <link rel="stylesheet" href="styles.css">
</head>
<body>
  <main class="app">
    <header>
      <div><span>Local notes</span><h1>Kodex Notepad</h1></div>
      <button id="new-note" type="button">New note</button>
    </header>
    <section class="workspace">
      <aside><h2>Notes</h2><div id="notes"></div></aside>
      <article>
        <input id="title" aria-label="Note title" placeholder="Untitled note">
        <textarea id="content" aria-label="Note content" placeholder="Start writing…"></textarea>
        <footer><span id="status">Saved locally</span><button id="delete-note" type="button">Delete note</button></footer>
      </article>
    </section>
  </main>
  <script src="app.js"></script>
</body>
</html>
""",
            target / "styles.css": """:root { color-scheme: dark; font-family: Avenir Next, sans-serif; background: #0e1012; color: #f3f3ef; }
* { box-sizing: border-box; }
body { min-height: 100vh; margin: 0; display: grid; place-items: center; background: radial-gradient(circle at 20% 0, #26342f, transparent 35%), #0e1012; }
button, input, textarea { font: inherit; color: inherit; }
button { border: 1px solid #34393b; border-radius: 10px; background: #23272a; padding: 9px 13px; cursor: pointer; }
.app { width: min(1000px, calc(100vw - 32px)); height: min(720px, calc(100vh - 32px)); overflow: hidden; border: 1px solid #303438; border-radius: 22px; background: #15181aee; box-shadow: 0 28px 90px #0009; }
header { height: 96px; display: flex; align-items: center; justify-content: space-between; padding: 0 28px; border-bottom: 1px solid #2a2e31; }
header span { color: #8fc9aa; font-size: 12px; text-transform: uppercase; letter-spacing: .12em; }
h1 { margin: 5px 0 0; font-size: 27px; }
.workspace { height: calc(100% - 96px); display: grid; grid-template-columns: 260px 1fr; }
aside { padding: 20px 14px; overflow: auto; border-right: 1px solid #2a2e31; }
aside h2 { margin: 0 8px 14px; color: #92999b; font-size: 11px; text-transform: uppercase; }
.note { width: 100%; display: grid; gap: 3px; margin-bottom: 7px; padding: 11px; text-align: left; background: transparent; border-color: transparent; }
.note.active { background: #29322e; border-color: #43534b; }
.note small { color: #7f8789; }
article { min-width: 0; display: grid; grid-template-rows: 64px 1fr 54px; padding: 12px 24px 0; }
#title, #content { width: 100%; border: 0; outline: 0; background: transparent; }
#title { padding: 0 8px; font-size: 23px; font-weight: 650; }
#content { resize: none; padding: 16px 8px; border-top: 1px solid #282c2f; color: #ced2d1; line-height: 1.7; }
article footer { display: flex; align-items: center; justify-content: space-between; border-top: 1px solid #282c2f; color: #788082; font-size: 11px; }
#delete-note { color: #e0a09a; }
@media (max-width: 700px) { .workspace { grid-template-columns: 1fr; } aside { display: none; } }
""",
            target / "app.js": """const storageKey = 'kodex-notepad-notes';
const notesList = document.querySelector('#notes');
const titleInput = document.querySelector('#title');
const contentInput = document.querySelector('#content');
const status = document.querySelector('#status');
let notes = JSON.parse(localStorage.getItem(storageKey) || '[]');
let activeId = notes[0]?.id || null;

function save() {
  localStorage.setItem(storageKey, JSON.stringify(notes));
  status.textContent = 'Saved locally';
}
function createNote() {
  const note = { id: crypto.randomUUID(), title: 'Untitled note', content: '', updated: Date.now() };
  notes.unshift(note);
  activeId = note.id;
  save();
  render();
  titleInput.focus();
}
function renderList() {
  notesList.replaceChildren(...notes.map((note) => {
    const button = document.createElement('button');
    button.className = `note ${note.id === activeId ? 'active' : ''}`;
    const strong = document.createElement('strong');
    strong.textContent = note.title || 'Untitled note';
    const small = document.createElement('small');
    small.textContent = new Date(note.updated).toLocaleString();
    button.append(strong, small);
    button.onclick = () => { activeId = note.id; render(); };
    return button;
  }));
}
function render() {
  if (!notes.length) {
    createNote();
    return;
  }
  const active = notes.find((note) => note.id === activeId) || notes[0];
  activeId = active.id;
  titleInput.value = active.title;
  contentInput.value = active.content;
  renderList();
}
function updateActive() {
  const note = notes.find((item) => item.id === activeId);
  if (!note) return;
  note.title = titleInput.value;
  note.content = contentInput.value;
  note.updated = Date.now();
  status.textContent = 'Saving…';
  save();
  renderList();
}
titleInput.addEventListener('input', updateActive);
contentInput.addEventListener('input', updateActive);
document.querySelector('#new-note').onclick = createNote;
document.querySelector('#delete-note').onclick = () => {
  notes = notes.filter((note) => note.id !== activeId);
  activeId = notes[0]?.id || null;
  save();
  render();
};
render();
""",
        }
        self.update_run(run_id, "working", "Creating a complete local notepad app")
        self.create_snapshot(f"Agent run #{run_id} safety snapshot")
        for path, file_content in files.items():
            step = self.add_step(
                run_id,
                "tool_started",
                "running",
                "write_file",
                {"path": str(path)},
            )
            result = self.execute_tool(
                run_id,
                "write_file",
                {"path": str(path), "content": file_content},
                cancel,
            )
            self.finish_step(step, "completed", result)
        validation_step = self.add_step(
            run_id, "validation", "running", "node --check app.js"
        )
        result = self.execute_tool(
            run_id,
            "run_command",
            {
                "command": "node --check app.js",
                "cwd": str(target),
                "timeout": 30,
            },
            cancel,
        )
        self.finish_step(
            validation_step,
            "completed" if result["exit_code"] == 0 else "failed",
            result,
        )
        if result["exit_code"] != 0:
            raise RuntimeError(result["output"] or "Notepad validation failed")
        location = str(target) if str(target) != "." else "the workspace root"
        self.complete_run(
            run_id,
            f"Created a runnable local notepad app in {location} with create, save, select, and delete functionality.\n\nValidation passed. Run the detected static app profile to open it.",
        )
        return True

    def execute_run(self, run_id: int, content: str, cancel: threading.Event) -> None:
        try:
            run = self.get_run(run_id)
            profile = self.active_profile_for_run(run)
            allowed_tools = self.allowed_tools_for_profile(profile)
            self.set_phase(run_id, "understand", "understanding")
            self.update_task(run_id, 1, "in_progress")
            self.emit("agent.started", {"run_id": run_id, "contract": run["contract"]})
            choice = self.select_provider(run["model"])
            overview = self.workspace_overview(
                run["context"], content, run["conversation_id"], profile
            )
            overview = self.compress_context(run_id, choice, overview, cancel)
            checkpoint = run.get("checkpoint") or {}
            observations: list[dict[str, Any]] = list(
                checkpoint.get("observations", [])
            )
            snapshot_created = False
            mutated = bool(checkpoint.get("mutated", False))
            validation_passed = bool(checkpoint.get("validation_passed", False))
            max_iterations = 20 if run["pursue_goal"] else 8
            mode = run["mode"]
            previous_fingerprint = str(checkpoint.get("previous_fingerprint", ""))
            no_progress_cycles = int(checkpoint.get("no_progress_cycles", 0))
            automatic_recoveries = int(checkpoint.get("automatic_recoveries", 0))
            progress_revision = int(checkpoint.get("progress_revision", 0))
            changed_files: list[str] = list(checkpoint.get("changed_files", []))
            successful_commands: set[str] = set(
                checkpoint.get("successful_commands", [])
            )
            successful_reads: set[tuple[str, int, int]] = set()
            successful_read_requests: dict[
                tuple[str, int, int], tuple[int, int]
            ] = {}
            last_error_code = str(checkpoint.get("last_error_code", ""))
            excluded_providers: set[str] = set(
                checkpoint.get("excluded_providers", [])
            )
            generated_plan: dict[str, Any] = dict(
                checkpoint.get("generated_plan") or run.get("plan") or {}
            )
            system = f"""You are Kodex, a local software engineering agent. Return only JSON.
Schema version 3: {{"summary":"short user-facing progress","phase":"investigate|plan|execute|validate|review|repair|complete","task_updates":[{{"position":1,"status":"pending|in_progress|completed|blocked"}}],"actions":[{{"tool":"tool id","arguments":{{}}}}],"evidence":{{"files":[],"checks":[]}},"done":false,"validation":"optional finite command"}}.
Active profile: {profile.get("name")} ({profile.get("role")}).
Profile instructions: {profile.get("prompt")}
Constitution: {profile.get("constitution")}
Available typed tools: {json.dumps(self.tool_registry(), default=str)[:24000]}
Build complete runnable projects, not snippets. Inspect before editing.
Use create_file only for new files. Use edit_file for existing files with arguments {{"path":"file","edits":[{{"old_text":"exact text","new_text":"replacement"}}]}} or 1-based line ranges.
read_file accepts one concrete path. read_files accepts {{"paths":["file-a","file-b"]}}; Kodex expands it into ordered reads. Never use placeholders. Return only executable shell syntax in run_command.command, never prose such as "Run 'npm test'".
Preserve unrelated code, configuration, formatting, and user work. Re-read a file after any stale-version or anchor error.
Batch independent reads and create or update up to 12 related files in one response. Include the finite validation command in the same response as the final edits.
Do not re-read a file that was just created or successfully edited unless a tool reported a stale version, anchor error, or validation failure.
For web apps ensure package.json scripts and all entry files exist. Prefer a dependency-free HTML/CSS/JS app when a framework was not requested.
Use npm install when package dependencies are required. Never use a dev server as validation; use build, test, lint, or syntax checks.
All file paths and command working directories are relative to the workspace root. Never begin a path with / and never cd to an absolute path.
Never claim success until validation has passed. If a tool or validation fails, diagnose it from observations and repair it.
In plan mode, return a useful structured plan in summary, no actions, and done=true.
In chat mode, answer in summary, no mutating actions, and done=true."""
            system += (
                "\nSelected provider capabilities: "
                + ", ".join(getattr(choice, "capabilities", ()) or ("chat",))
                + ". Keep decisions compact for local models and use only the declared JSON schema."
            )
            if run["pursue_goal"]:
                system += """
Pursue-goal mode is enabled. Continue through investigation, implementation, validation, diagnosis, repair, and independent review until every measurable acceptance criterion passes. Do not stop after a partial edit or the first failed check."""
            else:
                system += """
Pursue-goal mode is disabled. Make one focused implementation and validation pass, then report remaining work precisely instead of entering extended repair cycles."""
            resolved_intent = str(
                run.get("resolved_intent")
                or run["contract"].get("resolved_intent")
                or ("plan_only" if mode == "plan" else "execute")
            )
            if (
                mode == "plan"
                or (
                    resolved_intent == "plan_then_execute"
                    and not generated_plan
                )
            ):
                self.set_phase(run_id, "plan", "planning")
                plan_instruction = (
                    "Return JSON containing objective, tasks, acceptance_criteria, affected_areas, "
                    "validation_strategy, risks, summary, actions=[], and done=true. Do not call tools."
                )
                plan_ready = False
                for attempt in range(2):
                    step_id = self.add_step(run_id, "thinking", "running", "Creating an implementation plan")
                    response_text, choice = self.call_with_fallback(
                        run_id,
                        run["model"],
                        [
                            {"role": "system", "content": system},
                            {"role": "user", "content": json.dumps({
                                "request": content,
                                "workspace": overview,
                                "instruction": plan_instruction,
                            })},
                        ],
                        cancel,
                        excluded_providers,
                    )
                    try:
                        decision = self.parse_model_json(response_text)
                        summary = str(decision.get("summary", "")).strip()
                        if not summary or len(summary.splitlines()) < 2:
                            decision = self.repair_plan(response_text, content)
                            summary = decision["summary"]
                        tasks = [
                            str(item).strip()
                            for item in decision.get("tasks", [])
                            if str(item).strip()
                        ] or [
                            line.split(". ", 1)[-1]
                            for line in summary.splitlines()
                            if line.strip()
                        ]
                        with self.connect() as connection:
                            connection.execute(
                                "delete from agent_run_tasks where run_id = ?", (run_id,)
                            )
                            for position, task in enumerate(tasks, 1):
                                connection.execute(
                                    """
                                    insert into agent_run_tasks(
                                        run_id, position, title, status, details_json,
                                        created_at, updated_at
                                    ) values (?, ?, ?, 'pending', '{}', ?, ?)
                                    """,
                                    (run_id, position, task, utc_now(), utc_now()),
                                )
                            connection.execute(
                                """
                                update agent_runs
                                set structured_plan_json = ?, updated_at = ?
                                where id = ?
                                """,
                                (json.dumps(decision), utc_now(), run_id),
                            )
                            connection.commit()
                        self.finish_step(step_id, "completed", {"summary": summary})
                        if resolved_intent == "plan_only":
                            self.complete_run(
                                run_id,
                                summary,
                                completion_evidence={"plan": decision, "validated": True},
                            )
                            return
                        plan_ready = True
                        generated_plan = decision
                        run["plan"] = decision
                        run["context"] = {
                            **run["context"],
                            "approved_plan": decision,
                            "plan_generated_in_run": True,
                        }
                        overview += "\n\nGenerated execution plan:\n" + summary
                        observations.append({"generated_plan": decision})
                        self.save_observation(run_id, "plan", decision)
                        self.save_checkpoint(
                            run_id,
                            "execute",
                            {
                                "observations": observations[-24:],
                                "mutated": False,
                                "validation_passed": False,
                                "changed_files": [],
                                "successful_commands": [],
                                "generated_plan": decision,
                            },
                        )
                        self.emit(
                            "agent.plan_ready",
                            {"run_id": run_id, "automatic_execution": True},
                        )
                        break
                    except (ValueError, json.JSONDecodeError) as error:
                        provider_id = str(getattr(choice, "provider", ""))
                        if provider_id:
                            excluded_providers.add(provider_id)
                        self.finish_step(step_id, "failed", {
                            "code": "invalid_plan",
                            "message": str(error),
                            "suggested_recovery": "Return a structured plan with ordered steps.",
                        })
                        plan_instruction += f" Previous response failed because: {error}."
                if not plan_ready:
                    repaired = self.repair_plan("", content)
                    with self.connect() as connection:
                        connection.execute(
                            "update agent_runs set structured_plan_json = ?, updated_at = ? where id = ?",
                            (json.dumps(repaired), utc_now(), run_id),
                        )
                        connection.commit()
                    if resolved_intent == "plan_only":
                        self.complete_run(
                            run_id,
                            repaired["summary"],
                            completion_evidence={
                                "plan": repaired,
                                "validated": True,
                                "repaired": True,
                            },
                        )
                        return
                    run["plan"] = repaired
                    generated_plan = repaired
                    run["context"] = {
                        **run["context"],
                        "approved_plan": repaired,
                        "plan_generated_in_run": True,
                    }
                    overview += "\n\nDeterministically repaired execution plan:\n" + repaired["summary"]
                    observations.append({"generated_plan": repaired, "repaired": True})
            for iteration in range(max_iterations):
                self.wait_if_paused(run_id, cancel)
                if cancel.is_set():
                    raise InterruptedError("Agent run cancelled")
                steering = self.consume_steering(run_id)
                if steering:
                    observations.append({"user_steering": steering})
                    self.save_observation(run_id, "steering", {"messages": steering})
                    content += "\n\nAdditional user direction:\n" + "\n".join(steering)
                phase = (
                    "investigate"
                    if not mutated
                    else "repair"
                    if last_error_code
                    else "execute"
                )
                self.set_phase(
                    run_id,
                    phase,
                    "planning" if iteration == 0 else "working",
                )
                self.update_task(run_id, 1, "completed")
                if len(run.get("tasks", [])) >= 2:
                    self.update_task(run_id, 2, "in_progress")
                prompt = {
                    "request": content,
                    "contract": run["contract"],
                    "mode": mode,
                    "permission": run["permission"],
                    "workspace": overview,
                    "observations": observations[-16:],
                    "progress": {
                        "revision": progress_revision,
                        "changed_files": changed_files,
                        "successful_commands": sorted(successful_commands),
                        "last_error_code": last_error_code,
                    },
                    "instruction": (
                        "Choose the next smallest set of actions. Set done=true only when all files exist "
                        "and validation has passed. If implementation is ready, provide a finite validation command. "
                        "When context includes approved_plan, execute that approved plan while adapting only where "
                        "workspace evidence or validation requires it."
                    ),
                }
                step_id = self.add_step(run_id, "thinking", "running", "Understanding and planning")
                response_text, choice = self.call_with_fallback(
                    run_id,
                    run["model"],
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": json.dumps(prompt)},
                    ],
                    cancel,
                    excluded_providers,
                )
                try:
                    decision = self.parse_model_json(response_text)
                except (ValueError, json.JSONDecodeError) as error:
                    try:
                        decision = self.repair_decision_response(
                            run_id, choice, response_text, cancel
                        )
                        observations.append(
                            {"model_repair": "Recovered malformed structured output."}
                        )
                    except (ValueError, json.JSONDecodeError, RuntimeError) as repair_error:
                        provider_id = str(getattr(choice, "provider", ""))
                        if provider_id:
                            excluded_providers.add(provider_id)
                        self.finish_step(step_id, "failed", {"error": str(repair_error), "response": response_text[-4000:]})
                        observations.append({"model_error": str(error), "repair_error": str(repair_error), "response": response_text[-2000:]})
                        self.save_observation(
                            run_id,
                            "model_error",
                            {"error": str(error), "repair_error": str(repair_error), "response": response_text[-2000:]},
                        )
                        continue
                decision["actions"] = self.prepare_actions(decision.get("actions") or [])
                decision["actions"] = [
                    action
                    for action in decision["actions"]
                    if action.get("tool") in allowed_tools
                ]
                for task_update in decision.get("task_updates", []):
                    if not isinstance(task_update, dict):
                        continue
                    try:
                        self.update_task(
                            run_id,
                            int(task_update["position"]),
                            str(task_update["status"]),
                        )
                    except (KeyError, TypeError, ValueError):
                        continue
                fingerprint = json.dumps(
                    {
                        "actions": decision.get("actions", []),
                        "done": decision.get("done", False),
                        "validation": decision.get("validation", ""),
                        "progress_revision": progress_revision,
                        "last_error_code": last_error_code,
                    },
                    sort_keys=True,
                    default=str,
                )
                if fingerprint == previous_fingerprint:
                    no_progress_cycles += 1
                else:
                    no_progress_cycles = 0
                    previous_fingerprint = fingerprint
                if no_progress_cycles >= 2:
                    self.emit(
                        "agent.stuck_detected",
                        {"run_id": run_id, "cycles": no_progress_cycles},
                    )
                    observations.append(
                        {
                            "stuck_detection": (
                                "No new evidence or workspace progress was made. Inspect the failed tool schema, "
                                "target file, nearest project manifest, and latest error before choosing one repaired action."
                            )
                        }
                    )
                    self.finish_step(
                        step_id,
                        "failed",
                        {
                            "code": "no_progress",
                            "message": "Kodex is repairing a repeated action.",
                            "cycles": no_progress_cycles,
                            "suggested_recovery": "Inspect the latest error and choose a different evidence-gathering action.",
                        },
                    )
                    if no_progress_cycles >= 3 and automatic_recoveries < 2:
                        automatic_recoveries += 1
                        no_progress_cycles = 0
                        previous_fingerprint = ""
                        observations.append(
                            {
                                "automatic_recovery": (
                                    "Start a fresh decision from current workspace evidence. "
                                    "Do not repeat the previous action. If files were changed, "
                                    "validate them now; otherwise inspect a different concrete file."
                                ),
                                "attempt": automatic_recoveries,
                            }
                        )
                        self.emit(
                            "agent.automatic_recovery",
                            {
                                "run_id": run_id,
                                "attempt": automatic_recoveries,
                            },
                        )
                    elif no_progress_cycles >= 3:
                        self.complete_run(
                            run_id,
                            "Kodex exhausted its automatic stall recovery. Review the latest structured blocker.",
                            status="needs_review",
                            failure={
                                "code": last_error_code or "no_progress",
                                "message": "The run could not produce new evidence or a successful workspace change.",
                                "actions": ["retry_with_repair", "open_affected_file", "change_model"],
                            },
                        )
                        return
                    self.save_checkpoint(
                        run_id,
                        "repair",
                        {
                            "observations": observations[-24:],
                            "mutated": mutated,
                            "validation_passed": validation_passed,
                            "progress_revision": progress_revision,
                            "changed_files": changed_files,
                            "successful_commands": sorted(successful_commands),
                            "last_error_code": last_error_code or "no_progress",
                            "previous_fingerprint": previous_fingerprint,
                            "no_progress_cycles": no_progress_cycles,
                            "automatic_recoveries": automatic_recoveries,
                            "excluded_providers": sorted(excluded_providers),
                            "generated_plan": generated_plan,
                        },
                    )
                    continue
                self.finish_step(step_id, "completed", {"summary": decision.get("summary", "")})
                self.emit("agent.token", {"run_id": run_id, "content": decision.get("summary", "")})
                actions = decision.get("actions") or []
                if mode in {"plan", "chat"}:
                    actions = [
                        action
                        for action in actions
                        if action.get("tool") in {
                            "list_files", "read_file", "search", "fetch_url",
                        }
                    ]
                action_failures = 0
                ordered_actions = sorted(
                    actions,
                    key=lambda action: {
                        "list_files": 0, "search": 0, "fetch_url": 0, "read_file": 1,
                        "create_file": 2, "edit_file": 2, "delete_file": 2, "run_command": 3,
                    }.get(action.get("tool", ""), 4),
                )
                for action in ordered_actions:
                    self.wait_if_paused(run_id, cancel)
                    tool = action.get("tool", "")
                    try:
                        arguments = self.validate_tool_arguments(tool, action.get("arguments") or {})
                    except ToolInputError as error:
                        action_failures += 1
                        last_error_code = error.details["code"]
                        observations.append({"tool_error": error.details})
                        failed_step = self.add_step(run_id, "tool_started", "failed", tool, error.details)
                        self.finish_step(failed_step, "failed", error.details)
                        continue
                    if tool == "run_command" and arguments["command"] in successful_commands:
                        observations.append({
                            "tool": tool,
                            "skipped": True,
                            "reason": "This unchanged command already succeeded.",
                        })
                        continue
                    if tool == "read_file":
                        read_path = self.safe_path(
                            str(arguments["path"]),
                            run["permission"],
                        )
                        request_key = (
                            str(arguments["path"]),
                            int(arguments.get("start_line", 1)),
                            int(arguments.get("end_line", 0)),
                        )
                        try:
                            stat = read_path.stat()
                            current_version = (stat.st_mtime_ns, stat.st_size)
                        except OSError:
                            current_version = (-1, -1)
                        if successful_read_requests.get(request_key) == current_version:
                            observations.append({
                                "tool": tool,
                                "skipped": True,
                                "path": arguments["path"],
                                "reason": "This exact unchanged file range was already read.",
                            })
                            continue
                    if self.requires_approval(run["permission"], tool, arguments):
                        approval = self.create_approval(run_id, tool, arguments)
                        if not self.wait_for_approval(run_id, approval["id"], cancel):
                            raise PermissionError(f"Action rejected: {tool}")
                    if tool in {
                        "create_file", "edit_file", "move_file", "delete_file",
                    } and not snapshot_created:
                        snapshot = self.create_snapshot(f"Agent run #{run_id} safety snapshot")
                        snapshot_created = True
                        self.emit("agent.snapshot_created", {"run_id": run_id, "snapshot": snapshot})
                    tool_step = self.add_step(
                        run_id,
                        "tool_started",
                        "running",
                        tool,
                        self.tool_activity_data(arguments),
                    )
                    try:
                        result = self.execute_tool(run_id, tool, arguments, cancel, run["permission"])
                        self.finish_step(tool_step, "completed", result)
                        observations.append(self.compact_observation(tool, arguments, result))
                        self.save_observation(
                            run_id,
                            "tool_result",
                            self.compact_observation(tool, arguments, result),
                        )
                        made_progress = True
                        last_error_code = ""
                        if tool == "read_file":
                            version = result.get("version") or {}
                            read_key = (
                                str(result.get("path", "")),
                                int(version.get("mtime_ns", 0)),
                                int(version.get("size", 0)),
                            )
                            made_progress = read_key not in successful_reads
                            successful_reads.add(read_key)
                            successful_read_requests[request_key] = (
                                int(version.get("mtime_ns", 0)),
                                int(version.get("size", 0)),
                            )
                        if tool == "run_command" and result.get("exit_code") == 0:
                            made_progress = arguments["command"] not in successful_commands
                            successful_commands.add(arguments["command"])
                        if tool in {"create_file", "edit_file"}:
                            made_progress = bool(result.get("created") or result.get("changed", True))
                        if made_progress:
                            progress_revision += 1
                        mutated = mutated or tool in {
                            "create_file", "edit_file", "move_file", "delete_file",
                        }
                        if tool in {"create_file", "edit_file", "move_file", "delete_file"}:
                            path = str(result.get("path", arguments.get("path", "")))
                            successful_read_requests = {
                                key: value
                                for key, value in successful_read_requests.items()
                                if key[0] != path
                            }
                            if path and path not in changed_files:
                                changed_files.append(path)
                            overview = (
                                overview[:CODEX_CONTEXT_TARGET]
                                + "\n\nWORKSPACE UPDATE:\n"
                                + json.dumps(
                                    self.compact_observation(tool, arguments, result),
                                    default=str,
                                )[:3000]
                            )
                    except Exception as error:
                        action_failures += 1
                        details = error.details if isinstance(error, ToolInputError) else {
                            "code": "tool_failed",
                            "tool": tool,
                            "message": str(error),
                            "suggested_recovery": "Inspect the latest workspace state and repair this action.",
                        }
                        last_error_code = str(details["code"])
                        self.finish_step(tool_step, "failed", details)
                        observations.append({"tool_error": details, "arguments": self.tool_activity_data(arguments)})
                        self.save_observation(
                            run_id,
                            "tool_error",
                            {
                                "details": details,
                                "arguments": self.tool_activity_data(arguments),
                            },
                        )
                        self.emit("agent.tool_failed", {"run_id": run_id, "tool": tool, **details})
                if mode in {"plan", "chat"} and decision.get("done"):
                    summary = str(decision.get("summary") or "Task completed.")
                    self.complete_run(run_id, summary)
                    return
                if (
                    decision.get("done")
                    and not mutated
                    and "explain" in run["contract"].get("intent_types", [])
                ):
                    self.complete_run(
                        run_id,
                        str(decision.get("summary") or "Explanation completed."),
                        completion_evidence={
                            "validated": True,
                            "reason": "read-only explanation",
                        },
                    )
                    return
                quiet_after_edits = mutated and not actions
                should_validate = action_failures == 0 and (
                    bool(decision.get("validation"))
                    or (decision.get("done") and mutated)
                    or quiet_after_edits
                )
                if should_validate:
                    self.set_phase(run_id, "validate", "testing")
                    validation_passed, validation_results = self.run_validation(
                        run_id,
                        str(decision.get("validation") or ""),
                        run["permission"],
                        cancel,
                        changed_files,
                        successful_commands,
                    )
                    observations.extend(
                        self.compact_observation(
                            "run_command",
                            {"command": str(result.get("command", "validation"))},
                            result if "error" not in result else None,
                            str(result.get("error", "")),
                        )
                        for result in validation_results
                    )
                    if validation_passed and (
                        decision.get("done") or quiet_after_edits
                    ):
                        self.set_phase(run_id, "review", "testing")
                        review_results = (
                            self.parallel_review(
                                run_id, choice, content, observations, cancel
                            )
                            if run["pursue_goal"]
                            else {
                                "validation": {
                                    "passed": True,
                                    "approved": True,
                                    "summary": "Focused run completed with passing validation.",
                                    "actions": [],
                                }
                            }
                        )
                        blockers = [
                            action
                            for result in review_results.values()
                            for action in result.get("actions", [])
                        ]
                        if blockers:
                            last_error_code = "review_blocker"
                            validation_passed = False
                            observations.append(
                                {"review_blockers": blockers, "review": review_results}
                            )
                            self.save_checkpoint(
                                run_id,
                                "repair",
                                {
                                    "observations": observations[-24:],
                                    "mutated": mutated,
                                    "validation_passed": False,
                                    "progress_revision": progress_revision,
                                    "changed_files": changed_files,
                                    "successful_commands": sorted(successful_commands),
                                    "last_error_code": last_error_code,
                                    "previous_fingerprint": previous_fingerprint,
                                    "no_progress_cycles": 0,
                                    "automatic_recoveries": automatic_recoveries,
                                    "excluded_providers": sorted(excluded_providers),
                                    "generated_plan": generated_plan,
                                },
                            )
                            continue
                        summary = str(decision.get("summary") or "Task completed.")
                        summary += (
                            "\n\nValidation passed. Independent reviewer and tester agents completed: "
                            + "; ".join(
                                f"{role}: {result['summary']}"
                                for role, result in review_results.items()
                            )
                        )
                        reflection = {
                            "outcome": "completed",
                            "summary": summary,
                            "validation_passed": True,
                            "changed_files": changed_files,
                            "unresolved_issues": [],
                        }
                        specialist_activity = [
                            {
                                "role": role,
                                "approved": result.get("approved", True),
                                "summary": result.get("summary", ""),
                            }
                            for role, result in review_results.items()
                        ]
                        with self.connect() as connection:
                            connection.execute(
                                """
                                update agent_runs
                                set reflection_json = ?, specialist_activity_json = ?,
                                    updated_at = ?
                                where id = ?
                                """,
                                (
                                    json.dumps(reflection),
                                    json.dumps(specialist_activity),
                                    utc_now(),
                                    run_id,
                                ),
                            )
                            connection.commit()
                        self.complete_run(
                            run_id,
                            summary,
                            changed_files=changed_files,
                            validation={"passed": True, "results": validation_results},
                            completion_evidence={
                                "acceptance_criteria": run["contract"].get(
                                    "acceptance_criteria", []
                                ),
                                "changed_files": changed_files,
                                "validation": validation_results,
                                "review": review_results,
                                "provider": {
                                    "id": getattr(choice, "provider", "scripted"),
                                    "model": getattr(choice, "model", "default"),
                                },
                            },
                        )
                        return
                self.save_checkpoint(
                    run_id,
                    "repair" if last_error_code else "execute",
                    {
                        "observations": observations[-24:],
                        "mutated": mutated,
                        "validation_passed": validation_passed,
                        "progress_revision": progress_revision,
                        "changed_files": changed_files,
                        "successful_commands": sorted(successful_commands),
                        "last_error_code": last_error_code,
                        "previous_fingerprint": previous_fingerprint,
                        "no_progress_cycles": no_progress_cycles,
                        "automatic_recoveries": automatic_recoveries,
                        "excluded_providers": sorted(excluded_providers),
                        "generated_plan": generated_plan,
                    },
                )
            summary = "Stopped after the maximum safe iteration count. Review the trace and continue if needed."
            self.complete_run(
                run_id,
                summary,
                status="needs_review",
                failure={
                    "code": last_error_code or "iteration_limit",
                    "message": summary,
                    "actions": ["retry_with_repair", "review_diagnostics", "change_model"],
                },
                changed_files=changed_files,
            )
        except InterruptedError:
            self.complete_run(run_id, "Agent run cancelled.", status="cancelled")
        except PermissionError as error:
            self.complete_run(run_id, str(error), status="rejected")
        except Exception as error:
            failure = (
                error.details
                if isinstance(error, ProviderResponseError)
                else {
                    "code": "agent_failed",
                    "message": str(error),
                    "retryable": True,
                }
            )
            self.complete_run(
                run_id,
                f"Agent failed: {failure['message']}",
                status="failed",
                failure=failure,
            )
            self.emit("task.failed", {"run_id": run_id, "error": str(error)})
        finally:
            self.terminate_registered_operations(run_id)
            with self._lock:
                self.cancel_events.pop(run_id, None)
                self.pause_events.pop(run_id, None)
                self.run_threads.pop(run_id, None)
                self.read_versions.pop(run_id, None)

    def complete_run(
        self,
        run_id: int,
        summary: str,
        status: str = "completed",
        failure: Optional[dict[str, Any]] = None,
        changed_files: Optional[list[str]] = None,
        validation: Optional[dict[str, Any]] = None,
        completion_evidence: Optional[dict[str, Any]] = None,
    ) -> None:
        with self._lock:
            if run_id in self.cancellation_claims and status != "cancelled":
                return
        run_record: dict[str, Any] | None = None
        with self.connect() as connection:
            run = connection.execute(
                "select * from agent_runs where id = ?", (run_id,)
            ).fetchone()
            if run and run["status"] in self.TERMINAL_STATUSES:
                return
            if run:
                run_record = dict(run)
                connection.execute(
                    """
                    insert into conversation_messages(conversation_id, role, content, created_at)
                    values (?, 'assistant', ?, ?)
                    """,
                    (run["conversation_id"], summary, utc_now()),
                )
                connection.execute(
                    "update conversations set updated_at = ? where id = ?",
                    (utc_now(), run["conversation_id"]),
                )
            connection.execute(
                """
                update agent_runs
                set status = ?, summary = ?, failure_code = ?, failure_details_json = ?,
                    changed_files_json = coalesce(?, changed_files_json),
                    validation_json = coalesce(?, validation_json),
                    completion_evidence_json = coalesce(?, completion_evidence_json),
                    phase = ?, updated_at = ?
                where id = ?
                """,
                (
                    status,
                    summary,
                    str((failure or {}).get("code", "")),
                    json.dumps(failure or {}),
                    json.dumps(changed_files) if changed_files is not None else None,
                    json.dumps(validation) if validation is not None else None,
                    (
                        json.dumps(completion_evidence)
                        if completion_evidence is not None
                        else None
                    ),
                    "complete" if status == "completed" else "repair",
                    utc_now(),
                    run_id,
                ),
            )
            connection.execute(
                """
                update agent_run_tasks
                set status = ?, updated_at = ?
                where run_id = ? and status not in ('completed', 'blocked')
                """,
                (
                    "completed" if status == "completed" else "blocked",
                    utc_now(),
                    run_id,
                ),
            )
            connection.commit()
        if run_record:
            try:
                intelligence = analyze_workspace(self.workspace)
                update_project_state(
                    self.workspace,
                    intelligence,
                    last_run={
                        "id": run_id,
                        "status": status,
                        "summary": summary,
                        "updated_at": utc_now(),
                    },
                )
            except OSError:
                pass
            if status == "completed" and (changed_files or completion_evidence):
                self.suggest_completion_memory(
                    run_id,
                    summary,
                    changed_files or [],
                    validation or {},
                )
        self.emit(
            "task.completed" if status == "completed" else "agent.status_changed",
            {
                "run_id": run_id,
                "status": status,
                "phase": "complete" if status == "completed" else "repair",
                "summary": summary,
            },
        )

    def suggest_completion_memory(
        self,
        run_id: int,
        summary: str,
        changed_files: list[str],
        validation: dict[str, Any],
    ) -> None:
        try:
            with self.connect() as connection:
                existing = connection.execute(
                    """
                    select 1 from structured_memories
                    where source = ? and status = 'suggested'
                    """,
                    (f"agent-run:{run_id}",),
                ).fetchone()
                if existing:
                    return
                now = utc_now()
                content = "\n".join(
                    [
                        summary[:4000],
                        (
                            "Changed files: " + ", ".join(changed_files[:30])
                            if changed_files
                            else ""
                        ),
                        (
                            "Validation passed."
                            if validation.get("passed")
                            else ""
                        ),
                    ]
                ).strip()
                connection.execute(
                    """
                    insert into structured_memories(
                        scope, owner_id, title, content, tags_json, confidence,
                        status, source, created_at, updated_at
                    ) values ('project', ?, ?, ?, ?, 0.75, 'suggested', ?, ?, ?)
                    """,
                    (
                        str(self.workspace),
                        f"Run {run_id} outcome",
                        content,
                        json.dumps(["agent-run", "completion"]),
                        f"agent-run:{run_id}",
                        now,
                        now,
                    ),
                )
                connection.commit()
            self.emit("memory.suggested", {"run_id": run_id})
        except sqlite3.Error:
            return

    def trajectory(self, run_id: int) -> dict[str, Any]:
        run = self.get_run(run_id)
        with self.connect() as connection:
            messages = connection.execute(
                """
                select role, content, created_at from conversation_messages
                where conversation_id = ? order by id
                """,
                (run["conversation_id"],),
            ).fetchall()
        return {
            "schema_version": "1.0",
            "exported_at": utc_now(),
            "workspace": str(self.workspace),
            "run": run,
            "messages": [dict(message) for message in messages],
        }

    def cancel(self, run_id: int) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run["status"] in self.TERMINAL_STATUSES:
            return {
                **run,
                "terminated_operations": [],
                "already_terminal": True,
            }
        cancel = self.cancel_events.get(run_id)
        with self._lock:
            self.cancellation_claims.add(run_id)
        if cancel:
            cancel.set()
        self.update_run(run_id, "stopping")
        terminated = self.terminate_registered_operations(run_id)
        with self.connect() as connection:
            connection.execute(
                """
                update approvals set status = 'rejected', updated_at = ?
                where run_id = ? and status = 'pending'
                """,
                (utc_now(), run_id),
            )
            connection.execute(
                """
                update agent_steps set status = 'cancelled', updated_at = ?
                where run_id = ? and status in ('queued', 'running')
                """,
                (utc_now(), run_id),
            )
            connection.commit()
        self.complete_run(
            run_id,
            "Agent run cancelled.",
            status="cancelled",
            failure={
                "code": "cancelled",
                "message": "Stopped by the user.",
                "retryable": True,
            },
        )
        return {
            **self.get_run(run_id),
            "terminated_operations": terminated,
            "already_terminal": False,
        }

    def pause(self, run_id: int) -> dict[str, Any]:
        pause = self.pause_events.get(run_id)
        if pause:
            pause.set()
            self.update_run(run_id, "paused")
        return self.get_run(run_id)

    def resume(self, run_id: int) -> dict[str, Any]:
        pause = self.pause_events.get(run_id)
        if pause:
            pause.clear()
            self.update_run(run_id, "working")
        return self.get_run(run_id)

    def continue_interrupted(self, run_id: int) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run["status"] != "interrupted":
            raise ValueError("Only interrupted runs can be continued")
        objective = str(
            run.get("goal", {}).get("objective")
            or run.get("contract", {}).get("objective")
            or ""
        )
        if not objective:
            raise ValueError("The original instruction is unavailable")
        return self.start(
            run["conversation_id"],
            objective,
            run["mode"],
            bool(run["pursue_goal"]),
            run["permission"],
            run["context"],
            run["model"],
            record_message=False,
            agent_profile_id=run.get("agent_profile_id"),
            resume_state=run.get("checkpoint") or run.get("progress") or {},
        )

    def execute_approved_plan(self, run_id: int) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run["mode"] != "plan" or run["status"] != "completed":
            raise ValueError("Only a completed plan can be executed")
        with self.connect() as connection:
            message = connection.execute(
                """
                select content from conversation_messages
                where conversation_id = ? and role = 'user'
                order by id desc limit 1
                """,
                (run["conversation_id"],),
            ).fetchone()
        if not message:
            raise ValueError("The original instruction is unavailable")
        context = {
            **run["context"],
            "approved_plan": run["summary"],
            "approved_plan_run_id": run_id,
        }
        self.emit("agent.plan_approved", {"run_id": run_id})
        return self.start(
            run["conversation_id"],
            message["content"],
            "auto",
            bool(run["pursue_goal"]),
            run["permission"],
            context,
            run["model"],
            record_message=False,
            agent_profile_id=run.get("agent_profile_id"),
        )

    def replay(self, run_id: int) -> dict[str, Any]:
        run = self.get_run(run_id)
        with self.connect() as connection:
            message = connection.execute(
                """
                select content from conversation_messages
                where conversation_id = ? and role = 'user'
                order by id desc limit 1
                """,
                (run["conversation_id"],),
            ).fetchone()
        if not message:
            raise ValueError("The original instruction is unavailable")
        return self.start(
            run["conversation_id"],
            message["content"],
            run["mode"],
            bool(run["pursue_goal"]),
            run["permission"],
            run["context"],
            run["model"],
            agent_profile_id=run.get("agent_profile_id"),
        )

    def retry_with_repair(self, run_id: int, mode: str = "repair") -> dict[str, Any]:
        run = self.get_run(run_id)
        if run["status"] not in {"failed", "needs_review", "rejected", "cancelled", "interrupted"}:
            raise ValueError("Only a stopped run can be retried")
        with self.connect() as connection:
            message = connection.execute(
                """
                select content from conversation_messages
                where conversation_id = ? and role = 'user'
                order by id desc limit 1
                """,
                (run["conversation_id"],),
            ).fetchone()
        if not message:
            raise ValueError("The original instruction is unavailable")
        context = {
            **run["context"],
            "retry_of_run_id": run_id,
            "recovery_mode": mode,
            "previous_failure": run.get("failure_details") or {
                "code": run.get("failure_code", ""),
                "message": run.get("summary", ""),
            },
            "previous_changed_files": run.get("changed_files", []),
        }
        self.emit("agent.retry_started", {"run_id": run_id, "mode": mode})
        return self.start(
            run["conversation_id"],
            message["content"],
            "auto",
            True,
            run["permission"],
            context,
            run["model"],
            record_message=False,
            agent_profile_id=run.get("agent_profile_id"),
            resume_state=(
                run.get("checkpoint") or run.get("progress") or {}
                if mode == "repair"
                else {}
            ),
        )

    def resolve_approval(
        self, approval_id: int, approved: bool, always_allow: bool = False
    ) -> dict[str, Any]:
        status = "approved" if approved else "rejected"
        with self.connect() as connection:
            row = connection.execute(
                "select * from approvals where id = ?", (approval_id,)
            ).fetchone()
            if row is None:
                raise KeyError("Approval not found")
            connection.execute(
                "update approvals set status = ?, updated_at = ? where id = ?",
                (status, utc_now(), approval_id),
            )
            if approved and always_allow:
                connection.execute(
                    """
                    insert or replace into permission_grants(tool, workspace, created_at)
                    values (?, ?, ?)
                    """,
                    (row["kind"], str(self.workspace), utc_now()),
                )
            connection.commit()
        result = {**dict(row), "status": status, "details": json.loads(row["details_json"])}
        self.emit("approval.resolved", result)
        return result

    def tool_registry(self) -> list[dict[str, Any]]:
        tools = [
            {
                "id": tool,
                "mutating": tool in {
                    "create_file", "edit_file", "move_file", "delete_file",
                    "run_command", "stop_process",
                },
                "description": {
                    "list_files": "List directory entries",
                    "tree": "Inspect a bounded workspace tree",
                    "glob": "Find files using a glob pattern",
                    "read_file": "Read one UTF-8 file",
                    "read_files": "Read several UTF-8 files in one action",
                    "search": "Search text under a directory",
                    "fetch_url": "Visit a public web page after user approval and extract readable text",
                    "create_file": "Create a new file or safely update an existing target",
                    "edit_file": "Apply anchored or line-range edits to an existing file",
                    "move_file": "Move or rename a workspace file",
                    "delete_file": "Delete a file or directory",
                    "git_status": "Inspect branch and working-tree changes",
                    "git_diff": "Read the current Git diff",
                    "workspace_diagnostics": "Inspect project entry points and validation candidates",
                    "list_ports": "List local listening ports and process owners",
                    "stop_process": "Stop a process after explicit approval",
                    "run_command": "Run a finite shell command",
                }[tool],
                "input_schema": {
                    "type": "object",
                    "required": schema["required"],
                    "properties": {
                        key: {"type": value}
                        for key, value in schema["properties"].items()
                    },
                },
                "access": "full-access permits explicit absolute paths; destructive and sensitive operations require approval",
            }
            for tool, schema in AgentRuntime.TOOL_SCHEMAS.items()
        ]
        tools.extend(
            {
                "id": f"mcp__{server['id']}",
                "mutating": server["permission"] != "read-only",
                "description": (
                    f"Invoke the enabled MCP server {server['name']} using JSON-RPC"
                ),
                "input_schema": {
                    "type": "object",
                    "required": [],
                    "properties": {
                        "method": {"type": "string"},
                        "params": {"type": "object"},
                    },
                },
                "access": server["permission"],
            }
            for server in self.enabled_mcp_servers()
        )
        return tools

    def agent_manifests(self) -> list[dict[str, Any]]:
        roles = {
            "planner": "Inspect context and produce a decision-complete execution plan.",
            "coder": "Implement small, correct workspace changes.",
            "reviewer": "Review changes for regressions and incomplete requirements.",
            "tester": "Select and run relevant validation.",
            "debugger": "Diagnose failures, repair causes, and rerun validation.",
        }
        return [
            {
                "schema_version": "1.0",
                "id": role,
                "name": role.title(),
                "version": "1.0.0",
                "role": role,
                "instructions": instructions,
                "permission_profile": "workspace",
                "tools": {"allow": [tool["id"] for tool in self.tool_registry()]},
            }
            for role, instructions in roles.items()
        ]


def detect_run_profiles(workspace: Path) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    package_files = [workspace / "package.json"]
    package_files.extend(
        path
        for path in workspace.glob("*/package.json")
        if path.parent.name not in {"node_modules", "dist"}
    )
    for package_json in package_files:
        if not package_json.exists():
            continue
        try:
            scripts = json.loads(package_json.read_text(encoding="utf-8")).get("scripts", {})
            cwd = str(package_json.parent.relative_to(workspace)) or "."
            for kind in ("dev", "start", "build", "test", "lint"):
                if kind in scripts:
                    profiles.append(
                        {
                            "id": f"npm:{kind}",
                            "name": f"npm {kind}",
                            "kind": "run" if kind == "start" else kind,
                            "command": f"npm run {kind}",
                            "cwd": cwd,
                        }
                    )
            main_script = next(
                (
                    candidate
                    for candidate in ("server.js", "app.js", "index.js", "src/index.js", "src/server.js")
                    if (package_json.parent / candidate).exists()
                ),
                None,
            )
            if main_script:
                profiles.append(
                    {
                        "id": f"node:inspect:{cwd}",
                        "name": "Node Inspector",
                        "kind": "debug",
                        "command": f"node --inspect-brk=127.0.0.1:9229 {shlex.quote(main_script)}",
                        "cwd": cwd,
                        "debug_url": "devtools://devtools/bundled/inspector.html",
                    }
                )
        except (OSError, ValueError):
            pass
    if not any(profile["kind"] == "run" for profile in profiles):
        for entry in (
            "server.js",
            "app.js",
            "index.js",
            "src/server.js",
            "src/index.js",
            "*/server.js",
            "*/app.js",
            "*/index.js",
        ):
            matches = [
                path
                for path in workspace.glob(entry)
                if not (path.parent / "index.html").exists()
            ]
            if matches:
                target = matches[0]
                profiles.append(
                    {
                        "id": f"node:{target.relative_to(workspace)}",
                        "name": f"Run {target.name}",
                        "kind": "run",
                        "command": f"node {shlex.quote(target.name)}",
                        "cwd": str(target.parent.relative_to(workspace)) or ".",
                    }
                )
                break
    if not any(profile["kind"] == "run" for profile in profiles):
        for entry in ("main.py", "app.py", "server.py"):
            if (workspace / entry).exists():
                profiles.append(
                    {
                        "id": f"python:{entry}",
                        "name": f"Run {entry}",
                        "kind": "run",
                        "command": f"python3 {shlex.quote(entry)}",
                    }
                )
                break
    if not any(profile["kind"] == "run" for profile in profiles) and (
        list(workspace.glob("index.html")) + list(workspace.glob("*/index.html"))
    ):
        index_file = (
            list(workspace.glob("index.html")) + list(workspace.glob("*/index.html"))
        )[0]
        profiles.append(
            {
                "id": "static:http",
                "name": "Open static app",
                "kind": "run",
                "command": "python3 -m http.server 4173",
                "cwd": str(index_file.parent.relative_to(workspace)) or ".",
                "url": "http://127.0.0.1:4173",
            }
        )
    if (workspace / "pyproject.toml").exists() or (workspace / "pytest.ini").exists():
        profiles.append({"id": "python:pytest", "name": "Pytest", "kind": "test", "command": "python3 -m pytest"})
    python_entry = next(
        (entry for entry in ("main.py", "app.py", "server.py") if (workspace / entry).exists()),
        None,
    )
    if python_entry:
        profiles.append(
            {
                "id": f"python:debugpy:{python_entry}",
                "name": f"Debug {python_entry}",
                "kind": "debug",
                "command": f"python3 -m debugpy --listen 127.0.0.1:5678 --wait-for-client {shlex.quote(python_entry)}",
                "cwd": ".",
                "debug_port": 5678,
            }
        )
    return profiles


def listening_ports() -> list[dict[str, Any]]:
    try:
        result = subprocess.run(
            ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    ports: list[dict[str, Any]] = []
    for line in result.stdout.splitlines()[1:]:
        columns = line.split()
        if len(columns) < 9 or ":" not in columns[-2]:
            continue
        address = columns[-2]
        port_text = address.rsplit(":", 1)[-1]
        if not port_text.isdigit():
            continue
        port = int(port_text)
        ports.append(
            {
                "process": columns[0],
                "pid": int(columns[1]),
                "port": port,
                "address": address,
                "url": f"http://127.0.0.1:{port}",
            }
        )
    return ports
