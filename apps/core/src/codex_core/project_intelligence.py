from __future__ import annotations

import json
import ast
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from tree_sitter_language_pack import get_parser
except ImportError:
    try:
        from tree_sitter_languages import get_parser
    except ImportError:  # The release bundle installs this optional native dependency.
        get_parser = None


IGNORED_DIRECTORIES = {
    ".git",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "venv",
}

LANGUAGES = {
    ".css": "CSS",
    ".go": "Go",
    ".html": "HTML",
    ".java": "Java",
    ".js": "JavaScript",
    ".json": "JSON",
    ".jsx": "JavaScript",
    ".md": "Markdown",
    ".php": "PHP",
    ".py": "Python",
    ".rb": "Ruby",
    ".rs": "Rust",
    ".scss": "SCSS",
    ".sh": "Shell",
    ".sql": "SQL",
    ".swift": "Swift",
    ".toml": "TOML",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".vue": "Vue",
    ".yaml": "YAML",
    ".yml": "YAML",
}

SYMBOL_PATTERNS = (
    ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)")),
    ("class", re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_$][\w$]*)")),
    ("variable", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=")),
    ("function", re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_][\w]*)")),
    ("class", re.compile(r"^\s*class\s+([A-Za-z_][\w]*)")),
)

IMPORT_PATTERNS = (
    re.compile(r"""(?:from|import)\s+['"]([^'"]+)['"]"""),
    re.compile(r"""require\(\s*['"]([^'"]+)['"]\s*\)"""),
    re.compile(r"^\s*(?:from|import)\s+([A-Za-z_][\w.]*)"),
)

ROUTE_PATTERNS = (
    re.compile(r"""(?:app|router)\.(?:get|post|put|patch|delete)\(\s*['"]([^'"]+)['"]"""),
    re.compile(r"""@(?:app|router)\.(?:get|post|put|patch|delete)\(\s*['"]([^'"]+)['"]"""),
)

TREE_SITTER_LANGUAGES = {
    ".c": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cs": "c_sharp",
    ".go": "go",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".php": "php",
    ".py": "python",
    ".rb": "ruby",
    ".rs": "rust",
    ".swift": "swift",
    ".ts": "typescript",
    ".tsx": "tsx",
}

