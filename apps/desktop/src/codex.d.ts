type CodexWorkspace = {
  path: string
  name: string
}

type CodexDesktopSettings = {
  backgroundCore: boolean
  openaiBaseUrl?: string
  openaiModel?: string
  lmStudioBaseUrl?: string
  selectedProvider?: string
  selectedModel?: string
  theme?: 'black' | 'vscode' | 'high-contrast' | 'graphite' | 'midnight' | 'aurora' | 'ember' | 'ocean' | 'sakura' | 'forest' | 'solarized'
  defaultPermission?: 'read-only' | 'confirm-edits' | 'workspace' | 'full-access'
  pluginStates?: Record<string, boolean>
}

type CodexTerminal = {
  id: number
  name: string
  cwd: string
  pid: number
  status: string
  cols: number
  rows: number
  shell: string
  output: string
  sequence: number
  exitCode: number | null
  signal: number | null
  error: string
  command: string
  createdAt: string
}

type CodexPlugin = {
  id: string
  name: string
  description: string
  marketplace: string
  installed: boolean
  enabled: boolean
  version: string
  agentTab?: boolean
  uninstall?: string
  error?: string
  appearance: {
    accent: string
    brand?: 'openai' | 'claude' | 'generic'
    icon: string
    title: string
    subtitle: string
    emptyState: 'minimal' | 'grid' | 'gradient'
    controls: string[]
  }
}

interface Window {
  codex?: {
    coreBase: string
    coreToken: string
    currentWorkspace: () => Promise<CodexWorkspace | null>
    recentWorkspaces: () => Promise<CodexWorkspace[]>
    startUntitledWorkspace: () => Promise<CodexWorkspace>
    createCodingWorkspace: () => Promise<CodexWorkspace | null>
    openWorkspace: () => Promise<CodexWorkspace | null>
    openRecentWorkspace: (workspace: string) => Promise<CodexWorkspace>
    chooseWorkspaceFiles: () => Promise<Array<{
      name: string
      path: string
      kind: 'image' | 'text'
      mimeType: string
      content: string
      dataUrl: string
    }>>
    restartCore: () => Promise<{ restarted: boolean; owned: boolean }>
    coreDiagnostics: () => Promise<Record<string, unknown>>
    getSettings: () => Promise<CodexDesktopSettings>
    updateSettings: (settings: Partial<CodexDesktopSettings>) => Promise<CodexDesktopSettings>
    setProviderApiKey: (apiKey: string) => Promise<{ configured: boolean }>
    providerCredentialStatus: () => Promise<{ configured: boolean }>
    loginCodex: () => Promise<{ ok: boolean; output: string }>
    logoutCodex: () => Promise<{ ok: boolean; output: string }>
    loginClaude: () => Promise<{ ok: boolean; output: string }>
    logoutClaude: () => Promise<{ ok: boolean; output: string }>
    listPlugins: () => Promise<CodexPlugin[]>
    installPlugin: (pluginId: string) => Promise<CodexPlugin>
    setPluginEnabled: (pluginId: string, enabled: boolean) => Promise<CodexPlugin>
    uninstallPlugin: (pluginId: string) => Promise<{ id: string; deleted: boolean }>
    onMenuCommand: (listener: (command: string) => void) => () => void
    openExternal: (url: string) => Promise<void>
    checkForUpdates: () => Promise<{ configured: boolean }>
    downloadUpdate: () => Promise<unknown>
    installUpdate: () => Promise<void>
    onUpdateStatus: (listener: (payload: { event: string; payload: unknown }) => void) => () => void
    listTerminals: () => Promise<CodexTerminal[]>
    createTerminal: (options?: Partial<CodexTerminal> & { shell?: string; command?: string }) => Promise<CodexTerminal>
    renameTerminal: (id: number, name: string) => Promise<CodexTerminal>
    duplicateTerminal: (id: number) => Promise<CodexTerminal>
    writeTerminal: (id: number, data: string) => Promise<void>
    resizeTerminal: (id: number, cols: number, rows: number) => Promise<CodexTerminal | null>
    killTerminal: (id: number) => Promise<CodexTerminal>
    onTerminalData: (listener: (payload: { id: number; sequence: number; data: string }) => void) => () => void
    onTerminalExit: (listener: (payload: { id: number; exitCode: number; signal: number }) => void) => () => void
    onTerminalError: (listener: (payload: { id: number; code: string; message: string }) => void) => () => void
  }
}
