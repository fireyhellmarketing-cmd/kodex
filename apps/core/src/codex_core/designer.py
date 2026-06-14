from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


DESIGNER_EXTENSIONS = {
    ".html",
    ".htm",
    ".jsx",
    ".tsx",
    ".dart",
    ".xaml",
    ".kt",
    ".kts",
    ".swift",
}

DEVICE_PROFILES = [
    {"id": "responsive", "name": "Responsive", "width": 1180, "height": 760, "platform": "web"},
    {"id": "desktop", "name": "Desktop", "width": 1440, "height": 900, "platform": "desktop"},
    {"id": "iphone", "name": "iPhone", "width": 393, "height": 852, "platform": "ios"},
    {"id": "android", "name": "Android", "width": 412, "height": 915, "platform": "android"},
    {"id": "tablet", "name": "Tablet", "width": 820, "height": 1180, "platform": "mobile"},
]

COMMON_TOOLBOX = [
    ("window", "Window", "structure"),
    ("page", "Page", "structure"),
    ("row", "Row", "layout"),
    ("column", "Column", "layout"),
    ("grid", "Grid", "layout"),
    ("stack", "Stack", "layout"),
    ("card", "Card", "content"),
    ("button", "Button", "controls"),
    ("text", "Text", "controls"),
    ("input", "Input", "controls"),
    ("image", "Image", "media"),
    ("list", "List", "content"),
    ("navigation", "Navigation", "navigation"),
    ("dialog", "Dialog", "overlays"),
    ("menu", "Menu", "navigation"),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def line_column(content: str, offset: int) -> tuple[int, int]:
    line = content.count("\n", 0, offset) + 1
    previous = content.rfind("\n", 0, offset)
    return line, offset - previous


def serialize_props(raw: str) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for match in re.finditer(
        r"""([:@\w-]+)\s*=\s*(?:\{([^{}]*)\}|"([^"]*)"|'([^']*)')|([:@\w-]+)\b""",
        raw,
    ):
        name = match.group(1) or match.group(5)
        if not name or name in {"/"}:
            continue
        value = next((part for part in match.groups()[1:4] if part is not None), True)
        props[name] = value
    return props


@dataclass(frozen=True)
class Adapter:
    id: str
    name: str
    tier: str
    extensions: tuple[str, ...]
    markers: tuple[str, ...]
    node_pattern: re.Pattern[str]
    child_template: Callable[[str], str]

    def supports(self, path: Path, content: str) -> bool:
        if path.suffix.lower() not in self.extensions:
            return False
        return not self.markers or any(marker in content for marker in self.markers)


def web_template(kind: str) -> str:
    templates = {
        "window": '<main className="window">Window</main>',
        "page": '<section className="page">New page</section>',
        "row": '<div className="row"></div>',
        "column": '<div className="column"></div>',
        "grid": '<div className="grid"></div>',
        "stack": '<div className="stack"></div>',
        "card": '<article className="card">Card</article>',
        "button": '<button type="button">Button</button>',
        "text": "<p>Text</p>",
        "input": '<input aria-label="Input" />',
        "image": '<img src="" alt="" />',
        "list": "<ul><li>Item</li></ul>",
        "navigation": '<nav aria-label="Primary"></nav>',
        "dialog": '<dialog open>Dialog</dialog>',
        "menu": '<menu><li>Menu item</li></menu>',
    }
    return templates.get(kind, f"<div>{kind.title()}</div>")


def flutter_template(kind: str) -> str:
    templates = {
        "row": "Row(children: const [])",
        "column": "Column(children: const [])",
        "grid": "GridView.count(crossAxisCount: 2, children: const [])",
        "stack": "Stack(children: const [])",
        "card": "const Card(child: Padding(padding: EdgeInsets.all(16), child: Text('Card')))",
        "button": "ElevatedButton(onPressed: () {}, child: const Text('Button'))",
        "text": "const Text('Text')",
        "input": "const TextField(decoration: InputDecoration(labelText: 'Input'))",
        "image": "Image.asset('assets/image.png')",
        "list": "ListView(children: const [])",
        "navigation": "const NavigationBar(destinations: [])",
        "dialog": "const AlertDialog(title: Text('Dialog'))",
        "page": "const Scaffold(body: SafeArea(child: Text('Page')))",
    }
    return templates.get(kind, "const SizedBox()")


def xaml_template(kind: str) -> str:
    tags = {
        "row": '<HorizontalStackLayout Spacing="12" />',
        "column": '<VerticalStackLayout Spacing="12" />',
        "grid": "<Grid />",
        "stack": "<AbsoluteLayout />",
        "card": '<Border Padding="16"><Label Text="Card" /></Border>',
        "button": '<Button Text="Button" />',
        "text": '<Label Text="Text" />',
        "input": '<Entry Placeholder="Input" />',
        "image": '<Image Source="image.png" />',
        "list": "<CollectionView />",
        "navigation": "<Shell />",
        "page": '<ContentPage><Label Text="Page" /></ContentPage>',
    }
    return tags.get(kind, "<ContentView />")


def compose_template(kind: str) -> str:
    tags = {
        "row": "Row { }",
        "column": "Column { }",
        "grid": "LazyVerticalGrid(columns = GridCells.Fixed(2)) { }",
        "stack": "Box { }",
        "card": 'Card { Text("Card") }',
        "button": 'Button(onClick = {}) { Text("Button") }',
        "text": 'Text("Text")',
        "input": 'TextField(value = "", onValueChange = {})',
        "image": "Image(painter = painterResource(0), contentDescription = null)",
        "list": "LazyColumn { }",
        "navigation": "NavigationBar { }",
        "dialog": "AlertDialog(onDismissRequest = {}, confirmButton = {})",
    }
    return tags.get(kind, "Box { }")


def swiftui_template(kind: str) -> str:
    tags = {
        "row": "HStack { }",
        "column": "VStack { }",
        "grid": "LazyVGrid(columns: []) { }",
        "stack": "ZStack { }",
        "card": 'GroupBox { Text("Card") }',
        "button": 'Button("Button") { }',
        "text": 'Text("Text")',
        "input": 'TextField("Input", text: .constant(""))',
        "image": 'Image("image")',
        "list": "List { }",
        "navigation": "NavigationStack { }",
    }
    return tags.get(kind, "Group { }")


ADAPTERS = [
    Adapter(
        "react",
        "React / TSX",
        "full",
        (".tsx", ".jsx"),
        (),
        re.compile(r"<([A-Za-z][\w.:-]*)([^<>]*?)(/?)>", re.MULTILINE),
        web_template,
    ),
    Adapter(
        "html",
        "HTML",
        "full",
        (".html", ".htm"),
        (),
        re.compile(r"<([A-Za-z][\w:-]*)([^<>]*?)(/?)>", re.MULTILINE),
        web_template,
    ),
    Adapter(
        "flutter",
        "Flutter",
        "full",
        (".dart",),
        ("Widget", "Scaffold", "MaterialApp", "CupertinoApp"),
        re.compile(r"\b([A-Z][A-Za-z0-9_]*)\s*\(([^;\n]*)", re.MULTILINE),
        flutter_template,
    ),
    Adapter(
        "maui-xaml",
        ".NET MAUI / XAML",
        "core",
        (".xaml",),
        (),
        re.compile(r"<([A-Za-z][\w.:-]*)([^<>]*?)(/?)>", re.MULTILINE),
        xaml_template,
    ),
    Adapter(
        "compose",
        "Jetpack Compose",
        "core",
        (".kt", ".kts"),
        ("@Composable", "setContent"),
        re.compile(r"\b([A-Z][A-Za-z0-9_]*)\s*\(([^;\n]*)", re.MULTILINE),
        compose_template,
    ),
    Adapter(
        "swiftui",
        "SwiftUI",
        "core",
        (".swift",),
        ("SwiftUI", "some View", "@main"),
        re.compile(r"\b([A-Z][A-Za-z0-9_]*)\s*(?:\(([^;\n{}]*)\))?\s*\{?", re.MULTILINE),
        swiftui_template,
    ),
]


def adapter_for(path: Path, content: str) -> Adapter | None:
    return next((adapter for adapter in ADAPTERS if adapter.supports(path, content)), None)


def property_schema(adapter_id: str, node_type: str) -> list[dict[str, Any]]:
    common = [
        {"name": "id", "label": "ID", "type": "text", "group": "identity"},
        {"name": "className", "label": "Class", "type": "text", "group": "style"},
        {"name": "text", "label": "Text", "type": "text", "group": "content"},
        {"name": "aria-label", "label": "Accessible label", "type": "text", "group": "accessibility"},
    ]
    if adapter_id == "maui-xaml":
        common = [
            {"name": "x:Name", "label": "Name", "type": "text", "group": "identity"},
            {"name": "Text", "label": "Text", "type": "text", "group": "content"},
            {"name": "AutomationProperties.Name", "label": "Accessible label", "type": "text", "group": "accessibility"},
        ]
    return common + [
        {"name": "width", "label": "Width", "type": "text", "group": "layout"},
        {"name": "height", "label": "Height", "type": "text", "group": "layout"},
        {"name": "onClick", "label": "Action", "type": "event", "group": "events"},
    ]


class DesignerService:
    def __init__(self, workspace: Path, emit_event: Callable[[str, dict[str, Any]], Any]):
        self.workspace = workspace.resolve()
        self.emit_event = emit_event
        self.lock = threading.RLock()
        self.metadata_root = self.workspace / ".kodex-agent" / "designer"
        self.history_path = self.metadata_root / "transactions.json"
        self.transactions: list[dict[str, Any]] = []
        self.cursor = -1
        self._load_history()

    def _load_history(self) -> None:
        try:
            payload = json.loads(self.history_path.read_text(encoding="utf-8"))
            self.transactions = payload.get("transactions", [])
            self.cursor = min(int(payload.get("cursor", -1)), len(self.transactions) - 1)
        except (OSError, ValueError, TypeError):
            self.transactions = []
            self.cursor = -1

    def _save_history(self) -> None:
        self.metadata_root.mkdir(parents=True, exist_ok=True)
        self.history_path.write_text(
            json.dumps({"version": 1, "cursor": self.cursor, "transactions": self.transactions[-100:]}, indent=2),
            encoding="utf-8",
        )

    def _resolve(self, relative: str) -> Path:
        path = (self.workspace / relative).resolve()
        if path == self.workspace or self.workspace not in path.parents:
            raise ValueError("Designer path is outside the workspace")
        return path

    def detect_project(self) -> dict[str, Any]:
        detected: list[dict[str, Any]] = []
        source_files: list[str] = []
        ignored = {".git", ".kodex-agent", "node_modules", "build", "dist", ".dart_tool"}
        for path in self.workspace.rglob("*"):
            if not path.is_file() or any(part in ignored for part in path.parts):
                continue
            if path.suffix.lower() not in DESIGNER_EXTENSIONS or path.stat().st_size > 2_000_000:
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            adapter = adapter_for(path, content)
            if adapter:
                relative = str(path.relative_to(self.workspace))
                source_files.append(relative)
                if not any(item["id"] == adapter.id for item in detected):
                    detected.append({"id": adapter.id, "name": adapter.name, "tier": adapter.tier})
        return {
            "workspace": str(self.workspace),
            "adapters": detected,
            "source_files": source_files[:500],
            "primary_adapter": detected[0]["id"] if detected else None,
            "metadata": str(self.metadata_root.relative_to(self.workspace)),
        }

    def parse_document(self, relative: str) -> dict[str, Any]:
        path = self._resolve(relative)
        content = path.read_text(encoding="utf-8")
        adapter = adapter_for(path, content)
        if not adapter:
            raise ValueError("No visual designer adapter supports this file")
        nodes: list[dict[str, Any]] = []
        stack: list[str] = []
        for index, match in enumerate(adapter.node_pattern.finditer(content)):
            node_type = match.group(1)
            raw_props = match.group(2) or ""
            start_line, start_column = line_column(content, match.start())
            end_line, end_column = line_column(content, match.end())
            node_id = f"{adapter.id}:{match.start()}:{node_type}"
            lowered = node_type.lower()
            if lowered.startswith(("import", "package", "return")):
                continue
            node = {
                "id": node_id,
                "type": node_type,
                "name": serialize_props(raw_props).get("id")
                or serialize_props(raw_props).get("x:Name")
                or f"{node_type} {index + 1}",
                "parent_id": stack[-1] if stack else None,
                "children": [],
                "properties": serialize_props(raw_props),
                "source": {
                    "path": relative,
                    "start_line": start_line,
                    "start_column": start_column,
                    "end_line": end_line,
                    "end_column": end_column,
                    "start_offset": match.start(),
                    "end_offset": match.end(),
                },
            }
            if nodes and stack:
                parent = next((item for item in reversed(nodes) if item["id"] == stack[-1]), None)
                if parent:
                    parent["children"].append(node_id)
            nodes.append(node)
            token = match.group(0)
            if adapter.id in {"react", "html", "maui-xaml"} and not token.rstrip().endswith("/>"):
                stack.append(node_id)
            elif adapter.id not in {"react", "html", "maui-xaml"} and token.rstrip().endswith(("{", "[")):
                stack.append(node_id)
            if len(stack) > 12:
                stack = stack[-12:]
        return {
            "path": relative,
            "adapter": {"id": adapter.id, "name": adapter.name, "tier": adapter.tier},
            "hash": content_hash(content),
            "nodes": nodes,
            "root_ids": [node["id"] for node in nodes if node["parent_id"] is None],
            "property_schemas": {
                node["id"]: property_schema(adapter.id, node["type"]) for node in nodes
            },
            "diagnostics": [] if nodes else [{"severity": "warning", "message": "No visual nodes were discovered."}],
        }

    def toolbox(self, adapter_id: str = "") -> list[dict[str, Any]]:
        adapter = next((item for item in ADAPTERS if item.id == adapter_id), ADAPTERS[0])
        return [
            {
                "id": kind,
                "label": label,
                "category": category,
                "adapter": adapter.id,
                "source_preview": adapter.child_template(kind),
            }
            for kind, label, category in COMMON_TOOLBOX
        ]

    def apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        relative = str(payload.get("path", ""))
        path = self._resolve(relative)
        with self.lock:
            before = path.read_text(encoding="utf-8")
            expected = str(payload.get("expected_hash", ""))
            if expected and expected != content_hash(before):
                raise RuntimeError("Source changed outside the designer. Reload before applying this edit.")
            document = self.parse_document(relative)
            node = next((item for item in document["nodes"] if item["id"] == payload.get("node_id")), None)
            operation = str(payload.get("operation", "update"))
            if operation == "add" and not node:
                node = next(
                    (item for item in document["nodes"] if item["id"] in document["root_ids"]),
                    document["nodes"][0] if document["nodes"] else None,
                )
            if operation != "add" and not node:
                raise ValueError("Designer node no longer exists")
            adapter = next(item for item in ADAPTERS if item.id == document["adapter"]["id"])
            if operation == "add":
                snippet = adapter.child_template(str(payload.get("component", "button")))
                insert_at = len(before)
                if node:
                    insert_at = int(node["source"]["end_offset"])
                after = before[:insert_at] + f"\n  {snippet}\n" + before[insert_at:]
            elif operation == "delete":
                start, end = self._node_span(before, node, adapter.id)
                after = before[:start] + before[end:]
            elif operation == "update":
                after = self._update_node(before, node, str(payload.get("property", "")), str(payload.get("value", "")))
            else:
                raise ValueError("Unsupported designer operation")
            path.write_text(after, encoding="utf-8")
            try:
                parsed = self.parse_document(relative)
            except Exception:
                path.write_text(before, encoding="utf-8")
                raise
            transaction = {
                "id": uuid.uuid4().hex,
                "label": str(payload.get("label") or f"{operation.title()} {node['type'] if node else payload.get('component', 'component')}"),
                "path": relative,
                "operation": operation,
                "before": before,
                "after": after,
                "before_hash": content_hash(before),
                "after_hash": content_hash(after),
                "changed_files": [relative],
                "created_at": now_iso(),
            }
            self.transactions = self.transactions[: self.cursor + 1] + [transaction]
            self.cursor = len(self.transactions) - 1
            self._save_history()
            self.emit_event("designer.transaction.applied", {"transaction_id": transaction["id"], "path": relative})
            return {
                "transaction": {key: value for key, value in transaction.items() if key not in {"before", "after"}},
                "document": parsed,
                "changed_files": [relative],
                "source_ranges": [node["source"]] if node else [],
                "diagnostics": parsed["diagnostics"],
                "validation": {"parsed": True, "source_hash": parsed["hash"]},
            }

    def _update_node(self, content: str, node: dict[str, Any], prop: str, value: str) -> str:
        if not prop:
            raise ValueError("Property name is required")
        start = int(node["source"]["start_offset"])
        end = int(node["source"]["end_offset"])
        token = content[start:end]
        if prop == "text":
            closing = re.search(rf"</{re.escape(node['type'])}\s*>", content[end:], re.IGNORECASE)
            if closing:
                text_end = end + closing.start()
                return content[:end] + value + content[text_end:]
            quoted_text = re.search(r"""(["'])(.*?)\1""", token)
            if not quoted_text:
                raise ValueError("This node does not expose editable text")
            replacement = token[: quoted_text.start(2)] + value + token[quoted_text.end(2):]
        else:
            quoted = json.dumps(value)
            pattern = re.compile(rf"(\b{re.escape(prop)}\s*=\s*)(?:\{{[^}}]*\}}|\"[^\"]*\"|'[^']*')")
            if pattern.search(token):
                replacement = pattern.sub(rf"\g<1>{quoted}", token, count=1)
            else:
                insert_at = token.rfind("/>") if token.rstrip().endswith("/>") else token.rfind(">")
                replacement = token[:insert_at].rstrip() + f" {prop}={quoted}" + token[insert_at:]
        return content[:start] + replacement + content[end:]

    def _node_span(self, content: str, node: dict[str, Any], adapter_id: str) -> tuple[int, int]:
        start = int(node["source"]["start_offset"])
        end = int(node["source"]["end_offset"])
        opening = content[start:end]
        if adapter_id in {"react", "html", "maui-xaml"} and not opening.rstrip().endswith("/>"):
            closing = re.search(rf"</{re.escape(node['type'])}\s*>", content[end:], re.IGNORECASE)
            if closing:
                return start, end + closing.end()
        return start, end

    def history(self) -> dict[str, Any]:
        return {
            "cursor": self.cursor,
            "transactions": [
                {key: value for key, value in item.items() if key not in {"before", "after"}}
                for item in self.transactions
            ],
            "can_undo": self.cursor >= 0,
            "can_redo": self.cursor + 1 < len(self.transactions),
        }

    def undo(self) -> dict[str, Any]:
        with self.lock:
            if self.cursor < 0:
                raise ValueError("Nothing to undo")
            transaction = self.transactions[self.cursor]
            path = self._resolve(transaction["path"])
            current = path.read_text(encoding="utf-8")
            if content_hash(current) != transaction["after_hash"]:
                raise RuntimeError("Source changed after this transaction. Reload before undoing.")
            path.write_text(transaction["before"], encoding="utf-8")
            self.cursor -= 1
            self._save_history()
            self.emit_event("designer.transaction.undone", {"transaction_id": transaction["id"]})
            return {"undone": transaction["id"], "document": self.parse_document(transaction["path"])}

    def redo(self) -> dict[str, Any]:
        with self.lock:
            if self.cursor + 1 >= len(self.transactions):
                raise ValueError("Nothing to redo")
            transaction = self.transactions[self.cursor + 1]
            path = self._resolve(transaction["path"])
            current = path.read_text(encoding="utf-8")
            if content_hash(current) != transaction["before_hash"]:
                raise RuntimeError("Source changed after undo. Reload before redoing.")
            path.write_text(transaction["after"], encoding="utf-8")
            self.cursor += 1
            self._save_history()
            self.emit_event("designer.transaction.redone", {"transaction_id": transaction["id"]})
            return {"redone": transaction["id"], "document": self.parse_document(transaction["path"])}


PROJECT_TEMPLATES = [
    {
        "id": "react-web",
        "name": "Website",
        "platform": "Web",
        "framework": "React + TypeScript",
        "adapter": "react",
        "validation": ["npm install", "npm run build"],
    },
    {
        "id": "electron",
        "name": "Desktop app",
        "platform": "Windows, macOS, Linux",
        "framework": "Electron + React",
        "adapter": "react",
        "validation": ["npm install", "npm run build"],
    },
    {
        "id": "flutter",
        "name": "Cross-platform app",
        "platform": "Android, iOS, desktop, web",
        "framework": "Flutter",
        "adapter": "flutter",
        "validation": ["flutter pub get", "flutter analyze"],
    },
    {
        "id": "maui",
        "name": "Windows app",
        "platform": "Windows, macOS, mobile",
        "framework": ".NET MAUI / XAML",
        "adapter": "maui-xaml",
        "validation": ["dotnet restore", "dotnet build"],
    },
    {
        "id": "compose",
        "name": "Android app",
        "platform": "Android",
        "framework": "Jetpack Compose",
        "adapter": "compose",
        "validation": ["./gradlew assembleDebug"],
    },
    {
        "id": "swiftui",
        "name": "macOS / iOS app",
        "platform": "macOS, iOS",
        "framework": "SwiftUI",
        "adapter": "swiftui",
        "validation": ["swift build"],
    },
    {
        "id": "api",
        "name": "API",
        "platform": "Server",
        "framework": "FastAPI",
        "adapter": None,
        "validation": ["python3 -m compileall ."],
    },
    {
        "id": "custom",
        "name": "Custom project",
        "platform": "Custom",
        "framework": "AI selected",
        "adapter": None,
        "validation": [],
    },
]
