# Contributing to Kodex

Thank you for helping improve Kodex. Contributions may include bug fixes, tests, documentation,
AI provider integrations, visual designer adapters, project templates, accessibility work, and
platform packaging improvements.

## Development Setup

```bash
git clone https://github.com/fireyhellmarketing-cmd/kodex.git
cd kodex
npm install
npm run start
```

## Before Opening a Pull Request

1. Keep the change focused and preserve unrelated user work.
2. Follow existing Core, Electron, React, and TypeScript patterns.
3. Add focused tests for behavioral changes.
4. Run:

```bash
PYTHONPATH=apps/core/src python3 -m unittest discover -s apps/core/tests -q
npm run lint -w apps/desktop
npm run test:terminal -w apps/desktop
npm run build -w apps/desktop
node scripts/validate-release.cjs
```

5. Describe the user-facing behavior, risks, validation, and any platform limitations.

## Design Principles

- Real project source files remain authoritative.
- Local-first behavior and clear permission boundaries are defaults.
- Agent work should remain visible, interruptible, and recoverable.
- Errors should be actionable rather than silent.
- New abstractions should remove meaningful complexity.

## Reporting Bugs

Include your operating system, Kodex version or commit, reproduction steps, expected behavior,
actual behavior, relevant diagnostics, and whether the issue occurs with a local or cloud model.
Do not include API keys, authentication tokens, private source code, or personal information.
