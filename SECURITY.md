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

Supported releases receive security fixes on the active development branch. Until signed
release artifacts are published, build from source and review changes before installation.
