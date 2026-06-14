# Security Policy

## Reporting a Vulnerability

Please do not publish exploitable security vulnerabilities in a public issue. Contact the
maintainer through the GitHub account at
<https://github.com/fireyhellmarketing-cmd> with enough information to reproduce and assess the
issue.

Do not include real API keys, account tokens, passwords, private repositories, or unrelated
personal data. Replace sensitive values with safe examples.

## Security Boundaries

Kodex is a local development tool that can edit files and run commands with user-selected
permissions. Users should review approval prompts, inspect agent changes, and avoid granting
full access to untrusted models, plugins, workspaces, or instructions.

The Electron renderer is sandboxed with context isolation and no Node integration. A narrow preload
bridge is the only renderer-to-host path. New windows and unknown navigation are blocked; external
navigation is limited to HTTPS, with HTTP allowed only for local development addresses.

Kodex Core binds to loopback, uses an origin allowlist, and requires a random per-launch bearer token
for sensitive HTTP and WebSocket operations. API documentation is disabled in packaged builds.
Workspace operations must remain within the selected workspace after canonical path resolution.

Provider credentials are stored through the operating system's secure storage. Kodex fails closed
instead of writing plaintext credentials when secure storage is unavailable. Tokens, authorization
headers, API keys, and secrets must be redacted from diagnostics and support bundles.

The integrated terminal is a trusted direct-user shell and can perform any operation allowed by the
user account. Agent-proposed commands are a separate surface and remain subject to permissions and
approvals. Install only trusted plugins and review their declared capabilities.

## Release Security

Release validation includes Core tests, renderer lint/build, terminal-manager tests, dependency
review, and package metadata checks. Production releases should additionally use platform signing,
notarization where applicable, a software bill of materials, and verifiable CI provenance.

Supported releases receive security fixes on the active development branch. Until signed
release artifacts are published, build from source and review changes before installation.
