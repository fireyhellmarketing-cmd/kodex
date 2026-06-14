import json
import tempfile
import unittest
from pathlib import Path

from codex_core.project_intelligence import (
    analyze_workspace,
    discover_skills,
    load_project_state,
    set_skill_trust,
    update_project_state,
)


class ProjectIntelligenceTests(unittest.TestCase):
    def test_analysis_indexes_frameworks_symbols_imports_routes_and_state(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            (workspace / "src").mkdir()
            (workspace / "package.json").write_text(
                json.dumps(
                    {
                        "scripts": {"dev": "vite", "build": "vite build"},
                        "dependencies": {"react": "^19", "vite": "^7"},
                    }
                ),
                encoding="utf-8",
            )
            (workspace / "src" / "main.ts").write_text(
                "import React from 'react'\n"
                "export function App() { return null }\n"
                "app.get('/health', handler)\n",
                encoding="utf-8",
            )
            intelligence = analyze_workspace(workspace)
            self.assertIn("React", intelligence["frameworks"])
            self.assertTrue(any(item["name"] == "App" for item in intelligence["symbols"]))
            self.assertTrue(any(item["target"] == "react" for item in intelligence["imports"]))
            self.assertTrue(any(item["path"] == "/health" for item in intelligence["routes"]))
            self.assertIn("typescript", intelligence["tree_sitter"]["languages"])
            state = update_project_state(workspace, intelligence)
            self.assertEqual(load_project_state(workspace)["summary"], state["summary"])

    def test_project_skill_frontmatter_is_discovered(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            skill_dir = workspace / ".kodex-agent" / "skills"
            skill_dir.mkdir(parents=True)
            (skill_dir / "review.md").write_text(
                "---\nname: Review\ndescription: Review code carefully.\n---\n# Review\n",
                encoding="utf-8",
            )
            skills = discover_skills(workspace)
            self.assertEqual(skills[0]["name"], "Review")
            self.assertEqual(skills[0]["description"], "Review code carefully.")
            self.assertEqual(skills[0]["trust"], "review")
            trusted = set_skill_trust(workspace, skills[0]["id"], "trusted")
            self.assertEqual(trusted["trust"], "trusted")


if __name__ == "__main__":
    unittest.main()
