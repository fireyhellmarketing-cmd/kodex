# Kodex Architecture

Kodex is a local-first Electron coding IDE. Framework source files remain the source of truth;
the desktop shell coordinates editing, terminals, Git, Run/Debug, plugins, and the Kodex agent.

## Repository Ownership

- `apps/desktop/electron/`: trusted Electron lifecycle, native menus, secure IPC, PTY ownership,
  credential storage, plugins, and Core lifecycle.
- `apps/desktop/src/`: sandboxed React renderer. `TerminalPane.tsx` owns bottom-panel presentation,
  `telemetry/` owns system-monitor UI, and `App.tsx` coordinates workbench state.
- `apps/core/src/codex_core/`: authenticated loopback API, agent runtime, workspace services,
  process policy, telemetry, project intelligence, and durable local state.
- `packaging/`: platform entitlements and release metadata.
- `scripts/`: build and release validation. Generated output belongs in ignored `dist/`,
  `release/`, cache, or `.kodex-agent/` directories.

## Runtime Boundaries

The renderer has no Node.js integration. Its minimal preload bridge exposes typed IPC methods.
Electron owns native capabilities and validates requests before forwarding them. Core binds to
`127.0.0.1`, requires a per-launch bearer token for sensitive routes, and resolves workspace file
operations inside the selected workspace.

The interactive terminal is an explicit trusted-user shell backed by `node-pty`. Run and Debug
profiles create named PTY sessions so the command, working directory, output, errors, duration,
and exit state remain visible. Agent-issued commands continue through the agent approval policy.

## Workbench State

`codex.layout` persists sidebar, agent, and bottom-panel visibility, dimensions, active bottom tab,
and maximized state. Collapsing the bottom panel hides presentation only; Electron retains PTY
processes and bounded scrollback.

## Data And Secrets

Workspace content stays local unless a configured provider request requires it. Provider keys are
stored only through Electron `safeStorage`; when secure storage is unavailable Kodex refuses to
persist them. Logs should contain operational metadata, not authorization values or provider keys.

## Extension Direction

New capabilities should be added behind narrow service or component contracts. Electron modules
own privileged OS behavior, Core services own authenticated domain behavior, and renderer modules
own presentation. Avoid adding new privileged behavior directly to the React shell.
