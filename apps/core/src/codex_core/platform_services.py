from __future__ import annotations

import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_checked(
    command: list[str],
    *,
    cwd: Path,
    timeout: int = 20,
    allow_failure: bool = False,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode and not allow_failure:
        raise ValueError((result.stderr or result.stdout or "Command failed").strip())
    return result


class PlatformServices:
    def __init__(
        self,
        workspace: Path,
        db_path: Callable[[], Path],
        emit: Callable[[str, dict[str, Any]], dict[str, Any]],
    ) -> None:
        self.workspace = workspace.resolve()
        self.db_path = db_path
        self.emit = emit

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path())
        connection.row_factory = sqlite3.Row
        return connection

    def init_schema(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                create table if not exists structured_memories (
                    id integer primary key autoincrement,
                    scope text not null,
                    owner_id text not null,
                    title text not null,
                    content text not null,
                    tags_json text not null,
                    confidence real not null,
                    status text not null,
                    source text not null,
                    created_at text not null,
                    updated_at text not null
                );
                create table if not exists agent_profiles (
                    id integer primary key autoincrement,
                    agent_id text not null,
                    version integer not null,
                    status text not null,
                    name text not null,
                    role text not null,
                    prompt text not null,
                    constitution text not null,
                    workflow_json text not null,
                    tool_policy_json text not null,
                    created_at text not null,
                    updated_at text not null,
                    unique(agent_id, version)
                );
                create table if not exists evaluation_runs (
                    id integer primary key autoincrement,
                    agent_id text not null,
                    agent_version integer not null,
                    fixture text not null,
                    status text not null,
                    score real not null,
                    duration_ms integer not null,
                    tool_calls integer not null,
                    failures_json text not null,
                    output text not null,
                    created_at text not null
                );
                create table if not exists mcp_servers (
                    id integer primary key autoincrement,
                    name text not null unique,
                    command_json text not null,
                    env_json text not null,
                    permission text not null,
                    enabled integer not null,
                    status text not null,
                    last_error text not null,
                    created_at text not null,
                    updated_at text not null
                );
                """
            )
            connection.commit()

    @staticmethod
    def memory_record(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        record["tags"] = json.loads(record.pop("tags_json") or "[]")
        return record

    def list_memories(
        self, scope: str | None = None, owner_id: str | None = None
    ) -> list[dict[str, Any]]:
        query = "select * from structured_memories where 1=1"
        params: list[Any] = []
        if scope:
            query += " and scope = ?"
            params.append(scope)
        if owner_id:
            query += " and owner_id = ?"
            params.append(owner_id)
        query += " order by updated_at desc"
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self.memory_record(row) for row in rows]

    def create_memory(self, payload: dict[str, Any]) -> dict[str, Any]:
        scope = str(payload.get("scope", "project"))
        owner_id = str(payload.get("owner_id", self.workspace))
        title = str(payload.get("title", "")).strip()
        content = str(payload.get("content", "")).strip()
        tags = [str(item) for item in payload.get("tags", [])][:30]
        confidence = min(1.0, max(0.0, float(payload.get("confidence", 1))))
        status = str(payload.get("status", "active"))
        source = str(payload.get("source", "user"))
        if scope not in {"global", "project", "agent", "session", "task"}:
            raise ValueError("Unsupported memory scope")
        if not title or not content:
            raise ValueError("Memory title and content are required")
        conflicts = [
            item
            for item in self.list_memories(scope, owner_id)
            if item["title"].casefold() == title.casefold()
            and item["content"].casefold() != content.casefold()
            and item["status"] == "active"
        ]
        if conflicts and not payload.get("resolve_conflicts"):
            return {"created": False, "conflicts": conflicts}
        now = utc_now()
        with self.connect() as connection:
            if conflicts:
                connection.executemany(
                    "update structured_memories set status = 'superseded', updated_at = ? where id = ?",
                    [(now, item["id"]) for item in conflicts],
                )
            cursor = connection.execute(
                """
                insert into structured_memories(
                    scope, owner_id, title, content, tags_json, confidence, status,
                    source, created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scope,
                    owner_id,
                    title,
                    content,
                    json.dumps(tags),
                    confidence,
                    status,
                    source,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "select * from structured_memories where id = ?", (cursor.lastrowid,)
            ).fetchone()
            connection.commit()
        record = self.memory_record(row)
        self.emit("memory.structured_created", record)
        return {"created": True, "memory": record, "resolved_conflicts": len(conflicts)}

    def suggest_memories(self) -> list[dict[str, Any]]:
        suggestions: list[dict[str, Any]] = [
            memory
            for memory in self.list_memories()
            if memory["status"] == "suggested"
        ]
        state_path = self.workspace / ".kodex-agent" / "project-state.json"
        legacy_state_path = self.workspace / ".codex-agent" / "project-state.json"
        if not state_path.exists() and legacy_state_path.exists():
            state_path = legacy_state_path
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        for title, key in (
            ("Project summary", "summary"),
            ("Known issues", "known_issues"),
            ("Pending work", "pending_work"),
        ):
            value = state.get(key)
            if value:
                content = value if isinstance(value, str) else "\n".join(map(str, value))
                suggestions.append(
                    {
                        "scope": "project",
                        "owner_id": str(self.workspace),
                        "title": title,
                        "content": content[:8_000],
                        "tags": ["continuity", key],
                        "confidence": 0.8,
                        "status": "suggested",
                        "source": "project-state",
                    }
                )
        return suggestions

    def export_memories(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "exported_at": utc_now(),
            "workspace": str(self.workspace),
            "memories": self.list_memories(),
        }

    def import_memories(self, memories: list[dict[str, Any]]) -> dict[str, Any]:
        created = 0
        conflicts = 0
        for memory in memories[:2_000]:
            result = self.create_memory({**memory, "resolve_conflicts": False})
            created += int(result.get("created", False))
            conflicts += len(result.get("conflicts", []))
        return {"created": created, "conflicts": conflicts}

    def reset_memories(self, scope: str | None = None) -> dict[str, Any]:
        with self.connect() as connection:
            if scope:
                cursor = connection.execute(
                    "delete from structured_memories where scope = ?", (scope,)
                )
            else:
                cursor = connection.execute("delete from structured_memories")
            connection.commit()
        self.emit("memory.reset", {"scope": scope, "deleted": cursor.rowcount})
        return {"deleted": cursor.rowcount}

    def compress_memories(self) -> dict[str, Any]:
        memories = self.list_memories()
        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for memory in memories:
            key = (
                memory["scope"],
                memory["owner_id"],
                memory["title"].strip().casefold(),
            )
            groups.setdefault(key, []).append(memory)
        archived = 0
        trimmed = 0
        with self.connect() as connection:
            for items in groups.values():
                active = [item for item in items if item["status"] == "active"]
                if len(active) > 1:
                    newest = active[0]
                    for item in active[1:]:
                        connection.execute(
                            "update structured_memories set status = 'superseded', updated_at = ? where id = ?",
                            (utc_now(), item["id"]),
                        )
                        archived += 1
                    if len(newest["content"]) > 8_000:
                        connection.execute(
                            "update structured_memories set content = ?, updated_at = ? where id = ?",
                            (newest["content"][:8_000], utc_now(), newest["id"]),
                        )
                        trimmed += 1
            connection.commit()
        return {"archived_duplicates": archived, "trimmed": trimmed}

    @staticmethod
    def profile_record(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        record["workflow"] = json.loads(record.pop("workflow_json") or "[]")
        record["tool_policy"] = json.loads(record.pop("tool_policy_json") or "{}")
        return record

    def list_agent_profiles(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "select * from agent_profiles order by agent_id, version desc"
            ).fetchall()
        return [self.profile_record(row) for row in rows]

    def save_agent_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        agent_id = re.sub(r"[^a-z0-9-]+", "-", str(payload.get("agent_id", "")).lower()).strip("-")
        if not agent_id:
            raise ValueError("Agent id is required")
        with self.connect() as connection:
            latest = connection.execute(
                "select max(version) as version from agent_profiles where agent_id = ?",
                (agent_id,),
            ).fetchone()["version"]
            version = int(latest or 0) + 1
            now = utc_now()
            cursor = connection.execute(
                """
                insert into agent_profiles(
                    agent_id, version, status, name, role, prompt, constitution,
                    workflow_json, tool_policy_json, created_at, updated_at
                ) values (?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_id,
                    version,
                    str(payload.get("name", agent_id.title())),
                    str(payload.get("role", "coder")),
                    str(payload.get("prompt", "")),
                    str(payload.get("constitution", "")),
                    json.dumps(payload.get("workflow", [])),
                    json.dumps(payload.get("tool_policy", {})),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "select * from agent_profiles where id = ?", (cursor.lastrowid,)
            ).fetchone()
            connection.commit()
        record = self.profile_record(row)
        self.emit("agent_studio.saved", {"agent_id": agent_id, "version": version})
        return record

    def validate_agent_profile(self, profile_id: int) -> dict[str, Any]:
        profile = self.get_agent_profile(profile_id)
        errors: list[str] = []
        if len(profile["prompt"].strip()) < 20:
            errors.append("Prompt must contain at least 20 characters")
        if not profile["constitution"].strip():
            errors.append("Constitution is required")
        allowed = {
            "list_files", "read_file", "search", "create_file", "edit_file",
            "write_file", "apply_patch", "delete_file", "run_command",
        }
        configured = set(profile["tool_policy"].get("allow", []))
        unknown = sorted(configured - allowed)
        if unknown:
            errors.append("Unknown tools: " + ", ".join(unknown))
        return {"valid": not errors, "errors": errors, "profile": profile}

    def get_agent_profile(self, profile_id: int) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "select * from agent_profiles where id = ?", (profile_id,)
            ).fetchone()
        if row is None:
            raise KeyError("Agent profile not found")
        return self.profile_record(row)

    def compare_agent_profiles(self, first_id: int, second_id: int) -> dict[str, Any]:
        first = self.get_agent_profile(first_id)
        second = self.get_agent_profile(second_id)
        return {
            "first": first,
            "second": second,
            "changes": {
                key: {"before": first.get(key), "after": second.get(key)}
                for key in ("prompt", "constitution", "workflow", "tool_policy", "status")
                if first.get(key) != second.get(key)
            },
        }

    def promote_agent_profile(self, profile_id: int, status: str) -> dict[str, Any]:
        if status not in {"candidate", "active", "archived"}:
            raise ValueError("Unsupported profile status")
        validation = self.validate_agent_profile(profile_id)
        if not validation["valid"]:
            raise ValueError("; ".join(validation["errors"]))
        profile = validation["profile"]
        if status == "active":
            with self.connect() as connection:
                passing = connection.execute(
                    """
                    select count(*) as count from evaluation_runs
                    where agent_id = ? and agent_version = ? and status = 'passed'
                    """,
                    (profile["agent_id"], profile["version"]),
                ).fetchone()["count"]
            if not passing:
                raise ValueError("A passing evaluation is required before activation")
        with self.connect() as connection:
            if status == "active":
                connection.execute(
                    "update agent_profiles set status = 'archived', updated_at = ? where agent_id = ? and status = 'active'",
                    (utc_now(), profile["agent_id"]),
                )
            connection.execute(
                "update agent_profiles set status = ?, updated_at = ? where id = ?",
                (status, utc_now(), profile_id),
            )
            connection.commit()
        record = self.get_agent_profile(profile_id)
        self.emit("agent_studio.promoted", {"profile_id": profile_id, "status": status})
        return record

    def record_evaluation(self, payload: dict[str, Any]) -> dict[str, Any]:
        required = ("agent_id", "agent_version", "fixture")
        if any(payload.get(key) in (None, "") for key in required):
            raise ValueError("Agent id, version, and fixture are required")
        score = min(1.0, max(0.0, float(payload.get("score", 0))))
        status = "passed" if score >= float(payload.get("passing_score", 0.7)) else "failed"
        with self.connect() as connection:
            cursor = connection.execute(
                """
                insert into evaluation_runs(
                    agent_id, agent_version, fixture, status, score, duration_ms,
                    tool_calls, failures_json, output, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(payload["agent_id"]),
                    int(payload["agent_version"]),
                    str(payload["fixture"]),
                    status,
                    score,
                    int(payload.get("duration_ms", 0)),
                    int(payload.get("tool_calls", 0)),
                    json.dumps(payload.get("failures", [])),
                    str(payload.get("output", "")),
                    utc_now(),
                ),
            )
            row = connection.execute(
                "select * from evaluation_runs where id = ?", (cursor.lastrowid,)
            ).fetchone()
            connection.commit()
        return self.evaluation_record(row)

    @staticmethod
    def evaluation_record(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        record["failures"] = json.loads(record.pop("failures_json") or "[]")
        return record

    def evaluation_report(self, agent_id: str | None = None) -> dict[str, Any]:
        query = "select * from evaluation_runs"
        params: tuple[Any, ...] = ()
        if agent_id:
            query += " where agent_id = ?"
            params = (agent_id,)
        query += " order by id desc"
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        runs = [self.evaluation_record(row) for row in rows]
        by_version: dict[str, dict[str, Any]] = {}
        for run in runs:
            key = f"{run['agent_id']}@{run['agent_version']}"
            item = by_version.setdefault(
                key,
                {"runs": 0, "passed": 0, "score": 0.0, "duration_ms": 0, "tool_calls": 0, "failures": 0},
            )
            item["runs"] += 1
            item["passed"] += int(run["status"] == "passed")
            item["score"] += run["score"]
            item["duration_ms"] += run["duration_ms"]
            item["tool_calls"] += run["tool_calls"]
            item["failures"] += len(run["failures"])
        for item in by_version.values():
            item["score"] = round(item["score"] / max(item["runs"], 1), 3)
        proposals = [
            {
                "agent": key,
                "proposal": "Reduce tool calls and inspect failures before editing"
                if value["failures"]
                else "Candidate is stable; review for promotion",
                "auto_activate": False,
            }
            for key, value in by_version.items()
        ]
        return {"runs": runs, "comparison": by_version, "proposals": proposals}

    def git_state(self) -> dict[str, Any]:
        inside = run_checked(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=self.workspace,
            allow_failure=True,
        )
        if inside.returncode:
            return {"repository": False, "branch": "", "changes": [], "branches": []}
        branch = run_checked(
            ["git", "branch", "--show-current"], cwd=self.workspace, allow_failure=True
        ).stdout.strip()
        status = run_checked(
            ["git", "status", "--porcelain=v1"], cwd=self.workspace, allow_failure=True
        ).stdout
        changes = [
            {"status": line[:2], "path": line[3:]}
            for line in status.splitlines()
            if len(line) > 3
        ]
        branches = [
            line.strip().removeprefix("* ")
            for line in run_checked(
                ["git", "branch", "--format=%(refname:short)"],
                cwd=self.workspace,
                allow_failure=True,
            ).stdout.splitlines()
        ]
        return {
            "repository": True,
            "branch": branch,
            "changes": changes,
            "branches": branches,
            "summary": self.git_summary(changes),
        }

    @staticmethod
    def git_summary(changes: list[dict[str, Any]]) -> str:
        if not changes:
            return "Working tree is clean."
        counts: dict[str, int] = {}
        for item in changes:
            label = item["status"].strip() or "modified"
            counts[label] = counts.get(label, 0) + 1
        return f"{len(changes)} changed files: " + ", ".join(
            f"{value} {key}" for key, value in sorted(counts.items())
        )

    def git_diff(self, path: str | None = None, staged: bool = False) -> dict[str, Any]:
        command = ["git", "diff"]
        if staged:
            command.append("--cached")
        if path:
            command.extend(["--", path])
        result = run_checked(command, cwd=self.workspace, allow_failure=True)
        return {"diff": result.stdout[-200_000:], "staged": staged, "path": path}

    def git_mutation(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        paths = [str(item) for item in payload.get("paths", [])]
        if action == "stage":
            command = ["git", "add", "--", *paths]
        elif action == "unstage":
            command = ["git", "restore", "--staged", "--", *paths]
        elif action == "commit":
            message = str(payload.get("message", "")).strip()
            if not message:
                raise ValueError("Commit message is required")
            command = ["git", "commit", "-m", message]
        elif action == "branch":
            name = str(payload.get("name", "")).strip()
            if not re.fullmatch(r"[A-Za-z0-9._/-]+", name):
                raise ValueError("Invalid branch name")
            command = ["git", "switch", "-c", name]
        elif action == "switch":
            name = str(payload.get("name", "")).strip()
            command = ["git", "switch", name]
        elif action == "pull":
            command = ["git", "pull", "--ff-only"]
        elif action == "push":
            command = ["git", "push"]
        else:
            raise ValueError("Unsupported Git action")
        result = run_checked(command, cwd=self.workspace, timeout=120)
        state = self.git_state()
        self.emit("git.changed", {"action": action, "state": state})
        return {"action": action, "output": (result.stdout + result.stderr).strip(), "state": state}

    def hardware(self) -> dict[str, Any]:
        memory_bytes = 0
        try:
            if platform.system() == "Darwin":
                memory_bytes = int(
                    run_checked(["sysctl", "-n", "hw.memsize"], cwd=self.workspace).stdout.strip()
                )
            elif hasattr(os, "sysconf"):
                memory_bytes = int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
        except (OSError, ValueError):
            memory_bytes = 0
        disk = shutil.disk_usage(self.workspace)
        gpu = ""
        if platform.system() == "Darwin":
            gpu_result = run_checked(
                ["system_profiler", "SPDisplaysDataType", "-detailLevel", "mini"],
                cwd=self.workspace,
                timeout=15,
                allow_failure=True,
            )
            match = re.search(r"Chipset Model:\s*(.+)", gpu_result.stdout)
            gpu = match.group(1).strip() if match else ""
        return {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "cpu": platform.processor() or platform.machine(),
            "cpu_count": os.cpu_count() or 1,
            "memory_bytes": memory_bytes,
            "disk_free_bytes": disk.free,
            "gpu": gpu,
        }

    def model_advice(self, providers: list[dict[str, Any]]) -> dict[str, Any]:
        hardware = self.hardware()
        memory_gb = hardware["memory_bytes"] / 1024**3 if hardware["memory_bytes"] else 0
        catalogue = [
            {"id": "small-code", "size": "3B-7B", "minimum_memory_gb": 8, "use": "fast edits and chat"},
            {"id": "balanced-code", "size": "8B-14B", "minimum_memory_gb": 16, "use": "multi-file coding"},
            {"id": "large-reasoning", "size": "20B-32B", "minimum_memory_gb": 32, "use": "deep planning and review"},
            {"id": "cloud-frontier", "size": "hosted", "minimum_memory_gb": 0, "use": "largest context and hard tasks"},
        ]
        recommended = [
            model for model in catalogue if model["minimum_memory_gb"] <= memory_gb
        ] or [catalogue[0]]
        runtimes = [
            {
                "id": provider["id"],
                "available": provider["available"],
                "models": provider["models"],
            }
            for provider in providers
        ]
        return {
            "hardware": hardware,
            "catalogue": catalogue,
            "recommended": recommended,
            "runtimes": runtimes,
        }

    @staticmethod
    def parse_diagnostics(output: str) -> list[dict[str, Any]]:
        pattern = re.compile(
            r"(?P<path>[^:\n]+\.(?:py|js|jsx|ts|tsx|css|html)):(?P<line>\d+):(?:(?P<column>\d+):)?\s*(?P<message>.+)"
        )
        return [
            {
                "path": match.group("path").strip(),
                "line": int(match.group("line")),
                "column": int(match.group("column") or 1),
                "message": match.group("message").strip(),
            }
            for match in pattern.finditer(output)
        ][:500]

    def list_mcp_servers(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("select * from mcp_servers order by name").fetchall()
        return [self.mcp_record(row) for row in rows]

    @staticmethod
    def mcp_record(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        record["command"] = json.loads(record.pop("command_json") or "[]")
        record["env"] = json.loads(record.pop("env_json") or "{}")
        record["enabled"] = bool(record["enabled"])
        return record

    def configure_mcp(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name", "")).strip()
        command = payload.get("command", [])
        permission = str(payload.get("permission", "read-only"))
        if not name or not isinstance(command, list) or not command:
            raise ValueError("MCP name and command are required")
        if permission not in {"read-only", "confirm-edits", "workspace"}:
            raise ValueError("Unsupported MCP permission")
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                insert into mcp_servers(
                    name, command_json, env_json, permission, enabled, status,
                    last_error, created_at, updated_at
                ) values (?, ?, ?, ?, ?, 'configured', '', ?, ?)
                on conflict(name) do update set
                    command_json=excluded.command_json,
                    env_json=excluded.env_json,
                    permission=excluded.permission,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at
                """,
                (
                    name,
                    json.dumps(command),
                    json.dumps(payload.get("env", {})),
                    permission,
                    int(bool(payload.get("enabled", True))),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "select * from mcp_servers where name = ?", (name,)
            ).fetchone()
            connection.commit()
        record = self.mcp_record(row)
        self.emit("mcp.configured", {"server": name})
        return record

    def test_mcp(self, server_id: int) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "select * from mcp_servers where id = ?", (server_id,)
            ).fetchone()
        if row is None:
            raise KeyError("MCP server not found")
        server = self.mcp_record(row)
        executable = shutil.which(server["command"][0])
        status = "available" if executable else "missing"
        error = "" if executable else f"{server['command'][0]} is not installed"
        with self.connect() as connection:
            connection.execute(
                "update mcp_servers set status = ?, last_error = ?, updated_at = ? where id = ?",
                (status, error, utc_now(), server_id),
            )
            connection.commit()
        return {**server, "status": status, "last_error": error}

    def invoke_mcp(
        self, server_id: int, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "select * from mcp_servers where id = ?", (server_id,)
            ).fetchone()
        if row is None:
            raise KeyError("MCP server not found")
        server = self.mcp_record(row)
        if not server["enabled"]:
            raise ValueError("MCP server is disabled")
        if method.startswith("tools/call") and server["permission"] == "read-only":
            raise PermissionError("This MCP server is restricted to read-only operations")
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }
        with tempfile.TemporaryDirectory(prefix="kodex-mcp-") as temporary:
            environment = {
                key: value
                for key, value in os.environ.items()
                if key in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR"}
            }
            environment.update(
                {
                    "HOME": temporary,
                    "TMPDIR": temporary,
                    "CODEX_MCP_PERMISSION": server["permission"],
                    "CODEX_MCP_WORKSPACE": str(self.workspace),
                    **{
                        str(key): str(value)
                        for key, value in server["env"].items()
                        if not re.search(r"(TOKEN|SECRET|PASSWORD|KEY)", str(key), re.I)
                    },
                }
            )
            try:
                result = subprocess.run(
                    server["command"],
                    input=json.dumps(request) + "\n",
                    cwd=self.workspace,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=False,
                    start_new_session=True,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise ValueError(str(error)) from error
        responses = []
        for line in result.stdout.splitlines():
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            if isinstance(payload, dict) and payload.get("id") == 1:
                responses.append(payload)
        if not responses:
            raise ValueError((result.stderr or "MCP server returned no JSON-RPC response").strip())
        response = responses[-1]
        self.emit(
            "mcp.invoked",
            {
                "server_id": server_id,
                "method": method,
                "permission": server["permission"],
                "isolated": True,
            },
        )
        return response

    def backup_database(self) -> dict[str, Any]:
        source = self.db_path()
        backup_dir = source.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        destination = backup_dir / f"codex-core-{datetime.now().strftime('%Y%m%d-%H%M%S')}.sqlite3"
        with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as target_db:
            source_db.backup(target_db)
        return {"path": str(destination), "size": destination.stat().st_size, "created_at": utc_now()}

    def list_backups(self) -> list[dict[str, Any]]:
        backup_dir = self.db_path().parent / "backups"
        if not backup_dir.exists():
            return []
        return [
            {
                "path": str(path),
                "size": path.stat().st_size,
                "created_at": datetime.fromtimestamp(
                    path.stat().st_mtime, timezone.utc
                ).isoformat(),
            }
            for path in sorted(backup_dir.glob("codex-core-*.sqlite3"), reverse=True)
        ]

    def restore_database(self, backup_path: str) -> dict[str, Any]:
        candidate = Path(backup_path).resolve()
        backup_root = (self.db_path().parent / "backups").resolve()
        if backup_root not in candidate.parents or not candidate.is_file():
            raise ValueError("Backup is outside the managed backup directory")
        safety = self.backup_database()
        with sqlite3.connect(candidate) as source_db, sqlite3.connect(self.db_path()) as target_db:
            source_db.backup(target_db)
        return {"restored": str(candidate), "safety_backup": safety}

    def runtime_validation(self) -> dict[str, Any]:
        checks = {
            "python": platform.python_version(),
            "core_source": (self.workspace / "apps" / "core" / "src").exists(),
            "renderer_build": (self.workspace / "apps" / "desktop" / "dist" / "index.html").exists(),
            "electron": (self.workspace / "node_modules" / "electron").exists(),
            "node_pty": (self.workspace / "apps" / "desktop" / "node_modules" / "node-pty").exists()
            or (self.workspace / "node_modules" / "node-pty").exists(),
            "database": self.db_path().exists(),
        }
        return {
            "ready": all(value for key, value in checks.items() if key != "python"),
            "checks": checks,
            "platform": platform.system(),
            "packaging": {
                "development": True,
                "mac_signing_identity": bool(os.environ.get("CSC_NAME")),
                "windows_runner_required": platform.system() != "Windows",
                "linux_runner_required": platform.system() != "Linux",
            },
        }
