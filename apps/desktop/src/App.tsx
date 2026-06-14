import { type CSSProperties, FormEvent, type ReactNode, useEffect, useRef, useState } from 'react'
import Editor, { type Monaco, type OnMount } from '@monaco-editor/react'
import TerminalPane, { type BottomPanelId, type TerminalLaunchRequest } from './TerminalPane'
import KodexCodeField from './KodexCodeField'
import { type PluginAppearance } from './PluginAgentSurface'
import ProviderBrandMark from './ProviderBrandMark'
import WelcomeScreen from './WelcomeScreen'
import KodexSystemIsland from './telemetry/KodexSystemIsland'

type Health = { status: string; service: string; version: string }
type Memory = { id: number; kind: string; title: string; content: string; created_at: string }
type Task = { id: number; title: string; details: string; status: string; created_at: string; updated_at: string }
type FileEntry = { name: string; kind: 'file' | 'directory'; path: string }
type FilesResponse = { path: string; kind: 'file' | 'directory'; entries: FileEntry[] }
type FilePayload = { path: string; content: string }
type AgentEditRange = { startLine: number; endLine: number }
type ActiveAgentEdit = {
  surface: AgentSurfaceId
  operation: 'created' | 'modified'
  path: string
  ranges: AgentEditRange[]
  status: string
  timestamp: string
}
type SearchResult = {
  path: string
  line: number
  column: number
  preview: string
  kind: 'text' | 'symbol'
  symbol?: string
}
type SearchResponse = {
  query: string
  mode: 'text' | 'symbols'
  results: SearchResult[]
  scanned_files: number
  truncated: boolean
}
type WorkspaceEvent = {
  sequence: number
  type: 'created' | 'modified' | 'deleted'
  path: string
  kind: 'file' | 'directory'
  time: string
}
type WorkspaceChangesResponse = {
  cursor: number
  events: WorkspaceEvent[]
  reset: boolean
}
type Snapshot = {
  id: number
  label: string
  workspace: string
  file_count: number
  total_bytes: number
  created_at: string
}
type SnapshotDiffFile = {
  path: string
  status: 'added' | 'modified' | 'deleted'
  binary: boolean
  additions: number
  deletions: number
  diff: string
}
type SnapshotDiff = {
  snapshot: Snapshot
  files: SnapshotDiffFile[]
  file_count: number
  additions: number
  deletions: number
}
type ProcessRecord = {
  id: number
  name: string
  command: string[]
  cwd: string
  pid: number
  status: string
  logs: { stream: string; line: string; time: string }[]
}
type OpenFile = {
  path: string
  content: string
  savedContent: string
  externallyChanged?: boolean
}
type ChatMessage = { role: 'user' | 'assistant'; content: string; attachments?: Attachment[] }
type ConversationPayload = {
  id: number
  title: string
  created_at?: string
  updated_at?: string
  messages: ChatMessage[]
  runs: AgentRun[]
}
type ConversationSummary = {
  id: number
  title: string
  created_at?: string
  updated_at?: string
}
type Panel = 'explorer' | 'search' | 'outline' | 'git' | 'run' | 'history' | 'memory' | 'tasks' | 'plugins'
type IconName = Panel | 'plus' | 'sparkles' | 'arrow-up' | 'stop' | 'paperclip' | 'folder' | 'file' | 'terminal' | 'check' | 'info' | 'context' | 'target' | 'open-files' | 'permission' | 'model' | 'chevron' | 'clean' | 'trash'
type Workspace = { path: string; name: string }
type CoreEvent = {
  schema_version: string
  sequence: number
  event: string
  time: string
  data: Record<string, unknown>
}
type CoreDiagnostics = {
  status: string
  version: string
  session_id: string
  authenticated: boolean
  workspace: string
  database: string
  watcher: boolean
  processes: { total: number; running: number }
  events: { sequence: number; retained: number }
  logs: { retained: number }
}
type DesktopSettings = {
  backgroundCore: boolean
  openaiBaseUrl?: string
  openaiModel?: string
  lmStudioBaseUrl?: string
  selectedProvider?: string
  selectedModel?: string
  theme?: ThemeId
  defaultPermission?: string
}
type ThemeId = 'black' | 'vscode' | 'high-contrast' | 'graphite' | 'midnight' | 'aurora' | 'ember' | 'ocean' | 'sakura' | 'forest' | 'solarized'
type ProjectIntelligence = {
  file_count: number
  summary: string
  languages: Array<{ name: string; files: number }>
  frameworks: string[]
  package_managers: string[]
  entry_points: string[]
  dependencies: string[]
  symbols: Array<unknown>
  imports: Array<unknown>
  routes: Array<unknown>
}
type EvaluationReport = {
  passed: boolean
  fixtures: Array<{ id: string; name: string; passed: boolean; detail: string }>
}
type BenchmarkReport = {
  passed: boolean
  scorecard: { total: number; passed: number; pass_rate: number; duration_ms: number }
  results: Array<{ id: string; name: string; exit_code: number; duration_ms: number }>
}
type ProjectSkill = {
  id: string
  name: string
  description: string
  path: string
  trust: 'trusted' | 'review' | 'disabled'
}
type StructuredMemory = {
  id: number
  scope: string
  owner_id: string
  title: string
  content: string
  tags: string[]
  confidence: number
  status: string
}
type GitState = {
  repository: boolean
  branch: string
  changes: Array<{ status: string; path: string }>
  branches: string[]
  summary?: string
}
type GitSummary = {
  source: string
  summary: string
  commit_message: string
  bullets: string[]
}
type WorkspaceSession = {
  open_files: string[]
  active_file: string
  unfinished_tasks: Task[]
}
type AgentProfile = {
  id: number
  agent_id: string
  version: number
  status: string
  name: string
  role: string
  prompt: string
  constitution: string
  workflow: Array<Record<string, unknown>>
  tool_policy: Record<string, unknown>
}
type ModelAdvice = {
  hardware: { platform: string; cpu: string; cpu_count: number; memory_bytes: number; disk_free_bytes: number; gpu: string }
  catalogue: Array<{ id: string; size: string; minimum_memory_gb: number; use: string }>
  recommended: Array<{ id: string; size: string; minimum_memory_gb: number; use: string }>
  runtimes: Array<{ id: string; available: boolean; models: Array<{ id: string }> }>
}
type McpServer = {
  id: number
  name: string
  command: string[]
  permission: string
  enabled: boolean
  status: string
  last_error: string
}
type RuntimeValidation = {
  ready: boolean
  checks: Record<string, string | boolean>
  platform: string
  packaging: Record<string, boolean>
}
type Provider = {
  id: string
  name: string
  kind: 'local' | 'cloud' | 'chatgpt' | 'claude'
  available: boolean
  health: string
  base_url: string
  detail: string
  latency_ms: number | null
  capabilities: string[]
  models: Array<{ id: string; name: string; context_length: number; supports_tools: boolean; capabilities: string[] }>
}
type DesktopPlugin = {
  id: string
  name: string
  description: string
  marketplace: string
  installed: boolean
  enabled: boolean
  version: string
  error?: string
  agentTab?: boolean
  uninstall?: string
  appearance: PluginAppearance
}
type AgentSurfaceId = 'kodex' | `plugin:${string}`
type RunProfile = {
  id: string
  name: string
  kind: string
  command: string
  cwd?: string
}
type AgentStep = {
  id: number
  kind: string
  status: string
  title: string
  data: Record<string, unknown>
}
type LiveAgentEvent = { label: string; detail?: string }
type Approval = {
  id: number
  kind: string
  status: string
  summary: string
  details: Record<string, unknown>
}
type AgentRun = {
  id: number
  conversation_id: number
  status: string
  mode: string
  permission: string
  pursue_goal: number
  model: string
  summary: string
  contract: { objective?: string; risk_level?: string; acceptance_criteria?: string[] }
  steps: AgentStep[]
  approvals: Approval[]
  failure_code?: string
  failure_details?: {
    code?: string
    message?: string
    actions?: string[]
    provider?: string
    model?: string
    retryable?: boolean
  }
  changed_files?: string[]
  validation?: { passed?: boolean; results?: Array<Record<string, unknown>> }
  recovery_attempts?: number
  access_scope?: string
  phase?: string
  requested_mode?: string
  resolved_intent?: 'chat' | 'plan_only' | 'plan_then_execute' | 'execute'
  intent_confidence?: number
  plan?: {
    objective?: string
    tasks?: string[]
    acceptance_criteria?: string[]
    affected_areas?: string[]
    risks?: string[]
    validation_strategy?: string
  }
  reflection?: Record<string, unknown>
  specialist_activity?: Array<{ role: string; approved?: boolean; summary?: string }>
  goal?: { objective?: string; acceptance_criteria?: string[] }
  tasks?: Array<{
    id: number
    position: number
    title: string
    status: string
    details?: Record<string, unknown>
  }>
  checkpoint?: Record<string, unknown>
  steering_messages?: Array<{ id: number; content: string; status: string }>
  provider_attempts?: Array<{
    id: number
    provider: string
    model: string
    status: string
    error: string
  }>
  completion_evidence?: {
    acceptance_criteria?: string[]
    changed_files?: string[]
    validation?: Array<Record<string, unknown>>
    review?: Record<string, { summary?: string }>
    provider?: { id?: string; model?: string }
  }
}
type Attachment = {
  name: string
  path: string
  kind: 'image' | 'text'
  mimeType: string
  content: string
  dataUrl: string
}
type LayoutSettings = {
  sidebar: number
  agent: number
  bottom: number
  sidebarOpen: boolean
  agentOpen: boolean
  bottomCollapsed: boolean
  bottomMaximized: boolean
  bottomPanel: BottomPanelId
}

const terminalStatuses = new Set(['completed', 'failed', 'cancelled', 'rejected', 'needs_review', 'interrupted'])
const themeMigrationKey = 'kodex.theme.vscode-default-v1'
const activeRunStatuses = new Set([
  'queued',
  'understanding',
  'planning',
  'working',
  'testing',
  'waiting_for_approval',
  'paused',
  'stopping',
])
const themes: Array<{ id: ThemeId; name: string; detail: string; colors: [string, string, string] }> = [
  { id: 'black', name: 'True Black', detail: 'Pure black Kodex workspace', colors: ['#000000', '#ffffff', '#383838'] },
  { id: 'vscode', name: 'VS Code Dark', detail: 'Familiar dark editor contrast', colors: ['#1e1e1e', '#007acc', '#3c3c3c'] },
  { id: 'high-contrast', name: 'High Contrast', detail: 'Maximum edge definition', colors: ['#000000', '#ffffff', '#ffd700'] },
  { id: 'graphite', name: 'Graphite', detail: 'Quiet neutral workspace', colors: ['#111214', '#f4f1ea', '#a6adb7'] },
  { id: 'midnight', name: 'Midnight', detail: 'Deep blue focus', colors: ['#07111f', '#7db7ff', '#345b88'] },
  { id: 'aurora', name: 'Aurora', detail: 'Teal and arctic light', colors: ['#071613', '#68e0c1', '#2c7c70'] },
  { id: 'ember', name: 'Ember', detail: 'Warm copper energy', colors: ['#190e0a', '#ff9b62', '#923f25'] },
  { id: 'ocean', name: 'Ocean', detail: 'Clear cyan depth', colors: ['#06151b', '#57d4ef', '#24758e'] },
  { id: 'sakura', name: 'Sakura', detail: 'Soft rose contrast', colors: ['#180f16', '#f4a7c1', '#8c4d69'] },
  { id: 'forest', name: 'Forest', detail: 'Moss and evergreen', colors: ['#0b150f', '#91d18b', '#3c7046'] },
  { id: 'solarized', name: 'Solarized', detail: 'Classic amber and cyan', colors: ['#07191d', '#d6a84b', '#2a8793'] },
]

const coreBase = window.codex?.coreBase ?? 'http://127.0.0.1:7799'
const coreToken = window.codex?.coreToken ?? ''

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (coreToken) headers.set('Authorization', `Bearer ${coreToken}`)
  const response = await fetch(`${coreBase}${path}`, { ...init, headers })
  if (!response.ok) throw new Error(`${path} returned ${response.status}`)
  return response.json() as Promise<T>
}

const icons: Record<Panel, IconName> = {
  explorer: 'explorer',
  search: 'search',
  outline: 'outline',
  git: 'git',
  run: 'run',
  history: 'history',
  memory: 'memory',
  tasks: 'tasks',
  plugins: 'plugins',
}

const panelLabels: Record<Panel, string> = {
  explorer: 'Explorer',
  search: 'Search',
  outline: 'Outline',
  git: 'Source Control',
  run: 'Run and Debug',
  history: 'Timeline',
  memory: 'Memory',
  tasks: 'Tasks',
  plugins: 'Plugins',
}
const primaryPanels: Panel[] = ['explorer', 'search', 'git', 'run', 'plugins']

function fileGlyph(entry: FileEntry) {
  if (entry.kind === 'directory') return '▱'
  if (entry.name.endsWith('.tsx') || entry.name.endsWith('.ts')) return 'TS'
  if (entry.name.endsWith('.py')) return 'PY'
  if (entry.name.endsWith('.json')) return '{}'
  if (entry.name.endsWith('.md')) return 'M'
  return '·'
}

function editorLanguage(path: string) {
  const extension = path.split('.').at(-1)?.toLowerCase()
  const languages: Record<string, string> = {
    c: 'c',
    cpp: 'cpp',
    css: 'css',
    go: 'go',
    html: 'html',
    java: 'java',
    js: 'javascript',
    json: 'json',
    jsx: 'javascript',
    md: 'markdown',
    py: 'python',
    rs: 'rust',
    sh: 'shell',
    sql: 'sql',
    ts: 'typescript',
    tsx: 'typescript',
    yaml: 'yaml',
    yml: 'yaml',
  }
  return extension ? languages[extension] ?? 'plaintext' : 'plaintext'
}

