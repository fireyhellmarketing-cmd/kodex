type Props = {
  recentWorkspaces: Array<{ path: string; name: string }>
  onCreateWorkspace: () => void
  onNewFile: () => void
  onOpenWorkspace: () => void
  onOpenRecent: (path: string) => void
  onAskKodex: () => void
  onOpenDocs: () => void
}

export default function WelcomeScreen({
  recentWorkspaces,
  onCreateWorkspace,
  onNewFile,
  onOpenWorkspace,
  onOpenRecent,
  onAskKodex,
  onOpenDocs,
}: Props) {
  return (
    <main className="coder-welcome">
      <section className="coder-welcome-hero">
        <span className="launch-eyebrow">AI coding workspace</span>
        <h1>Code with Kodex</h1>
        <p>Open a repository, start with an empty folder, or ask Kodex to build and debug alongside you.</p>
        <div className="coder-welcome-actions">
          <button className="primary" onClick={onOpenWorkspace}>Open Folder</button>
          <button onClick={onCreateWorkspace}>New Coding Workspace</button>
          <button onClick={onNewFile}>New File</button>
        </div>
        <button className="coder-welcome-agent" onClick={onAskKodex}>
          <span>
            <strong>Ask Kodex</strong>
            <small>Create an empty workspace, then describe what you want to code.</small>
          </span>
          <b>→</b>
        </button>
      </section>

      <aside className="coder-welcome-side">
        <section>
          <header><strong>Recent</strong><span>{recentWorkspaces.length}</span></header>
          <div className="coder-recent-list">
            {recentWorkspaces.slice(0, 7).map((workspace) => (
              <button key={workspace.path} onClick={() => onOpenRecent(workspace.path)}>
                <strong>{workspace.name}</strong>
                <small>{workspace.path}</small>
              </button>
            ))}
            {!recentWorkspaces.length && <p>Your recent coding workspaces will appear here.</p>}
          </div>
        </section>
        <section className="coder-start-guide">
          <header><strong>Start</strong></header>
          <button onClick={onOpenDocs}><span>Read the Kodex documentation</span><b>↗</b></button>
          <p>Kodex keeps source files authoritative and shows edits, commands, approvals, and validation in the agent timeline.</p>
        </section>
      </aside>
    </main>
  )
}
