import sqlite3
import tempfile
import unittest
from pathlib import Path

from codex_core.platform_services import PlatformServices


class PlatformServicesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.database = self.workspace / "core.sqlite3"
        self.events = []
        self.services = PlatformServices(
            self.workspace,
            lambda: self.database,
            lambda event, data: self.events.append((event, data)) or {},
        )
        self.services.init_schema()

    def tearDown(self):
        self.temp.cleanup()

    def test_structured_memory_conflicts_export_and_reset(self):
        first = self.services.create_memory(
            {
                "scope": "project",
                "owner_id": "workspace",
                "title": "Style",
                "content": "Use tabs",
                "tags": ["convention"],
            }
        )
        self.assertTrue(first["created"])
        conflict = self.services.create_memory(
            {
                "scope": "project",
                "owner_id": "workspace",
                "title": "Style",
                "content": "Use spaces",
            }
        )
        self.assertFalse(conflict["created"])
        resolved = self.services.create_memory(
            {
                "scope": "project",
                "owner_id": "workspace",
                "title": "Style",
                "content": "Use spaces",
                "resolve_conflicts": True,
            }
        )
        self.assertEqual(resolved["resolved_conflicts"], 1)
        self.assertEqual(len(self.services.export_memories()["memories"]), 2)
        self.assertEqual(self.services.compress_memories()["archived_duplicates"], 0)
        self.assertEqual(self.services.reset_memories("project")["deleted"], 2)

    def test_agent_profile_requires_validation_and_passing_evaluation(self):
        draft = self.services.save_agent_profile(
            {
                "agent_id": "reviewer",
                "name": "Reviewer",
                "role": "reviewer",
                "prompt": "Review every changed file for concrete regressions.",
                "constitution": "Never claim a finding without evidence.",
                "workflow": [{"action": "review"}],
                "tool_policy": {"allow": ["read_file", "search"]},
            }
        )
        self.assertTrue(self.services.validate_agent_profile(draft["id"])["valid"])
        with self.assertRaises(ValueError):
            self.services.promote_agent_profile(draft["id"], "active")
        self.services.record_evaluation(
            {
                "agent_id": "reviewer",
                "agent_version": 1,
                "fixture": "review-smoke",
                "score": 0.9,
            }
        )
        active = self.services.promote_agent_profile(draft["id"], "active")
        self.assertEqual(active["status"], "active")

    def test_git_state_diff_and_stage(self):
        self.services.git_mutation = self.services.git_mutation
        from subprocess import run

        run(["git", "init"], cwd=self.workspace, check=True, capture_output=True)
        (self.workspace / "note.txt").write_text("hello\n", encoding="utf-8")
        state = self.services.git_state()
        self.assertTrue(state["repository"])
        self.assertIn("note.txt", {item["path"] for item in state["changes"]})
        staged = self.services.git_mutation("stage", {"paths": ["note.txt"]})
        note = next(item for item in staged["state"]["changes"] if item["path"] == "note.txt")
        self.assertEqual(note["status"][0], "A")

    def test_diagnostics_mcp_and_database_backup(self):
        diagnostics = self.services.parse_diagnostics(
            "src/app.ts:12:4: Type 'string' is not assignable\nmain.py:9: SyntaxError"
        )
        self.assertEqual(len(diagnostics), 2)
        server = self.services.configure_mcp(
            {
                "name": "missing-example",
                "command": ["definitely-not-installed-codex-command"],
                "permission": "read-only",
            }
        )
        self.assertEqual(self.services.test_mcp(server["id"])["status"], "missing")
        with sqlite3.connect(self.database) as connection:
            connection.execute("create table if not exists marker(value text)")
            connection.execute("insert into marker values ('safe')")
        backup = self.services.backup_database()
        self.assertTrue(Path(backup["path"]).exists())
        self.assertEqual(len(self.services.list_backups()), 1)


if __name__ == "__main__":
    unittest.main()
