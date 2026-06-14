# Kodex Visual App Studio

Kodex Visual App Studio is a source-authoritative UI designer. The framework files in the
workspace are always the source of truth; `.kodex-agent/designer/` contains only blueprints,
transaction history, canvas preferences, and recoverable metadata.

## Project Creation

1. Describe the application on the Kodex welcome screen.
2. Choose a platform template and AI provider.
3. Select a parent directory and enter one project name.
4. Kodex creates exactly one named directory, writes a blueprint, installs available
   dependencies, runs the template validation commands, opens the workspace, and detects
   its first visual document.

Interrupted creation state is stored in the Kodex user-data directory and shown on the next
launch. Missing SDKs are reported as setup work instead of being treated as a successful build.

## Designer Workflow

Supported UI files can be opened from the Designer activity panel or by double-clicking them
in Explorer. The editor offers Design, Source, Split, and Preview modes.

- Drag or double-click toolbox components to add them.
- Select hierarchy or canvas nodes to edit properties.
- Double-click a node to open its exact source line.
- Use transaction Undo and Redo for canvas, property, deletion, and AI changes.
- External source changes invalidate stale transactions and require a reload.

React, HTML/Electron, and Flutter are full-tier adapters. XAML/.NET MAUI, Jetpack Compose,
and SwiftUI are core-tier adapters.

## AI Transactions

The right panel always keeps Kodex visible. Enabled provider plugins appear as additional tabs.
Designer prompts include the selected node, hierarchy, properties, source range, active file,
adapter, diagnostics, and device context. The required workflow is preview, review, apply as one
transaction, rebuild, and refresh.

## Plugin Management

Installed plugins can be enabled, disabled, opened as agent tabs, or deleted from the Plugins
activity panel. Deleting requires confirmation. If the active plugin is disabled or deleted,
the panel returns to the built-in Kodex tab.

Plugin manifests may contribute:

```json
{
  "enabled": true,
  "uninstall": { "provider": "integration", "command": [] },
  "agentTab": { "id": "provider-id", "label": "Provider", "order": 20 },
  "designerAdapters": ["framework-id"],
  "toolboxContributions": [],
  "projectTemplates": []
}
```

## Adapter SDK

An adapter must provide these capabilities:

- project and file detection
- source parsing into `DesignerDocument` and `DesignerNode`
- property schemas and toolbox source templates
- stable source ranges
- conflict-checked source transforms
- validation and run/debug profiles
- optional device profiles and platform diagnostics

Core endpoints are available under `/v1/designer/*`. Every edit returns changed files, source
ranges, diagnostics, validation state, and an undo transaction identifier. New adapters should
add parser/transform fixtures that verify valid output and byte-equivalent undo.

## Release Verification

Run:

```sh
PYTHONPATH=apps/core/src python3 -m unittest discover -s apps/core/tests -v
npm run lint -w apps/desktop
npm run build -w apps/desktop
npm run release:validate
```

Native smoke testing should verify the welcome creator, menus, supported-file double-click,
designer edits, source navigation, Run/Debug, plugin fallback, and Core/renderer health.