TREE_SITTER_SYMBOL_TYPES = {
    "class_declaration": "class",
    "class_definition": "class",
    "function_declaration": "function",
    "function_definition": "function",
    "function_item": "function",
    "method_declaration": "method",
    "method_definition": "method",
    "struct_item": "struct",
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def iter_source_files(workspace: Path, limit: int = 2_000) -> list[Path]:
    files: list[Path] = []
    for path in workspace.rglob("*"):
        if len(files) >= limit:
            break
        if not path.is_file() or any(part in IGNORED_DIRECTORIES for part in path.parts):
            continue
        if path.suffix.lower() in LANGUAGES and path.stat().st_size <= 1_000_000:
            files.append(path)
    return files


def package_metadata(workspace: Path) -> tuple[list[str], list[str], list[str], dict[str, str]]:
    package_managers: set[str] = set()
    frameworks: set[str] = set()
    dependencies: set[str] = set()
    scripts: dict[str, str] = {}
    lockfiles = {
        "package-lock.json": "npm",
        "pnpm-lock.yaml": "pnpm",
        "yarn.lock": "yarn",
        "bun.lock": "bun",
        "poetry.lock": "Poetry",
        "uv.lock": "uv",
        "Cargo.lock": "Cargo",
        "go.sum": "Go modules",
    }
    for filename, manager in lockfiles.items():
        if (workspace / filename).exists():
            package_managers.add(manager)
    package_files = [
        path
        for path in workspace.rglob("package.json")
        if "node_modules" not in path.parts
        and len(path.relative_to(workspace).parts) <= 4
    ]
    for package_json in package_files:
        package_managers.add("npm")
        try:
            payload = json.loads(package_json.read_text(encoding="utf-8"))
            prefix = str(package_json.parent.relative_to(workspace))
            for key, value in payload.get("scripts", {}).items():
                script_key = str(key) if prefix == "." else f"{prefix}:{key}"
                scripts[script_key] = str(value)
            all_dependencies = {
                **payload.get("dependencies", {}),
                **payload.get("devDependencies", {}),
            }
            dependencies.update(all_dependencies)
            framework_packages = {
                "react": "React",
                "next": "Next.js",
                "vite": "Vite",
                "vue": "Vue",
                "svelte": "Svelte",
                "electron": "Electron",
                "express": "Express",
                "nestjs": "NestJS",
            }
            for package, framework in framework_packages.items():
                if package in all_dependencies:
                    frameworks.add(framework)
        except (OSError, ValueError):
            pass
    pyproject_files = [
        path
        for path in workspace.rglob("pyproject.toml")
        if not any(part in IGNORED_DIRECTORIES for part in path.parts)
        and len(path.relative_to(workspace).parts) <= 4
    ]
    for pyproject in pyproject_files:
        package_managers.add("Python")
        text = pyproject.read_text(encoding="utf-8", errors="ignore").lower()
        for token, framework in {
            "fastapi": "FastAPI",
            "django": "Django",
            "flask": "Flask",
            "pytest": "Pytest",
        }.items():
            if token in text:
                frameworks.add(framework)
                dependencies.add(token)
    if (workspace / "Cargo.toml").exists():
        package_managers.add("Cargo")
    if (workspace / "go.mod").exists():
        package_managers.add("Go modules")
    return (
        sorted(package_managers),
        sorted(frameworks),
        sorted(dependencies)[:300],
        scripts,
    )


def entry_points(workspace: Path) -> list[str]:
    candidates = (
        "index.html",
        "main.py",
        "app.py",
        "server.py",
        "main.ts",
        "main.tsx",
        "src/main.ts",
        "src/main.tsx",
        "src/index.ts",
        "src/index.tsx",
        "src/server.ts",
        "src/server.js",
        "src/main.py",
        "electron/main.cjs",
    )
    entries = [candidate for candidate in candidates if (workspace / candidate).exists()]
    nested_names = {
        "main.py",
        "main.ts",
        "main.tsx",
        "server.js",
        "server.py",
        "electron/main.cjs",
    }
    for path in workspace.rglob("*"):
        if len(entries) >= 30 or not path.is_file():
            continue
        relative = str(path.relative_to(workspace))
        if any(part in IGNORED_DIRECTORIES for part in path.parts):
            continue
        if path.name in nested_names or relative.endswith("electron/main.cjs"):
            if relative not in entries and len(path.relative_to(workspace).parts) <= 6:
                entries.append(relative)
    return entries


def tree_sitter_nodes(path: Path, source: str, relative: str) -> list[dict[str, Any]]:
    language = TREE_SITTER_LANGUAGES.get(path.suffix.lower())
    if not language or get_parser is None:
        return []
    try:
        parser = get_parser(language)
        tree = parser.parse(source.encode("utf-8"))
    except (LookupError, RuntimeError, TypeError, ValueError):
        return []
    nodes: list[dict[str, Any]] = []
    stack = [tree.root_node]
    while stack and len(nodes) < 1_000:
        node = stack.pop()
        kind = TREE_SITTER_SYMBOL_TYPES.get(node.type)
        if kind:
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = source.encode("utf-8")[name_node.start_byte:name_node.end_byte].decode(
                    "utf-8", errors="replace"
                )
                nodes.append(
                    {
                        "id": f"{relative}:{name}:{node.start_point[0] + 1}",
                        "name": name,
                        "kind": kind,
                        "path": relative,
                        "line": node.start_point[0] + 1,
                        "parser": f"tree-sitter-{language}",
                    }
                )
        stack.extend(reversed(node.children))
    return nodes


def analyze_workspace(workspace: Path) -> dict[str, Any]:
    workspace = workspace.resolve()
    files = iter_source_files(workspace)
    languages = Counter(LANGUAGES[path.suffix.lower()] for path in files)
    symbols: list[dict[str, Any]] = []
    imports: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    graph_nodes: list[dict[str, Any]] = []
    graph_edges: list[dict[str, Any]] = []
    for path in files:
        relative = str(path.relative_to(workspace))
        try:
            source = path.read_text(encoding="utf-8")
            lines = source.splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        parsed_nodes = tree_sitter_nodes(path, source, relative)
        if parsed_nodes:
            graph_nodes.extend(parsed_nodes)
            symbols.extend(
                {
                    "name": node["name"],
                    "kind": node["kind"],
                    "path": node["path"],
                    "line": node["line"],
                }
                for node in parsed_nodes
                if len(symbols) < 2_000
            )
        if path.suffix == ".py":
            try:
                tree = ast.parse(source)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        graph_nodes.append(
                            {
                                "id": f"{relative}:{node.name}:{node.lineno}",
                                "name": node.name,
                                "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                                "path": relative,
                                "line": node.lineno,
                                "parser": "python-ast",
                            }
                        )
                    elif isinstance(node, (ast.Import, ast.ImportFrom)):
                        targets = (
                            [alias.name for alias in node.names]
                            if isinstance(node, ast.Import)
                            else [node.module or ""]
                        )
                        graph_edges.extend(
                            {
                                "source": relative,
                                "target": target,
                                "kind": "imports",
                                "line": node.lineno,
                            }
                            for target in targets
                            if target
                        )
            except SyntaxError:
                pass
        for line_number, line in enumerate(lines, 1):
            if len(symbols) < 2_000 and not parsed_nodes:
                for kind, pattern in SYMBOL_PATTERNS:
                    match = pattern.search(line)
                    if match:
                        symbols.append(
                            {
                                "name": match.group(1),
                                "kind": kind,
                                "path": relative,
                                "line": line_number,
                            }
                        )
                        graph_nodes.append(
                            {
                                "id": f"{relative}:{match.group(1)}:{line_number}",
                                "name": match.group(1),
                                "kind": kind,
                                "path": relative,
                                "line": line_number,
                                "parser": "syntax-pattern",
                            }
                        )
                        break
            if len(imports) < 2_000:
                for pattern in IMPORT_PATTERNS:
                    match = pattern.search(line)
                    if match:
                        imports.append(
                            {
                                "source": relative,
                                "target": match.group(1),
                                "line": line_number,
                            }
                        )
                        graph_edges.append(
                            {
                                "source": relative,
                                "target": match.group(1),
                                "kind": "imports",
                                "line": line_number,
                            }
                        )
                        break
            if len(routes) < 1_000:
                for pattern in ROUTE_PATTERNS:
                    match = pattern.search(line)
                    if match:
                        routes.append(
                            {
                                "path": match.group(1),
                                "source": relative,
                                "line": line_number,
                            }
                        )
                        break
    managers, frameworks, dependencies, scripts = package_metadata(workspace)
    entries = entry_points(workspace)
    top_languages = [
        {"name": name, "files": count} for name, count in languages.most_common(12)
    ]
    summary_parts = []
    if top_languages:
        summary_parts.append(
            "Primary languages: "
            + ", ".join(f"{item['name']} ({item['files']})" for item in top_languages[:4])
        )
    if frameworks:
        summary_parts.append("Frameworks: " + ", ".join(frameworks))
    if entries:
        summary_parts.append("Entry points: " + ", ".join(entries))
    return {
        "schema_version": "1.0",
        "workspace": str(workspace),
        "generated_at": utc_now(),
        "file_count": len(files),
        "languages": top_languages,
        "frameworks": frameworks,
        "package_managers": managers,
        "entry_points": entries,
        "scripts": scripts,
        "dependencies": dependencies,
        "symbols": symbols,
        "imports": imports,
        "routes": routes,
        "semantic_graph": {
            "nodes": graph_nodes[:3_000],
            "edges": graph_edges[:4_000],
            "parsers": sorted({node["parser"] for node in graph_nodes}),
        },
        "tree_sitter": {
            "available": get_parser is not None,
            "languages": sorted(
                {
                    TREE_SITTER_LANGUAGES[path.suffix.lower()]
                    for path in files
                    if path.suffix.lower() in TREE_SITTER_LANGUAGES
                }
            ),
        },
        "summary": ". ".join(summary_parts) or "No supported project files detected yet.",
    }


def project_state_path(workspace: Path) -> Path:
    return workspace / ".kodex-agent" / "project-state.json"


def legacy_project_state_path(workspace: Path) -> Path:
    return workspace / ".codex-agent" / "project-state.json"


def load_project_state(workspace: Path) -> dict[str, Any]:
    path = project_state_path(workspace)
    if not path.exists() and legacy_project_state_path(workspace).exists():
        path = legacy_project_state_path(workspace)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def update_project_state(
    workspace: Path,
    intelligence: dict[str, Any],
    *,
    last_run: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = project_state_path(workspace)
    previous = load_project_state(workspace)
    state = {
        "schema_version": "1.0",
        "workspace": str(workspace.resolve()),
        "updated_at": utc_now(),
        "summary": intelligence["summary"],
        "languages": intelligence["languages"],
        "frameworks": intelligence["frameworks"],
        "entry_points": intelligence["entry_points"],
        "scripts": intelligence["scripts"],
        "milestones": previous.get("milestones", []),
        "current_work": previous.get("current_work", []),
        "pending_work": previous.get("pending_work", []),
        "known_issues": previous.get("known_issues", []),
        "last_run": last_run or previous.get("last_run"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return state


def discover_skills(workspace: Path) -> list[dict[str, Any]]:
    trust_path = workspace / ".kodex-agent" / "skill-trust.json"
    if not trust_path.exists():
        trust_path = workspace / ".codex-agent" / "skill-trust.json"
    try:
        trust = json.loads(trust_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        trust = {}
    candidates = list((workspace / ".kodex-agent" / "skills").glob("*.md"))
    candidates.extend((workspace / ".codex-agent" / "skills").glob("*.md"))
    candidates.extend((workspace / ".agents" / "skills").glob("*/SKILL.md"))
    skills: list[dict[str, Any]] = []
    for path in candidates:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        frontmatter: dict[str, str] = {}
        if content.startswith("---"):
            _, block, _ = content.split("---", 2)
            for line in block.splitlines():
                key, separator, value = line.partition(":")
                if separator:
                    frontmatter[key.strip()] = value.strip().strip("\"'")
        skills.append(
            {
                "id": str(path.relative_to(workspace)),
                "name": frontmatter.get("name", path.parent.name if path.name == "SKILL.md" else path.stem),
                "description": frontmatter.get("description", ""),
                "path": str(path.relative_to(workspace)),
                "content": content[:20_000],
                "trust": trust.get(str(path.relative_to(workspace)), "review"),
            }
        )
    return sorted(skills, key=lambda item: item["name"].lower())


def set_skill_trust(workspace: Path, skill_id: str, trust: str) -> dict[str, Any]:
    if trust not in {"trusted", "review", "disabled"}:
        raise ValueError("Unsupported skill trust state")
    known = {skill["id"] for skill in discover_skills(workspace)}
    if skill_id not in known:
        raise KeyError("Workspace skill not found")
    path = workspace / ".kodex-agent" / "skill-trust.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    payload[skill_id] = trust
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return next(skill for skill in discover_skills(workspace) if skill["id"] == skill_id)