function fileOutline(file: OpenFile | null) {
  if (!file) return []
  const patterns = [
    /^\s*(?:export\s+)?(?:async\s+)?(?:function|class|interface|type|enum)\s+([A-Za-z_$][\w$]*)/,
    /^\s*(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=/,
    /^\s*(?:def|class)\s+([A-Za-z_][\w]*)/,
    /^\s*(?:public|private|protected)?\s*(?:static\s+)?(?:async\s+)?([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{/,
    /^\s*#{1,6}\s+(.+)/,
  ]
  return file.content.split('\n').flatMap((line, index) => {
    for (const pattern of patterns) {
      const match = line.match(pattern)
      if (match) return [{ name: match[1].trim(), line: index + 1, preview: line.trim() }]
    }
    return []
  }).slice(0, 250)
}

function activityKind(step: AgentStep) {
  const title = step.title.toLowerCase()
  if (step.kind === 'validation' || title.includes('test') || title.includes('lint') || title.includes('build')) return 'testing'
  if (title === 'run_command' || title.includes('command')) return 'running'
  if (['create_file', 'edit_file', 'write_file', 'apply_patch', 'delete_file'].includes(title)) return 'editing'
  if (step.kind === 'thinking') return 'thinking'
  return 'reviewing'
}

function stepPresentation(step: AgentStep) {
  const kind = activityKind(step)
  const title = step.title.replaceAll('_', ' ')
  const path = typeof step.data.path === 'string' ? step.data.path : ''
  const command = typeof step.data.command === 'string' ? step.data.command : ''
  const error = typeof step.data.message === 'string'
    ? step.data.message
    : typeof step.data.error === 'string'
      ? step.data.error
      : ''
  const output = typeof step.data.output === 'string'
    ? step.data.output.trim().split('\n').filter(Boolean).at(-1) ?? ''
    : ''
  const labels = {
    thinking: 'Thinking',
    editing: title === 'delete file' ? 'Deleting a file' : title === 'apply patch' ? 'Editing a file' : 'Creating a file',
    running: 'Running a command',
    testing: 'Checking the work',
    reviewing: title === 'read file'
      ? 'Reading a file'
      : title === 'list files'
        ? 'Inspecting the workspace'
        : title === 'fetch url' ? 'Gathering web data' : 'Reviewing the workspace',
  }
  const ranges = Array.isArray(step.data.changed_ranges)
    ? step.data.changed_ranges
        .map((range) => {
          if (!range || typeof range !== 'object') return ''
          const item = range as Record<string, unknown>
          return typeof item.start_line === 'number'
            ? `lines ${item.start_line}-${item.end_line ?? item.start_line}`
            : ''
        })
        .filter(Boolean)
        .join(', ')
    : ''
  const friendlyErrors: Record<string, string> = {
    missing_field: 'The model omitted a required value. Kodex will correct the action.',
    placeholder_value: 'The model used a placeholder instead of a real path.',
    prose_command: 'The command contained instructions instead of executable shell syntax.',
    anchor_not_found: 'The file changed or the edit target was not found.',
    no_progress: 'Kodex is changing strategy after a no-progress cycle.',
  }
  const code = typeof step.data.code === 'string' ? step.data.code : ''
  const url = typeof step.data.url === 'string' ? step.data.url : ''
  return {
    kind,
    label: labels[kind],
    detail: friendlyErrors[code]
      || (path && ranges ? `${path} · ${ranges}` : '')
      || path
      || url
      || command
      || error
      || output
      || (title !== labels[kind].toLowerCase() ? title : ''),
  }
}

type ConversationProgressItem =
  | { id: string; type: 'narrative'; text: string }
  | { id: string; type: 'activity'; kind: string; label: string; detail?: string; steps: AgentStep[]; additions?: number; deletions?: number }

function diffLineCounts(step: AgentStep) {
  const diff = typeof step.data.diff === 'string' ? step.data.diff : ''
  if (diff) {
    return diff.split('\n').reduce(
      (counts, line) => {
        if (line.startsWith('+') && !line.startsWith('+++')) counts.additions += 1
        if (line.startsWith('-') && !line.startsWith('---')) counts.deletions += 1
        return counts
      },
      { additions: 0, deletions: 0 },
    )
  }
  if (step.data.created && Array.isArray(step.data.changed_ranges)) {
    const additions = step.data.changed_ranges.reduce((total, range) => {
      if (!range || typeof range !== 'object') return total
      const item = range as Record<string, unknown>
      const start = typeof item.start_line === 'number' ? item.start_line : 1
      const end = typeof item.end_line === 'number' ? item.end_line : start
      return total + Math.max(1, end - start + 1)
    }, 0)
    return { additions, deletions: 0 }
  }
  return { additions: 0, deletions: 0 }
}

function agentEditFromStep(step: AgentStep, surface: AgentSurfaceId): ActiveAgentEdit | null {
  const path = typeof step.data.path === 'string' ? step.data.path : ''
  if (!path || step.data.deleted || activityKind(step) !== 'editing') return null
  const ranges = Array.isArray(step.data.changed_ranges)
    ? step.data.changed_ranges.flatMap((range) => {
        if (!range || typeof range !== 'object') return []
        const value = range as Record<string, unknown>
        const startLine = typeof value.start_line === 'number' ? Math.max(1, value.start_line) : 1
        const endLine = typeof value.end_line === 'number' ? Math.max(startLine, value.end_line) : startLine
        return [{ startLine, endLine }]
      })
    : []
  return {
    surface,
    operation: step.data.created ? 'created' : 'modified',
    path,
    ranges: ranges.length ? ranges : [{ startLine: 1, endLine: 1 }],
    status: step.status,
    timestamp: String(step.id),
  }
}

function conversationProgress(steps: AgentStep[]): ConversationProgressItem[] {
  const items: ConversationProgressItem[] = []
  let pending: AgentStep[] = []
  const flushPending = () => {
    if (!pending.length) return
    const groups = new Map<string, AgentStep[]>()
    pending.forEach((step) => {
      const kind = activityKind(step)
      const key = step.status === 'failed' ? `failed-${step.id}` : kind
      groups.set(key, [...(groups.get(key) ?? []), step])
    })
    groups.forEach((group, key) => {
      const kind = activityKind(group[0])
      const paths = [...new Set(group.map((step) => typeof step.data.path === 'string' ? step.data.path : '').filter(Boolean))]
      const labels: Record<string, string> = {
        reviewing: paths.length ? `Read ${paths.length} ${paths.length === 1 ? 'file' : 'files'} and inspected the workspace` : 'Inspected the workspace',
        running: `Ran ${group.length} ${group.length === 1 ? 'command' : 'commands'}`,
        testing: `Ran ${group.length} ${group.length === 1 ? 'check' : 'checks'}`,
      }
      items.push({
        id: `activity-${key}-${group[0].id}`,
        type: 'activity',
        kind,
        label: group[0].status === 'failed' ? stepPresentation(group[0]).label : labels[kind] || 'Reviewed the work',
        detail: group[0].status === 'failed' ? stepPresentation(group[0]).detail : paths.join(', '),
        steps: group,
      })
    })
    pending = []
  }

  steps.forEach((step) => {
    if (step.kind === 'thinking') {
      flushPending()
      const summary = typeof step.data.summary === 'string' ? step.data.summary.trim() : ''
      const previous = items.at(-1)
      if (summary && (previous?.type !== 'narrative' || previous.text !== summary)) {
        items.push({ id: `narrative-${step.id}`, type: 'narrative', text: summary })
      }
      return
    }
    if (activityKind(step) === 'editing') {
      flushPending()
      const path = typeof step.data.path === 'string' ? step.data.path : step.title.replaceAll('_', ' ')
      const counts = diffLineCounts(step)
      items.push({
        id: `edit-${step.id}`,
        type: 'activity',
        kind: 'editing',
        label: step.data.deleted ? 'Deleted' : step.data.created ? 'Created' : 'Edited',
        detail: path,
        steps: [step],
        ...counts,
      })
      return
    }
    pending.push(step)
  })
  flushPending()
  return items
}

function isPlanRun(run: AgentRun) {
  return run.mode === 'plan' || run.requested_mode === 'plan' || run.resolved_intent === 'plan_only'
}

function agentCoreState(run: AgentRun | null) {
  if (!run) return { id: 'ready', label: 'Ready' }
  if (run.failure_code === 'context_overflow') return { id: 'attention', label: 'Context limit' }
  if (run.status === 'needs_review' || run.status === 'failed') return { id: 'attention', label: 'Needs review' }
  if (run.status === 'stopping') return { id: 'stopping', label: 'Stopping' }
  if (run.status === 'cancelled') return { id: 'cancelled', label: 'Stopped' }
  if (run.status === 'waiting_for_approval') return { id: 'waiting', label: 'Awaiting approval' }
  if (run.status === 'paused') return { id: 'paused', label: 'Paused' }
  if (run.status === 'completed') return { id: 'complete', label: 'Complete' }
  if (run.phase === 'validate' || run.phase === 'review' || run.status === 'testing') return { id: 'testing', label: 'Validating' }
  if (run.phase === 'execute' || run.phase === 'repair') return { id: 'editing', label: run.phase === 'repair' ? 'Repairing' : 'Editing' }
  return { id: 'thinking', label: 'Thinking' }
}

function liveEventPresentation(event: CoreEvent): LiveAgentEvent | null {
  const data = event.data
  if (event.event === 'agent.token') return { label: 'Thinking through the next change' }
  if (event.event === 'agent.tool_output') {
    const line = typeof data.line === 'string' ? data.line.trim() : ''
    return { label: 'Command output', detail: line || undefined }
  }
  if (event.event === 'agent.tool_started') {
    const tool = typeof data.tool === 'string' ? data.tool.replaceAll('_', ' ') : 'workspace tool'
    const args = data.arguments && typeof data.arguments === 'object'
      ? data.arguments as Record<string, unknown>
      : {}
    const detail = typeof args.path === 'string'
      ? args.path
      : typeof args.command === 'string'
        ? args.command
        : typeof args.url === 'string'
          ? args.url
        : undefined
    return { label: `${tool.charAt(0).toUpperCase()}${tool.slice(1)}`, detail }
  }
  if (event.event === 'agent.validation') return { label: 'Validating the result' }
  if (event.event === 'agent.thinking' || event.event === 'agent.model_started') return { label: 'Thinking' }
  return null
}

function agentEditFromEvent(event: CoreEvent, surface: AgentSurfaceId): ActiveAgentEdit | null {
  const data = event.data
  const argumentsData = data.arguments && typeof data.arguments === 'object'
    ? data.arguments as Record<string, unknown>
    : {}
  const resultData = data.result && typeof data.result === 'object'
    ? data.result as Record<string, unknown>
    : {}
  const tool = typeof data.tool === 'string' ? data.tool : ''
  const isFileEdit = ['create_file', 'edit_file'].includes(tool)
    || event.event === 'file.patch_applied'
  if (!isFileEdit) return null
  const path = [data.path, resultData.path, argumentsData.path].find(
    (value): value is string => typeof value === 'string' && Boolean(value),
  ) ?? ''
  if (!path) return null
  const rawRanges = [data.changed_ranges, resultData.changed_ranges, argumentsData.changed_ranges]
    .find(Array.isArray)
  const ranges = Array.isArray(rawRanges)
    ? rawRanges.flatMap((range) => {
        if (!range || typeof range !== 'object') return []
        const value = range as Record<string, unknown>
        const startLine = typeof value.start_line === 'number' ? Math.max(1, value.start_line) : 1
        const endLine = typeof value.end_line === 'number' ? Math.max(startLine, value.end_line) : startLine
        return [{ startLine, endLine }]
      })
    : []
  return {
    surface,
    operation: tool === 'create_file' || data.created === true || resultData.created === true
      ? 'created'
      : 'modified',
    path,
    ranges: ranges.length ? ranges : [{ startLine: 1, endLine: 1 }],
    status: event.event === 'agent.tool_started' ? 'working' : 'completed',
    timestamp: event.time,
  }
}

function planItems(summary: string) {
  const lines = summary
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => line.replace(/^#{1,4}\s*/, '').replace(/^[-*]\s+/, '').replace(/^\d+[.)]\s+/, ''))
  return lines.length > 1 ? lines : [summary.trim()]
}

function configureMonaco(monaco: Monaco) {
  const palettes: Record<ThemeId, { background: string; surface: string; accent: string; selection: string }> = {
    black: { background: '#000000', surface: '#080808', accent: '#ffffff', selection: '#303030' },
    vscode: { background: '#1e1e1e', surface: '#252526', accent: '#569cd6', selection: '#264f78' },
    'high-contrast': { background: '#000000', surface: '#050505', accent: '#ffd700', selection: '#4d4300' },
    graphite: { background: '#111214', surface: '#17191c', accent: '#d9dde3', selection: '#35404f' },
    midnight: { background: '#07111f', surface: '#0b192b', accent: '#7db7ff', selection: '#173a63' },
    aurora: { background: '#071613', surface: '#0d211d', accent: '#68e0c1', selection: '#195245' },
    ember: { background: '#190e0a', surface: '#25130d', accent: '#ff9b62', selection: '#67321f' },
    ocean: { background: '#06151b', surface: '#0a2029', accent: '#57d4ef', selection: '#155268' },
    sakura: { background: '#180f16', surface: '#241721', accent: '#f4a7c1', selection: '#60384d' },
    forest: { background: '#0b150f', surface: '#122019', accent: '#91d18b', selection: '#315a3c' },
    solarized: { background: '#07191d', surface: '#0d2428', accent: '#d6a84b', selection: '#28515a' },
  }
  for (const [id, palette] of Object.entries(palettes) as Array<[ThemeId, typeof palettes[ThemeId]]>) {
    monaco.editor.defineTheme(`codex-${id}`, {
      base: 'vs-dark',
      inherit: true,
      rules: [
        { token: 'comment', foreground: '738078', fontStyle: 'italic' },
        { token: 'keyword', foreground: palette.accent.slice(1) },
        { token: 'string', foreground: 'a9cf9f' },
        { token: 'number', foreground: 'deb878' },
        { token: 'type', foreground: '76c7bd' },
      ],
      colors: {
        'editor.background': palette.background,
        'editor.foreground': '#d6d8dc',
        'editorLineNumber.foreground': '#596068',
        'editorLineNumber.activeForeground': '#c5c9ce',
        'editorCursor.foreground': palette.accent,
        'editor.selectionBackground': palette.selection,
        'editor.inactiveSelectionBackground': `${palette.selection}aa`,
        'editor.lineHighlightBackground': palette.surface,
        'editorIndentGuide.background1': '#283038',
        'editorIndentGuide.activeBackground1': '#4b5864',
        'editorGutter.background': palette.background,
        'minimap.background': palette.background,
        'scrollbarSlider.background': '#7b849044',
        'scrollbarSlider.hoverBackground': '#929ba866',
      },
    })
  }
}

function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [workspace, setWorkspace] = useState<Workspace | null>(null)
  const [recentWorkspaces, setRecentWorkspaces] = useState<Workspace[]>([])
  const [activePanel, setActivePanel] = useState<Panel>('explorer')
  const [currentDirectory, setCurrentDirectory] = useState('.')
  const [files, setFiles] = useState<FileEntry[]>([])
  const [treeChildren, setTreeChildren] = useState<Record<string, FileEntry[]>>({})
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(() => new Set())
  const [loadingFolders, setLoadingFolders] = useState<Set<string>>(() => new Set())
  const [selectedEntry, setSelectedEntry] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchMode, setSearchMode] = useState<'text' | 'symbols'>('text')
  const [searchResults, setSearchResults] = useState<SearchResult[]>([])
  const [searchMeta, setSearchMeta] = useState('')
  const [snapshots, setSnapshots] = useState<Snapshot[]>([])
  const [selectedSnapshot, setSelectedSnapshot] = useState<SnapshotDiff | null>(null)
  const [openFiles, setOpenFiles] = useState<OpenFile[]>([])
  const [activeFile, setActiveFile] = useState<string | null>(null)
  const [processes, setProcesses] = useState<ProcessRecord[]>([])
  const [memories, setMemories] = useState<Memory[]>([])
  const [tasks, setTasks] = useState<Task[]>([])
  const [chatInput, setChatInput] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [eventConnected, setEventConnected] = useState(false)
  const [, setLastCoreEvent] = useState<CoreEvent | null>(null)
  const [commandPaletteOpen, setCommandPaletteOpen] = useState(false)
  const [commandQuery, setCommandQuery] = useState('')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsSection, setSettingsSection] = useState<'general' | 'appearance' | 'terminal' | 'run-debug' | 'models' | 'agent' | 'studio' | 'project' | 'advisor' | 'integrations' | 'security' | 'diagnostics' | 'about'>('general')
  const [diagnostics, setDiagnostics] = useState<CoreDiagnostics | null>(null)
  const [projectIntelligence, setProjectIntelligence] = useState<ProjectIntelligence | null>(null)
  const [evaluationReport, setEvaluationReport] = useState<EvaluationReport | null>(null)
  const [benchmarkReport, setBenchmarkReport] = useState<BenchmarkReport | null>(null)
  const [projectSkills, setProjectSkills] = useState<ProjectSkill[]>([])
  const [structuredMemories, setStructuredMemories] = useState<StructuredMemory[]>([])
  const [memorySuggestions, setMemorySuggestions] = useState<Array<Omit<StructuredMemory, 'id'>>>([])
  const [gitState, setGitState] = useState<GitState | null>(null)
  const [gitDiff, setGitDiff] = useState('')
  const [gitSummary, setGitSummary] = useState<GitSummary | null>(null)
  const [agentProfiles, setAgentProfiles] = useState<AgentProfile[]>([])
  const [modelAdvice, setModelAdvice] = useState<ModelAdvice | null>(null)
  const [mcpServers, setMcpServers] = useState<McpServer[]>([])
  const [runtimeValidation, setRuntimeValidation] = useState<RuntimeValidation | null>(null)
  const [studioDraft, setStudioDraft] = useState({
    agentId: 'workspace-engineer',
    name: 'Workspace Engineer',
    role: 'coder',
    prompt: 'Inspect the workspace, implement complete changes, and validate every acceptance criterion.',
    constitution: 'Stay inside the workspace, preserve user changes, request approval for risky actions, and never claim success without validation.',
    workflow: 'plan, implement, test, review',
    tools: 'list_files, read_file, search, create_file, edit_file, run_command',
  })
  const [desktopSettings, setDesktopSettings] = useState<DesktopSettings>({ backgroundCore: false })
  const [providers, setProviders] = useState<Provider[]>([])
  const [runProfiles, setRunProfiles] = useState<RunProfile[]>([])
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [conversationId, setConversationId] = useState<number | null>(null)
  const [currentRun, setCurrentRun] = useState<AgentRun | null>(null)
  const [agentHeaderMenu, setAgentHeaderMenu] = useState<'history' | 'clean' | null>(null)
  const [liveAgentEvent, setLiveAgentEvent] = useState<LiveAgentEvent | null>(null)
  const [liveAgentEdit, setLiveAgentEdit] = useState<ActiveAgentEdit | null>(null)
  const [controlMenuOpen, setControlMenuOpen] = useState(false)
  const [composerSelectOpen, setComposerSelectOpen] = useState<'permission' | 'model' | null>(null)
  const [expandedActivity, setExpandedActivity] = useState<string | null>(null)
  const [includeIdeContext, setIncludeIdeContext] = useState(() => localStorage.getItem('kodex.ide-context') !== 'false')
  const [planMode, setPlanMode] = useState(false)
  const [pursueGoal, setPursueGoal] = useState(() => {
    const migrationKey = 'kodex.pursue-goal-default-v2'
    if (localStorage.getItem(migrationKey) !== 'done') {
      localStorage.setItem(migrationKey, 'done')
      localStorage.setItem('kodex.pursue-goal', 'false')
      return false
    }
    return localStorage.getItem('kodex.pursue-goal') === 'true'
  })
  const [permission, setPermission] = useState('full-access')
  const [selectedModel, setSelectedModel] = useState('auto')
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [composerDragging, setComposerDragging] = useState(false)
  const [providerKey, setProviderKey] = useState('')
  const [providerKeyConfigured, setProviderKeyConfigured] = useState(false)
  const [codexStatus, setCodexStatus] = useState<{ installed: boolean; authenticated: boolean; message: string } | null>(null)
  const [claudeStatus, setClaudeStatus] = useState<{ installed: boolean; authenticated: boolean; message: string } | null>(null)
  const [plugins, setPlugins] = useState<DesktopPlugin[]>([])
  const [installingPlugin, setInstallingPlugin] = useState<string | null>(null)
  const [agentTab, setAgentTab] = useState<AgentSurfaceId>('kodex')
  const themeMigrationPendingRef = useRef(localStorage.getItem(themeMigrationKey) !== 'done')
  const [theme, setTheme] = useState<ThemeId>(() => {
    const saved = localStorage.getItem('codex.theme') as ThemeId | null
    if (themeMigrationPendingRef.current && (!saved || saved === 'black')) return 'vscode'
    return themes.some((item) => item.id === saved) ? saved! : 'vscode'
  })
  const [terminalLaunch, setTerminalLaunch] = useState<TerminalLaunchRequest | null>(null)
  const [bootVisible, setBootVisible] = useState(true)
  const [stopPending, setStopPending] = useState(false)
  const [onboardingOpen, setOnboardingOpen] = useState(
    () => localStorage.getItem('codex.onboarding.completed') !== 'true',
  )
  const [layout, setLayout] = useState<LayoutSettings>(() => {
    try {
      return {
        sidebar: 248,
        agent: 390,
        bottom: 250,
        sidebarOpen: true,
        agentOpen: true,
        bottomCollapsed: true,
        bottomMaximized: false,
        bottomPanel: 'terminal',
        ...JSON.parse(localStorage.getItem('codex.layout') ?? '{}'),
      }
    } catch {
      return {
        sidebar: 248,
        agent: 390,
        bottom: 250,
        sidebarOpen: true,
        agentOpen: true,
        bottomCollapsed: true,
        bottomMaximized: false,
        bottomPanel: 'terminal',
      }
    }
  })
  const [error, setError] = useState<string | null>(null)
  const saveActiveFileRef = useRef<() => Promise<void>>(async () => undefined)
  const currentDirectoryRef = useRef(currentDirectory)
  const openFilesRef = useRef(openFiles)
  const treeChildrenRef = useRef(treeChildren)
  const workspaceCursorRef = useRef(0)
  const coreEventCursorRef = useRef(0)
  const currentRunIdRef = useRef<number | null>(null)
  const agentTabRef = useRef<AgentSurfaceId>(agentTab)
  const workspaceSessionHydratedRef = useRef(false)
  const chatEndRef = useRef<HTMLDivElement | null>(null)
  const composerRef = useRef<HTMLFormElement | null>(null)
  const chatInputRef = useRef<HTMLTextAreaElement | null>(null)
  const editorRef = useRef<Parameters<OnMount>[0] | null>(null)
  const agentDecorationsRef = useRef<string[]>([])
  const viewedAgentEditRef = useRef<ActiveAgentEdit | null>(null)
  currentDirectoryRef.current = currentDirectory
  openFilesRef.current = openFiles
  treeChildrenRef.current = treeChildren
  currentRunIdRef.current = currentRun?.id ?? null
  agentTabRef.current = agentTab

  const selectedFile = openFiles.find((file) => file.path === activeFile) ?? null
  const outlineItems = fileOutline(selectedFile)
  const running = processes.filter((process) => process.status === 'running')
  const agentActive = Boolean(currentRun && activeRunStatuses.has(currentRun.status))
  const pendingApprovals = currentRun?.approvals.filter((approval) => approval.status === 'pending') ?? []
  const permissionLabel: Record<string, string> = {
    'read-only': 'Read only',
    'confirm-edits': 'Ask for approval',
    workspace: 'Workspace access',
    'full-access': 'Full access',
  }
  const selectedModelLabel = selectedModel === 'auto'
    ? 'Auto'
    : providers.flatMap((provider) =>
      provider.models.map((model) => ({
        id: `${provider.id}:${model.id}`,
        label: model.name,
      })),
    ).find((model) => model.id === selectedModel)?.label ?? selectedModel.split(':').at(-1) ?? 'Auto'
  const changedAgentPaths = new Set([
    ...(currentRun?.changed_files ?? []),
    ...(liveAgentEdit?.path ? [liveAgentEdit.path] : []),
    ...(currentRun?.steps ?? [])
      .filter((step) => activityKind(step) === 'editing')
      .map((step) => typeof step.data.path === 'string' ? step.data.path : '')
      .filter(Boolean),
  ])
  const ideStyle = {
    '--sidebar-width': `${layout.sidebarOpen ? layout.sidebar : 0}px`,
    '--agent-width': `${layout.agentOpen ? layout.agent : 0}px`,
    '--bottom-height': `${layout.bottomCollapsed ? 34 : layout.bottom}px`,
  } as CSSProperties

  function persistLayout(next: LayoutSettings) {
    setLayout(next)
    localStorage.setItem('codex.layout', JSON.stringify(next))
  }

  function applyLoadedDesktopSettings(settings: DesktopSettings) {
    const localTheme = localStorage.getItem('codex.theme') as ThemeId | null
    const hasPreservedLocalTheme = Boolean(
      localTheme
      && localTheme !== 'black'
      && themes.some((item) => item.id === localTheme),
    )
    const shouldMigrate = themeMigrationPendingRef.current
      && (!settings.theme || settings.theme === 'black')
      && !hasPreservedLocalTheme
    const nextTheme = shouldMigrate
      ? 'vscode'
      : themeMigrationPendingRef.current && hasPreservedLocalTheme ? localTheme! : settings.theme
    const shouldSyncTheme = themeMigrationPendingRef.current && nextTheme !== settings.theme
    setDesktopSettings(shouldSyncTheme ? { ...settings, theme: nextTheme } : settings)
    if (settings.defaultPermission) setPermission(settings.defaultPermission)
    if (settings.selectedModel) setSelectedModel(settings.selectedModel)
    if (nextTheme && themes.some((item) => item.id === nextTheme)) {
      setTheme(nextTheme)
      localStorage.setItem('codex.theme', nextTheme)
    }
    if (themeMigrationPendingRef.current) {
      themeMigrationPendingRef.current = false
      localStorage.setItem(themeMigrationKey, 'done')
      if (shouldSyncTheme) void window.codex?.updateSettings({ theme: nextTheme })
    }
  }

  function resizePanel(kind: 'sidebar' | 'agent' | 'bottom', event: React.PointerEvent) {
    event.preventDefault()
    const startX = event.clientX
    const startY = event.clientY
    const start = layout
    const onMove = (move: PointerEvent) => {
      if (kind === 'sidebar') {
        persistLayout({ ...start, sidebar: Math.min(460, Math.max(180, start.sidebar + move.clientX - startX)) })
      } else if (kind === 'agent') {
        persistLayout({ ...start, agent: Math.min(620, Math.max(280, start.agent - move.clientX + startX)) })
      } else {
        persistLayout({ ...start, bottom: Math.min(480, Math.max(130, start.bottom - move.clientY + startY)) })
      }
    }
    const onUp = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }

  async function loadWorkspaceShell(): Promise<Workspace | null> {
    if (!window.codex) return null
    const [current, recent] = await Promise.all([
      window.codex.currentWorkspace(),
      window.codex.recentWorkspaces(),
    ])
    setWorkspace(current)
    setRecentWorkspaces(recent)
    return current
  }

  async function loadDiagnostics() {
    const [core, desktop, intelligence, evaluations, skills, memoryItems, suggestions, git, profiles, advisor, mcp, runtime, codex, claude] = await Promise.allSettled([
      request<CoreDiagnostics>('/v1/diagnostics'),
      window.codex?.getSettings() ?? Promise.resolve({ backgroundCore: false }),
      request<ProjectIntelligence>('/v1/project-intelligence'),
      request<EvaluationReport>('/v1/evaluations'),
      request<ProjectSkill[]>('/v1/skills'),
      request<StructuredMemory[]>('/v1/memory'),
      request<Array<Omit<StructuredMemory, 'id'>>>('/v1/memory/suggestions'),
      request<GitState>('/v1/git'),
      request<AgentProfile[]>('/v1/agent-studio'),
      request<ModelAdvice>('/v1/model-advisor'),
      request<McpServer[]>('/v1/mcp/servers'),
      request<RuntimeValidation>('/v1/runtime-validation'),
      request<{ installed: boolean; authenticated: boolean; message: string }>('/v1/providers/codex/status'),
      request<{ installed: boolean; authenticated: boolean; message: string }>('/v1/providers/claude/status'),
    ])
    if (core.status === 'fulfilled') setDiagnostics(core.value as CoreDiagnostics)
    if (desktop.status === 'fulfilled') {
      const settings = desktop.value as DesktopSettings
      applyLoadedDesktopSettings(settings)
    }
    if (intelligence.status === 'fulfilled') setProjectIntelligence(intelligence.value as ProjectIntelligence)
    if (evaluations.status === 'fulfilled') setEvaluationReport(evaluations.value as EvaluationReport)
    if (skills.status === 'fulfilled') setProjectSkills(skills.value as ProjectSkill[])
    if (memoryItems.status === 'fulfilled') setStructuredMemories(memoryItems.value)
    if (suggestions.status === 'fulfilled') setMemorySuggestions(suggestions.value)
    if (git.status === 'fulfilled') setGitState(git.value)
    if (profiles.status === 'fulfilled') setAgentProfiles(profiles.value)
    if (advisor.status === 'fulfilled') setModelAdvice(advisor.value)
    if (mcp.status === 'fulfilled') setMcpServers(mcp.value)
    if (runtime.status === 'fulfilled') setRuntimeValidation(runtime.value)
    if (codex.status === 'fulfilled') setCodexStatus(codex.value)
    if (claude.status === 'fulfilled') setClaudeStatus(claude.value)
  }

  async function refreshGit() {
    setGitState(await request<GitState>('/v1/git'))
  }

  async function generateGitSummary() {
    setGitSummary(
      await request<GitSummary>('/v1/git/summary', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ staged: false, kind: 'change' }),
      }),
    )
  }

  async function updateSkillTrust(skill: ProjectSkill, trust: ProjectSkill['trust']) {
    const updated = await request<ProjectSkill>(
      `/v1/skills/${encodeURIComponent(skill.id)}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ trust }),
      },
    )
    setProjectSkills((current) =>
      current.map((item) => (item.id === updated.id ? updated : item)),
    )
  }

  async function restoreWorkspaceSession() {
    try {
      const session = await request<WorkspaceSession>('/v1/workspace/session')
      const restored = await Promise.all(
        session.open_files.map((path) =>
          request<FilePayload>(`/v1/file?path=${encodeURIComponent(path)}`).catch(() => null),
        ),
      )
      const open = restored
        .filter((item): item is FilePayload => item !== null)
        .map((item) => ({
          path: item.path,
          content: item.content,
          savedContent: item.content,
        }))
      setOpenFiles(open)
      setActiveFile(
        open.some((file) => file.path === session.active_file)
          ? session.active_file
          : open[0]?.path ?? null,
      )
      if (session.unfinished_tasks.length) setTasks(session.unfinished_tasks)
    } finally {
      workspaceSessionHydratedRef.current = true
    }
  }

  async function reviewGitFile(path: string, staged = false) {
    const payload = await request<{ diff: string }>(
      `/v1/git/diff?path=${encodeURIComponent(path)}&staged=${staged}`,
    )
    setGitDiff(payload.diff || 'No textual diff is available for this file.')
  }

  async function mutateGit(action: 'stage' | 'unstage', path: string) {
    await request(`/v1/git/${action}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths: [path] }),
    })
    await refreshGit()
  }

  async function acceptMemorySuggestion(suggestion: Omit<StructuredMemory, 'id'>) {
    await request('/v1/memory', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...suggestion, status: 'active', resolve_conflicts: true }),
    })
    setStructuredMemories(await request<StructuredMemory[]>('/v1/memory'))
    setMemorySuggestions((current) => current.filter((item) => item.title !== suggestion.title))
  }

  async function createStarterAgent() {
    await request('/v1/agent-studio', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        agent_id: studioDraft.agentId,
        name: studioDraft.name,
        role: studioDraft.role,
        prompt: studioDraft.prompt,
        constitution: studioDraft.constitution,
        workflow: studioDraft.workflow.split(',').map((action) => ({ action: action.trim() })).filter((item) => item.action),
        tool_policy: { allow: studioDraft.tools.split(',').map((tool) => tool.trim()).filter(Boolean) },
      }),
    })
    setAgentProfiles(await request<AgentProfile[]>('/v1/agent-studio'))
  }

  async function createDatabaseBackup() {
    await request('/v1/database/backups', { method: 'POST' })
    setRuntimeValidation(await request<RuntimeValidation>('/v1/runtime-validation'))
  }

  async function runBenchmarks() {
    setBenchmarkReport(
      await request<BenchmarkReport>('/v1/benchmarks/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ adapter: 'local' }),
      }),
    )
  }

  async function loadAgentShell() {
    const [providerItems, conversationItems, profiles] = await Promise.all([
      request<Provider[]>('/v1/providers'),
      request<ConversationSummary[]>('/v1/conversations'),
      request<RunProfile[]>('/v1/run-profiles'),
    ])
    setProviders(providerItems)
    setConversations(conversationItems)
    setRunProfiles(profiles)
    const surfaceKey = `kodex.surface-conversation.${workspace?.path ?? 'default'}.kodex`
    const storedConversation = Number(localStorage.getItem(surfaceKey))
    let conversationIdToLoad = conversationItems.some((item) => item.id === storedConversation)
      ? storedConversation
      : conversationItems[0]?.id
    if (!conversationIdToLoad) {
      const created = await request<{ id: number }>('/v1/conversations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: 'New conversation' }),
      })
      conversationIdToLoad = created.id
      setConversations(await request<ConversationSummary[]>('/v1/conversations'))
    }
    await openConversation(conversationIdToLoad, 'kodex')
  }

  async function openConversation(id: number, surface: AgentSurfaceId = agentTab) {
    const conversation = await request<ConversationPayload>(`/v1/conversations/${id}`)
    localStorage.setItem(
      `kodex.surface-conversation.${workspace?.path ?? 'default'}.${surface}`,
      String(id),
    )
    setConversationId(conversation.id)
    setMessages(
      conversation.messages
        .filter((message) => message.role === 'user' || message.role === 'assistant')
        .map((message) => ({ role: message.role, content: message.content })),
    )
    const latestRun = conversation.runs[0] ?? null
    setCurrentRun(latestRun && (activeRunStatuses.has(latestRun.status) || ['interrupted', 'needs_review'].includes(latestRun.status)) ? latestRun : null)
    setChatInput('')
    setAttachments([])
    setLiveAgentEvent(null)
    setLiveAgentEdit(null)
    setExpandedActivity(null)
    setAgentHeaderMenu(null)
    if (window.codex) {
      setProviderKeyConfigured((await window.codex.providerCredentialStatus()).configured)
    }
  }

  async function startFreshSession() {
    const conversation = await request<{ id: number }>('/v1/conversations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: 'New conversation' }),
    })
    setConversations(await request<ConversationSummary[]>('/v1/conversations'))
    await openConversation(conversation.id)
    setControlMenuOpen(false)
  }

  async function deleteAgentSession(id: number) {
    const target = conversations.find((conversation) => conversation.id === id)
    if (!window.confirm(`Delete "${target?.title || 'this session'}"? This cannot be undone.`)) return
    try {
      await request(`/v1/conversations/${id}`, { method: 'DELETE' })
      const remaining = await request<ConversationSummary[]>('/v1/conversations')
      setConversations(remaining)
      if (id === conversationId) {
        if (remaining[0]) await openConversation(remaining[0].id)
        else await startFreshSession()
      }
    } catch (error) {
      window.alert(error instanceof Error ? error.message : 'Unable to delete this session.')
    }
  }

  async function clearAgentHistory() {
    if (!window.confirm('Delete all saved agent chats and sessions? This cannot be undone.')) return
    const result = await request<{ deleted: number; blocked: number[] }>('/v1/conversations', {
      method: 'DELETE',
    })
    const remaining = await request<ConversationSummary[]>('/v1/conversations')
    setConversations(remaining)
    if (result.blocked.length) {
      window.alert('Active sessions were kept. Stop them before deleting their history.')
    }
    await startFreshSession()
  }

  async function refreshRunProfiles() {
    setRunProfiles(await request<RunProfile[]>('/v1/run-profiles'))
  }

  async function refreshCurrentRun(runId = currentRunIdRef.current) {
    if (!runId) return
    const run = await request<AgentRun>(`/v1/agent-runs/${runId}`)
    setCurrentRun(run)
    if (['completed', 'failed', 'cancelled', 'rejected', 'needs_review'].includes(run.status)) {
      setLiveAgentEvent(null)
      setLiveAgentEdit(null)
      await refreshRunProfiles()
    }
  }

  async function chooseAttachments() {
    if (!window.codex) return
    const selected = await window.codex.chooseWorkspaceFiles()
    addAttachments(selected)
    setControlMenuOpen(false)
  }

  function addAttachments(incoming: Attachment[]) {
    setAttachments((current) => {
      const unique = incoming.filter(
        (item) => !current.some((existing) => existing.path === item.path),
      )
      let remainingImages = Math.max(
        0,
        10 - current.filter((item) => item.kind === 'image').length,
      )
      const accepted = unique.filter(
        (item) => item.kind !== 'image' || remainingImages-- > 0,
      )
      if (accepted.length !== unique.length) {
        setError('You can attach up to 10 images per message.')
      }
      return [...current, ...accepted]
    })
  }

  async function droppedAttachments(files: FileList) {
    const images = Array.from(files).filter((file) => file.type.startsWith('image/'))
    const supported = images.filter((file) =>
      ['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(file.type)
      && file.size <= 10_000_000,
    )
    if (!supported.length) {
      setError('Drop PNG, JPEG, WebP, or GIF images smaller than 10 MB.')
      return
    }
    if (supported.length !== images.length) {
      setError('Some images were unsupported or larger than 10 MB.')
    }
    const prepared = await Promise.all(
      supported.slice(0, 10).map(async (file) => ({
        name: file.name,
        path: `dropped:${file.name}:${file.lastModified}`,
        kind: 'image' as const,
        mimeType: file.type,
        content: '',
        dataUrl: await new Promise<string>((resolve, reject) => {
          const reader = new FileReader()
          reader.onload = () => resolve(String(reader.result || ''))
          reader.onerror = () => reject(reader.error)
          reader.readAsDataURL(file)
        }),
      })),
    )
    if (images.length > 10) setError('Only the first 10 dropped images were attached.')
    addAttachments(prepared)
  }

  function attachOpenFiles() {
    setAttachments((current) => [
      ...current,
      ...openFiles
        .filter((file) => !current.some((item) => item.path === file.path))
        .map((file) => ({
          name: file.path.split('/').at(-1) ?? file.path,
          path: file.path,
          kind: 'text' as const,
          mimeType: 'text/plain',
          content: file.content,
          dataUrl: '',
        })),
    ])
    setControlMenuOpen(false)
  }

  async function configureProvider() {
    if (!window.codex) return
    await window.codex.setProviderApiKey(providerKey.trim())
    await window.codex.updateSettings({
      openaiBaseUrl: desktopSettings.openaiBaseUrl,
      openaiModel: desktopSettings.openaiModel,
      lmStudioBaseUrl: desktopSettings.lmStudioBaseUrl,
    })
    setProviderKeyConfigured(Boolean(providerKey.trim()))
    setProviderKey('')
    await restartCore()
    setProviders(await request<Provider[]>('/v1/providers'))
  }

  async function refreshCodexStatus() {
    setCodexStatus(await request('/v1/providers/codex/status'))
    setProviders(await request<Provider[]>('/v1/providers'))
  }

  async function refreshClaudeStatus() {
    setClaudeStatus(await request('/v1/providers/claude/status'))
    setProviders(await request<Provider[]>('/v1/providers'))
  }

  async function signInToCodex() {
    if (!window.codex) return
    await window.codex.loginCodex()
    await refreshCodexStatus()
    await selectModel('openai-codex:default')
  }

  async function signOutOfCodex() {
    if (!window.codex) return
    await window.codex.logoutCodex()
    await refreshCodexStatus()
  }

  async function signInToClaude() {
    if (!window.codex) return
    await window.codex.loginClaude()
    await refreshClaudeStatus()
    await selectModel('anthropic-claude-code:sonnet')
  }

  async function signOutOfClaude() {
    if (!window.codex) return
    await window.codex.logoutClaude()
    await refreshClaudeStatus()
    await selectModel('auto')
  }

  async function refreshPlugins() {
    if (!window.codex) return
    const items = await window.codex.listPlugins()
    setPlugins(items)
  }

  async function installPlugin(pluginId: string) {
    if (!window.codex) return
    setInstallingPlugin(pluginId)
    try {
      await window.codex.installPlugin(pluginId)
      await Promise.all([refreshPlugins(), refreshCodexStatus(), refreshClaudeStatus()])
    } finally {
      setInstallingPlugin(null)
    }
  }

  async function setPluginEnabled(pluginId: string, enabled: boolean) {
    if (!window.codex) return
    await window.codex.setPluginEnabled(pluginId, enabled)
    if (!enabled && agentTab === `plugin:${pluginId}`) setAgentTab('kodex')
    await refreshPlugins()
  }

  async function uninstallPlugin(plugin: DesktopPlugin) {
    if (!window.codex || !window.confirm(`Delete "${plugin.name}" from Kodex? Its agent tab and contributed UI will be removed.`)) return
    await window.codex.uninstallPlugin(plugin.id)
    if (agentTab === `plugin:${plugin.id}`) setAgentTab('kodex')
    await refreshPlugins()
  }

  async function selectModel(value: string) {
    setSelectedModel(value)
    await updateDesktopSettings({
      selectedProvider: value === 'auto' ? 'auto' : value.split(':', 1)[0],
      selectedModel: value,
    })
  }

  async function restartCore() {
    if (!window.codex) return
    setHealth(null)
    await window.codex.restartCore()
    for (let attempt = 0; attempt < 30; attempt += 1) {
      try {
        setHealth(await request<Health>('/v1/health'))
        break
      } catch {
        await new Promise((resolve) => window.setTimeout(resolve, 200))
      }
    }
    await loadDiagnostics()
  }

  async function updateDesktopSettings(settings: Partial<DesktopSettings>) {
    const updated = window.codex
      ? await window.codex.updateSettings(settings)
      : { ...desktopSettings, ...settings }
    setDesktopSettings(updated)
  }

  async function selectPermission(nextPermission: string) {
    setPermission(nextPermission)
    await updateDesktopSettings({ defaultPermission: nextPermission })
  }

  async function selectTheme(nextTheme: ThemeId) {
    setTheme(nextTheme)
    localStorage.setItem('codex.theme', nextTheme)
    await updateDesktopSettings({ theme: nextTheme })
  }

  function completeOnboarding() {
    localStorage.setItem('codex.onboarding.completed', 'true')
    setOnboardingOpen(false)
  }

  async function openWorkspace() {
    if (!window.codex) {
      setError('Native workspace picker is available only inside Electron')
      return
    }
    try {
      await window.codex.openWorkspace()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to open workspace')
    }
  }

  async function createCodingWorkspace(focusAgent = false) {
    if (!window.codex) {
      setError('Native workspace creation is available only inside Electron')
      return
    }
    try {
      if (focusAgent) localStorage.setItem('kodex.focus-agent-after-workspace', 'true')
      const created = await window.codex.createCodingWorkspace()
      if (!created && focusAgent) localStorage.removeItem('kodex.focus-agent-after-workspace')
    } catch (reason) {
      if (focusAgent) localStorage.removeItem('kodex.focus-agent-after-workspace')
      setError(reason instanceof Error ? reason.message : 'Unable to create coding workspace')
    }
  }

  async function startUntitledFile() {
    if (!window.codex) {
      setError('Native file creation is available only inside Electron')
      return
    }
    try {
      await window.codex.startUntitledWorkspace()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to start a new file')
    }
  }

  async function openRecentWorkspace(path: string) {
    if (!window.codex) return
    try {
      await window.codex.openRecentWorkspace(path)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to open recent workspace')
    }
  }

  async function loadTreeFolder(path: string, select = false) {
    setLoadingFolders((current) => new Set(current).add(path))
    try {
      const payload = await request<FilesResponse>(`/v1/files?path=${encodeURIComponent(path)}`)
      const resolvedPath = payload.path || '.'
      setTreeChildren((current) => ({ ...current, [resolvedPath]: payload.entries }))
      if (resolvedPath === '.') setFiles(payload.entries)
      if (select) setCurrentDirectory(resolvedPath)
      return payload.entries
    } finally {
      setLoadingFolders((current) => {
        const next = new Set(current)
        next.delete(path)
        return next
      })
    }
  }

  async function loadDirectory(path: string) {
    await loadTreeFolder(path, true)
  }

  async function toggleFolder(entry: FileEntry) {
    setSelectedEntry(entry.path)
    setCurrentDirectory(entry.path)
    if (expandedFolders.has(entry.path)) {
      setExpandedFolders((current) => {
        const next = new Set(current)
        next.delete(entry.path)
        return next
      })
      return
    }
    if (!treeChildrenRef.current[entry.path]) {
      try {
        await loadTreeFolder(entry.path)
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : `Unable to open ${entry.path}`)
        return
      }
    }
    setExpandedFolders((current) => new Set(current).add(entry.path))
  }

  async function openEntry(entry: FileEntry) {
    setSelectedEntry(entry.path)
    if (entry.kind === 'directory') {
      await toggleFolder(entry)
      return
    }
    setCurrentDirectory(entry.path.split('/').slice(0, -1).join('/') || '.')
    const existing = openFiles.find((file) => file.path === entry.path)
    if (!existing) {
      const payload = await request<FilePayload>(`/v1/file?path=${encodeURIComponent(entry.path)}`)
      setOpenFiles((current) => [
        ...current,
        { path: payload.path, content: payload.content, savedContent: payload.content },
      ])
    }
    setActiveFile(entry.path)
  }

  async function openSourceLocation(path: string, line: number, column: number) {
    await openEntry({ name: path.split('/').at(-1) || path, kind: 'file', path })
    window.setTimeout(() => {
      editorRef.current?.revealPositionInCenter({ lineNumber: line, column })
      editorRef.current?.setPosition({ lineNumber: line, column })
      editorRef.current?.focus()
    }, 0)
  }

  function highlightAgentEdit(edit: ActiveAgentEdit) {
    const editor = editorRef.current
    if (!editor) return
    const primary = edit.ranges[0]
    editor.revealLinesInCenter(primary.startLine, primary.endLine)
    editor.setSelection({
      startLineNumber: primary.startLine,
      startColumn: 1,
      endLineNumber: primary.endLine,
      endColumn: 1,
    })
    agentDecorationsRef.current = editor.deltaDecorations(
      agentDecorationsRef.current,
      edit.ranges.map((range, index) => ({
        range: {
          startLineNumber: range.startLine,
          startColumn: 1,
          endLineNumber: range.endLine,
          endColumn: 1,
        },
        options: {
          isWholeLine: true,
          className: index === 0 ? 'agent-active-edit-line agent-active-edit-primary' : 'agent-active-edit-line',
          linesDecorationsClassName: 'agent-active-edit-gutter',
          hoverMessage: { value: `${edit.surface} ${edit.operation} ${edit.path}` },
        },
      })),
    )
    editor.focus()
  }

  async function viewAgentEdit(edit: ActiveAgentEdit) {
    setSettingsOpen(false)
    try {
      await openEntry({
        path: edit.path,
        name: edit.path.split('/').at(-1) ?? edit.path,
        kind: 'file',
      })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : `Unable to open ${edit.path}`)
      return
    }
    viewedAgentEditRef.current = edit
    window.setTimeout(() => {
      highlightAgentEdit(edit)
    }, 80)
  }

  async function viewAgentStep(step: AgentStep) {
    const edit = agentEditFromStep(step, agentTab)
    if (edit) await viewAgentEdit(edit)
  }

  async function viewAgentPath(path: string) {
    const step = [...(currentRun?.steps ?? [])].reverse().find(
      (item) => item.data.path === path,
    )
    const edit = step ? agentEditFromStep(step, agentTab) : null
    if (edit) {
      await viewAgentEdit(edit)
      return
    }
    try {
      await openEntry({
        path,
        name: path.split('/').at(-1) ?? path,
        kind: 'file',
      })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : `Unable to open ${path}`)
    }
  }

  function pathInCurrentDirectory(name: string) {
    return currentDirectory === '.' ? name : `${currentDirectory}/${name}`
  }

  async function createEntry(kind: FileEntry['kind']) {
    const label = kind === 'directory' ? 'folder' : 'file'
    const name = window.prompt(`New ${label} name`)
    if (!name?.trim()) return
    await request('/v1/files', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        path: pathInCurrentDirectory(name.trim()),
        kind,
      }),
    })
    await loadDirectory(currentDirectory)
  }

  async function renameEntry(entry: FileEntry) {
    const name = window.prompt('Rename to', entry.name)
    if (!name?.trim() || name.trim() === entry.name) return
    const parent = entry.path.split('/').slice(0, -1).join('/') || '.'
    const destination = parent === '.' ? name.trim() : `${parent}/${name.trim()}`
    await request('/v1/files', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source: entry.path, destination }),
    })
    setOpenFiles((current) =>
      current.map((file) =>
        file.path === entry.path || file.path.startsWith(`${entry.path}/`)
          ? { ...file, path: `${destination}${file.path.slice(entry.path.length)}` }
          : file,
      ),
    )
    if (activeFile === entry.path || activeFile?.startsWith(`${entry.path}/`)) {
      setActiveFile(`${destination}${activeFile.slice(entry.path.length)}`)
    }
    setSelectedEntry(destination)
    await loadTreeFolder(parent, parent === currentDirectory)
  }

  async function deleteEntry(entry: FileEntry) {
    const confirmed = window.confirm(`Delete ${entry.kind} "${entry.name}"?`)
    if (!confirmed) return
    await request(`/v1/files?path=${encodeURIComponent(entry.path)}`, { method: 'DELETE' })
    setOpenFiles((current) =>
      current.filter(
        (file) => file.path !== entry.path && !file.path.startsWith(`${entry.path}/`),
      ),
    )
    if (activeFile === entry.path || activeFile?.startsWith(`${entry.path}/`)) {
      setActiveFile(null)
    }
    setSelectedEntry(null)
    const parent = entry.path.split('/').slice(0, -1).join('/') || '.'
    await loadTreeFolder(parent, parent === currentDirectory)
  }

  async function searchWorkspace(event?: FormEvent) {
    event?.preventDefault()
    const query = searchQuery.trim()
    if (!query) {
      setSearchResults([])
      setSearchMeta('')
      return
    }
    const payload = await request<SearchResponse>(
      `/v1/search?q=${encodeURIComponent(query)}&mode=${searchMode}`,
    )
    setSearchResults(payload.results)
    setSearchMeta(
      `${payload.results.length} result${payload.results.length === 1 ? '' : 's'} in ${payload.scanned_files} files${payload.truncated ? ' · limited' : ''}`,
    )
  }

  async function openSearchResult(result: SearchResult) {
    await openEntry({
      name: result.path.split('/').at(-1) ?? result.path,
      kind: 'file',
      path: result.path,
    })
  }

  async function refreshSnapshots() {
    setSnapshots(await request<Snapshot[]>('/v1/snapshots'))
  }

  async function createSnapshot() {
    const label = window.prompt('Snapshot name', 'Manual workspace snapshot')
    if (!label?.trim()) return
    await request('/v1/snapshots', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label: label.trim() }),
    })
    await refreshSnapshots()
  }

  async function reviewSnapshot(snapshot: Snapshot) {
    setActiveFile(null)
    setSelectedSnapshot(
      await request<SnapshotDiff>(`/v1/snapshots/${snapshot.id}/diff`),
    )
  }

  async function restoreSnapshot(snapshot: Snapshot) {
    const confirmed = window.confirm(
      `Restore snapshot "${snapshot.label}"?\n\nA safety snapshot of the current workspace will be created first.`,
    )
    if (!confirmed) return
    await request(`/v1/snapshots/${snapshot.id}/restore`, { method: 'POST' })
    setOpenFiles([])
    setActiveFile(null)
    setSelectedSnapshot(null)
    await Promise.all([loadDirectory('.'), refreshSnapshots()])
  }

  async function deleteSnapshot(snapshot: Snapshot) {
    const confirmed = window.confirm(`Delete snapshot "${snapshot.label}"?`)
    if (!confirmed) return
    await request(`/v1/snapshots/${snapshot.id}`, { method: 'DELETE' })
    if (selectedSnapshot?.snapshot.id === snapshot.id) setSelectedSnapshot(null)
    await refreshSnapshots()
  }

  async function reconcileWorkspaceEvents(events: WorkspaceEvent[], reset: boolean) {
    const loadedFolders = Object.keys(treeChildrenRef.current)
    const affectedFolders = reset
      ? loadedFolders
      : loadedFolders.filter((folder) =>
          events.some((event) => {
            const parent = event.path.split('/').slice(0, -1).join('/') || '.'
            return parent === folder || event.path === folder
          }),
        )
    await Promise.all(
      affectedFolders.map((folder) =>
        loadTreeFolder(folder, folder === currentDirectoryRef.current)
          .catch(() => folder === '.' ? undefined : loadTreeFolder('.', true)),
      ),
    )

    for (const event of events) {
      const openFile = openFilesRef.current.find((file) => file.path === event.path)
      if (!openFile) continue
      if (event.type === 'deleted') {
        setOpenFiles((current) => current.filter((file) => file.path !== event.path))
        setActiveFile((current) => (current === event.path ? null : current))
        continue
      }
      if (event.kind !== 'file') continue
      if (openFile.content !== openFile.savedContent) {
        setOpenFiles((current) =>
          current.map((file) =>
            file.path === event.path ? { ...file, externallyChanged: true } : file,
          ),
        )
        continue
      }
      try {
        const payload = await request<FilePayload>(
          `/v1/file?path=${encodeURIComponent(event.path)}`,
        )
        setOpenFiles((current) =>
          current.map((file) =>
            file.path === event.path
              ? {
                  ...file,
                  content: payload.content,
                  savedContent: payload.content,
                  externallyChanged: false,
                }
              : file,
          ),
        )
      } catch {
        // The next watcher event or manual refresh will reconcile transient races.
      }
    }
  }

  function closeFile(path: string) {
    setOpenFiles((current) => {
      const next = current.filter((file) => file.path !== path)
      if (activeFile === path) setActiveFile(next.at(-1)?.path ?? null)
      return next
    })
  }

  async function saveActiveFile() {
    if (!selectedFile) return
    await request('/v1/file', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: selectedFile.path, content: selectedFile.content }),
    })
    setOpenFiles((current) =>
      current.map((file) =>
        file.path === selectedFile.path
          ? { ...file, savedContent: file.content, externallyChanged: false }
          : file,
      ),
    )
  }
  saveActiveFileRef.current = saveActiveFile

  const mountEditor: OnMount = (editor, monaco) => {
    editorRef.current = editor
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
      void saveActiveFileRef.current()
    })
    if (viewedAgentEditRef.current?.path === activeFile) {
      window.setTimeout(() => highlightAgentEdit(viewedAgentEditRef.current!), 0)
    }
    editor.focus()
  }

  async function refreshProcesses() {
    setProcesses(await request<ProcessRecord[]>('/v1/processes'))
  }

  async function runCommand(command: string, name = 'terminal', cwd = '.', mode: 'run' | 'debug' = 'run') {
    if (!command.trim()) return
    persistLayout({
      ...layout,
      bottomCollapsed: false,
      bottomMaximized: false,
      bottomPanel: 'terminal',
    })
    setTerminalLaunch({
      key: Date.now(),
      name,
      command,
      cwd,
      mode,
    })
  }

  async function runProject(mode: 'run' | 'debug') {
    const preferred = runProfiles.find((profile) =>
      mode === 'debug'
        ? profile.kind === 'debug'
        : profile.kind === 'run' || profile.id.endsWith(':start'),
    ) ?? runProfiles.find((profile) => profile.kind === 'dev') ?? runProfiles[0]
    const fallback = mode === 'run'
      ? 'npm run start'
      : 'npm run dev'
    await runCommand(preferred?.command ?? fallback, preferred?.name ?? mode, preferred?.cwd ?? '.', mode)
  }

  async function stopProcess(id: number) {
    await request(`/v1/processes/${id}`, { method: 'DELETE' })
    await refreshProcesses()
  }

  async function sendMessage(event: FormEvent) {
    event.preventDefault()
    const typedContent = chatInput.trim()
    const content = typedContent || (
      attachments.some((item) => item.kind === 'image')
        ? 'Please inspect the attached image(s).'
        : ''
    )
    if (!content || !conversationId) return
    if (currentRun && activeRunStatuses.has(currentRun.status)) {
      setMessages((current) => [...current, { role: 'user', content, attachments: [...attachments] }])
      setChatInput('')
      await request(`/v1/agent-runs/${currentRun.id}/steering`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          content,
          attachments: attachments.filter((item) => item.kind === 'image'),
        }),
      })
      setAttachments([])
      setLiveAgentEvent({ label: 'Applying your new direction' })
      await refreshCurrentRun(currentRun.id)
      return
    }
    const contextAttachments = [...attachments]
    if (includeIdeContext) {
      for (const file of openFiles) {
        if (!contextAttachments.some((item) => item.path === file.path)) {
          contextAttachments.push({
            name: file.path.split('/').at(-1) ?? file.path,
            path: file.path,
            kind: 'text',
            mimeType: 'text/plain',
            content: file.content,
            dataUrl: '',
          })
        }
      }
    }
    setMessages((current) => {
      const previousResult = currentRun && terminalStatuses.has(currentRun.status) && currentRun.summary
        && current.at(-1)?.content !== currentRun.summary
        ? [{ role: 'assistant' as const, content: currentRun.summary }]
        : []
      return [...current, ...previousResult, { role: 'user', content, attachments: [...attachments] }]
    })
    setChatInput('')
    setAttachments([])
    setLiveAgentEvent({ label: 'Understanding your request' })
    const run = await request<AgentRun>(`/v1/conversations/${conversationId}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        content,
        mode: planMode ? 'plan' : 'auto',
        pursue_goal: pursueGoal,
        permission,
        model: selectedModel,
        context: {
          ide_context_enabled: includeIdeContext,
          workspace: workspace.path,
          current_directory: currentDirectory,
          active_file: activeFile,
          open_files: openFiles.map((file) => file.path),
          selected_entry: selectedEntry,
          attachments: contextAttachments,
          diagnostics: includeIdeContext ? diagnostics : null,
          processes: includeIdeContext ? processes : [],
          git: includeIdeContext ? gitState : null,
          project_intelligence: includeIdeContext ? projectIntelligence : null,
          run_profiles: includeIdeContext ? runProfiles : [],
          unsaved_files: includeIdeContext
            ? openFiles.filter((file) => file.content !== file.savedContent).map((file) => file.path)
            : [],
        },
      }),
    })
    setCurrentRun(run)
    setConversations(await request<ConversationSummary[]>('/v1/conversations'))
  }

  function applyAgentShortcut(prompt: string) {
    setChatInput(prompt)
    window.requestAnimationFrame(() => {
      chatInputRef.current?.focus()
      chatInputRef.current?.setSelectionRange(prompt.length, prompt.length)
    })
  }

  async function cancelAgent() {
    if (!currentRun || stopPending) return
    const runId = currentRun.id
    setStopPending(true)
    setCurrentRun((run) => run ? { ...run, status: 'stopping', phase: 'repair' } : run)
    setLiveAgentEvent({ label: 'Stopping every active operation' })
    try {
      const stopped = await request<AgentRun & {
        terminated_operations?: string[]
        already_terminal?: boolean
      }>(`/v1/agent-runs/${runId}/cancel`, { method: 'POST' })
      setCurrentRun(stopped)
      setLiveAgentEvent(null)
      setLiveAgentEdit(null)
    } finally {
      setStopPending(false)
      await refreshCurrentRun(runId)
    }
  }

  async function testLmStudio() {
    await request('/v1/providers/lm-studio/test', { method: 'POST' })
    setProviders(await request<Provider[]>('/v1/providers'))
  }

  async function resolveApproval(approvalId: number, approved: boolean, alwaysAllow = false) {
    await request(`/v1/approvals/${approvalId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approved, always_allow: alwaysAllow }),
    })
    await refreshCurrentRun()
  }

  async function setAgentPaused(paused: boolean) {
    if (!currentRun) return
    await request(`/v1/agent-runs/${currentRun.id}/${paused ? 'pause' : 'resume'}`, {
      method: 'POST',
    })
    await refreshCurrentRun(currentRun.id)
  }

  async function continueInterruptedRun() {
    if (!currentRun) return
    setCurrentRun(
      await request<AgentRun>(`/v1/agent-runs/${currentRun.id}/continue`, {
        method: 'POST',
      }),
    )
  }

  async function executeApprovedPlan() {
    if (!currentRun || currentRun.mode !== 'plan' || currentRun.status !== 'completed') return
    const planSummary = currentRun.summary
    setMessages((current) => [...current, { role: 'assistant', content: planSummary }])
    setLiveAgentEvent({ label: 'Starting the approved plan' })
    setPlanMode(false)
    setCurrentRun(
      await request<AgentRun>(`/v1/agent-runs/${currentRun.id}/execute-plan`, {
        method: 'POST',
      }),
    )
  }

  async function retryCurrentRun(mode: 'repair' | 'fresh' = 'repair') {
    if (!currentRun) return
    setLiveAgentEvent({ label: mode === 'repair' ? 'Repairing the failed run' : 'Starting a fresh attempt' })
    setCurrentRun(
      await request<AgentRun>(`/v1/agent-runs/${currentRun.id}/retry`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
      }),
    )
  }

  async function openAffectedFile() {
    const path = currentRun?.changed_files?.[0]
      || currentRun?.steps.map((step) => step.data.path).find((value): value is string => typeof value === 'string')
    if (!path || path.startsWith('/')) return
    await openEntry({ path, name: path.split('/').at(-1) ?? path, kind: 'file' })
  }

  useEffect(() => {
    localStorage.setItem('kodex.ide-context', String(includeIdeContext))
    localStorage.setItem('kodex.pursue-goal', String(pursueGoal))
  }, [includeIdeContext, pursueGoal])

  useEffect(() => {
    if (!providers.length || selectedModel === 'auto') return
    const available = providers.some((provider) =>
      provider.available
      && provider.models.some((model) => `${provider.id}:${model.id}` === selectedModel),
    )
    if (!available) {
      setSelectedModel('auto')
      setError('The saved model is no longer available. Kodex switched to Auto.')
      void updateDesktopSettings({ selectedProvider: 'auto', selectedModel: 'auto' })
    }
  }, [providers, selectedModel])

  useEffect(() => {
    const textarea = chatInputRef.current
    if (!textarea) return
    textarea.style.height = 'auto'
    textarea.style.height = `${Math.min(140, Math.max(52, textarea.scrollHeight))}px`
  }, [chatInput])

  useEffect(() => {
    if (!controlMenuOpen && !composerSelectOpen) return
    const closeMenus = (event: PointerEvent) => {
      if (!composerRef.current?.contains(event.target as Node)) {
        setControlMenuOpen(false)
        setComposerSelectOpen(null)
      }
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setControlMenuOpen(false)
        setComposerSelectOpen(null)
      }
    }
    window.addEventListener('pointerdown', closeMenus)
    window.addEventListener('keydown', closeOnEscape)
    return () => {
      window.removeEventListener('pointerdown', closeMenus)
      window.removeEventListener('keydown', closeOnEscape)
    }
  }, [controlMenuOpen, composerSelectOpen])

  useEffect(() => {
    const initialLoads = [
      request<Health>('/v1/health').then(setHealth),
      loadWorkspaceShell(),
      refreshPlugins(),
      window.codex?.getSettings().then((settings) => {
        applyLoadedDesktopSettings(settings)
      }) ?? Promise.resolve(),
    ]
    void Promise.allSettled(initialLoads).then((results) => {
      const failure = results.find((result): result is PromiseRejectedResult => result.status === 'rejected')
      if (failure) setError(failure.reason instanceof Error ? failure.reason.message : String(failure.reason))
    })
  }, [])

  useEffect(() => {
    if (!workspace) return
    workspaceSessionHydratedRef.current = false
    setTreeChildren({})
    setExpandedFolders(new Set())
    setLoadingFolders(new Set())
    setCurrentDirectory('.')
    setSelectedEntry(null)
    const initialLoads = [
      request<Memory[]>('/v1/memories').then(setMemories),
      request<Task[]>('/v1/tasks').then(setTasks),
      loadDirectory('.'),
      refreshProcesses(),
      refreshSnapshots(),
      loadDiagnostics(),
      loadAgentShell(),
      restoreWorkspaceSession(),
    ]
    void Promise.allSettled(initialLoads).then((results) => {
      const failure = results.find((result): result is PromiseRejectedResult => result.status === 'rejected')
      if (failure) setError(failure.reason instanceof Error ? failure.reason.message : String(failure.reason))
    })
    const timer = window.setInterval(() => {
      refreshProcesses().catch(() => undefined)
    }, 1500)
    const workspaceTimer = window.setInterval(async () => {
      try {
        const payload = await request<WorkspaceChangesResponse>(
          `/v1/workspace/changes?since=${workspaceCursorRef.current}`,
        )
        workspaceCursorRef.current = payload.cursor
        if (payload.reset || payload.events.length) {
          await reconcileWorkspaceEvents(payload.events, payload.reset)
        }
      } catch {
        // Core health is surfaced elsewhere; watcher polling retries automatically.
      }
    }, 1000)
    let socket: WebSocket | null = null
    let reconnectTimer = 0
    let disposed = false
    const connectEvents = () => {
      if (disposed) return
      const socketUrl = `${coreBase.replace(/^http/, 'ws')}/events?token=${encodeURIComponent(coreToken)}&since=${coreEventCursorRef.current}`
      socket = new WebSocket(socketUrl, 'codex.events.v1')
      socket.onopen = () => setEventConnected(true)
      socket.onclose = () => {
        setEventConnected(false)
        if (!disposed) reconnectTimer = window.setTimeout(connectEvents, 750)
      }
      socket.onerror = () => setEventConnected(false)
      socket.onmessage = (message) => {
        const event = JSON.parse(message.data) as CoreEvent
        coreEventCursorRef.current = Math.max(coreEventCursorRef.current, event.sequence)
        setLastCoreEvent(event)
        const eventRunId = Number(event.data.run_id) || currentRunIdRef.current
        if (!eventRunId || eventRunId === currentRunIdRef.current) {
          const presentation = liveEventPresentation(event)
          if (presentation) setLiveAgentEvent(presentation)
          const edit = agentEditFromEvent(event, agentTabRef.current)
          if (edit) setLiveAgentEdit(edit)
        }
        if (event.event.startsWith('process.')) void refreshProcesses()
        if (event.event.startsWith('task.')) void request<Task[]>('/v1/tasks').then(setTasks)
        if (event.event.startsWith('memory.')) void request<Memory[]>('/v1/memories').then(setMemories)
        if (event.event.startsWith('snapshot.')) void refreshSnapshots()
        if (event.event.startsWith('file.')) void refreshRunProfiles()
        if (
          (event.event.startsWith('agent.')
            && event.event !== 'agent.token'
            && event.event !== 'agent.tool_output')
          || event.event.startsWith('task.')
          || event.event.startsWith('approval.')
        ) {
          void refreshCurrentRun(eventRunId)
        }
      }
    }
    connectEvents()
    return () => {
      disposed = true
      window.clearInterval(timer)
      window.clearInterval(workspaceTimer)
      window.clearTimeout(reconnectTimer)
      socket?.close()
    }
  }, [workspace?.path])

  useEffect(() => {
    if (!workspace || !workspaceSessionHydratedRef.current) return
    const timer = window.setTimeout(() => {
      void request('/v1/workspace/session', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          open_files: openFiles.map((file) => file.path),
          active_file: activeFile ?? '',
          unfinished_task_ids: tasks
            .filter((task) => !['completed', 'cancelled'].includes(task.status))
            .map((task) => task.id),
        }),
      })
    }, 250)
    return () => window.clearTimeout(timer)
  }, [workspace, openFiles, activeFile, tasks])

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: agentActive ? 'smooth' : 'auto', block: 'end' })
  }, [messages.length, currentRun?.steps.length, currentRun?.status, liveAgentEvent?.detail, liveAgentEvent?.label])

  useEffect(() => {
    if (!currentRun || !terminalStatuses.has(currentRun.status)) return
    setStopPending(false)
    setLiveAgentEvent(null)
    setLiveAgentEdit(null)
  }, [currentRun?.id, currentRun?.status])

  useEffect(() => {
    const viewed = viewedAgentEditRef.current
    if (!viewed || activeFile !== viewed.path) return
    const latest = [...(currentRun?.steps ?? [])].reverse()
      .map((step) => agentEditFromStep(step, agentTab))
      .find((edit) => edit?.path === viewed.path)
    if (latest) viewedAgentEditRef.current = latest
    const edit = latest ?? viewed
    window.setTimeout(() => highlightAgentEdit(edit), 0)
  }, [activeFile, currentRun?.steps.length, agentTab])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
        event.preventDefault()
        saveActiveFile()
      }
      if ((event.metaKey || event.ctrlKey) && event.shiftKey && event.key.toLowerCase() === 'p') {
        event.preventDefault()
        setCommandPaletteOpen((open) => !open)
      }
      if (event.key === 'Escape') {
        setCommandPaletteOpen(false)
        setSettingsOpen(false)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [selectedFile])

  useEffect(() => {
    if (!workspace || localStorage.getItem('kodex.focus-agent-after-workspace') !== 'true') return
    localStorage.removeItem('kodex.focus-agent-after-workspace')
    window.setTimeout(() => chatInputRef.current?.focus(), 250)
  }, [workspace?.path])

  useEffect(() => {
    const dispose = window.codex?.onMenuCommand((command) => {
      if (command === 'new-workspace') {
        void createCodingWorkspace()
      } else if (command === 'open-folder') {
        void openWorkspace()
      } else if (command === 'save' || command === 'save-all') {
        void saveActiveFile()
      } else if (command === 'command-palette') {
        setCommandPaletteOpen(true)
      } else if (command === 'show-explorer') {
        setActivePanel('explorer')
        persistLayout({ ...layout, sidebarOpen: true })
      } else if (command === 'toggle-terminal' || command === 'new-terminal') {
        persistLayout({
          ...layout,
          bottomCollapsed: command === 'new-terminal' ? false : !layout.bottomCollapsed,
          bottomPanel: 'terminal',
        })
      } else if (command === 'run') {
        void runProject('run')
      } else if (command === 'debug') {
        void runProject('debug')
      } else if (command === 'diagnostics') {
        setSettingsOpen(true)
        setSettingsSection('diagnostics')
      }
    })
    return dispose
  }, [selectedFile, runProfiles, layout])

  if (!workspace) {
    return (
      <div className="ide welcome-only" data-theme={theme}>
        {bootVisible && <KodexBootSequence onComplete={() => setBootVisible(false)} />}
        <div className="theme-particles" aria-hidden="true">
          <i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i />
        </div>
        <header className="titlebar">
          <div className="titlebar-brand"><KodexLogo size="small" /><span>Kodex</span></div>
          <div className="command-center welcome-command">Select a folder to begin</div>
          <div className="window-actions"><span>◫</span><span>◧</span><span>□</span></div>
        </header>
        <WelcomeScreen
          recentWorkspaces={recentWorkspaces}
          onCreateWorkspace={() => void createCodingWorkspace()}
          onNewFile={() => void startUntitledFile()}
          onOpenWorkspace={() => void openWorkspace()}
          onOpenRecent={(path) => void openRecentWorkspace(path)}
          onAskKodex={() => void createCodingWorkspace(true)}
          onOpenDocs={() => void window.codex?.openExternal('https://github.com/fireyhellmarketing-cmd/kodex')}
        />
        {error && <div className="error-toast" onClick={() => setError(null)}>{error}</div>}
      </div>
    )
  }

  const commands = [
    { label: 'Files: New Coding Workspace', detail: 'Open an empty folder for a new project', run: createCodingWorkspace },
    { label: 'Files: Open Folder', detail: 'Choose a workspace', run: openWorkspace },
    { label: 'View: Toggle Primary Sidebar', detail: 'Show or hide the active sidebar', run: () => persistLayout({ ...layout, sidebarOpen: !layout.sidebarOpen }) },
    { label: 'View: Toggle Kodex Agent', detail: 'Show or hide the AI panel', run: () => persistLayout({ ...layout, agentOpen: !layout.agentOpen }) },
    { label: 'View: Toggle Bottom Panel', detail: 'Expand or collapse the panel strip', run: () => persistLayout({ ...layout, bottomCollapsed: !layout.bottomCollapsed, bottomMaximized: false }) },
    { label: 'View: Focus Terminal', detail: 'Open and focus the workspace terminal', run: () => persistLayout({ ...layout, bottomPanel: 'terminal', bottomCollapsed: false }) },
    { label: 'View: Maximize Bottom Panel', detail: 'Use the editor area for the active panel', run: () => persistLayout({ ...layout, bottomCollapsed: false, bottomMaximized: !layout.bottomMaximized }) },
    { label: 'View: Reset Layout', detail: 'Restore the default workbench arrangement', run: () => persistLayout({ sidebar: 248, agent: 390, bottom: 250, sidebarOpen: true, agentOpen: true, bottomCollapsed: true, bottomMaximized: false, bottomPanel: 'terminal' }) },
    { label: 'View: Explorer', detail: 'Show workspace files', run: () => { setActivePanel('explorer'); persistLayout({ ...layout, sidebarOpen: true }) } },
    { label: 'View: Search', detail: 'Search files and symbols', run: () => { setActivePanel('search'); persistLayout({ ...layout, sidebarOpen: true }) } },
    { label: 'View: Outline', detail: 'Show symbols in the active file', run: () => setActivePanel('outline') },
    { label: 'View: Timeline', detail: 'Show agent work and workspace snapshots', run: () => setActivePanel('history') },
    { label: 'View: Run and Debug', detail: 'Show process controls', run: () => setActivePanel('run') },
    { label: 'Workspace: Create Snapshot', detail: 'Save a rollback point', run: createSnapshot },
    { label: 'Core: Restart', detail: 'Restart the owned local Core', run: restartCore },
    { label: 'Preferences: Open Settings', detail: 'Core and desktop settings', run: () => { setSettingsOpen(true); void loadDiagnostics() } },
  ].filter((command) =>
    `${command.label} ${command.detail}`.toLowerCase().includes(commandQuery.toLowerCase()),
  )

  function runPaletteCommand(command: (typeof commands)[number]) {
    setCommandPaletteOpen(false)
    setCommandQuery('')
    void command.run()
  }

  function renderFileTree(entries: FileEntry[], depth = 0): ReactNode {
    return entries.map((entry) => {
      const expanded = entry.kind === 'directory' && expandedFolders.has(entry.path)
      const loading = entry.kind === 'directory' && loadingFolders.has(entry.path)
      const agentChanged = entry.kind === 'directory'
        ? [...changedAgentPaths].some((path) => path.startsWith(`${entry.path}/`))
        : changedAgentPaths.has(entry.path)
      const showChangeDot = agentChanged && (entry.kind === 'file' || !expanded)
      const children = entry.kind === 'directory' ? treeChildren[entry.path] ?? [] : []
      return (
        <div className="file-tree-node" key={entry.path}>
          <div
            className={`file-row ${selectedEntry === entry.path ? 'selected' : ''} ${agentChanged ? 'agent-changed' : ''} ${expanded ? 'expanded' : ''}`}
            style={{ '--tree-depth': depth } as CSSProperties}
          >
            <button
              className="file-open"
              onClick={() => void openEntry(entry)}
              title={entry.path}
            >
              <span className={`tree-chevron ${entry.kind === 'file' ? 'hidden' : ''} ${expanded ? 'expanded' : ''}`}>
                {loading ? '·' : '›'}
              </span>
              <span className={`file-icon ${entry.kind}`}>{fileGlyph(entry)}</span>
              <span className="file-name">{entry.name}</span>
              {showChangeDot && <i className="agent-change-dot" title="Changed by the active agent" />}
            </button>
            <div className="file-actions">
              <button title="Rename" onClick={() => void renameEntry(entry)}>✎</button>
              <button title="Delete" onClick={() => void deleteEntry(entry)}>×</button>
            </div>
          </div>
          {expanded && children.length > 0 && (
            <div className="file-tree-children">{renderFileTree(children, depth + 1)}</div>
          )}
          {expanded && !loading && children.length === 0 && (
            <div className="file-tree-empty" style={{ '--tree-depth': depth + 1 } as CSSProperties}>Empty folder</div>
          )}
        </div>
      )
    })
  }

  function renderSidePanel() {
    if (activePanel === 'plugins') {
      return (
        <>
          <PanelHeader title="Plugins" meta={`${plugins.filter((plugin) => plugin.installed).length}`} />
          <div className="plugins-panel">
            <div className="plugins-panel-heading">
              <span>Extend Kodex</span>
              <button onClick={() => void refreshPlugins()}>Refresh</button>
            </div>
            {plugins.map((plugin) => (
              <article className={`plugin-card ${plugin.installed ? 'installed' : ''}`} key={plugin.id}>
                <div className={`plugin-card-icon brand-${plugin.appearance.brand ?? 'generic'}`}>
                  <ProviderBrandMark brand={plugin.appearance.brand ?? 'generic'} size="medium" />
                </div>
                <div className="plugin-card-copy">
                  <strong>{plugin.name}</strong>
                  <span>{plugin.description}</span>
                  <small>{plugin.installed ? `Ready · v${plugin.version}` : 'Available'}</small>
                </div>
                {plugin.installed ? (
                  <div className="plugin-card-actions">
                    <button className="plugin-open" onClick={() => {
                      if (!plugin.enabled) void setPluginEnabled(plugin.id, true)
                      setAgentTab(`plugin:${plugin.id}`)
                      void selectModel(plugin.id === 'openai-codex-kodex' ? 'openai-codex:default' : 'anthropic-claude-code:sonnet')
                    }}>
                      {plugin.enabled ? 'Open tab' : 'Enable'}
                    </button>
                    <button onClick={() => void setPluginEnabled(plugin.id, !plugin.enabled)}>{plugin.enabled ? 'Disable' : 'Enable'}</button>
                    <button className="danger" onClick={() => void uninstallPlugin(plugin)}>Delete</button>
                  </div>
                ) : (
                  <button
                    className="plugin-install"
                    disabled={installingPlugin === plugin.id}
                    onClick={() => void installPlugin(plugin.id)}
                  >
                    {installingPlugin === plugin.id ? 'Installing…' : 'Install'}
                  </button>
                )}
                {plugin.error && <code>{plugin.error}</code>}
              </article>
            ))}
            {!plugins.length && <Empty label="No plugins discovered" />}
          </div>
        </>
      )
    }
    if (activePanel === 'memory') {
      return (
        <>
          <PanelHeader title="Memory" meta={`${structuredMemories.length || memories.length}`} />
          <div className="side-list">
            {memorySuggestions.map((memory) => (
              <div className="memory-item memory-suggestion" key={`suggestion-${memory.title}`}>
                <strong>{memory.title}</strong>
                <span>{memory.content}</span>
                <button onClick={() => void acceptMemorySuggestion(memory)}>Accept suggestion</button>
              </div>
            ))}
            {structuredMemories.map((memory) => (
              <div className="memory-item" key={memory.id}>
                <strong>{memory.title} <small>{memory.scope}</small></strong>
                <span>{memory.content}</span>
              </div>
            ))}
            {!structuredMemories.length && !memorySuggestions.length && memories.map((memory) => (
              <div className="memory-item" key={memory.id}>
                <strong>{memory.title}</strong>
                <span>{memory.content}</span>
              </div>
            ))}
            {!structuredMemories.length && !memorySuggestions.length && !memories.length && <Empty label="No memories stored" />}
          </div>
        </>
      )
    }
    if (activePanel === 'tasks') {
      return (
        <>
          <PanelHeader title="Tasks" meta={`${tasks.length}`} />
          <div className="side-list">
            {tasks.map((task) => (
              <div className="task-item" key={task.id}>
                <span className={`task-dot ${task.status}`} />
                <div>
                  <strong>{task.title}</strong>
                  <span>{task.status}</span>
                </div>
              </div>
            ))}
            {!tasks.length && <Empty label="No active tasks" />}
          </div>
        </>
      )
    }
    if (activePanel === 'run') {
      return (
        <>
          <PanelHeader title="Run and Debug" />
          <div className="run-side">
            <button className="primary-button" onClick={() => runProject('run')}>▷ Run project</button>
            <button className="secondary-button" onClick={() => runProject('debug')}>⌁ Debug project</button>
            <h4>Detected profiles</h4>
            {runProfiles.map((profile) => (
              <button
                className="run-profile"
                key={profile.id}
                title={profile.command}
                onClick={() => void runCommand(profile.command, profile.name, profile.cwd ?? '.')}
              >
                <span>{profile.name}</span>
                <small>{profile.command}</small>
              </button>
            ))}
            {!runProfiles.length && <div className="run-profile-empty">No project profiles detected.</div>}
            <h4>Processes</h4>
            {processes.map((process) => (
              <div className="process-row" key={process.id}>
                <span className={`process-light ${process.status}`} />
                <div>
                  <strong>{process.name}</strong>
                  <small>PID {process.pid} · {process.status}</small>
                </div>
                {process.status === 'running' && (
                  <button onClick={() => stopProcess(process.id)}>■</button>
                )}
              </div>
            ))}
          </div>
        </>
      )
    }
    if (activePanel === 'history') {
      return (
        <>
          <div className="side-panel-header">
            <span>TIMELINE</span>
            <div className="explorer-actions">
              <b>{(currentRun?.steps.length ?? 0) + snapshots.length}</b>
              <button title="Create snapshot" onClick={createSnapshot}>＋</button>
              <button title="Refresh snapshots" onClick={refreshSnapshots}>↻</button>
            </div>
          </div>
          <div className="timeline-list">
            {currentRun && (
              <section className="timeline-group">
                <header><span>Current agent run</span><small>{currentRun.phase?.replaceAll('_', ' ') ?? currentRun.status}</small></header>
                {currentRun.steps.slice().reverse().map((step) => {
                  const presentation = stepPresentation(step)
                  return (
                    <button className={`timeline-event ${step.status}`} key={step.id} onClick={() => setExpandedActivity(`step-${step.id}`)}>
                      <i />
                      <span><strong>{presentation.label}</strong><small>{presentation.detail || step.status}</small></span>
                    </button>
                  )
                })}
              </section>
            )}
            <div className="timeline-group">
              <header><span>Workspace snapshots</span><small>{snapshots.length}</small></header>
            {snapshots.map((snapshot) => (
              <div className="snapshot-card" key={snapshot.id}>
                <button className="snapshot-open" onClick={() => reviewSnapshot(snapshot)}>
                  <strong>{snapshot.label}</strong>
                  <span>{snapshot.file_count} files · {(snapshot.total_bytes / 1024).toFixed(1)} KB</span>
                  <time>{new Date(snapshot.created_at).toLocaleString()}</time>
                </button>
                <div className="snapshot-actions">
                  <button onClick={() => restoreSnapshot(snapshot)}>Restore</button>
                  <button className="snapshot-delete" onClick={() => deleteSnapshot(snapshot)}>×</button>
                </div>
              </div>
            ))}
            {!snapshots.length && <Empty label="No workspace snapshots" />}
            </div>
          </div>
        </>
      )
    }
    if (activePanel === 'outline') {
      return (
        <>
          <PanelHeader title="Outline" meta={`${outlineItems.length}`} />
          <div className="outline-panel">
            <header>
              <Icon name="file" />
              <span>{selectedFile?.path ?? 'Open a file to inspect its symbols'}</span>
            </header>
            {outlineItems.map((item) => (
              <button
                key={`${item.line}-${item.name}`}
                onClick={() => {
                  setActiveFile(selectedFile?.path ?? null)
                  editorRef.current?.revealLineInCenter(item.line)
                  editorRef.current?.setPosition({ lineNumber: item.line, column: 1 })
                  editorRef.current?.focus()
                }}
                title={item.preview}
              >
                <span><Icon name="outline" /></span>
                <strong>{item.name}</strong>
                <small>{item.line}</small>
              </button>
            ))}
            {!outlineItems.length && <Empty label={selectedFile ? 'No symbols detected in this file' : 'Open a code file to see its outline'} />}
          </div>
        </>
      )
    }
    if (activePanel === 'search') {
      return (
        <>
          <PanelHeader title="Search" meta={searchResults.length ? `${searchResults.length}` : undefined} />
          <form className="search-form" onSubmit={searchWorkspace}>
            <div className="search-box">
              <input
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                placeholder="Search workspace"
              />
              <button type="submit">⌕</button>
            </div>
            <div className="search-modes">
              <button
                type="button"
                className={searchMode === 'text' ? 'active' : ''}
                onClick={() => setSearchMode('text')}
              >
                Text
              </button>
              <button
                type="button"
                className={searchMode === 'symbols' ? 'active' : ''}
                onClick={() => setSearchMode('symbols')}
              >
                Symbols
              </button>
            </div>
          </form>
          {searchMeta && <div className="search-meta">{searchMeta}</div>}
          <div className="search-results">
            {searchResults.map((result, index) => (
              <button
                key={`${result.path}-${result.line}-${index}`}
                onClick={() => openSearchResult(result)}
              >
                <strong>{result.symbol ?? result.path.split('/').at(-1)}</strong>
                <span>{result.path}:{result.line}:{result.column}</span>
                <code>{result.preview}</code>
              </button>
            ))}
            {searchMeta && !searchResults.length && <Empty label="No matches found" />}
          </div>
        </>
      )
    }
    if (activePanel === 'git') {
      return (
        <>
          <PanelHeader title="Source Control" meta={gitState?.changes.length ? `${gitState.changes.length}` : undefined} />
          <div className="git-panel">
            <header>
              <div><strong>{gitState?.repository ? gitState.branch || 'Detached HEAD' : 'No repository'}</strong><span>{gitState?.summary}</span></div>
              <div>
                <button title="Generate change and commit summary" onClick={() => void generateGitSummary()}>AI</button>
                <button onClick={() => void refreshGit()}>↻</button>
              </div>
            </header>
            {gitSummary && (
              <div className="git-summary">
                <strong>{gitSummary.commit_message}</strong>
                <span>{gitSummary.summary}</span>
                <small>{gitSummary.source}</small>
              </div>
            )}
            {gitState?.changes.map((change) => {
              const staged = change.status[0] !== ' ' && change.status[0] !== '?'
              return (
                <div className="git-change" key={`${change.status}-${change.path}`}>
                  <button onClick={() => void reviewGitFile(change.path, staged)}>
                    <b>{change.status.trim() || 'M'}</b>
                    <span>{change.path}</span>
                  </button>
                  <button onClick={() => void mutateGit(staged ? 'unstage' : 'stage', change.path)}>
                    {staged ? '−' : '+'}
                  </button>
                </div>
              )
            })}
            {gitDiff && <pre className="git-diff">{gitDiff}</pre>}
            {gitState?.repository && !gitState.changes.length && <Empty label="Working tree is clean" />}
            {!gitState?.repository && <Empty label="Open a Git repository to use Source Control" />}
          </div>
        </>
      )
    }
    return (
      <>
        <div className="side-panel-header">
          <span>EXPLORER</span>
          <div className="explorer-actions">
            <b>{files.length}</b>
            <button title="New file" onClick={() => createEntry('file')}>＋F</button>
            <button title="New folder" onClick={() => createEntry('directory')}>＋D</button>
          </div>
        </div>
        <div className="workspace-title workspace-heading">
          <span>⌄ {workspace.name.toUpperCase()}</span>
          <button title="Open folder" onClick={openWorkspace}>Open</button>
        </div>
        <div className="breadcrumb-row">
          <button onClick={() => { setCurrentDirectory('.'); setSelectedEntry(null) }}>⌂</button>
          <span>{currentDirectory}</span>
          {currentDirectory !== '.' && (
            <button onClick={() => {
              const parent = currentDirectory.split('/').slice(0, -1).join('/') || '.'
              setCurrentDirectory(parent)
              setSelectedEntry(parent === '.' ? null : parent)
            }}>↑</button>
          )}
        </div>
        <div className="file-tree">
          {renderFileTree(treeChildren['.'] ?? files)}
        </div>
        <button className="side-section-label" onClick={() => setActivePanel('outline')}>
          <span>⌄ OUTLINE</span><Icon name="outline" />
        </button>
        <button className="side-section-label" onClick={() => setActivePanel('history')}>
          <span>⌄ TIMELINE</span><Icon name="history" />
        </button>
      </>
    )
  }

  return (
    <div className="ide" data-theme={theme} style={ideStyle}>
      <div className="theme-particles" aria-hidden="true">
        <i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i />
      </div>
      <header className="titlebar">
        <div className="titlebar-brand"><KodexLogo size="small" /><span>Kodex</span></div>
        <button className="command-center" onClick={() => setCommandPaletteOpen(true)}>
          <Icon name="search" />
          <span>Search Kodex or run a command</span>
          <kbd>⌘ K</kbd>
        </button>
        <div className="titlebar-tools">
          <KodexSystemIsland
            agentActive={agentActive}
            provider={selectedModel === 'auto' ? 'Kodex Auto' : selectedModel.split(':', 1)[0]}
            model={selectedModelLabel}
            terminalActive={!layout.bottomCollapsed}
            onFocusAgent={() => {
              persistLayout({ ...layout, agentOpen: true })
              window.setTimeout(() => chatInputRef.current?.focus(), 0)
            }}
          />
          <div className="window-actions"><span>＋</span><span>−</span><span>□</span><span>×</span></div>
        </div>
      </header>

      <div className={`workbench ${layout.sidebarOpen ? '' : 'sidebar-closed'} ${layout.agentOpen ? '' : 'agent-closed'}`}>
        <nav className="activity-bar">
          <div className="activity-top">
            {primaryPanels.map((panel) => (
              <button
                key={panel}
                title={panelLabels[panel]}
                className={activePanel === panel && layout.sidebarOpen ? 'active' : ''}
                onClick={() => {
                  if (activePanel === panel && layout.sidebarOpen) {
                    persistLayout({ ...layout, sidebarOpen: false })
                  } else {
                    setActivePanel(panel)
                    persistLayout({ ...layout, sidebarOpen: true })
                  }
                }}
              >
                <Icon name={icons[panel]} />
                {panel === 'tasks' && tasks.length > 0 && <b>{tasks.length}</b>}
                {panel === 'plugins' && plugins.some((plugin) => plugin.installed && plugin.enabled) && <b>{plugins.filter((plugin) => plugin.installed && plugin.enabled).length}</b>}
              </button>
            ))}
          </div>
          <div className="activity-bottom">
            <button title="Account">◎</button>
            <button title="Settings" onClick={() => { setSettingsOpen(true); void loadDiagnostics() }}>⚙</button>
          </div>
        </nav>

        <aside className={`side-panel ${layout.sidebarOpen ? '' : 'panel-closed'}`}>{renderSidePanel()}</aside>
        {layout.sidebarOpen && <div className="resize-handle resize-sidebar" onPointerDown={(event) => resizePanel('sidebar', event)} />}

        <section className={`center-stack terminal-visible ${layout.bottomCollapsed ? 'bottom-collapsed' : ''} ${layout.bottomMaximized ? 'bottom-maximized' : ''}`}>
          <div className={`editor-area ${layout.bottomMaximized ? 'panel-closed' : ''}`}>
            <div className="tabbar">
              {openFiles.map((file) => (
                <button
                  className={`tab ${activeFile === file.path ? 'active' : ''}`}
                  key={file.path}
                  onClick={() => setActiveFile(file.path)}
                >
                  <span className="tab-file-icon">{file.path.endsWith('.py') ? 'PY' : 'TS'}</span>
                  <span>{file.path.split('/').at(-1)}</span>
                  {file.content !== file.savedContent && <i>●</i>}
                  {file.externallyChanged && <i className="external-change" title="Changed outside Kodex">!</i>}
                  <span className="tab-close" onClick={(event) => { event.stopPropagation(); closeFile(file.path) }}>×</span>
                </button>
              ))}
              {!openFiles.length && <div className="empty-tab">Welcome</div>}
              <div className="editor-actions">
                <button onClick={() => runProject('run')} title="Run"><Icon name="run" /></button>
                <button onClick={() => runProject('debug')} title="Debug"><Icon name="sparkles" /></button>
                <button
                  onClick={() => persistLayout({ ...layout, bottomCollapsed: !layout.bottomCollapsed, bottomPanel: 'terminal' })}
                  title="Toggle terminal"
                >
                  <Icon name="terminal" />
                </button>
                <button onClick={saveActiveFile} title="Save"><Icon name="check" /></button>
              </div>
            </div>

            {settingsOpen ? (
              <div className="settings-editor">
                <header>
                  <div className="settings-brand">
                    <KodexLogo size="large" />
                    <div>
                      <span>Control center</span>
                      <h1>Kodex Settings</h1>
                      <p>Models, agent intelligence, project context, and local security.</p>
                    </div>
                  </div>
                  <button onClick={() => setSettingsOpen(false)}>×</button>
                </header>
                <div className="settings-workspace">
                  <nav>
                    {([
                      ['general', 'General', 'Core lifecycle'],
                      ['appearance', 'Appearance', 'Themes and motion'],
                      ['terminal', 'Terminal', 'Shell and sessions'],
                      ['run-debug', 'Run and Debug', 'Profiles and output'],
                      ['models', 'Models & Subscriptions', 'Providers and routing'],
                      ['agent', 'Kodex Agent', 'Execution behavior'],
                      ['studio', 'Agent Studio', 'Prompts and versions'],
                      ['project', 'Project', 'Intelligence and skills'],
                      ['advisor', 'Model Advisor', 'Hardware matching'],
                      ['integrations', 'Integrations', 'MCP and plugins'],
                      ['security', 'Security', 'Permissions and isolation'],
                      ['diagnostics', 'Diagnostics', 'Runtime health'],
                      ['about', 'About Kodex', 'Credits and technology'],
                    ] as const).map(([id, label, detail]) => (
                      <button
                        key={id}
                        className={settingsSection === id ? 'active' : ''}
                        onClick={() => setSettingsSection(id)}
                      >
                        <strong>{label}</strong>
                        <span>{detail}</span>
                      </button>
                    ))}
                  </nav>
                  <main>
                    {settingsSection === 'general' && (
                      <section>
                        <div className="settings-section-heading">
                          <span>Desktop runtime</span>
                          <h2>General</h2>
                          <p>Control how the native app and authenticated local Core behave.</p>
                        </div>
                        <label className="setting-row settings-card">
                          <div>
                            <strong>Keep Core active in background</strong>
                            <span>Leave the local service running after the last window closes.</span>
                          </div>
                          <input
                            type="checkbox"
                            checked={desktopSettings.backgroundCore}
                            onChange={(event) => void updateDesktopSettings({ backgroundCore: event.target.checked })}
                          />
                        </label>
                        <div className="settings-actions">
                          <button onClick={() => void restartCore()}>Restart Core</button>
                          <button onClick={() => void loadDiagnostics()}>Refresh everything</button>
                        </div>
                      </section>
                    )}
                    {settingsSection === 'appearance' && (
                      <section>
                        <div className="settings-section-heading">
                          <span>Visual system</span>
                          <h2>Appearance</h2>
                          <p>Choose a complete workspace atmosphere. Each theme updates panels, editor color, focus states, activity, and ambient particles.</p>
                        </div>
                        <div className="theme-grid">
                          {themes.map((item) => (
                            <button
                              type="button"
                              key={item.id}
                              className={theme === item.id ? 'active' : ''}
                              onClick={() => void selectTheme(item.id)}
                            >
                              <span className="theme-preview" style={{
                                '--theme-a': item.colors[0],
                                '--theme-b': item.colors[1],
                                '--theme-c': item.colors[2],
                              } as CSSProperties}>
                                <i /><i /><i />
                              </span>
                              <strong>{item.name}</strong>
                              <small>{item.detail}</small>
                              <b>{theme === item.id ? 'Selected' : 'Apply'}</b>
                            </button>
                          ))}
                        </div>
                        <div className="settings-card motion-note">
                          <div>
                            <strong>Ambient motion</strong>
                            <span>Slow particles and focus glows respond to the selected theme without distracting from code.</span>
                          </div>
                          <b>Adaptive</b>
                        </div>
                      </section>
                    )}
                    {settingsSection === 'terminal' && (
                      <section>
                        <div className="settings-section-heading">
                          <span>Workspace shell</span>
                          <h2>Terminal</h2>
                          <p>Real workspace PTY sessions remain alive while the bottom panel is collapsed.</p>
                        </div>
                        <div className="settings-card agent-policy-list">
                          <div><span>Default shell</span><b>System login shell</b></div>
                          <div><span>Font</span><b>SF Mono / Menlo · 12px</b></div>
                          <div><span>Scrollback</span><b>5,000 lines</b></div>
                          <div><span>Default location</span><b>Active workspace</b></div>
                          <div><span>Session restore</span><b>Enabled while Kodex runs</b></div>
                        </div>
                        <div className="settings-actions">
                          <button onClick={() => persistLayout({ ...layout, bottomCollapsed: false, bottomPanel: 'terminal' })}>Open Terminal</button>
                        </div>
                      </section>
                    )}
                    {settingsSection === 'run-debug' && (
                      <section>
                        <div className="settings-section-heading">
                          <span>Visible execution</span>
                          <h2>Run and Debug</h2>
                          <p>Each launch opens a named terminal with its command, working directory, live output, and exit result.</p>
                        </div>
                        <div className="settings-card agent-policy-list">
                          <div><span>Detected profiles</span><b>{runProfiles.length}</b></div>
                          <div><span>Run output</span><b>Dedicated PTY</b></div>
                          <div><span>Debug output</span><b>Terminal + Debug Console</b></div>
                          <div><span>Errors</span><b>Problems source links</b></div>
                        </div>
                      </section>
                    )}
                    {settingsSection === 'models' && (
                      <section>
                        <div className="settings-section-heading">
                          <span>Model gateway</span>
                          <h2>Models and subscriptions</h2>
                          <p>ChatGPT Codex and Claude use their official login flows. Ollama and LM Studio remain local; Kodex never handles subscription payments or provider passwords.</p>
                        </div>
                        <div className="provider-list">
                          {providers.map((provider) => (
                            <div key={provider.id}>
                              <span className={`provider-light ${provider.available ? 'online' : ''}`} />
                              <div>
                                <strong>{provider.name}</strong>
                                <span>{provider.detail}</span>
                              </div>
                              <small>{provider.latency_ms == null ? provider.health : `${provider.latency_ms} ms`}</small>
                            </div>
                          ))}
                        </div>
                        <div className="settings-card settings-fields">
                          <label className="provider-field">
                            <span>LM Studio server URL</span>
                            <input
                              value={desktopSettings.lmStudioBaseUrl ?? 'http://127.0.0.1:1234/v1'}
                              onChange={(event) => setDesktopSettings((current) => ({ ...current, lmStudioBaseUrl: event.target.value }))}
                            />
                          </label>
                          <div className="codex-connect-actions">
                            <button onClick={() => void request('/v1/providers/lm-studio/test', { method: 'POST' }).then(() => request<Provider[]>('/v1/providers')).then(setProviders)}>
                              Retry LM Studio detection
                            </button>
                            <button onClick={() => void window.codex?.openExternal('https://lmstudio.ai/docs/developer/core/server')}>
                              Setup instructions
                            </button>
                          </div>
                        </div>
                        <div className="settings-card codex-connect-card">
                          <div>
                            <span>ChatGPT Codex</span>
                            <strong>{codexStatus?.authenticated ? 'Connected' : 'Use your Codex subscription'}</strong>
                            <p>{codexStatus?.message ?? 'Sign in through the official Codex browser flow. Kodex never receives your password or access token.'}</p>
                          </div>
                          <div className="codex-connect-actions">
                            <button onClick={() => void refreshCodexStatus()}>Check status</button>
                            {codexStatus?.authenticated ? (
                              <button onClick={() => void signOutOfCodex()}>Sign out</button>
                            ) : (
                              <button className="primary" onClick={() => void signInToCodex()}>Sign in with ChatGPT</button>
                            )}
                          </div>
                        </div>
                        <div className="settings-card codex-connect-card claude-connect-card">
                          <div>
                            <span>Anthropic Claude Code</span>
                            <strong>{claudeStatus?.authenticated ? 'Connected' : claudeStatus?.installed ? 'Sign in to Claude' : 'Install Claude Code'}</strong>
                            <p>{claudeStatus?.message ?? 'Use the official Claude Code login. Kodex never receives your password or access token.'}</p>
                          </div>
                          <div className="codex-connect-actions">
                            <button onClick={() => void refreshClaudeStatus()}>Check status</button>
                            {claudeStatus?.authenticated ? (
                              <button onClick={() => void signOutOfClaude()}>Sign out</button>
                            ) : claudeStatus?.installed ? (
                              <button className="primary claude-primary" onClick={() => void signInToClaude()}>Sign in to Claude</button>
                            ) : (
                              <button className="primary claude-primary" onClick={() => void installPlugin('anthropic-claude-code')}>Install Claude Code</button>
                            )}
                          </div>
                        </div>
                        <div className="settings-card settings-fields">
                          <label className="provider-field">
                            <span>OpenAI-compatible base URL</span>
                            <input
                              value={desktopSettings.openaiBaseUrl ?? 'https://api.openai.com/v1'}
                              onChange={(event) => setDesktopSettings((current) => ({ ...current, openaiBaseUrl: event.target.value }))}
                            />
                          </label>
                          <label className="provider-field">
                            <span>Default cloud model</span>
                            <input
                              value={desktopSettings.openaiModel ?? 'gpt-4.1-mini'}
                              onChange={(event) => setDesktopSettings((current) => ({ ...current, openaiModel: event.target.value }))}
                            />
                          </label>
                          <label className="provider-field">
                            <span>API key {providerKeyConfigured ? '· encrypted and configured' : ''}</span>
                            <input
                              type="password"
                              value={providerKey}
                              onChange={(event) => setProviderKey(event.target.value)}
                              placeholder={providerKeyConfigured ? 'Enter a replacement key' : 'Stored by Electron safeStorage'}
                            />
                          </label>
                        </div>
                        <button className="provider-save" onClick={() => void configureProvider()}>
                          Save provider and restart Core
                        </button>
                      </section>
                    )}
                    {settingsSection === 'agent' && (
                      <section>
                        <div className="settings-section-heading">
                          <span>Execution engine</span>
                          <h2>Agent intelligence</h2>
                          <p>OpenHands-inspired safeguards keep long tasks grounded and recoverable.</p>
                        </div>
                        <div className="capability-grid">
                          <div><b>20</b><span>Pursue-goal iterations</span></div>
                          <div><b>3×</b><span>Repeated-plan warning</span></div>
                          <div><b>5×</b><span>Automatic stuck stop</span></div>
                          <div><b>300s</b><span>Maximum command timeout</span></div>
                        </div>
                        <div className="settings-card agent-policy-list">
                          <div><span>Context condensation</span><b>Enabled</b></div>
                          <div><span>Validation before completion</span><b>Required</b></div>
                          <div><span>Safety snapshot before edits</span><b>Required</b></div>
                          <div><span>Crash recovery</span><b>Enabled</b></div>
                          <div><span>Trajectory recording</span><b>Enabled</b></div>
                        </div>
                      </section>
                    )}
                    {settingsSection === 'studio' && (
                      <section>
                        <div className="settings-section-heading">
                          <span>Versioned agents</span>
                          <h2>Agent Studio</h2>
                          <p>Create durable prompts, constitutions, workflows, and tool policies. Activation is gated by validation and passing evaluations.</p>
                        </div>
                        <div className="settings-card studio-editor">
                          <div className="studio-editor-row">
                            <label><span>Agent ID</span><input value={studioDraft.agentId} onChange={(event) => setStudioDraft((current) => ({ ...current, agentId: event.target.value }))} /></label>
                            <label><span>Name</span><input value={studioDraft.name} onChange={(event) => setStudioDraft((current) => ({ ...current, name: event.target.value }))} /></label>
                            <label><span>Role</span><input value={studioDraft.role} onChange={(event) => setStudioDraft((current) => ({ ...current, role: event.target.value }))} /></label>
                          </div>
                          <label><span>Prompt</span><textarea value={studioDraft.prompt} onChange={(event) => setStudioDraft((current) => ({ ...current, prompt: event.target.value }))} /></label>
                          <label><span>Constitution</span><textarea value={studioDraft.constitution} onChange={(event) => setStudioDraft((current) => ({ ...current, constitution: event.target.value }))} /></label>
                          <div className="studio-editor-row two">
                            <label><span>Workflow steps</span><input value={studioDraft.workflow} onChange={(event) => setStudioDraft((current) => ({ ...current, workflow: event.target.value }))} /></label>
                            <label><span>Allowed tools</span><input value={studioDraft.tools} onChange={(event) => setStudioDraft((current) => ({ ...current, tools: event.target.value }))} /></label>
                          </div>
                          <button onClick={() => void createStarterAgent()}>Save new draft version</button>
                        </div>
                        <div className="studio-list">
                          {agentProfiles.map((profile) => (
                            <div className="settings-card" key={profile.id}>
                              <header><div><strong>{profile.name}</strong><span>{profile.agent_id} · v{profile.version}</span></div><b>{profile.status}</b></header>
                              <p>{profile.prompt}</p>
                              <div><span>{profile.role}</span><span>{profile.workflow.length} workflow steps</span></div>
                            </div>
                          ))}
                          {!agentProfiles.length && <Empty label="No custom agents yet" />}
                        </div>
                      </section>
                    )}
                    {settingsSection === 'project' && (
                      <section>
                        <div className="settings-section-heading">
                          <span>Workspace graph</span>
                          <h2>Project intelligence</h2>
                          <p>{projectIntelligence?.summary ?? 'Analyzing workspace…'}</p>
                        </div>
                        <div className="capability-grid">
                          <div><b>{projectIntelligence?.file_count ?? 0}</b><span>Indexed files</span></div>
                          <div><b>{projectIntelligence?.symbols.length ?? 0}</b><span>Symbols</span></div>
                          <div><b>{projectIntelligence?.imports.length ?? 0}</b><span>Import edges</span></div>
                          <div><b>{projectIntelligence?.routes.length ?? 0}</b><span>Routes</span></div>
                        </div>
                        <div className="settings-card project-facts">
                          <div><span>Languages</span><b>{projectIntelligence?.languages.map((item) => item.name).join(', ') || 'None'}</b></div>
                          <div><span>Frameworks</span><b>{projectIntelligence?.frameworks.join(', ') || 'None detected'}</b></div>
                          <div><span>Package managers</span><b>{projectIntelligence?.package_managers.join(', ') || 'None detected'}</b></div>
                          <div><span>Entry points</span><b>{projectIntelligence?.entry_points.join(', ') || 'None detected'}</b></div>
                        </div>
                        <h3>Workspace skills</h3>
                        <div className="skill-list">
                          {projectSkills.map((skill) => (
                            <div key={skill.id}>
                              <div><strong>{skill.name}</strong><span>{skill.description || skill.path}</span></div>
                              <select value={skill.trust} onChange={(event) => void updateSkillTrust(skill, event.target.value as ProjectSkill['trust'])}>
                                <option value="trusted">Trusted</option>
                                <option value="review">Review</option>
                                <option value="disabled">Disabled</option>
                              </select>
                            </div>
                          ))}
                          {!projectSkills.length && <div><strong>No project skills yet</strong><span>Add Markdown skills under .kodex-agent/skills.</span></div>}
                        </div>
                      </section>
                    )}
                    {settingsSection === 'advisor' && (
                      <section>
                        <div className="settings-section-heading">
                          <span>Local capability</span>
                          <h2>Model Advisor</h2>
                          <p>Hardware-aware recommendations for fast local work and larger reasoning tasks.</p>
                        </div>
                        <div className="capability-grid">
                          <div><b>{modelAdvice ? Math.round(modelAdvice.hardware.memory_bytes / 1024 ** 3) : 0} GB</b><span>System memory</span></div>
                          <div><b>{modelAdvice?.hardware.cpu_count ?? 0}</b><span>CPU cores</span></div>
                          <div><b>{modelAdvice ? Math.round(modelAdvice.hardware.disk_free_bytes / 1024 ** 3) : 0} GB</b><span>Disk available</span></div>
                          <div><b>{modelAdvice?.runtimes.filter((runtime) => runtime.available).length ?? 0}</b><span>Active runtimes</span></div>
                        </div>
                        <div className="advisor-list">
                          {modelAdvice?.catalogue.map((model) => (
                            <div className={`settings-card ${modelAdvice.recommended.some((item) => item.id === model.id) ? 'recommended' : ''}`} key={model.id}>
                              <div><strong>{model.size}</strong><span>{model.use}</span></div>
                              <b>{modelAdvice.recommended.some((item) => item.id === model.id) ? 'Recommended' : `${model.minimum_memory_gb} GB minimum`}</b>
                            </div>
                          ))}
                        </div>
                      </section>
                    )}
                    {settingsSection === 'integrations' && (
                      <section>
                        <div className="settings-section-heading">
                          <span>Tool ecosystem</span>
                          <h2>MCP and plugins</h2>
                          <p>Configured servers are isolated by an explicit permission profile and tested before use.</p>
                        </div>
                        <div className="integration-list">
                          {mcpServers.map((server) => (
                            <div className="settings-card" key={server.id}>
                              <div><strong>{server.name}</strong><span>{server.command.join(' ')}</span></div>
                              <b className={server.status === 'available' ? 'online' : ''}>{server.status}</b>
                              <small>{server.permission}{server.last_error ? ` · ${server.last_error}` : ''}</small>
                            </div>
                          ))}
                          {!mcpServers.length && <Empty label="No MCP servers configured. Use the Core API or project configuration to add one." />}
                        </div>
                        <h3>Workspace skills</h3>
                        <div className="skill-list">
                          {projectSkills.map((skill) => (
                            <div key={skill.id}>
                              <div><strong>{skill.name}</strong><span>{skill.description || skill.path}</span></div>
                              <select value={skill.trust} onChange={(event) => void updateSkillTrust(skill, event.target.value as ProjectSkill['trust'])}>
                                <option value="trusted">Trusted</option>
                                <option value="review">Review</option>
                                <option value="disabled">Disabled</option>
                              </select>
                            </div>
                          ))}
                        </div>
                      </section>
                    )}
                    {settingsSection === 'security' && (
                      <section>
                        <div className="settings-section-heading">
                          <span>Local-first trust</span>
                          <h2>Security and permissions</h2>
                          <p>Workspace boundaries are enforced even when a model emits absolute paths.</p>
                        </div>
                        <div className="settings-card agent-policy-list">
                          <div><span>Local API authentication</span><b>{diagnostics?.authenticated ? 'Secured' : 'Development mode'}</b></div>
                          <div><span>Workspace path boundary</span><b>Enforced</b></div>
                          <div><span>External filesystem access</span><b>Approval required</b></div>
                          <div><span>Destructive commands</span><b>Approval required</b></div>
                          <div><span>Provider credentials</span><b>Electron safeStorage</b></div>
                          <div><span>Process-tree cancellation</span><b>Enabled</b></div>
                          <div><span>SQLite backup readiness</span><b>{runtimeValidation?.checks.database ? 'Ready' : 'Unavailable'}</b></div>
                        </div>
                        <div className="settings-actions">
                          <button onClick={() => void createDatabaseBackup()}>Create database backup</button>
                        </div>
                      </section>
                    )}
                    {settingsSection === 'diagnostics' && (
                      <section>
                        <div className="settings-section-heading">
                          <span>Live system</span>
                          <h2>Diagnostics and evaluations</h2>
                          <p>Runtime health, event transport, persistence, and golden smoke fixtures.</p>
                        </div>
                        <div className="diagnostic-grid">
                          <div><span>Status</span><strong>{diagnostics?.status ?? 'Loading'}</strong></div>
                          <div><span>Event stream</span><strong>{eventConnected ? 'Connected' : 'Reconnecting'}</strong></div>
                          <div><span>Watcher</span><strong>{diagnostics?.watcher ? 'Active' : 'Inactive'}</strong></div>
                          <div><span>Events retained</span><strong>{diagnostics?.events.retained ?? 0}</strong></div>
                          <div><span>Logs retained</span><strong>{diagnostics?.logs.retained ?? 0}</strong></div>
                          <div><span>Evaluation</span><strong>{evaluationReport?.passed ? 'Passing' : 'Needs attention'}</strong></div>
                          <div><span>Distribution runtime</span><strong>{runtimeValidation?.ready ? 'Ready' : 'Needs attention'}</strong></div>
                        </div>
                        <div className="evaluation-list">
                          {evaluationReport?.fixtures.map((fixture) => (
                            <div key={fixture.id}>
                              <span className={fixture.passed ? 'pass' : 'fail'}>{fixture.passed ? '✓' : '!'}</span>
                              <div><strong>{fixture.name}</strong><small>{fixture.detail}</small></div>
                            </div>
                          ))}
                        </div>
                        <div className="settings-card benchmark-scorecard">
                          <div>
                            <strong>Kodex task suite</strong>
                            <span>
                              {benchmarkReport
                                ? `${benchmarkReport.scorecard.passed}/${benchmarkReport.scorecard.total} passed in ${benchmarkReport.scorecard.duration_ms} ms`
                                : 'Run deterministic repository and runtime checks.'}
                            </span>
                          </div>
                          {benchmarkReport && <b>{Math.round(benchmarkReport.scorecard.pass_rate * 100)}%</b>}
                          <button onClick={() => void runBenchmarks()}>Run benchmarks</button>
                        </div>
                        <code className="diagnostic-path">{diagnostics?.workspace}</code>
                        <code className="diagnostic-path">{diagnostics?.database}</code>
                      </section>
                    )}
                    {settingsSection === 'about' && (
                      <section className="about-section">
                        <div className="settings-section-heading">
                          <span>About this IDE</span>
                          <h2>Kodex</h2>
                          <p>A local-first AI coding workspace created by Aayush Datta.</p>
                        </div>
                        <div className="about-hero settings-card">
                          <KodexLogo size="large" />
                          <div>
                            <strong>Kodex</strong>
                            <span>Autonomous coding IDE</span>
                            <p>Built to inspect projects, edit safely, run tools, recover from failures, and validate real outcomes on your machine.</p>
                          </div>
                        </div>
                        <h3>Created by</h3>
                        <div className="settings-card about-credit">
                          <strong>Aayush Datta</strong>
                          <span>Product design, engineering, and agent direction</span>
                        </div>
                        <h3>Technology</h3>
                        <div className="technology-grid">
                          {[
                            ['Desktop', 'Electron, React, TypeScript, Vite'],
                            ['Editor', 'Monaco Editor, xterm.js, node-pty'],
                            ['Core', 'Python, FastAPI, WebSocket'],
                            ['Storage', 'SQLite and local project continuity'],
                            ['AI', 'Codex CLI, Ollama, LM Studio, OpenAI-compatible providers'],
                            ['Tools', 'Git, MCP, typed workspace tools, process control'],
                          ].map(([name, detail]) => (
                            <div className="settings-card" key={name}><strong>{name}</strong><span>{detail}</span></div>
                          ))}
                        </div>
                        <p className="about-footnote">Kodex keeps project work local by default and gates consequential actions behind explicit permissions.</p>
                      </section>
                    )}
                  </main>
                </div>
              </div>
            ) : selectedFile ? (
              <>
                <div className="editor-breadcrumb">
                  <span>{selectedFile.path.split('/').join('  ›  ')}</span>
                </div>
                <div className="code-editor">
                  <Editor
                    beforeMount={configureMonaco}
                    onMount={mountEditor}
                    theme={`codex-${theme}`}
                    path={selectedFile.path}
                    language={editorLanguage(selectedFile.path)}
                    value={selectedFile.content}
                    onChange={(content) =>
                      setOpenFiles((current) =>
                        current.map((file) =>
                          file.path === selectedFile.path
                            ? { ...file, content: content ?? '' }
                            : file,
                        ),
                      )
                    }
                    loading={<div className="editor-loading">Starting Monaco Editor...</div>}
                    options={{
                      automaticLayout: true,
                      fontFamily: '"SF Mono", Menlo, Monaco, monospace',
                      fontSize: 13,
                      lineHeight: 20,
                      minimap: { enabled: true, renderCharacters: false, maxColumn: 100 },
                      padding: { top: 10, bottom: 10 },
                      roundedSelection: false,
                      scrollBeyondLastLine: false,
                      smoothScrolling: true,
                      tabSize: 2,
                      wordWrap: 'off',
                    }}
                  />
                </div>
              </>
            ) : (
              selectedSnapshot ? (
                <div className="diff-review">
                  <div className="diff-summary">
                    <div>
                      <strong>{selectedSnapshot.snapshot.label}</strong>
                      <span>{selectedSnapshot.file_count} changed files</span>
                    </div>
                    <b className="diff-additions">+{selectedSnapshot.additions}</b>
                    <b className="diff-deletions">-{selectedSnapshot.deletions}</b>
                    <button onClick={() => restoreSnapshot(selectedSnapshot.snapshot)}>Restore snapshot</button>
                  </div>
                  <div className="diff-files">
                    {selectedSnapshot.files.map((file) => (
                      <section key={file.path}>
                        <header>
                          <span className={`diff-status ${file.status}`}>{file.status}</span>
                          <strong>{file.path}</strong>
                          <b className="diff-additions">+{file.additions}</b>
                          <b className="diff-deletions">-{file.deletions}</b>
                        </header>
                        {file.binary ? (
                          <div className="binary-diff">Binary file changed</div>
                        ) : (
                          <pre>
                            {file.diff.split('\n').map((line, index) => (
                              <code
                                className={
                                  line.startsWith('+') && !line.startsWith('+++')
                                    ? 'added'
                                    : line.startsWith('-') && !line.startsWith('---')
                                      ? 'removed'
                                      : line.startsWith('@@')
                                        ? 'hunk'
                                        : ''
                                }
                                key={index}
                              >
                                {line || ' '}
                              </code>
                            ))}
                          </pre>
                        )}
                      </section>
                    ))}
                    {!selectedSnapshot.files.length && <Empty label="Workspace matches this snapshot" />}
                  </div>
                </div>
              ) : (
                <div className="welcome-editor">
                  <div className="welcome-dashboard">
                    <header className="welcome-hero">
                      <KodexLogo size="large" />
                      <h1>What do you want to build today?</h1>
                      <p>Describe an idea, open a workspace, or continue where you left off.</p>
                      <button className="welcome-prompt" onClick={() => chatInputRef.current?.focus()}>
                        <span>Ask Kodex to build, debug, explain, or explore...</span>
                        <span className="welcome-prompt-controls">
                          <small>Auto</small><small>Full access</small><b>↗</b>
                        </span>
                      </button>
                    </header>

                    <section className="dashboard-section">
                      <div className="dashboard-heading"><strong>Continue Working</strong><button onClick={openWorkspace}>View all →</button></div>
                      <div className="continue-grid">
                        <button onClick={() => activeFile ? setActiveFile(activeFile) : void createEntry('file')}>
                          <Icon name="folder" /><strong>{workspace.name}</strong><span>{workspace.path}</span><small>{openFiles.length ? `${openFiles.length} open files` : 'Current workspace'}</small>
                        </button>
                        <button onClick={() => setActivePanel('tasks')}>
                          <Icon name="tasks" /><strong>Agent Runtime</strong><span>{tasks.length ? `${tasks.length} tracked tasks` : 'Ready for a new task'}</span><small>{currentRun ? currentRun.status.replaceAll('_', ' ') : 'Local agent ready'}</small>
                        </button>
                        <button onClick={() => setActivePanel('plugins')}>
                          <Icon name="plugins" /><strong>Plugin SDK</strong><span>{plugins.filter((plugin) => plugin.installed).length} installed plugins</span><small>Extend Kodex</small>
                        </button>
                      </div>
                    </section>

                    <section className="dashboard-section">
                      <div className="dashboard-heading"><strong>Recent AI Sessions</strong><button onClick={() => setActivePanel('history')}>Timeline →</button></div>
                      <div className="session-grid">
                        <button onClick={() => setActivePanel('history')}>
                          <span className="session-state">✓</span>
                          <span><strong>{currentRun?.contract.objective || 'Workspace is ready'}</strong><small>{currentRun?.summary || 'Start a new session from the agent panel.'}</small></span>
                          <b>{currentRun?.status || 'ready'}</b>
                        </button>
                        <button onClick={() => chatInputRef.current?.focus()}>
                          <span className="session-state active">···</span>
                          <span><strong>Start a focused build session</strong><small>Plan, implement, test, and review with Kodex.</small></span>
                          <b>new</b>
                        </button>
                      </div>
                    </section>

                    <section className="dashboard-section quick-section">
                      <div className="dashboard-heading"><strong>Quick Actions</strong></div>
                      <div className="quick-actions">
                        <button onClick={() => void createEntry('file')}><Icon name="plus" />New File</button>
                        <button onClick={openWorkspace}><Icon name="folder" />Open Workspace</button>
                        <button onClick={() => setActivePanel('git')}><Icon name="git" />Source Control</button>
                        <button onClick={() => void runProject('run')}><Icon name="run" />Run Project</button>
                      </div>
                    </section>
                  </div>
                </div>
              )
            )}
          </div>

          <section className="bottom-panel">
            {!layout.bottomCollapsed && !layout.bottomMaximized && (
              <div className="resize-handle resize-bottom" onPointerDown={(event) => resizePanel('bottom', event)} />
            )}
            <TerminalPane
              processesRunning={running.length}
              theme={theme}
              collapsed={layout.bottomCollapsed}
              maximized={layout.bottomMaximized}
              activePanel={layout.bottomPanel}
              launchRequest={terminalLaunch}
              processLogs={processes}
              onPanelChange={(bottomPanel) => persistLayout({ ...layout, bottomPanel, bottomCollapsed: false })}
              onToggleCollapsed={() => persistLayout({
                ...layout,
                bottomCollapsed: !layout.bottomCollapsed,
                bottomMaximized: false,
              })}
              onToggleMaximized={() => persistLayout({
                ...layout,
                bottomCollapsed: false,
                bottomMaximized: !layout.bottomMaximized,
              })}
              onOpenSource={(path, line, column) => void openSourceLocation(path, line, column)}
            />
          </section>
        </section>

        {layout.agentOpen && <div className="resize-handle resize-agent" onPointerDown={(event) => resizePanel('agent', event)} />}
        <aside className={`agent-panel ${layout.agent <= 320 ? 'narrow' : ''} ${layout.agentOpen ? '' : 'panel-closed'}`}>
          <div className="agent-header">
            <div className="agent-provider-tabs">
              <button className={agentTab === 'kodex' ? 'active' : ''} onClick={() => setAgentTab('kodex')}>
                <KodexLogo size="tiny" /><span><strong>Kodex</strong><small>Built in</small></span>
              </button>
              {plugins.filter((plugin) => plugin.installed && plugin.enabled && plugin.agentTab).map((plugin) => (
                <button key={plugin.id} className={agentTab === `plugin:${plugin.id}` ? 'active' : ''} onClick={() => {
                  setAgentTab(`plugin:${plugin.id}`)
                  void selectModel(plugin.id === 'openai-codex-kodex' ? 'openai-codex:default' : 'anthropic-claude-code:sonnet')
                }}>
                  <ProviderBrandMark brand={plugin.appearance.brand ?? 'generic'} size="small" />
                  <span><strong>{plugin.appearance.title}</strong><small>{plugin.name}</small></span>
                </button>
              ))}
            </div>
            <div className="agent-header-actions">
              <span className={`access-badge ${permission}`}>{permission === 'full-access' ? 'Full access' : permission.replaceAll('-', ' ')}</span>
              <div className="agent-header-menu-wrap">
                <button
                  title="Previous chats and sessions"
                  className={agentHeaderMenu === 'history' ? 'active' : ''}
                  onClick={() => setAgentHeaderMenu((menu) => menu === 'history' ? null : 'history')}
                >
                  <Icon name="history" />
                </button>
                {agentHeaderMenu === 'history' && (
                  <div className="agent-session-menu">
                    <header><strong>Chats & sessions</strong><span>{conversations.length}</span></header>
                    <div className="agent-session-list">
                      {conversations.map((conversation) => (
                        <button
                          className={conversation.id === conversationId ? 'selected' : ''}
                          key={conversation.id}
                          onClick={() => void openConversation(conversation.id)}
                        >
                          <span>
                            <strong>{conversation.title || 'Untitled session'}</strong>
                            <small>
                              {conversation.updated_at
                                ? new Date(conversation.updated_at).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
                                : 'Saved session'}
                            </small>
                          </span>
                          <i
                            role="button"
                            tabIndex={0}
                            title="Delete this session"
                            onClick={(event) => {
                              event.stopPropagation()
                              void deleteAgentSession(conversation.id)
                            }}
                            onKeyDown={(event) => {
                              if (event.key === 'Enter' || event.key === ' ') {
                                event.preventDefault()
                                event.stopPropagation()
                                void deleteAgentSession(conversation.id)
                              }
                            }}
                          >
                            <Icon name="trash" />
                          </i>
                        </button>
                      ))}
                    </div>
                    <button className="agent-session-new" onClick={() => void startFreshSession()}>
                      <Icon name="plus" /><span>New clean session</span>
                    </button>
                  </div>
                )}
              </div>
              <div className="agent-header-menu-wrap">
                <button
                  title="Clean agent"
                  className={agentHeaderMenu === 'clean' ? 'active' : ''}
                  onClick={() => setAgentHeaderMenu((menu) => menu === 'clean' ? null : 'clean')}
                >
                  <Icon name="clean" />
                </button>
                {agentHeaderMenu === 'clean' && (
                  <div className="agent-clean-menu">
                    <header><strong>Clean agent</strong><small>Choose what to remove</small></header>
                    <button onClick={() => void startFreshSession()}><Icon name="plus" /><span><strong>Clear window</strong><small>Start clean and keep history</small></span></button>
                    <button disabled={!conversationId || agentActive} onClick={() => conversationId && void deleteAgentSession(conversationId)}><Icon name="trash" /><span><strong>Delete current session</strong><small>{agentActive ? 'Stop the active run first' : 'Remove this chat and its activity'}</small></span></button>
                    <button className="danger" disabled={agentActive} onClick={() => void clearAgentHistory()}><Icon name="clean" /><span><strong>Clear all history</strong><small>{agentActive ? 'Stop the active run first' : 'Delete every saved chat and session'}</small></span></button>
                  </div>
                )}
              </div>
              <button title="New clean session" onClick={() => void startFreshSession()}><Icon name="plus" /></button>
              <button title="Close Kodex Agent" onClick={() => persistLayout({ ...layout, agentOpen: false })}>×</button>
            </div>
          </div>
          <div className={`chat-scroll ${!messages.length && !currentRun ? 'empty' : ''}`}>
            {!messages.length && !currentRun && (
              <div className="kodex-idle">
                <KodexAgentCore variant="full" state={agentCoreState(null)} onShortcut={applyAgentShortcut} />
              </div>
            )}
            {messages.map((message, index) => (
              <div className={`message ${message.role}`} key={index}>
                <span>
                  {message.role === 'assistant'
                    ? <KodexLogo size="tiny" />
                    : 'You'}
                </span>
                <div className="message-content">
                  {message.attachments?.some((item) => item.kind === 'image') && (
                    <div className="message-image-grid">
                      {message.attachments
                        .filter((item) => item.kind === 'image')
                        .map((attachment) => (
                          <img key={attachment.path} src={attachment.dataUrl} alt={attachment.name} />
                        ))}
                    </div>
                  )}
                  <p>{message.content}</p>
                </div>
              </div>
            ))}
            {currentRun && (
              <div className="agent-turn">
                <div className="agent-turn-avatar"><KodexLogo size="tiny" /></div>
                <div className="agent-turn-body">
                  <KodexAgentCore variant="compact" state={agentCoreState(currentRun)} />
                  <div className="agent-turn-heading">
                    <strong>{currentRun.contract.objective || 'Working on your request'}</strong>
                    <small>
                      {currentRun.phase ? `${currentRun.phase.replaceAll('_', ' ')} · ` : ''}
                      {currentRun.model === 'auto' ? 'Auto' : currentRun.model}
                    </small>
                  </div>
                  {isPlanRun(currentRun) && currentRun.tasks && currentRun.tasks.length > 0 && (
                    <section className="agent-task-list">
                      {currentRun.tasks.map((task) => (
                        <div className={`agent-task ${task.status}`} key={task.id}>
                          <span>{task.status === 'completed' ? '✓' : task.status === 'blocked' ? '!' : task.position}</span>
                          <p>{task.title}</p>
                          <small>{task.status.replaceAll('_', ' ')}</small>
                        </div>
                      ))}
                    </section>
                  )}
                  {isPlanRun(currentRun) ? (
                    <div className="agent-trace">
                      {currentRun.steps.map((step) => {
                        const presentation = stepPresentation(step)
                        const expanded = expandedActivity === `step-${step.id}`
                        const canView = presentation.kind === 'editing'
                          && typeof step.data.path === 'string'
                          && !step.data.deleted
                        return (
                          <div className={`agent-trace-row ${presentation.kind} ${step.status} ${canView ? 'viewable' : ''}`} key={step.id}>
                            <span className="trace-marker">
                              {step.status === 'completed' ? '✓' : step.status === 'failed' ? '!' : '·'}
                            </span>
                            <button type="button" onClick={() => setExpandedActivity(expanded ? null : `step-${step.id}`)}>
                              <strong>{presentation.label}</strong>
                              {presentation.detail && <small>{presentation.detail}</small>}
                            </button>
                            {canView && (
                              <button className="agent-view-button" type="button" onClick={() => void viewAgentStep(step)}>
                                View
                              </button>
                            )}
                            {expanded && Object.keys(step.data).length > 0 && (
                              typeof step.data.diff === 'string' && step.data.diff
                                ? <pre className="agent-diff">{step.data.diff}</pre>
                                : <pre>{JSON.stringify(step.data, null, 2)}</pre>
                            )}
                          </div>
                        )
                      })}
                      {agentActive && liveAgentEdit && (
                        <div className="agent-progress-live viewable">
                          <span><i /></span>
                          <div>
                            <strong>{liveAgentEvent?.label || `${liveAgentEdit.operation} ${liveAgentEdit.path}`}</strong>
                            <small>{liveAgentEdit.path}</small>
                          </div>
                          <button className="agent-view-button" type="button" onClick={() => void viewAgentEdit(liveAgentEdit)}>
                            View
                          </button>
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="agent-conversation-progress">
                      {conversationProgress(currentRun.steps).map((item) => {
                        if (item.type === 'narrative') {
                          return <p className="agent-progress-message" key={item.id}>{item.text}</p>
                        }
                        const expanded = expandedActivity === item.id
                        const viewStep = item.steps.find((step) =>
                          activityKind(step) === 'editing'
                          && typeof step.data.path === 'string'
                          && !step.data.deleted
                        )
                        return (
                          <div className={`agent-progress-activity ${item.kind} ${viewStep ? 'viewable' : ''}`} key={item.id}>
                            <button type="button" onClick={() => setExpandedActivity(expanded ? null : item.id)}>
                              <Icon name={item.kind === 'editing' ? 'file' : item.kind === 'testing' ? 'check' : item.kind === 'running' ? 'terminal' : 'search'} />
                              <span>{item.label}</span>
                              {item.detail && <code>{item.detail}</code>}
                              {typeof item.additions === 'number' && item.additions > 0 && <b className="line-additions">+{item.additions}</b>}
                              {typeof item.deletions === 'number' && item.deletions > 0 && <b className="line-deletions">-{item.deletions}</b>}
                            </button>
                            {viewStep && (
                              <button className="agent-view-button" type="button" onClick={() => void viewAgentStep(viewStep)}>
                                View
                              </button>
                            )}
                            {expanded && (
                              <div className="agent-progress-details">
                                {item.steps.map((step) => (
                                  typeof step.data.diff === 'string' && step.data.diff
                                    ? <pre className="agent-diff" key={step.id}>{step.data.diff}</pre>
                                    : <div key={step.id}><strong>{stepPresentation(step).label}</strong><small>{stepPresentation(step).detail}</small></div>
                                ))}
                              </div>
                            )}
                          </div>
                        )
                      })}
                      {agentActive && (
                        <div className={`agent-progress-live ${liveAgentEdit ? 'viewable' : ''}`}>
                          <span><i /></span>
                          <div>
                            <strong>{liveAgentEvent?.label || currentRun.summary || 'Thinking'}</strong>
                            {liveAgentEvent?.detail && <small>{liveAgentEvent.detail}</small>}
                          </div>
                          {liveAgentEdit && (
                            <button className="agent-view-button" type="button" onClick={() => void viewAgentEdit(liveAgentEdit)}>
                              View
                            </button>
                          )}
                        </div>
                      )}
                      {!currentRun.steps.length && !agentActive && (
                        <p className="agent-progress-message">{currentRun.summary || 'Finished'}</p>
                      )}
                    </div>
                  )}
                  {isPlanRun(currentRun) && currentRun.status === 'completed' ? (
                    <section className="plan-review">
                      <header>
                        <span><Icon name="sparkles" /></span>
                        <div><strong>Plan ready</strong><small>Review the approach before Kodex changes files.</small></div>
                      </header>
                      <ol>
                        {planItems(currentRun.summary).map((item, index) => (
                          <li key={`${index}-${item}`}><span>{index + 1}</span><p>{item}</p></li>
                        ))}
                      </ol>
                      <button className="continue-plan" onClick={() => void executeApprovedPlan()}>
                        <Icon name="run" />
                        <span><strong>Continue with this plan</strong><small>Start implementation and validation</small></span>
                        <b>→</b>
                      </button>
                    </section>
                  ) : currentRun.summary
                    && !agentActive
                    && currentRun.status !== 'failed'
                    && currentRun.status !== 'needs_review' && (
                    <p className="agent-result">{currentRun.summary}</p>
                  )}
                  {!agentActive && isPlanRun(currentRun) && currentRun.completion_evidence && (
                    <section className="completion-evidence">
                      {currentRun.changed_files && currentRun.changed_files.length > 0 && (
                        <div>
                          <strong>Changed files</strong>
                          {currentRun.changed_files.map((path) => (
                            <button className="changed-file-button" key={path} onClick={() => void viewAgentPath(path)}>
                              <code>{path}</code><span>View</span>
                            </button>
                          ))}
                        </div>
                      )}
                      {currentRun.validation?.results && currentRun.validation.results.length > 0 && (
                        <div>
                          <strong>Checks run</strong>
                          {currentRun.validation.results.map((result, index) => (
                            <code key={index}>
                              {String(result.command ?? 'Validation')} · exit {String(result.exit_code ?? '?')}
                            </code>
                          ))}
                        </div>
                      )}
                      {currentRun.provider_attempts && currentRun.provider_attempts.some((attempt) => attempt.status === 'failed') && (
                        <div>
                          <strong>Provider recovery</strong>
                          {currentRun.provider_attempts
                            .filter((attempt) => attempt.status === 'failed')
                            .map((attempt) => (
                              <code key={attempt.id}>{attempt.provider} recovered through fallback</code>
                            ))}
                        </div>
                      )}
                    </section>
                  )}
                  <div className="agent-run-actions">
                    {agentActive && currentRun.status !== 'paused' && (
                      <button onClick={() => void setAgentPaused(true)}>Pause</button>
                    )}
                    {agentActive && (
                      <button
                        className="danger"
                        disabled={stopPending}
                        onClick={() => void cancelAgent()}
                      >
                        {stopPending || currentRun.status === 'stopping' ? 'Stopping…' : 'Stop now'}
                      </button>
                    )}
                    {currentRun.status === 'paused' && (
                      <button onClick={() => void setAgentPaused(false)}>Resume</button>
                    )}
                    {currentRun.status === 'interrupted' && (
                      <button onClick={() => void continueInterruptedRun()}>Continue run</button>
                    )}
                  </div>
                  {(currentRun.status === 'needs_review' || currentRun.status === 'failed') && (
                    <section className="run-recovery">
                      <header>
                        <span>
                          {currentRun.failure_code === 'context_overflow'
                            ? 'Context limit reached'
                            : currentRun.failure_details?.provider
                            ? `${currentRun.failure_details.provider} error`
                            : 'Needs review'}
                        </span>
                        <strong>{currentRun.failure_details?.message || currentRun.summary}</strong>
                      </header>
                      {currentRun.failure_code && <code>{currentRun.failure_code}</code>}
                      {currentRun.changed_files && currentRun.changed_files.length > 0 && (
                        <div className="changed-file-list">
                          {currentRun.changed_files.map((path) => (
                            <button key={path} onClick={() => void viewAgentPath(path)}>
                              <span>{path}</span><b>View</b>
                            </button>
                          ))}
                        </div>
                      )}
                      <div>
                        <button className="primary" onClick={() => void retryCurrentRun('repair')}>
                          {currentRun.failure_code === 'context_overflow'
                            ? 'Retry with reduced context'
                            : 'Retry with repair'}
                        </button>
                        {currentRun.failure_details?.provider === 'lm-studio' && (
                          <button onClick={() => void testLmStudio()}>Test LM Studio</button>
                        )}
                        {currentRun.failure_details?.provider && (
                          <button onClick={() => {
                            setSettingsOpen(true)
                            setSettingsSection('models')
                          }}>Open provider settings</button>
                        )}
                        {currentRun.changed_files && currentRun.changed_files.length > 0 && (
                          <button onClick={() => void openAffectedFile()}>Open affected file</button>
                        )}
                        <button onClick={() => { setSettingsOpen(true); setSettingsSection('diagnostics'); void loadDiagnostics() }}>Review diagnostics</button>
                        <button onClick={() => void retryCurrentRun('fresh')}>Start fresh</button>
                      </div>
                    </section>
                  )}
                </div>
              </div>
            )}
            {pendingApprovals.map((approval) => (
              <div className="approval-card" key={approval.id}>
                <span>{approval.kind === 'fetch_url' ? 'Web research request' : 'Approval needed'}</span>
                <strong>{approval.summary}</strong>
                {approval.kind === 'fetch_url' ? (
                  <div className="web-approval-detail">
                    <code>{String(approval.details.url ?? '')}</code>
                    <p>Kodex will read public text from this page. Creating or changing a file remains visible in the agent timeline.</p>
                  </div>
                ) : (
                  <pre>{JSON.stringify(approval.details, null, 2)}</pre>
                )}
                <div>
                  <button onClick={() => void resolveApproval(approval.id, false)}>Reject</button>
                  <button onClick={() => void resolveApproval(approval.id, true, true)}>
                    {approval.kind === 'fetch_url' ? 'Always allow web research' : 'Always allow'}
                  </button>
                  <button className="approve" onClick={() => void resolveApproval(approval.id, true)}>
                    {approval.kind === 'fetch_url' ? 'Allow once' : 'Approve'}
                  </button>
                </div>
              </div>
            ))}
            {(messages.length > 0 || currentRun) && !providers.some((provider) => provider.available) && (
              <div className="launch-note">
                <strong>Model setup required</strong>
                <span>Action needed</span>
                <p>Start Ollama or LM Studio, or configure an OpenAI-compatible provider in Settings.</p>
              </div>
            )}
            <div ref={chatEndRef} />
          </div>
          <form
            className={`composer ${composerDragging ? 'dragging' : ''}`}
            onSubmit={sendMessage}
            ref={composerRef}
            onDragEnter={(event) => {
              if (event.dataTransfer.types.includes('Files')) {
                event.preventDefault()
                setComposerDragging(true)
              }
            }}
            onDragOver={(event) => {
              if (event.dataTransfer.types.includes('Files')) event.preventDefault()
            }}
            onDragLeave={(event) => {
              if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
                setComposerDragging(false)
              }
            }}
            onDrop={(event) => {
              event.preventDefault()
              setComposerDragging(false)
              void droppedAttachments(event.dataTransfer.files)
            }}
          >
            {composerDragging && <div className="composer-drop-zone">Drop up to 10 images</div>}
            {attachments.length > 0 && (
              <div className="attachment-strip">
                {attachments.map((attachment) => (
                  attachment.kind === 'image' ? (
                    <div className="attachment-preview" key={attachment.path}>
                      <img src={attachment.dataUrl} alt={attachment.name} />
                      <button
                        type="button"
                        aria-label={`Remove ${attachment.name}`}
                        onClick={() => setAttachments((current) => current.filter((item) => item.path !== attachment.path))}
                      >×</button>
                      <span>{attachment.name}</span>
                    </div>
                  ) : (
                    <button
                      type="button"
                      className="attachment-file"
                      key={attachment.path}
                      onClick={() => setAttachments((current) => current.filter((item) => item.path !== attachment.path))}
                    >
                      {attachment.name} ×
                    </button>
                  )
                ))}
              </div>
            )}
            {includeIdeContext && (
              <div className="agent-context-summary" aria-label="Included IDE context">
                <span><b>Context</b></span>
                <span title={activeFile || 'No active file'}>{activeFile ? activeFile.split('/').at(-1) : 'No active file'}</span>
                <span>{openFiles.length} open</span>
                <span>{openFiles.filter((file) => file.content !== file.savedContent).length} unsaved</span>
                <span>{gitState?.branch || 'No Git branch'}</span>
                <span>{running.length} processes</span>
                <span>{runProfiles.length} run profiles</span>
                <span className={health ? 'ready' : 'warning'}>{health ? 'Core ready' : 'Core offline'}</span>
              </div>
            )}
            <textarea
              ref={chatInputRef}
              rows={2}
              value={chatInput}
              onChange={(event) => setChatInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault()
                  event.currentTarget.form?.requestSubmit()
                }
              }}
              placeholder={agentActive
                ? 'Kodex is working…'
                : 'Ask Kodex to build, change, explain, run, or review…'}
            />
            <div className="composer-controls">
              <button
                type="button"
                className="composer-icon-button attach-button"
                aria-label="Attach photos and files"
                title="Attach photos and files"
                onClick={() => void chooseAttachments()}
              >
                <Icon name="paperclip" />
              </button>
              <button
                type="button"
                className={`composer-mode-button ${planMode ? 'active' : ''}`}
                aria-pressed={planMode}
                onClick={() => setPlanMode((enabled) => !enabled)}
              >
                <Icon name="sparkles" /><span>{planMode ? 'Plan' : 'Normal'}</span>
              </button>
              <div className="add-control">
                <button
                  type="button"
                  className={`composer-icon-button ${controlMenuOpen ? 'active' : ''}`}
                  aria-label="More agent controls"
                  aria-expanded={controlMenuOpen}
                  onClick={() => {
                    setControlMenuOpen((open) => !open)
                    setComposerSelectOpen(null)
                  }}
                >
                  <Icon name="context" />
                </button>
                {controlMenuOpen && (
                  <div className="add-menu" role="menu">
                    <button type="button" role="menuitem" onClick={attachOpenFiles}><Icon name="open-files" /><strong>Add open files</strong><small>{openFiles.length}</small></button>
                    <hr />
                    <CompactSwitch icon="context" label="Include IDE context" checked={includeIdeContext} onChange={setIncludeIdeContext} />
                    <CompactSwitch icon="target" label="Pursue goal (slower)" checked={pursueGoal} onChange={setPursueGoal} />
                    <hr />
                    <button type="button" role="menuitem" onClick={() => { setActivePanel('plugins'); setControlMenuOpen(false) }}><Icon name="plugins" /><strong>Plugins</strong><Icon name="chevron" /></button>
                  </div>
                )}
              </div>
              <div className="composer-select">
                <button
                  type="button"
                  className={composerSelectOpen === 'permission' ? 'active' : ''}
                  onClick={() => {
                    setComposerSelectOpen((open) => open === 'permission' ? null : 'permission')
                    setControlMenuOpen(false)
                  }}
                >
                  <Icon name="permission" /><span>{permissionLabel[permission]}</span><Icon name="chevron" />
                </button>
                {composerSelectOpen === 'permission' && (
                  <div className="composer-select-menu permission-menu">
                    {Object.entries(permissionLabel).map(([value, label]) => (
                      <button type="button" className={permission === value ? 'selected' : ''} key={value} onClick={() => { void selectPermission(value); setComposerSelectOpen(null) }}>
                        <span>{label}</span>{permission === value && <Icon name="check" />}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <div className="composer-select model-select">
                <button
                  type="button"
                  className={composerSelectOpen === 'model' ? 'active' : ''}
                  onClick={() => {
                    setComposerSelectOpen((open) => open === 'model' ? null : 'model')
                    setControlMenuOpen(false)
                  }}
                >
                  <Icon name="model" /><span>{selectedModelLabel}</span><Icon name="chevron" />
                </button>
                {composerSelectOpen === 'model' && (
                  <div className="composer-select-menu model-menu">
                    <button type="button" className={selectedModel === 'auto' ? 'selected' : ''} onClick={() => { void selectModel('auto'); setComposerSelectOpen(null) }}><span><strong>Auto</strong><small>Reliability-first routing</small></span>{selectedModel === 'auto' && <Icon name="check" />}</button>
                    {providers.map((provider) => (
                      <div className="model-provider-group" key={provider.id}>
                        <header>
                          <span><i className={`provider-light ${provider.available ? 'online' : ''}`} />{provider.name}</span>
                          <small>{provider.available ? provider.health : 'offline'}</small>
                        </header>
                        {provider.models.map((model) => {
                          const value = `${provider.id}:${model.id}`
                          return (
                            <button
                              type="button"
                              className={selectedModel === value ? 'selected' : ''}
                              key={value}
                              onClick={() => {
                                void selectModel(value)
                                setComposerSelectOpen(null)
                              }}
                            >
                              <span>
                                <strong>{model.name}</strong>
                                <small>{Math.round(model.context_length / 1000)}k context · {model.capabilities.includes('vision') ? 'vision' : 'text'}</small>
                              </span>
                              {selectedModel === value && <Icon name="check" />}
                            </button>
                          )
                        })}
                        {!provider.models.length && <p>{provider.detail}</p>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
              {agentActive ? (
                <>
                  <button
                    className={`send-button stop-button ${stopPending ? 'stopping' : ''}`}
                    type="button"
                    title={stopPending ? 'Stopping run' : 'Stop run immediately'}
                    aria-label={stopPending ? 'Stopping run' : 'Stop run immediately'}
                    disabled={stopPending}
                    onClick={() => void cancelAgent()}
                  >
                    {stopPending ? <span className="stop-progress" /> : <Icon name="stop" />}
                  </button>
                  <button className="send-button" type="submit" title="Steer active run" disabled={!chatInput.trim() && !attachments.length}><Icon name="arrow-up" /></button>
                </>
              ) : (
                <button className="send-button" type="submit" disabled={(!chatInput.trim() && !attachments.length) || !conversationId}><Icon name="arrow-up" /></button>
              )}
            </div>
          </form>
        </aside>
      </div>

      <footer className="statusbar">
        <div>⑂ {gitState?.branch || 'main'}</div>
        <div>○ 0 &nbsp; △ 0</div>
        <div className="status-grow" />
        <div><span className={`status-dot ${providers.some((provider) => provider.kind === 'local' && provider.available) ? 'online' : ''}`} /> Local model {providers.some((provider) => provider.kind === 'local' && provider.available) ? 'ready' : 'offline'}</div>
        <div>Core {health ? 'ready' : 'offline'}</div>
        <div>UTF-8</div>
        <div>LF</div>
        <div>{selectedFile ? editorLanguage(selectedFile.path) : 'TypeScript'}</div>
      </footer>
      {commandPaletteOpen && (
        <div className="command-overlay" onMouseDown={() => setCommandPaletteOpen(false)}>
          <div className="command-palette" onMouseDown={(event) => event.stopPropagation()}>
            <input
              autoFocus
              value={commandQuery}
              onChange={(event) => setCommandQuery(event.target.value)}
              placeholder="Type a command"
            />
            <div>
              {commands.map((command, index) => (
                <button key={command.label} className={index === 0 ? 'selected' : ''} onClick={() => runPaletteCommand(command)}>
                  <strong>{command.label}</strong>
                  <span>{command.detail}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
      {onboardingOpen && (
        <div className="onboarding-overlay">
          <div className="onboarding-card">
            <div className="onboarding-mark"><KodexLogo size="large" /></div>
            <span>Welcome to Kodex</span>
            <h1>Your local AI development workspace is ready.</h1>
            <p>The desktop app owns an authenticated Core, a production editor, workspace tools, process controls, memory, tasks, and rollback history.</p>
            <div className="onboarding-features">
              <div><b>⌘⇧P</b><span>Open the command palette</span></div>
              <div><b>Open</b><span>Choose any local workspace</span></div>
              <div><b>Core</b><span>Inspect or restart it in Settings</span></div>
            </div>
            <button onClick={completeOnboarding}>Start working</button>
          </div>
        </div>
      )}
      {bootVisible && <KodexBootSequence onComplete={() => setBootVisible(false)} />}
      {error && <div className="error-toast">{error}</div>}
    </div>
  )
}

function KodexBootSequence({ onComplete }: { onComplete: () => void }) {
  const [stage, setStage] = useState(0)
  const completeRef = useRef(onComplete)
  completeRef.current = onComplete

  useEffect(() => {
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const duration = reducedMotion ? 650 : 2800
    const stageTimer = window.setInterval(
      () => setStage((current) => Math.min(3, current + 1)),
      reducedMotion ? 180 : 620,
    )
    const finishTimer = window.setTimeout(() => completeRef.current(), duration)
    const skip = (event: KeyboardEvent) => {
      if (['Enter', 'Escape', ' '].includes(event.key)) completeRef.current()
    }
    window.addEventListener('keydown', skip)
    return () => {
      window.clearInterval(stageTimer)
      window.clearTimeout(finishTimer)
      window.removeEventListener('keydown', skip)
    }
  }, [])

  const stages = [
    'Mounting local workspace',
    'Synchronizing agent runtime',
    'Calibrating model providers',
    'Kodex Core online',
  ]

  return (
    <button
      type="button"
      className={`kodex-boot stage-${stage}`}
      aria-label="Kodex starting. Click or press Enter, Escape, or Space to skip."
      onClick={onComplete}
    >
      <KodexCodeField density={1.55} provider="kodex" interactive />
      <span className="kodex-boot-grid" aria-hidden="true" />
      <span className="kodex-boot-scan" aria-hidden="true" />
      <span className="kodex-boot-telemetry top">KODEX // BOOT SEQUENCE</span>
      <span className="kodex-boot-telemetry left">LOCAL RUNTIME</span>
      <span className="kodex-boot-telemetry right">SIGNAL LOCKED</span>
      <span className="kodex-boot-core">
        <span className="kodex-boot-rings" aria-hidden="true"><i /><i /><i /></span>
        <KodexLogo size="hero" />
        <strong>KODEX</strong>
        <small>{stages[stage]}</small>
        <span className="kodex-boot-progress"><i /></span>
      </span>
      <span className="kodex-boot-skip">Click or press any skip key</span>
    </button>
  )
}

const kodexAgentShortcuts = [
  { command: '/build', label: 'Build', prompt: 'Build a complete, production-ready feature for: ', icon: 'run' as IconName },
  { command: '/debug', label: 'Debug', prompt: 'Reproduce, diagnose, and fix this issue, then verify the result: ', icon: 'clean' as IconName },
  { command: '/explain', label: 'Explain', prompt: 'Explain this clearly using the current workspace context: ', icon: 'info' as IconName },
  { command: '/test', label: 'Test', prompt: 'Add and run focused tests for: ', icon: 'check' as IconName },
  { command: '/review', label: 'Review', prompt: 'Review the current changes for bugs, regressions, risks, and missing tests: ', icon: 'search' as IconName },
]

function KodexAgentCore({
  variant,
  state,
  onShortcut,
}: {
  variant: 'full' | 'compact'
  state: { id: string; label: string }
  onShortcut?: (prompt: string) => void
}) {
  if (variant === 'compact') {
    return (
      <section className={`kodex-live-core state-${state.id}`} aria-label={`Kodex ${state.label}`}>
        <div className="kodex-live-orbit" aria-hidden="true"><i /><i /><i /></div>
        <div className="kodex-live-mark"><KodexLogo size="small" /></div>
        <div>
          <span>KODEX CORE</span>
          <strong>{state.label}</strong>
        </div>
        <b aria-hidden="true"><i /><i /><i /><i /></b>
      </section>
    )
  }

  return (
    <section className={`kodex-command-center state-${state.id}`} aria-label="Kodex command center">
      <KodexCodeField density={1.25} provider="kodex" interactive />
      <div className="kodex-idle-grid" aria-hidden="true" />
      <div className="kodex-idle-scan" aria-hidden="true" />
      <div className="kodex-idle-telemetry" aria-hidden="true">
        <span className="telemetry-label telemetry-label-top">KODEX // CORE STREAM</span>
        <span className="telemetry-label telemetry-label-left">LOCAL AGENT 01</span>
        <span className="telemetry-label telemetry-label-right">SIGNAL STABLE</span>
        <i /><i /><i /><i /><i /><i />
      </div>
      <div className="kodex-command-main">
        <div className="kodex-system-line"><span>◌</span><b>SYSTEM_READY</b><i /></div>
        <div className="idle-pulse-rings" aria-hidden="true">
          <i /><i /><i />
          <span className="orbit-node node-one" />
          <span className="orbit-node node-two" />
          <span className="orbit-node node-three" />
        </div>
        <div className="kodex-core-mark">
          <span className="core-bracket core-bracket-one" aria-hidden="true" />
          <span className="core-bracket core-bracket-two" aria-hidden="true" />
          <KodexLogo size="hero" />
        </div>
        <h2>Kodex</h2>
        <p>Your AI coding partner. Build, ship, scale.</p>
        <div className="kodex-ready-line"><strong>READY</strong><span>Awaiting your command</span><i /></div>
      </div>
      <div className="kodex-command-shortcuts">
        {kodexAgentShortcuts.map((shortcut) => (
          <button type="button" key={shortcut.command} onClick={() => onShortcut?.(shortcut.prompt)}>
            <Icon name={shortcut.icon} />
            <span><strong>{shortcut.command}</strong><small>{shortcut.label}</small></span>
          </button>
        ))}
      </div>
    </section>
  )
}

function PanelHeader({ title, meta }: { title: string; meta?: string }) {
  return (
    <div className="side-panel-header">
      <span>{title.toUpperCase()}</span>
      <div>{meta && <b>{meta}</b>} <button>•••</button></div>
    </div>
  )
}

function Empty({ label }: { label: string }) {
  return <div className="empty-side">{label}</div>
}

function CompactSwitch({
  icon,
  label,
  checked,
  onChange,
}: {
  icon: IconName
  label: string
  checked: boolean
  onChange: (checked: boolean) => void
}) {
  return (
    <button
      type="button"
      className="add-menu-switch"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
    >
      <Icon name={icon} />
      <strong>{label}</strong>
      <span className={`compact-switch ${checked ? 'on' : ''}`}><i /></span>
    </button>
  )
}

function Icon({ name }: { name: IconName }) {
  const paths: Record<IconName, ReactNode> = {
    explorer: <><path d="M3.5 5.5h6l1.7 2H20.5v10.5H3.5z" /><path d="M3.5 8h17" /></>,
    search: <><circle cx="10.5" cy="10.5" r="6" /><path d="m15 15 5 5" /></>,
    outline: <><path d="M5 5h5M5 12h8M5 19h5" /><path d="m15 5 2-2 2 2-2 2zM15 17h4v4h-4z" /></>,
    git: <><circle cx="6" cy="5" r="2" /><circle cx="18" cy="7" r="2" /><circle cx="8" cy="19" r="2" /><path d="M6 7v5a7 7 0 0 0 7 7h3M8 5h5a5 5 0 0 1 5 5v5" /></>,
    run: <path d="m8 5 11 7-11 7z" />,
    history: <><path d="M4 7v5h5" /><path d="M5.5 17a8 8 0 1 0-.8-9.5L4 12" /><path d="M12 8v5l3 2" /></>,
    memory: <><rect x="5" y="5" width="14" height="14" rx="3" /><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M19 9h3M2 15h3M19 15h3M9 9h6v6H9z" /></>,
    tasks: <><path d="M9 6h11M9 12h11M9 18h11" /><path d="m3.5 6 1.5 1.5L7.5 5M3.5 12 5 13.5 7.5 11M3.5 18 5 19.5 7.5 17" /></>,
    plugins: <><path d="M8 3v5M16 3v5M6 8h12v4a6 6 0 0 1-6 6 6 6 0 0 1-6-6z" /><path d="M12 18v3" /></>,
    plus: <path d="M12 5v14M5 12h14" />,
    sparkles: <><path d="m12 3 1.3 4.2L17.5 9l-4.2 1.8L12 15l-1.3-4.2L6.5 9l4.2-1.8z" /><path d="m18.5 15 .7 2.3 2.3.7-2.3.7-.7 2.3-.7-2.3-2.3-.7 2.3-.7z" /></>,
    'arrow-up': <><path d="m6 11 6-6 6 6" /><path d="M12 5v14" /></>,
    stop: <rect x="7" y="7" width="10" height="10" rx="2" />,
    paperclip: <path d="m9 12.5 6.6-6.6a3 3 0 1 1 4.2 4.2l-8.5 8.5a5 5 0 0 1-7.1-7.1l8-8" />,
    folder: <><path d="M3.5 6h6l2 2h9v10h-17z" /><path d="M3.5 9h17" /></>,
    file: <><path d="M6 3h8l4 4v14H6z" /><path d="M14 3v5h4" /></>,
    terminal: <><path d="m5 7 4 4-4 4" /><path d="M11 16h8" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7h.01" /></>,
    context: <><path d="M8 4H5a1 1 0 0 0-1 1v3M16 4h3a1 1 0 0 1 1 1v3M8 20H5a1 1 0 0 1-1-1v-3M16 20h3a1 1 0 0 0 1-1v-3" /><path d="M8 9h8M8 13h6M8 17h4" /></>,
    target: <><circle cx="12" cy="12" r="8" /><circle cx="12" cy="12" r="3" /><path d="M12 2v3M22 12h-3" /></>,
    'open-files': <><path d="M5 5h9l3 3v11H5z" /><path d="M14 5v4h4M8 2h8l3 3v11" /></>,
    permission: <><path d="M12 3 5 6v5c0 4.6 2.8 8.4 7 10 4.2-1.6 7-5.4 7-10V6z" /><path d="m9 12 2 2 4-5" /></>,
    model: <><rect x="4" y="5" width="16" height="14" rx="3" /><path d="M8 9h8M8 13h5M9 2v3M15 2v3" /></>,
    chevron: <path d="m9 6 6 6-6 6" />,
    clean: <><path d="m8 4 8 8" /><path d="m14 3 7 7-9.5 9.5H5L2.5 17z" /><path d="M5 19.5h16" /></>,
    trash: <><path d="M4 7h16M9 7V4h6v3M7 7l1 14h8l1-14M10 11v6M14 11v6" /></>,
  }
  return <svg className="ui-icon" viewBox="0 0 24 24" aria-hidden="true">{paths[name]}</svg>
}

function KodexLogo({ size = 'medium' }: { size?: 'tiny' | 'small' | 'medium' | 'large' | 'hero' }) {
  const pixels = [0, 4, 5, 8, 10, 12, 15, 18, 20, 24]
  return (
    <span className={`codex-logo codex-logo-${size}`} aria-label="Kodex">
      <span className="pixel-k" aria-hidden="true">
        {Array.from({ length: 25 }, (_, index) => <i className={pixels.includes(index) ? 'on' : ''} key={index} />)}
      </span>
    </span>
  )
}

export default App
