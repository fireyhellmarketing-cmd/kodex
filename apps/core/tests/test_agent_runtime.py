import json
import io
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import patch

import httpx

from codex_core.agent_runtime import (
    AgentRuntime,
    ProviderChoice,
    ToolInputError,
    detect_run_profiles,
)


SCHEMA = """
create table conversations(id integer primary key, title text, created_at text, updated_at text);
create table conversation_messages(id integer primary key, conversation_id integer, role text, content text, created_at text);
create table agent_runs(
  id integer primary key, conversation_id integer, status text, mode text, permission text,
  pursue_goal integer, model text, contract_json text, context_json text, summary text,
  progress_json text default '{}', failure_code text default '', failure_details_json text default '{}',
  changed_files_json text default '[]', validation_json text default '{}',
  recovery_attempts integer default 0, access_scope text default 'workspace',
  created_at text, updated_at text
);
create table agent_steps(
  id integer primary key autoincrement, run_id integer, kind text, status text,
  title text, data_json text, created_at text, updated_at text
);
create table approvals(
  id integer primary key autoincrement, run_id integer, kind text, status text,
  summary text, details_json text, created_at text, updated_at text
);
create table permission_grants(tool text, workspace text, created_at text, primary key(tool, workspace));
create table memories(id integer primary key, title text, content text);
create table structured_memories(
  id integer primary key, scope text, owner_id text, title text, content text,
  tags_json text, confidence real, status text, source text, created_at text, updated_at text
);
create table agent_profiles(
  id integer primary key, agent_id text, version integer, status text, name text,
  role text, prompt text, constitution text, workflow_json text, tool_policy_json text,
  created_at text, updated_at text
);
create table mcp_servers(
  id integer primary key, name text, command_json text, env_json text, permission text,
  enabled integer, status text, last_error text, created_at text, updated_at text
);
"""


class ScriptedRuntime(AgentRuntime):
    def __init__(self, workspace: Path, database: Path, decisions: list[dict]):
        super().__init__(workspace, lambda: database, lambda *_: {}, lambda label: {"label": label})
        self.decisions = iter(decisions)

    def select_provider(self, requested="auto"):
        return object()

    def model_call(self, run_id, choice, messages, cancel):
        return json.dumps(next(self.decisions))


class StartCaptureRuntime(ScriptedRuntime):
    def start(self, *args, **kwargs):
        self.started_with = {"args": args, "kwargs": kwargs}
        return self.started_with


class FallbackRuntime(AgentRuntime):
    def providers(self):
        return [
            {
                "id": "broken",
                "available": True,
                "base_url": "broken://",
                "models": [{"id": "bad"}],
            },
            {
                "id": "healthy",
                "available": True,
                "base_url": "healthy://",
                "models": [{"id": "good"}],
            },
        ]

    def model_call(self, run_id, choice, messages, cancel):
        if choice.provider == "broken":
            raise RuntimeError("provider unavailable")
        return '{"summary":"ok","actions":[],"done":true}'


class RetryingCodexRuntime(AgentRuntime):
    def __init__(self, workspace: Path, database: Path):
        super().__init__(
            workspace,
            lambda: database,
            lambda *_: {},
            lambda label: {"label": label},
        )
        self.calls = 0

    def provider_candidates(self, requested="auto", excluded=None):
        return [
            ProviderChoice(
                provider="openai-codex",
                model="default",
                base_url="codex://local-cli",
            )
        ]

    def model_call(self, run_id, choice, messages, cancel):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("Codex stream ended unexpectedly")
        return '{"summary":"recovered","actions":[],"done":true}'


class PassiveRuntime(AgentRuntime):
    def execute_run(self, run_id, content, cancel):
        return None


class StreamResponse:
    def __init__(self, status: int, lines: Optional[list[str]] = None):
        self.response = httpx.Response(
            status,
            request=httpx.Request("POST", "http://127.0.0.1/chat/completions"),
            headers={"content-type": "text/event-stream"},
        )
        self.headers = self.response.headers
        self.lines = lines or []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def raise_for_status(self):
        self.response.raise_for_status()

    def iter_lines(self):
        return iter(self.lines)


class JsonResponse:
    def __init__(self, payload: dict, status: int = 200):
        self.payload = payload
        self.response = httpx.Response(
            status,
            request=httpx.Request("POST", "http://127.0.0.1/chat/completions"),
            headers={"content-type": "application/json"},
            json=payload,
        )
        self.headers = self.response.headers

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def raise_for_status(self):
        self.response.raise_for_status()

    def read(self):
        return json.dumps(self.payload).encode()


class FakeCodexProcess:
    def __init__(self):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": '{"summary":"fast","actions":[],"done":true}',
                    },
                }
            )
            + "\n"
        )
        self.returncode = 0
        self.poll_count = 0

    def poll(self):
        self.poll_count += 1
        return None if self.poll_count == 1 else self.returncode


class AgentRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.database = self.workspace / "agent.db"
        with sqlite3.connect(self.database) as connection:
            connection.executescript(SCHEMA)
            connection.execute("insert into conversations values (1, 'test', '', '')")
            connection.execute(
                """insert into agent_runs(
                    id, conversation_id, status, mode, permission, pursue_goal, model,
                    contract_json, context_json, summary, created_at, updated_at
                ) values (1, 1, 'queued', 'auto', 'workspace', 1, 'auto', ?, '{}', '', '', '')""",
                (json.dumps({"objective": "make a notepad app"}),),
            )

    def tearDown(self):
        self.temp.cleanup()

    def test_multi_file_app_refreshes_context_and_validates(self):
        runtime = ScriptedRuntime(
            self.workspace,
            self.database,
            [
                {
                    "summary": "Create app files",
                    "actions": [
                        {
                            "tool": "write_file",
                            "arguments": {
                                "path": "index.html",
                                "content": "<!doctype html><textarea id='note'></textarea><script src='app.js'></script>",
                            },
                        },
                        {
                            "tool": "write_file",
                            "arguments": {
                                "path": "app.js",
                                "content": "const n=document.querySelector('#note');n.value=localStorage.note||'';n.oninput=()=>localStorage.note=n.value;",
                            },
                        },
                    ],
                    "done": False,
                },
                {
                    "summary": "Ready",
                    "actions": [],
                    "validation": "node --check app.js",
                    "done": True,
                },
            ],
        )
        runtime.execute_run(1, "make a normal notepad app", threading.Event())
        run = runtime.get_run(1)
        self.assertEqual(run["status"], "completed")
        self.assertTrue((self.workspace / "index.html").exists())
        self.assertIn("Validation passed", run["summary"])
        self.assertEqual(detect_run_profiles(self.workspace)[0]["id"], "static:http")

    def test_quiet_model_after_edit_is_validated_and_completed_automatically(self):
        runtime = ScriptedRuntime(
            self.workspace,
            self.database,
            [
                {
                    "summary": "Writing the implementation",
                    "actions": [
                        {
                            "tool": "create_file",
                            "arguments": {
                                "path": "app.js",
                                "content": "console.log('done')\n",
                            },
                        }
                    ],
                    "done": False,
                },
                {
                    "summary": "The implementation is ready",
                    "actions": [],
                    "done": False,
                },
            ],
        )

        runtime.execute_run(1, "create app.js", threading.Event())

        run = runtime.get_run(1)
        self.assertEqual(run["status"], "completed")
        self.assertTrue(run["validation"]["passed"])
        self.assertEqual(run["changed_files"], ["app.js"])

    def test_repeated_unchanged_read_is_skipped_and_run_can_validate(self):
        (self.workspace / "app.js").write_text(
            "console.log('ready')\n",
            encoding="utf-8",
        )
        runtime = ScriptedRuntime(
            self.workspace,
            self.database,
            [
                {
                    "summary": "Inspect the entry file",
                    "actions": [
                        {"tool": "read_file", "arguments": {"path": "app.js"}}
                    ],
                    "done": False,
                },
                {
                    "summary": "Confirm the unchanged entry file",
                    "actions": [
                        {"tool": "read_file", "arguments": {"path": "app.js"}}
                    ],
                    "done": False,
                },
                {
                    "summary": "The existing app is ready",
                    "actions": [],
                    "validation": "node --check app.js",
                    "done": True,
                },
            ],
        )

        runtime.execute_run(1, "finish the existing app", threading.Event())

        run = runtime.get_run(1)
        self.assertEqual(run["status"], "completed")
        read_steps = [
            step for step in run["steps"]
            if step["title"] == "read_file"
        ]
        self.assertEqual(len(read_steps), 1)
        self.assertTrue(run["validation"]["passed"])

    def test_npm_run_profiles_keep_dev_and_start_distinct(self):
        (self.workspace / "package.json").write_text(
            json.dumps({
                "scripts": {
                    "dev": "vite",
                    "start": "vite preview",
                    "build": "vite build",
                }
            }),
            encoding="utf-8",
        )

        profiles = {profile["id"]: profile for profile in detect_run_profiles(self.workspace)}

        self.assertEqual(profiles["npm:dev"]["kind"], "dev")
        self.assertEqual(profiles["npm:start"]["kind"], "run")
        self.assertEqual(profiles["npm:build"]["kind"], "build")

    def test_transient_codex_failure_is_retried_without_user_continuation(self):
        runtime = RetryingCodexRuntime(self.workspace, self.database)

        content, choice = runtime.call_with_fallback(
            1,
            "openai-codex:default",
            [{"role": "user", "content": "decide"}],
            threading.Event(),
        )

        self.assertEqual(runtime.calls, 2)
        self.assertEqual(choice.provider, "openai-codex")
        self.assertIn("recovered", content)

    def test_large_file_read_returns_a_bounded_slice(self):
        target = self.workspace / "large.txt"
        target.write_text(
            "".join(f"line {index}\n" for index in range(20_000)),
            encoding="utf-8",
        )
        runtime = ScriptedRuntime(self.workspace, self.database, [])

        result = runtime.execute_tool(
            1,
            "read_file",
            {"path": "large.txt", "start_line": 10, "end_line": 20},
            threading.Event(),
        )

        self.assertTrue(result["has_more"])
        self.assertIsNone(result["total_lines"])
        self.assertIn("line 9", result["content"])
        self.assertNotIn("line 20", result["content"])

    def test_builtin_notepad_is_runnable_without_a_model_provider(self):
        runtime = ScriptedRuntime(self.workspace, self.database, [])
        completed = runtime.execute_builtin_notepad(
            1, "make a normal notepad app with save and delete", threading.Event()
        )
        self.assertTrue(completed)
        self.assertTrue((self.workspace / "index.html").exists())
        self.assertIn("localStorage", (self.workspace / "app.js").read_text(encoding="utf-8"))
        self.assertEqual(runtime.get_run(1)["status"], "completed")
        self.assertEqual(detect_run_profiles(self.workspace)[0]["id"], "static:http")

    def test_builtin_notepad_uses_a_fresh_folder_after_a_partial_attempt(self):
        (self.workspace / "package.json").write_text("{}", encoding="utf-8")
        partial = self.workspace / "notepad-app"
        partial.mkdir()
        (partial / "index.html").write_text("unfinished", encoding="utf-8")
        runtime = ScriptedRuntime(self.workspace, self.database, [])

        completed = runtime.execute_builtin_notepad(
            1, "make a notepad app", threading.Event()
        )

        self.assertTrue(completed)
        self.assertEqual(
            (partial / "index.html").read_text(encoding="utf-8"),
            "unfinished",
        )
        self.assertTrue((self.workspace / "notepad-app-2" / "index.html").exists())
        self.assertTrue((self.workspace / "notepad-app-2" / "app.js").exists())

    def test_completed_plan_can_continue_as_an_auto_run_without_duplicate_message(self):
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "insert into conversation_messages values (1, 1, 'user', 'build a notes app', '')"
            )
            connection.execute(
                """
                update agent_runs
                set status = 'completed', mode = 'plan', summary = '1. Inspect files\n2. Build app',
                    context_json = '{"active_file":"README.md"}'
                where id = 1
                """
            )
            connection.commit()
        runtime = StartCaptureRuntime(self.workspace, self.database, [])

        result = runtime.execute_approved_plan(1)

        self.assertEqual(result["args"][1], "build a notes app")
        self.assertEqual(result["args"][2], "auto")
        self.assertEqual(result["args"][5]["approved_plan"], "1. Inspect files\n2. Build app")
        self.assertEqual(result["kwargs"]["record_message"], False)

    def test_local_npm_install_is_allowed_but_global_install_is_not(self):
        runtime = ScriptedRuntime(self.workspace, self.database, [])
        self.assertFalse(runtime.requires_approval("workspace", "run_command", {"command": "npm install vite"}))
        self.assertFalse(
            runtime.requires_approval(
                "workspace",
                "run_command",
                {"command": "npm install && npm run build"},
            )
        )
        self.assertTrue(runtime.requires_approval("workspace", "run_command", {"command": "npm install -g vite"}))
        self.assertTrue(runtime.requires_approval("workspace", "run_command", {"command": "npm install vite && rm -rf src"}))

    def test_leading_slash_is_safely_rebased_into_workspace(self):
        runtime = ScriptedRuntime(self.workspace, self.database, [])
        path = runtime.safe_path("/notepad/index.html")
        self.assertEqual(path, (self.workspace / "notepad" / "index.html").resolve())

    def test_local_model_argument_aliases_are_normalized(self):
        self.assertEqual(
            AgentRuntime.normalize_tool_arguments(
                "write_file", {"filename": "index.html", "content": "hello"}
            )["path"],
            "index.html",
        )
        self.assertEqual(
            AgentRuntime.normalize_tool_arguments("run_command", {"cmd": "npm test"})[
                "command"
            ],
            "npm test",
        )

    def test_command_timeout_terminates_long_running_process(self):
        runtime = ScriptedRuntime(self.workspace, self.database, [])
        result = runtime.execute_tool(
            1,
            "run_command",
            {"command": "sleep 10", "timeout": 5},
            threading.Event(),
        )
        self.assertEqual(result["exit_code"], 124)
        self.assertTrue(result["timed_out"])

    def test_existing_files_require_read_then_surgical_patch(self):
        runtime = ScriptedRuntime(self.workspace, self.database, [])
        target = self.workspace / "app.js"
        target.write_text("const title = 'Old';\nconst keep = true;\n", encoding="utf-8")
        converted = runtime.execute_tool(
            1,
            "write_file",
            {"path": "app.js", "content": "const title = 'Old';\nconst keep = true;\n"},
            threading.Event(),
        )
        self.assertFalse(converted["changed"])
        runtime.execute_tool(1, "read_file", {"path": "app.js"}, threading.Event())
        result = runtime.execute_tool(
            1,
            "apply_patch",
            {"path": "app.js", "old_text": "'Old'", "new_text": "'New'"},
            threading.Event(),
        )
        self.assertTrue(result["patched"])
        self.assertEqual(
            target.read_text(encoding="utf-8"),
            "const title = 'New';\nconst keep = true;\n",
        )

    def test_patch_recovers_from_stale_file_version(self):
        runtime = ScriptedRuntime(self.workspace, self.database, [])
        target = self.workspace / "app.js"
        target.write_text("const value = 1;\n", encoding="utf-8")
        runtime.execute_tool(1, "read_file", {"path": "app.js"}, threading.Event())
        target.write_text("const value = 2;\n", encoding="utf-8")
        result = runtime.execute_tool(
            1,
            "apply_patch",
            {"path": "app.js", "old_text": "2", "new_text": "3"},
            threading.Event(),
        )
        self.assertTrue(result["changed"])
        self.assertEqual(target.read_text(encoding="utf-8"), "const value = 3;\n")

    def test_repeated_plan_is_stopped_and_trajectory_is_exportable(self):
        repeated = {
            "summary": "Try the same thing",
            "actions": [
                {
                    "tool": "write_file",
                    "arguments": {"path": "repeat.txt", "content": "same"},
                }
            ],
            "done": False,
        }
        runtime = ScriptedRuntime(
            self.workspace,
            self.database,
            [repeated] * 20,
        )
        runtime.execute_run(1, "repeat forever", threading.Event())
        run = runtime.get_run(1)
        self.assertEqual(run["status"], "needs_review")
        self.assertIn("stall recovery", run["summary"].lower())
        trajectory = runtime.trajectory(1)
        self.assertEqual(trajectory["run"]["id"], 1)
        self.assertEqual(trajectory["schema_version"], "1.0")

    def test_restart_marks_every_orphaned_active_state_interrupted(self):
        runtime = ScriptedRuntime(self.workspace, self.database, [])
        active_states = [
            "queued",
            "understanding",
            "planning",
            "working",
            "testing",
            "waiting_for_approval",
            "paused",
            "stopping",
        ]
        with sqlite3.connect(self.database) as connection:
            for run_id, status in enumerate(active_states, start=1):
                if run_id == 1:
                    connection.execute(
                        "update agent_runs set status = ? where id = 1",
                        (status,),
                    )
                else:
                    connection.execute(
                        """insert into agent_runs(
                            id, conversation_id, status, mode, permission, pursue_goal, model,
                            contract_json, context_json, summary, created_at, updated_at
                        ) values (?, 1, ?, 'auto', 'workspace', 1, 'auto', '{}', '{}', '', '', '')""",
                        (run_id, status),
                    )
            connection.execute(
                """insert into agent_runs(
                    id, conversation_id, status, mode, permission, pursue_goal, model,
                    contract_json, context_json, summary, created_at, updated_at
                ) values (99, 1, 'completed', 'auto', 'workspace', 1, 'auto', '{}', '{}', '', '', '')"""
            )
        self.assertEqual(runtime.recover_interrupted(), len(active_states))
        with sqlite3.connect(self.database) as connection:
            statuses = dict(connection.execute("select id, status from agent_runs"))
        self.assertTrue(all(statuses[run_id] == "interrupted" for run_id in range(1, 9)))
        self.assertEqual(statuses[99], "completed")

    def test_git_summary_has_offline_fallback(self):
        runtime = ScriptedRuntime(self.workspace, self.database, [])
        summary = runtime.generate_git_summary(
            "diff --git a/app.py b/app.py\n+++ b/app.py\n+print('ok')\n",
            "feat",
        )
        self.assertEqual(summary["source"], "deterministic")
        self.assertIn("app.py", summary["commit_message"])

    def test_plural_read_paths_expand_into_ordered_actions(self):
        actions = AgentRuntime.normalize_actions([
            {"tool": "read_file", "arguments": {"paths": ["a.js", "b.js"]}}
        ])
        self.assertEqual(
            actions,
            [
                {"tool": "read_file", "arguments": {"path": "a.js"}},
                {"tool": "read_file", "arguments": {"path": "b.js"}},
            ],
        )

    def test_read_files_expands_into_ordered_read_file_actions(self):
        actions = AgentRuntime.normalize_actions([
            {"tool": "read_files", "arguments": {"paths": ["index.html", "app.js"]}}
        ])
        self.assertEqual(
            actions,
            [
                {"tool": "read_file", "arguments": {"path": "index.html"}},
                {"tool": "read_file", "arguments": {"path": "app.js"}},
            ],
        )

    def test_read_files_accepts_a_single_path_alias(self):
        actions = AgentRuntime.normalize_actions([
            {"tool": "read_files", "arguments": {"path": "package.json"}}
        ])
        self.assertEqual(
            actions,
            [{"tool": "read_file", "arguments": {"path": "package.json"}}],
        )

    def test_missing_read_target_is_repaired_from_workspace_entry_files(self):
        (self.workspace / "package.json").write_text("{}", encoding="utf-8")
        (self.workspace / "main.js").write_text("console.log('ok')", encoding="utf-8")
        (self.workspace / "package-lock.json").write_text("{}", encoding="utf-8")
        runtime = ScriptedRuntime(self.workspace, self.database, [])

        actions = runtime.prepare_actions([
            {"tool": "read_files", "arguments": {}}
        ])

        self.assertEqual(
            actions,
            [
                {"tool": "read_file", "arguments": {"path": "package.json"}},
                {"tool": "read_file", "arguments": {"path": "main.js"}},
            ],
        )

    def test_codex_context_is_trimmed_without_an_extra_model_call(self):
        runtime = ScriptedRuntime(self.workspace, self.database, [])
        context = "a" * 60_000
        choice = type("Choice", (), {"provider": "openai-codex"})()

        trimmed = runtime.compress_context(1, choice, context, threading.Event())

        self.assertLessEqual(len(trimmed), 14_100)
        self.assertIn("trimmed locally", trimmed)

    def test_codex_model_call_uses_low_latency_decision_profile(self):
        runtime = AgentRuntime(
            self.workspace,
            lambda: self.database,
            lambda *_: {},
            lambda label: {"label": label},
        )
        process = FakeCodexProcess()
        with patch(
            "codex_core.agent_runtime.shutil.which",
            return_value="/opt/codex",
        ), patch(
            "codex_core.agent_runtime.subprocess.Popen",
            return_value=process,
        ) as popen, patch(
            "codex_core.agent_runtime.select.select",
            return_value=([process.stdout], [], []),
        ):
            result = runtime.model_call(
                1,
                ProviderChoice(
                    provider="openai-codex",
                    model="default",
                    base_url="codex://local-cli",
                ),
                [{"role": "user", "content": "Return one decision"}],
                threading.Event(),
            )

        command = popen.call_args.args[0]
        self.assertIn('model_reasoning_effort="low"', command)
        self.assertIn('model_reasoning_summary="none"', command)
        self.assertIn('model_verbosity="low"', command)
        self.assertIn('approval_policy="never"', command)
        self.assertIn("plugins", command)
        self.assertIn('web_search="disabled"', command)
        self.assertIn("project_doc_max_bytes=0", command)
        self.assertIn("--ignore-rules", command)
        self.assertEqual(result, '{"summary":"fast","actions":[],"done":true}')

    def test_claude_status_uses_official_auth_command(self):
        runtime = ScriptedRuntime(self.workspace, self.database, [])
        completed = SimpleNamespace(
            returncode=0,
            stdout="Logged in as developer@example.com",
            stderr="",
        )

        with patch(
            "codex_core.agent_runtime.claude_executable",
            return_value="/opt/claude",
        ), patch(
            "codex_core.agent_runtime.subprocess.run",
            return_value=completed,
        ) as run:
            status = runtime.claude_status(refresh=True)

        self.assertTrue(status["installed"])
        self.assertTrue(status["authenticated"])
        run.assert_called_once()
        self.assertEqual(
            run.call_args.args[0],
            ["/opt/claude", "auth", "status", "--text"],
        )
        self.assertEqual(
            Path(run.call_args.kwargs["cwd"]).resolve(),
            self.workspace.resolve(),
        )
        self.assertTrue(run.call_args.kwargs["capture_output"])
        self.assertTrue(run.call_args.kwargs["text"])
        self.assertEqual(run.call_args.kwargs["timeout"], 8)

    def test_claude_model_call_disables_cli_tools(self):
        runtime = AgentRuntime(
            self.workspace,
            lambda: self.database,
            lambda *_: {},
            lambda label: {"label": label},
        )
        completed = SimpleNamespace(
            returncode=0,
            pid=12345,
            poll=lambda: 0,
            communicate=lambda: (
                json.dumps(
                    {"result": '{"summary":"Reviewed","actions":[],"done":true}'}
                ),
                "",
            ),
        )

        with patch(
            "codex_core.agent_runtime.claude_executable",
            return_value="/opt/claude",
        ), patch(
            "codex_core.agent_runtime.subprocess.Popen",
            return_value=completed,
        ) as popen:
            result = runtime.model_call(
                1,
                ProviderChoice(
                    provider="anthropic-claude-code",
                    model="sonnet",
                    base_url="claude-code://local-cli",
                ),
                [{"role": "user", "content": "Review this workspace"}],
                threading.Event(),
            )

        command = popen.call_args.args[0]
        self.assertEqual(result, '{"summary":"Reviewed","actions":[],"done":true}')
        self.assertIn("--permission-mode", command)
        self.assertIn("plan", command)
        self.assertIn("--tools", command)
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertIn("--no-session-persistence", command)

    def test_web_research_requires_clear_user_approval(self):
        runtime = ScriptedRuntime(self.workspace, self.database, [])
        arguments = {"url": "https://example.com/research"}

        self.assertTrue(runtime.requires_approval("full-access", "fetch_url", arguments))
        approval = runtime.create_approval(1, "fetch_url", arguments)

        self.assertEqual(
            approval["summary"],
            "Allow Kodex to visit example.com and gather data?",
        )
        self.assertEqual(approval["details"]["url"], arguments["url"])

    def test_fetch_url_extracts_readable_text(self):
        runtime = ScriptedRuntime(self.workspace, self.database, [])

        class Response:
            headers = {"content-type": "text/html; charset=utf-8"}
            encoding = "utf-8"
            url = "https://example.com/research"

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def raise_for_status(self):
                return None

            def iter_bytes(self):
                yield b"<html><body><h1>Research title</h1>"
                yield b"<script>ignore()</script><p>Useful data.</p></body></html>"

        with patch(
            "codex_core.agent_runtime.socket.getaddrinfo",
            return_value=[(None, None, None, None, ("93.184.216.34", 443))],
        ), patch(
            "codex_core.agent_runtime.httpx.stream",
            return_value=Response(),
        ):
            result = runtime.execute_tool(
                1,
                "fetch_url",
                {"url": "https://example.com/research"},
                threading.Event(),
            )

        self.assertIn("Research title", result["text"])
        self.assertIn("Useful data.", result["text"])
        self.assertNotIn("ignore()", result["text"])
        self.assertEqual(result["source_host"], "example.com")

    def test_fetch_url_returns_partial_text_when_page_exceeds_limit(self):
        runtime = ScriptedRuntime(self.workspace, self.database, [])

        class Response:
            headers = {"content-type": "text/plain; charset=utf-8"}
            encoding = "utf-8"
            url = "https://example.com/large-research"

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def raise_for_status(self):
                return None

            def iter_bytes(self):
                yield b"A" * 150_000
                yield b"B" * 100_000

        with patch(
            "codex_core.agent_runtime.socket.getaddrinfo",
            return_value=[(None, None, None, None, ("93.184.216.34", 443))],
        ), patch(
            "codex_core.agent_runtime.httpx.stream",
            return_value=Response(),
        ):
            result = runtime.execute_tool(
                1,
                "fetch_url",
                {"url": "https://example.com/large-research", "max_bytes": 200_000},
                threading.Event(),
            )

        self.assertEqual(result["bytes"], 200_000)
        self.assertTrue(result["truncated"])
        self.assertIn("first 200,000 bytes", result["truncation_reason"])
        self.assertTrue(result["text"].startswith("A"))

    def test_plural_read_then_edit_executes_against_the_existing_file(self):
        target = self.workspace / "app.js"
        target.write_text("const title = 'Old';\nconst keep = true;\n", encoding="utf-8")
        runtime = ScriptedRuntime(
            self.workspace,
            self.database,
            [
                {
                    "summary": "Inspect and update the app",
                    "actions": [
                        {"tool": "read_file", "arguments": {"paths": ["app.js"]}},
                        {
                            "tool": "edit_file",
                            "arguments": {
                                "path": "app.js",
                                "edits": [{"old_text": "'Old'", "new_text": "'New'"}],
                            },
                        },
                    ],
                    "done": False,
                },
                {"summary": "The requested edit is complete", "actions": [], "done": True},
            ],
        )

        runtime.execute_run(1, "rename the app title", threading.Event())

        self.assertEqual(runtime.get_run(1)["status"], "completed")
        self.assertEqual(
            target.read_text(encoding="utf-8"),
            "const title = 'New';\nconst keep = true;\n",
        )

    def test_missing_and_placeholder_paths_return_structured_errors(self):
        with self.assertRaises(ToolInputError) as missing:
            AgentRuntime.validate_tool_arguments("read_file", {})
        self.assertEqual(missing.exception.details["code"], "missing_field")
        with self.assertRaises(ToolInputError) as placeholder:
            AgentRuntime.validate_tool_arguments("read_file", {"path": "<path>"})
        self.assertEqual(placeholder.exception.details["code"], "placeholder_value")

    def test_line_range_edits_preserve_unrelated_sections(self):
        runtime = ScriptedRuntime(self.workspace, self.database, [])
        target = self.workspace / "app.js"
        target.write_text("first\nchange me\nkeep me\n", encoding="utf-8")
        result = runtime.execute_tool(
            1,
            "edit_file",
            {"path": "app.js", "edits": [{"start_line": 2, "end_line": 2, "new_text": "changed"}]},
            threading.Event(),
        )
        self.assertTrue(result["changed"])
        self.assertEqual(target.read_text(encoding="utf-8"), "first\nchanged\nkeep me\n")
        self.assertEqual(result["changed_ranges"], [{"start_line": 2, "end_line": 2}])

    def test_prose_wrapped_command_is_sanitized(self):
        arguments = AgentRuntime.validate_tool_arguments(
            "run_command", {"command": "Run 'npm test'"}
        )
        self.assertEqual(arguments["command"], "npm test")

    def test_full_access_accepts_an_explicit_external_path(self):
        runtime = ScriptedRuntime(self.workspace, self.database, [])
        external = Path(self.temp.name).parent / f"{self.workspace.name}-external.txt"
        self.assertEqual(runtime.safe_path(str(external), "full-access"), external.resolve())

    def test_full_access_still_requires_approval_for_sensitive_operations(self):
        runtime = ScriptedRuntime(self.workspace, self.database, [])
        self.assertTrue(
            runtime.requires_approval(
                "full-access",
                "edit_file",
                {"path": "/tmp/project/app.js", "edits": [{"old_text": "a", "new_text": "b"}]},
            )
        )
        self.assertTrue(
            runtime.requires_approval(
                "full-access",
                "edit_file",
                {"path": "/tmp/project/.env", "edits": [{"old_text": "a", "new_text": "b"}]},
            )
        )
        self.assertTrue(
            runtime.requires_approval(
                "full-access", "delete_file", {"path": "/tmp/project"}
            )
        )
        self.assertTrue(
            runtime.requires_approval(
                "full-access", "run_command", {"command": "sudo chmod 777 /etc"}
            )
        )

    def test_validation_uses_the_nearest_nested_project_and_available_script(self):
        project = self.workspace / "notepad-app"
        project.mkdir()
        (project / "package.json").write_text(
            json.dumps({"scripts": {"test": "node --check app.js"}}),
            encoding="utf-8",
        )
        (project / "app.js").write_text("const note = 'ok';\n", encoding="utf-8")
        runtime = ScriptedRuntime(self.workspace, self.database, [])

        validations = runtime.inferred_validations(["notepad-app/app.js"])

        self.assertEqual(validations, [("npm run test", "notepad-app")])

    def test_retry_with_repair_preserves_failure_context_without_duplicate_message(self):
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "insert into conversation_messages values (1, 1, 'user', 'repair the app', '')"
            )
            connection.execute(
                """
                update agent_runs
                set status = 'needs_review', failure_code = 'no_progress',
                    failure_details_json = '{"code":"no_progress","message":"same action"}',
                    changed_files_json = '["app.js"]'
                where id = 1
                """
            )
            connection.commit()
        runtime = StartCaptureRuntime(self.workspace, self.database, [])

        result = runtime.retry_with_repair(1, "repair")

        self.assertEqual(result["args"][1], "repair the app")
        self.assertEqual(result["args"][2], "auto")
        self.assertEqual(result["args"][5]["retry_of_run_id"], 1)
        self.assertEqual(result["args"][5]["previous_failure"]["code"], "no_progress")
        self.assertEqual(result["kwargs"]["record_message"], False)

    def test_provider_failure_falls_back_and_records_attempts(self):
        runtime = FallbackRuntime(
            self.workspace,
            lambda: self.database,
            lambda *_: {},
            lambda label: {"label": label},
        )
        content, choice = runtime.call_with_fallback(
            1,
            "auto",
            [{"role": "user", "content": "test"}],
            threading.Event(),
        )
        self.assertEqual(choice.provider, "healthy")
        self.assertIn('"summary":"ok"', content)
        attempts = runtime.get_run(1)["provider_attempts"]
        self.assertEqual(
            [(item["provider"], item["status"]) for item in attempts],
            [
                ("broken", "running"),
                ("broken", "failed"),
                ("healthy", "running"),
                ("healthy", "completed"),
            ],
        )

    def test_lm_studio_discovery_uses_configured_url_and_capabilities(self):
        runtime = ScriptedRuntime(self.workspace, self.database, [])
        lm_response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "data": [
                    {
                        "id": "hermes-4-local",
                        "context_length": 32768,
                    }
                ]
            },
        )

        def fake_get(url, **_kwargs):
            if url.endswith("/api/tags"):
                raise httpx.ConnectError("offline")
            if url == "http://127.0.0.1:4321/v1/models":
                return lm_response
            raise AssertionError(url)

        with patch.dict(os.environ, {"CODEX_LM_STUDIO_BASE_URL": "http://127.0.0.1:4321/v1"}):
            with patch("codex_core.agent_runtime.httpx.get", side_effect=fake_get):
                with patch.object(runtime, "codex_status", return_value={"authenticated": False, "message": "offline"}):
                    with patch.object(runtime, "claude_status", return_value={"authenticated": False, "message": "offline"}):
                        provider = next(item for item in runtime.providers() if item["id"] == "lm-studio")

        self.assertTrue(provider["available"])
        self.assertEqual(provider["health"], "ready")
        self.assertEqual(provider["models"][0]["context_length"], 32768)
        self.assertIn("structured-output", provider["capabilities"])

    def test_explicit_provider_rejects_an_unloaded_model(self):
        runtime = AgentRuntime(
            self.workspace,
            lambda: self.database,
            lambda *_: {},
            lambda label: {"label": label},
        )
        with patch.object(
            runtime,
            "providers",
            return_value=[
                {
                    "id": "lm-studio",
                    "name": "LM Studio",
                    "available": True,
                    "base_url": "http://127.0.0.1:1234/v1",
                    "capabilities": ["chat"],
                    "models": [{"id": "loaded-model"}],
                }
            ],
        ):
            with self.assertRaisesRegex(RuntimeError, "not loaded or available"):
                runtime.select_provider("lm-studio:missing-model")

    def test_openai_compatible_retries_without_response_format(self):
        runtime = AgentRuntime(
            self.workspace,
            lambda: self.database,
            lambda *_: {},
            lambda label: {"label": label},
        )
        first = StreamResponse(400)
        second = StreamResponse(
            200,
            [
                'data: {"choices":[{"delta":{"content":"{\\"summary\\":\\"ok\\","}}]}',
                'data: {"choices":[{"delta":{"content":"\\"actions\\":[],\\"done\\":true}"}}]}',
                "data: [DONE]",
            ],
        )
        choice = ProviderChoice(
            provider="lm-studio",
            model="local",
            base_url="http://127.0.0.1:1234/v1",
        )
        with patch("codex_core.agent_runtime.httpx.stream", side_effect=[first, second]) as stream:
            content = runtime.model_call(
                1,
                choice,
                [{"role": "user", "content": "Return JSON"}],
                threading.Event(),
            )

        self.assertIn('"summary":"ok"', content)
        self.assertIn("response_format", stream.call_args_list[0].kwargs["json"])
        self.assertNotIn("response_format", stream.call_args_list[1].kwargs["json"])

    def test_lm_studio_missing_choices_returns_structured_error(self):
        runtime = AgentRuntime(
            self.workspace,
            lambda: self.database,
            lambda *_: {},
            lambda label: {"label": label},
        )
        choice = ProviderChoice(
            provider="lm-studio",
            model="local",
            base_url="http://127.0.0.1:1234/v1",
        )
        response = JsonResponse({"error": {"message": "No model loaded"}})

        with patch("codex_core.agent_runtime.httpx.stream", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "No model loaded") as raised:
                runtime.model_call(
                    1,
                    choice,
                    [{"role": "user", "content": "Return JSON"}],
                    threading.Event(),
                )

        self.assertEqual(raised.exception.details["code"], "model_unloaded")
        self.assertTrue(raised.exception.details["retryable"])

    def test_lm_studio_context_overflow_retries_with_smaller_prompt(self):
        runtime = AgentRuntime(
            self.workspace,
            lambda: self.database,
            lambda *_: {},
            lambda label: {"label": label},
        )
        choice = ProviderChoice(
            provider="lm-studio",
            model="local",
            base_url="http://127.0.0.1:1234/v1",
            context_length=8192,
        )
        overflow = JsonResponse(
            {
                "error": {
                    "message": (
                        "The number of tokens to keep from the initial prompt "
                        "is greater than the context length."
                    )
                }
            },
            status=400,
        )
        success = StreamResponse(
            200,
            [
                'data: {"choices":[{"delta":{"content":"{\\"summary\\":\\"ok\\",\\"actions\\":[],\\"done\\":true}"}}]}',
                "data: [DONE]",
            ],
        )
        messages = [
            {"role": "system", "content": "system rules " * 2500},
            {"role": "user", "content": "workspace context " * 2500},
        ]

        with patch(
            "codex_core.agent_runtime.httpx.stream",
            side_effect=[overflow, success],
        ) as stream:
            content = runtime.model_call(
                1,
                choice,
                messages,
                threading.Event(),
            )

        first_messages = stream.call_args_list[0].kwargs["json"]["messages"]
        second_messages = stream.call_args_list[1].kwargs["json"]["messages"]
        self.assertIn('"summary":"ok"', content)
        self.assertLess(
            sum(len(item["content"]) for item in second_messages),
            sum(len(item["content"]) for item in first_messages),
        )
        self.assertIn("response_format", stream.call_args_list[1].kwargs["json"])

    def test_context_overflow_message_is_actionable(self):
        choice = ProviderChoice(
            provider="lm-studio",
            model="local",
            base_url="http://127.0.0.1:1234/v1",
        )
        error = AgentRuntime.provider_error_from_payload(
            choice,
            {
                "error": {
                    "message": "The prompt exceeds the model context length."
                }
            },
        )

        self.assertEqual(error.details["code"], "context_overflow")
        self.assertIn("Retry with reduced context", error.details["message"])

    def test_openai_stream_ignores_usage_and_role_only_chunks(self):
        runtime = AgentRuntime(
            self.workspace,
            lambda: self.database,
            lambda *_: {},
            lambda label: {"label": label},
        )
        choice = ProviderChoice(
            provider="lm-studio",
            model="local",
            base_url="http://127.0.0.1:1234/v1",
        )
        response = StreamResponse(
            200,
            [
                'data: {"choices":[{"delta":{"role":"assistant"}}]}',
                'data: {"usage":{"prompt_tokens":12},"choices":[]}',
                'data: {"choices":[{"delta":{"content":"{\\"summary\\":\\"ok\\",\\"actions\\":[],\\"done\\":true}"}}]}',
                "data: [DONE]",
            ],
        )

        with patch("codex_core.agent_runtime.httpx.stream", return_value=response):
            content = runtime.model_call(
                1,
                choice,
                [{"role": "user", "content": "Return JSON"}],
                threading.Event(),
            )

        self.assertIn('"summary":"ok"', content)

    def test_empty_provider_stream_is_rejected(self):
        runtime = AgentRuntime(
            self.workspace,
            lambda: self.database,
            lambda *_: {},
            lambda label: {"label": label},
        )
        choice = ProviderChoice(
            provider="lm-studio",
            model="local",
            base_url="http://127.0.0.1:1234/v1",
        )
        response = StreamResponse(200, ["data: [DONE]"])

        with patch("codex_core.agent_runtime.httpx.stream", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "without returning content"):
                runtime.model_call(
                    1,
                    choice,
                    [{"role": "user", "content": "Return JSON"}],
                    threading.Event(),
                )

    def test_cancel_terminates_registered_operations_and_is_idempotent(self):
        runtime = ScriptedRuntime(self.workspace, self.database, [])
        terminated: list[str] = []
        runtime.register_operation(1, "model_stream", lambda: terminated.append("stream"))
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """
                insert into agent_steps(
                    run_id, kind, status, title, data_json, created_at, updated_at
                ) values (1, 'thinking', 'running', 'Thinking', '{}', '', '')
                """
            )
            connection.execute(
                """
                insert into approvals(
                    run_id, kind, status, summary, details_json, created_at, updated_at
                ) values (1, 'run_command', 'pending', 'Allow', '{}', '', '')
                """
            )
            connection.commit()

        first = runtime.cancel(1)
        second = runtime.cancel(1)

        self.assertEqual(terminated, ["stream"])
        self.assertEqual(first["status"], "cancelled")
        self.assertEqual(first["terminated_operations"], ["model_stream"])
        self.assertFalse(first["already_terminal"])
        self.assertTrue(second["already_terminal"])
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(
                connection.execute("select status from approvals").fetchone()[0],
                "rejected",
            )
            self.assertEqual(
                connection.execute("select status from agent_steps").fetchone()[0],
                "cancelled",
            )

    def test_terminal_run_ignores_late_completion(self):
        runtime = ScriptedRuntime(self.workspace, self.database, [])
        runtime.cancel(1)

        runtime.complete_run(1, "Late success")

        run = runtime.get_run(1)
        self.assertEqual(run["status"], "cancelled")
        self.assertNotEqual(run["summary"], "Late success")

    def test_plain_text_plan_has_deterministic_repair(self):
        repaired = AgentRuntime.repair_plan(
            "- Inspect the runtime\n- Add durable state\n- Run tests",
            "improve the agent",
        )
        self.assertTrue(repaired["done"])
        self.assertEqual(len(repaired["tasks"]), 3)
        self.assertIn("1. Inspect the runtime", repaired["summary"])

    def test_natural_plan_intent_distinguishes_plan_only_and_execution(self):
        plan_only = AgentRuntime.resolve_request_intent(
            "Make a detailed plan for improving the terminal", "auto"
        )
        plan_then_execute = AgentRuntime.resolve_request_intent(
            "Make a plan and then implement it", "auto"
        )
        execute = AgentRuntime.resolve_request_intent(
            "Fix the terminal and run the tests", "auto"
        )

        self.assertEqual(plan_only["resolved_intent"], "plan_only")
        self.assertEqual(
            plan_then_execute["resolved_intent"], "plan_then_execute"
        )
        self.assertEqual(execute["resolved_intent"], "execute")

    def test_start_persists_resolved_natural_intent(self):
        runtime = PassiveRuntime(
            self.workspace,
            lambda: self.database,
            lambda *_: {},
            lambda label: {"label": label},
        )

        run = runtime.start(
            1,
            "Make a plan and do it",
            "auto",
            True,
            "workspace",
            {},
            "auto",
        )

        self.assertEqual(run["mode"], "auto")
        self.assertEqual(run["requested_mode"], "auto")
        self.assertEqual(run["resolved_intent"], "plan_then_execute")
        self.assertGreater(run["intent_confidence"], 0.9)

    def test_plan_then_execute_persists_plan_and_completes_work(self):
        runtime = ScriptedRuntime(
            self.workspace,
            self.database,
            [
                {
                    "objective": "Create a checked JavaScript file",
                    "tasks": ["Inspect the workspace", "Create app.js", "Validate syntax"],
                    "acceptance_criteria": ["app.js exists", "Syntax validation passes"],
                    "affected_areas": ["app.js"],
                    "risks": [],
                    "validation_strategy": "Run node --check app.js",
                    "summary": "1. Inspect the workspace\n2. Create app.js\n3. Validate syntax",
                    "actions": [],
                    "done": True,
                },
                {
                    "summary": "Creating the planned file",
                    "actions": [
                        {
                            "tool": "create_file",
                            "arguments": {
                                "path": "app.js",
                                "content": "console.log('Kodex plan complete')\n",
                            },
                        }
                    ],
                    "done": False,
                },
                {
                    "summary": "Implementation is ready",
                    "actions": [],
                    "validation": "node --check app.js",
                    "done": True,
                },
            ],
        )
        with sqlite3.connect(self.database) as connection:
            contract = {
                "objective": "Make a plan and do it",
                "acceptance_criteria": ["app.js exists", "Validation passes"],
                "intent_types": ["feature_add"],
                "resolved_intent": "plan_then_execute",
            }
            connection.execute(
                """
                update agent_runs
                set contract_json = ?, resolved_intent = 'plan_then_execute'
                where id = 1
                """,
                (json.dumps(contract),),
            )
            connection.commit()

        runtime.execute_run(1, "Make a plan and do it", threading.Event())

        run = runtime.get_run(1)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["plan"]["tasks"][1], "Create app.js")
        self.assertTrue((self.workspace / "app.js").exists())
        self.assertTrue(run["validation"]["passed"])

    def test_interrupted_run_continues_from_checkpoint(self):
        runtime = StartCaptureRuntime(self.workspace, self.database, [])
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """
                update agent_runs
                set status = 'interrupted',
                    goal_json = '{"objective":"repair the app"}',
                    checkpoint_json = '{"phase":"repair","changed_files":["app.js"]}'
                where id = 1
                """
            )
            connection.commit()

        result = runtime.continue_interrupted(1)

        self.assertEqual(result["args"][1], "repair the app")
        self.assertFalse(result["kwargs"]["record_message"])
        self.assertEqual(result["kwargs"]["resume_state"]["changed_files"], ["app.js"])

    def test_active_run_accepts_and_consumes_steering(self):
        runtime = ScriptedRuntime(self.workspace, self.database, [])
        queued = runtime.queue_steering(1, "also update the tests")
        self.assertEqual(queued["status"], "queued")
        self.assertEqual(runtime.consume_steering(1), ["also update the tests"])
        self.assertEqual(runtime.get_run(1)["steering_messages"][0]["status"], "consumed")

    def test_image_steering_is_merged_into_active_run_context(self):
        runtime = ScriptedRuntime(self.workspace, self.database, [])
        image = {
            "name": "screen.png",
            "path": "dropped:screen.png",
            "kind": "image",
            "mimeType": "image/png",
            "content": "",
            "dataUrl": "data:image/png;base64,aGVsbG8=",
        }

        runtime.queue_steering(1, "inspect this screenshot", [image])
        self.assertEqual(runtime.consume_steering(1), ["inspect this screenshot"])

        images = runtime.image_attachments_for_run(1)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["mime_type"], "image/png")

    def test_provider_messages_encode_images_for_openai_and_ollama(self):
        images = [
            {
                "name": "screen.png",
                "mime_type": "image/png",
                "data_url": "data:image/png;base64,aGVsbG8=",
                "base64": "aGVsbG8=",
            }
        ]
        messages = [
            {"role": "system", "content": "Return JSON."},
            {"role": "user", "content": "What is shown?"},
        ]

        openai_messages = AgentRuntime.messages_with_images(
            messages, images, "openai"
        )
        ollama_messages = AgentRuntime.messages_with_images(
            messages, images, "ollama"
        )

        self.assertEqual(
            openai_messages[-1]["content"][1]["image_url"]["url"],
            images[0]["data_url"],
        )
        self.assertEqual(ollama_messages[-1]["images"], ["aGVsbG8="])

    def test_profile_memory_and_mcp_are_connected_to_runtime_context(self):
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """
                insert into agent_profiles values(
                    4, 'workspace-engineer', 2, 'active', 'Workspace Engineer',
                    'coder', 'Inspect relevant files before editing.',
                    'Preserve user changes.', '[{"action":"inspect"},{"action":"test"}]',
                    '{"allow":["read_file","mcp"]}', '', ''
                )
                """
            )
            connection.execute(
                """
                insert into structured_memories values(
                    1, 'project', ?, 'Testing convention',
                    'Always run the nearest unit tests.', '["tests"]', 1.0,
                    'active', 'user', '', ''
                )
                """,
                (str(self.workspace),),
            )
            connection.execute(
                """
                insert into mcp_servers values(
                    3, 'docs', '["docs-server"]', '{}', 'read-only',
                    1, 'available', '', '', ''
                )
                """
            )
            connection.commit()
        runtime = ScriptedRuntime(self.workspace, self.database, [])
        profile = runtime.resolve_agent_profile(None)
        overview = runtime.workspace_overview(
            {}, "fix tests", conversation_id=1, profile=profile
        )

        self.assertEqual(profile["version"], 2)
        self.assertIn("Always run the nearest unit tests", overview)
        self.assertIn("Workspace Engineer", overview)
        self.assertIn("docs", overview)
        self.assertIn("mcp__3", {tool["id"] for tool in runtime.tool_registry()})

    def test_command_policy_distinguishes_workspace_and_sensitive_actions(self):
        self.assertEqual(
            AgentRuntime.classify_command("npm install vite && npm run build"),
            "workspace-safe",
        )
        self.assertEqual(
            AgentRuntime.classify_command(
                "node -e \"JSON.parse(require('fs').readFileSync("
                "'package.json','utf8')); console.log('valid')\" "
                "&& npm run check && node --check renderer.js"
            ),
            "workspace-safe",
        )
        self.assertEqual(
            AgentRuntime.classify_command("git status"),
            "read-only",
        )
        self.assertEqual(
            AgentRuntime.classify_command("npm install -g vite"),
            "sensitive",
        )

    def test_delete_conversation_removes_messages_and_run_records(self):
        runtime = ScriptedRuntime(self.workspace, self.database, [])
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "insert into conversation_messages values (2, 1, 'assistant', 'done', '')"
            )
            connection.execute(
                "insert into agent_steps(run_id, kind, status, title, data_json, created_at, updated_at) values (1, 'thinking', 'completed', 'Done', '{}', '', '')"
            )
            connection.execute("update agent_runs set status = 'completed' where id = 1")
            connection.commit()

        self.assertEqual(runtime.delete_conversation(1), {"deleted": True, "id": 1})

        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute("select count(*) from conversations").fetchone()[0], 0)
            self.assertEqual(connection.execute("select count(*) from conversation_messages").fetchone()[0], 0)
            self.assertEqual(connection.execute("select count(*) from agent_runs").fetchone()[0], 0)
            self.assertEqual(connection.execute("select count(*) from agent_steps").fetchone()[0], 0)

    def test_delete_conversation_rejects_active_run(self):
        runtime = ScriptedRuntime(self.workspace, self.database, [])
        with self.assertRaisesRegex(ValueError, "Stop the active agent run"):
            runtime.delete_conversation(1)

    def test_start_persists_goal_tasks_and_selected_profile(self):
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """
                insert into agent_profiles values(
                    7, 'builder', 3, 'active', 'Builder', 'coder',
                    'Build complete changes.', 'Validate before completion.',
                    '[{"action":"inspect"},{"action":"implement"},{"action":"test"}]',
                    '{"allow":["read_file","create_file","run_command"]}', '', ''
                )
                """
            )
            connection.commit()
        runtime = PassiveRuntime(
            self.workspace,
            lambda: self.database,
            lambda *_: {},
            lambda label: {"label": label},
        )

        run = runtime.start(
            1, "build the feature", "auto", True, "workspace", {}, "auto"
        )

        self.assertEqual(run["goal"]["objective"], "build the feature")
        self.assertEqual(run["agent_profile_id"], 7)
        self.assertEqual(
            [task["title"] for task in run["tasks"]],
            ["Inspect", "Implement", "Test"],
        )

    def test_ranged_read_returns_requested_lines(self):
        target = self.workspace / "large.txt"
        target.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
        runtime = ScriptedRuntime(self.workspace, self.database, [])

        result = runtime.execute_tool(
            1,
            "read_file",
            {"path": "large.txt", "start_line": 2, "end_line": 3},
            threading.Event(),
        )

        self.assertEqual(result["content"], "two\nthree\n")
        self.assertEqual((result["start_line"], result["end_line"]), (2, 3))


if __name__ == "__main__":
    unittest.main()
