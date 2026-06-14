---
name: kodex-workspace
description: Build, diagnose, repair, and verify the Kodex Electron desktop and Python Core agent, including its OpenAI Codex login/provider integration.
---

# Kodex Workspace

Use this skill when working in the Kodex repository or when the user asks to
repair its desktop agent, provider integration, permissions, chat UX, or plugin
behavior.

## Architecture

- `apps/desktop`: Electron main/preload plus the React renderer.
- `apps/core`: FastAPI Core and the agent execution runtime.
- `apps/core/src/codex_core/agent_runtime.py`: provider routing, model calls,
  tool execution, recovery, validation, and run state.
- `apps/desktop/src/App.tsx`: chat, settings, agent timeline, and workspace UI.
- `apps/desktop/electron/main.cjs`: local credentials, Codex login process, Core
  lifecycle, and privileged desktop operations.

## OpenAI Codex Integration

- Use the official local `codex` CLI and its cached authentication.
- Check authentication with `codex login status`.
- Start login with `codex login`; never ask for or copy ChatGPT passwords or
  access tokens into Kodex.
- In Kodex, Codex is a planning/model provider. Keep its subprocess read-only.
- Apply file changes through Kodex Core's typed tools so snapshots, approvals,
  changed ranges, recovery, and validation remain visible to the user.
- Preserve explicit approval for destructive, credential, system, and
  permission-changing actions even when Full access is selected.

## Workflow

1. Read the affected Core and desktop files before changing them.
2. Reproduce the failure using persisted run diagnostics when available.
3. Make the smallest complete cross-layer change.
4. Add focused regression coverage for runtime behavior.
5. Run:

```bash
PYTHONPATH=apps/core/src PYTHONPYCACHEPREFIX=.kodex-agent/pycache \
  python3 -m unittest discover -s apps/core/tests -q
npm run lint -w apps/desktop
npm run build -w apps/desktop
node scripts/validate-release.cjs
```

6. For frontend changes, inspect the running desktop renderer when a browser
   surface is available.
7. Report unavailable checks honestly.

## Interaction Rules

- Chat Enter sends; Shift+Enter inserts a newline.
- Keep agent work visible as inline chat activity.
- Do not display raw internal exceptions when a structured recovery message is
  available.
- Do not claim the ChatGPT Codex provider is connected unless
  `codex login status` succeeds and the provider appears available from
  `/v1/providers`.
