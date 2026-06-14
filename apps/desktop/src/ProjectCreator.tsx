import { useEffect, useState } from 'react'

type Props = {
  recentWorkspaces: Array<{ path: string; name: string }>
  onOpenWorkspace: () => void
  onOpenRecent: (path: string) => void
}

const projectTypes = [
  ['react-web', 'Website', 'React + TypeScript'],
  ['electron', 'Desktop app', 'Electron + React'],
  ['maui', 'Windows app', '.NET MAUI'],
  ['swiftui', 'macOS app', 'SwiftUI'],
  ['compose', 'Android app', 'Jetpack Compose'],
  ['swiftui', 'iOS app', 'SwiftUI'],
  ['flutter', 'Cross-platform', 'Flutter'],
  ['api', 'API', 'FastAPI'],
  ['custom', 'Custom project', 'AI selected'],
] as const

export default function ProjectCreator({ recentWorkspaces, onOpenWorkspace, onOpenRecent }: Props) {
  const [prompt, setPrompt] = useState('')
  const [template, setTemplate] = useState('react-web')
  const [provider, setProvider] = useState('openai-codex')
  const [name, setName] = useState('')
  const [parent, setParent] = useState('')
  const [creating, setCreating] = useState(false)
  const [progress, setProgress] = useState<Record<string, unknown> | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    void window.codex?.projectCreationState().then((state) => state && setProgress(state))
    return window.codex?.onProjectProgress((state) => setProgress(state))
  }, [])

  async function chooseParent() {
    const selected = await window.codex?.chooseProjectParent()
    if (selected) setParent(selected)
  }

  async function create() {
    if (!window.codex) {
      setError('Project creation is available in the Kodex desktop app.')
      return
    }
    if (!prompt.trim() || !name.trim() || !parent) {
      setError('Describe the app, choose a folder, and enter a project name.')
      return
    }
    setCreating(true)
    setError('')
    try {
      const selected = projectTypes.find((item) => item[0] === template)
      await window.codex.createProject({
        parent,
        name,
        template,
        prompt,
        framework: selected?.[2],
        platform: selected?.[1],
        provider,
        install: true,
      })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to create project')
      setCreating(false)
    }
  }

  return (
    <main className="project-creator">
      <section className="project-creator-main">
        <header>
          <span className="launch-eyebrow">Kodex Visual App Studio</span>
          <h1>What type of app or software do you want to create?</h1>
          <p>Describe the result. Kodex creates one clean project folder, validates it, and opens the first supported screen in the visual designer.</p>
        </header>
        <div className="project-prompt-card">
          <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="Build a modern inventory app with a dashboard, products table, and settings window…" autoFocus />
          <div>
            <select value={provider} onChange={(event) => setProvider(event.target.value)} aria-label="AI provider">
              <option value="openai-codex">ChatGPT Codex</option>
              <option value="anthropic-claude-code">Claude Code</option>
              <option value="ollama">Ollama</option>
              <option value="lm-studio">LM Studio</option>
              <option value="openai-compatible">OpenAI-compatible</option>
            </select>
            <span>AI reviews changes before applying them</span>
          </div>
        </div>
        <div className="project-type-chips">
          {projectTypes.map(([id, label, framework], index) => (
            <button key={`${id}-${label}`} className={template === id && (id !== 'swiftui' || index === projectTypes.findIndex((item) => item[0] === id)) ? 'active' : ''} onClick={() => setTemplate(id)}>
              <strong>{label}</strong><small>{framework}</small>
            </button>
          ))}
        </div>
        <div className="project-location">
          <label><span>Project name</span><input value={name} onChange={(event) => setName(event.target.value)} placeholder="my-new-app" /></label>
          <label><span>Parent folder</span><button onClick={() => void chooseParent()}>{parent || 'Choose folder…'}</button></label>
          <button className="project-create-button" disabled={creating} onClick={() => void create()}>{creating ? 'Creating and validating…' : 'Create project'}</button>
        </div>
        {progress && ['scaffolding', 'setting-up', 'needs-setup'].includes(String(progress.status)) && (
          <div className="project-progress">
            <strong>{progress.status === 'needs-setup' ? 'Project created; SDK setup needs attention' : 'Kodex is building your project'}</strong>
            <span>{Array.isArray(progress.command) ? progress.command.join(' ') : String(progress.project ?? '')}</span>
            {progress.output ? <pre>{String(progress.output).slice(-1600)}</pre> : null}
          </div>
        )}
        {error && <div className="project-create-error">{error}</div>}
        <div className="project-open-existing">
          <button onClick={onOpenWorkspace}>Open an existing folder</button>
          {recentWorkspaces.slice(0, 4).map((recent) => <button key={recent.path} onClick={() => onOpenRecent(recent.path)}><strong>{recent.name}</strong><small>{recent.path}</small></button>)}
        </div>
      </section>
    </main>
  )
}
