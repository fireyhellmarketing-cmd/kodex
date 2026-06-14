import tempfile
import unittest
from pathlib import Path

from codex_core.designer import DesignerService


class DesignerServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.events = []
        self.service = DesignerService(
            self.workspace,
            lambda event, data: self.events.append((event, data)),
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_react_source_mapping_and_byte_exact_undo(self) -> None:
        source = (
            "export function App() {\n"
            '  return <main className="app"><button type="button">Create</button></main>\n'
            "}\n"
        )
        path = self.workspace / "App.tsx"
        path.write_text(source, encoding="utf-8")
        document = self.service.parse_document("App.tsx")
        button = next(node for node in document["nodes"] if node["type"] == "button")
        self.assertEqual(button["source"]["start_line"], 2)

        result = self.service.apply(
            {
                "path": "App.tsx",
                "operation": "update",
                "node_id": button["id"],
                "property": "aria-label",
                "value": "Create project",
                "expected_hash": document["hash"],
            }
        )
        self.assertIn('aria-label="Create project"', path.read_text(encoding="utf-8"))
        self.assertEqual(result["changed_files"], ["App.tsx"])

        self.service.undo()
        self.assertEqual(path.read_text(encoding="utf-8"), source)
        self.service.redo()
        self.assertIn('aria-label="Create project"', path.read_text(encoding="utf-8"))

    def test_delete_removes_complete_element_and_add_targets_root(self) -> None:
        path = self.workspace / "App.tsx"
        path.write_text(
            "export const App = () => <main><button>One</button></main>\n",
            encoding="utf-8",
        )
        document = self.service.parse_document("App.tsx")
        button = next(node for node in document["nodes"] if node["type"] == "button")
        deleted = self.service.apply(
            {
                "path": "App.tsx",
                "operation": "delete",
                "node_id": button["id"],
                "expected_hash": document["hash"],
            }
        )
        self.assertNotIn("<button", path.read_text(encoding="utf-8"))
        self.service.apply(
            {
                "path": "App.tsx",
                "operation": "add",
                "component": "button",
                "expected_hash": deleted["document"]["hash"],
            }
        )
        content = path.read_text(encoding="utf-8")
        self.assertLess(content.index("<button"), content.index("</main>"))

    def test_external_change_blocks_transaction(self) -> None:
        path = self.workspace / "App.tsx"
        path.write_text("export const App = () => <button>One</button>\n", encoding="utf-8")
        document = self.service.parse_document("App.tsx")
        button = document["nodes"][0]
        path.write_text("export const App = () => <button>Two</button>\n", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            self.service.apply(
                {
                    "path": "App.tsx",
                    "operation": "delete",
                    "node_id": button["id"],
                    "expected_hash": document["hash"],
                }
            )

    def test_detects_tiered_adapters(self) -> None:
        (self.workspace / "main.dart").write_text(
            "class App extends StatelessWidget { Widget build(context) => Scaffold(); }\n",
            encoding="utf-8",
        )
        (self.workspace / "ContentView.swift").write_text(
            "import SwiftUI\nstruct ContentView: View { var body: some View { Text(\"Hi\") } }\n",
            encoding="utf-8",
        )
        project = self.service.detect_project()
        self.assertEqual({item["id"] for item in project["adapters"]}, {"flutter", "swiftui"})


if __name__ == "__main__":
    unittest.main()
