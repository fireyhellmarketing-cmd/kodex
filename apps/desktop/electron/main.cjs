const { app, BrowserWindow, Menu, dialog, ipcMain, nativeImage, safeStorage, shell } = require('electron')
const path = require('node:path')
const fs = require('node:fs')
const crypto = require('node:crypto')
const { spawn } = require('node:child_process')
const pty = require('node-pty')
const { autoUpdater } = require('electron-updater')
const { TerminalManager } = require('./terminal-manager.cjs')

const imageMimeTypes = {
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.gif': 'image/gif'
}

function readAttachment(filePath) {
  const resolved = path.resolve(filePath)
  const stat = fs.statSync(resolved)
  const extension = path.extname(resolved).toLowerCase()
  const mimeType = imageMimeTypes[extension] || 'text/plain'
  const isImage = mimeType.startsWith('image/')
  const limit = isImage ? 10_000_000 : 1_000_000
  if (stat.size > limit) {
    throw new Error(`${path.basename(resolved)} exceeds the ${isImage ? '10 MB image' : '1 MB file'} attachment limit`)
  }
  const bytes = fs.readFileSync(resolved)
  return {
    name: path.basename(resolved),
    path: currentWorkspace && resolved.startsWith(`${currentWorkspace}${path.sep}`)
      ? path.relative(currentWorkspace, resolved)
      : resolved,
    kind: isImage ? 'image' : 'text',
    mimeType,
    content: isImage ? '' : bytes.toString('utf8'),
    dataUrl: isImage ? `data:${mimeType};base64,${bytes.toString('base64')}` : ''
  }
}

const devUrl = process.env.VITE_DEV_SERVER_URL || 'http://127.0.0.1:5173'
const corePort = process.env.CODEX_CORE_PORT || '7799'
const rendererPort = process.env.CODEX_RENDERER_PORT || '5173'
const repoRoot = path.resolve(__dirname, '..', '..', '..')
const appIcon = path.join(__dirname, 'kodex-icon.png')
let mainWindow = null
let coreProc = null
let rendererProc = null
let isQuitting = false
let currentWorkspace = null
let coreToken = ''
let desktopSettings = {
  backgroundCore: false,
  defaultPermission: 'full-access',
  theme: 'vscode',
  selectedProvider: 'auto',
  selectedModel: 'auto',
  lmStudioBaseUrl: 'http://127.0.0.1:1234/v1',
  pluginStates: {}
}

function cleanChildEnvironment(overrides = {}) {
  const environment = { ...process.env, ...overrides }
  delete environment.NODE_OPTIONS
  delete environment.VSCODE_INSPECTOR_OPTIONS
  delete environment.ELECTRON_RUN_AS_NODE
  return environment
}
const terminalManager = new TerminalManager({
  pty,
  workspace: null,
  emit: (channel, payload) => {
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send(channel, payload)
  }
})

app.setName('Kodex')
process.title = 'Kodex'
if (process.env.CODEX_USER_DATA_DIR) {
  app.setPath('userData', path.resolve(process.env.CODEX_USER_DATA_DIR))
}

function workspaceStatePath() {
  return path.join(app.getPath('userData'), 'workspaces.json')
}

function welcomeWorkspacePath() {
  return path.join(app.getPath('userData'), 'Welcome Workspace')
}

function activeCoreWorkspace() {
  const workspace = currentWorkspace || welcomeWorkspacePath()
  fs.mkdirSync(workspace, { recursive: true })
  return workspace
}

function settingsPath() {
  return path.join(app.getPath('userData'), 'settings.json')
}

function tokenPath() {
  return path.join(app.getPath('userData'), 'core-token')
}

function credentialPath() {
  return path.join(app.getPath('userData'), 'provider-credential')
}

function codexExecutable() {
  if (process.env.CODEX_CLI_PATH) return process.env.CODEX_CLI_PATH
  const executableName = process.platform === 'win32' ? 'codex.exe' : 'codex'
  const extensionRoots = [
    path.join(app.getPath('home'), '.vscode', 'extensions'),
    path.join(app.getPath('home'), '.vscode-insiders', 'extensions')
  ]
  for (const root of extensionRoots) {
    try {
      const candidates = fs.readdirSync(root)
        .filter((name) => name.startsWith('openai.chatgpt-'))
        .sort()
        .reverse()
      for (const candidate of candidates) {
        const binRoot = path.join(root, candidate, 'bin')
        const platformDirectories = fs.existsSync(binRoot) ? fs.readdirSync(binRoot) : []
        for (const platformDirectory of platformDirectories) {
          const executable = path.join(binRoot, platformDirectory, executableName)
          if (fs.existsSync(executable)) return executable
        }
      }
    } catch {
      // Fall through to PATH resolution.
    }
  }
  return 'codex'
}

function claudeExecutable() {
  if (process.env.CLAUDE_CLI_PATH) return process.env.CLAUDE_CLI_PATH
  const executableName = process.platform === 'win32' ? 'claude.exe' : 'claude'
  const extensionRoots = [
    path.join(app.getPath('home'), '.vscode', 'extensions'),
    path.join(app.getPath('home'), '.vscode-insiders', 'extensions')
  ]
  for (const root of extensionRoots) {
    try {
      const candidates = fs.readdirSync(root)
        .filter((name) => name.startsWith('anthropic.claude-code-'))
        .sort()
        .reverse()
      for (const candidate of candidates) {
        const executable = path.join(root, candidate, 'resources', 'native-binary', executableName)
        if (fs.existsSync(executable)) return executable
      }
    } catch {
      // Fall through to PATH resolution.
    }
  }
  return 'claude'
}

function runCodexAccountCommand(args) {
  return new Promise((resolve, reject) => {
    const child = spawn(codexExecutable(), args, {
      cwd: currentWorkspace || repoRoot,
      env: process.env,
      stdio: ['ignore', 'pipe', 'pipe']
    })
    let output = ''
    child.stdout.on('data', (chunk) => { output += chunk.toString() })
    child.stderr.on('data', (chunk) => { output += chunk.toString() })
    child.once('error', reject)
    child.once('exit', (code) => {
      if (code === 0) resolve({ ok: true, output: output.trim() })
      else reject(new Error(output.trim() || `Codex exited with code ${code}`))
    })
  })
}

function runClaudeAccountCommand(args) {
  return new Promise((resolve, reject) => {
    const child = spawn(claudeExecutable(), args, {
      cwd: currentWorkspace || repoRoot,
      env: process.env,
      stdio: ['ignore', 'pipe', 'pipe']
    })
    let output = ''
    child.stdout.on('data', (chunk) => { output += chunk.toString() })
    child.stderr.on('data', (chunk) => { output += chunk.toString() })
    child.once('error', reject)
    child.once('exit', (code) => {
      if (code === 0) resolve({ ok: true, output: output.trim() })
      else reject(new Error(output.trim() || `Claude Code exited with code ${code}`))
    })
  })
}

async function listKodexPlugins() {
  const codexPlugin = {
    id: 'openai-codex-kodex',
    name: 'ChatGPT Codex',
    description: 'Use your official ChatGPT Codex session as the coding model inside Kodex.',
    marketplace: 'personal',
    installed: false,
    enabled: false,
    version: '0.1.0',
    appearance: {
      accent: '#ffffff',
      brand: 'openai',
      icon: 'OpenAI',
      title: 'ChatGPT Codex',
      subtitle: 'Use your ChatGPT Codex session with Kodex workspace tools.',
      emptyState: 'minimal',
      controls: ['attachments', 'context', 'permissions', 'model']
    }
  }
  const claudePlugin = {
    id: 'anthropic-claude-code',
    name: 'Claude Code',
    description: 'Use your Anthropic Claude Code account with Kodex workspace tools and approvals.',
    marketplace: 'built-in',
    installed: false,
    enabled: false,
    version: '0.1.0',
    appearance: {
      accent: '#D97757',
      brand: 'claude',
      icon: 'Claude',
      title: 'Claude Code',
      subtitle: 'Claude intelligence with Kodex editing, approvals, and diagnostics.',
      emptyState: 'minimal',
      controls: ['attachments', 'context', 'permissions', 'model']
    }
  }
  try {
    const result = await runCodexAccountCommand(['plugin', 'list'])
    const line = result.output
      .split(/\r?\n/)
      .find((item) => item.includes('openai-codex-kodex@personal'))
    if (line) {
      codexPlugin.installed = line.includes('installed')
      codexPlugin.enabled = line.includes('enabled')
      const version = line.match(/\b\d+\.\d+\.\d+(?:[-+][^\s]+)?\b/)
      if (version) codexPlugin.version = version[0]
    }
  } catch (error) {
    codexPlugin.error = error instanceof Error ? error.message : String(error)
  }
  try {
    const result = await runClaudeAccountCommand(['--version'])
    claudePlugin.installed = true
    claudePlugin.enabled = true
    const version = result.output.match(/\b\d+\.\d+\.\d+(?:[-+][^\s]+)?\b/)
    if (version) claudePlugin.version = version[0]
  } catch {
    // Keep the plugin available for installation without showing a noisy CLI error.
  }
  const pluginStates = desktopSettings.pluginStates || {}
  for (const plugin of [codexPlugin, claudePlugin]) {
    if (typeof pluginStates[plugin.id] === 'boolean') plugin.enabled = pluginStates[plugin.id] && plugin.installed
    plugin.agentTab = true
    plugin.uninstall = plugin.id === 'openai-codex-kodex' ? 'provider' : 'integration'
  }
  return [codexPlugin, claudePlugin]
}

function buildApplicationMenu() {
  const send = (command) => mainWindow && !mainWindow.isDestroyed() && mainWindow.webContents.send('menu:command', command)
  const template = [
    { label: 'File', submenu: [
      { label: 'New Coding Workspace…', accelerator: 'CmdOrCtrl+Shift+N', click: () => send('new-workspace') },
      { label: 'Open Folder…', accelerator: 'CmdOrCtrl+O', click: () => send('open-folder') },
      { type: 'separator' },
      { label: 'Save', accelerator: 'CmdOrCtrl+S', click: () => send('save') },
      { label: 'Save All', accelerator: 'CmdOrCtrl+Alt+S', click: () => send('save-all') },
      { role: process.platform === 'darwin' ? 'close' : 'quit' }
    ] },
    { label: 'Edit', submenu: [{ role: 'undo' }, { role: 'redo' }, { type: 'separator' }, { role: 'cut' }, { role: 'copy' }, { role: 'paste' }, { role: 'selectAll' }] },
    { label: 'Selection', submenu: [{ role: 'selectAll' }] },
    { label: 'View', submenu: [
      { label: 'Command Palette…', accelerator: 'CmdOrCtrl+Shift+P', click: () => send('command-palette') },
      { label: 'Explorer', accelerator: 'CmdOrCtrl+Shift+E', click: () => send('show-explorer') },
      { label: 'Terminal', accelerator: 'Ctrl+`', click: () => send('toggle-terminal') },
      { role: 'togglefullscreen' }, { role: 'reload' }, { role: 'toggleDevTools' }
    ] },
    { label: 'Go', submenu: [{ label: 'Back', accelerator: 'Alt+Left', click: () => send('go-back') }, { label: 'Forward', accelerator: 'Alt+Right', click: () => send('go-forward') }] },
    { label: 'Run', submenu: [{ label: 'Run', accelerator: 'F5', click: () => send('run') }, { label: 'Start Debugging', accelerator: 'CmdOrCtrl+F5', click: () => send('debug') }, { label: 'Stop', accelerator: 'Shift+F5', click: () => send('stop') }] },
    { label: 'Terminal', submenu: [{ label: 'New Terminal', accelerator: 'Ctrl+Shift+`', click: () => send('new-terminal') }, { label: 'Kill Terminal', click: () => send('kill-terminal') }] },
    { label: 'Window', submenu: process.platform === 'darwin' ? [{ role: 'minimize' }, { role: 'zoom' }, { type: 'separator' }, { role: 'front' }] : [{ role: 'minimize' }, { role: 'close' }] },
    { label: 'Help', submenu: [{ label: 'Kodex Documentation', click: () => shell.openExternal('https://github.com/fireyhellmarketing-cmd/kodex') }, { label: 'Diagnostics', click: () => send('diagnostics') }] }
  ]
  if (process.platform === 'darwin') {
    template.unshift({
      label: 'Kodex',
      submenu: [
        { role: 'about', label: 'About Kodex' },
        { type: 'separator' },
        { role: 'services' },
        { type: 'separator' },
        { role: 'hide', label: 'Hide Kodex' },
        { role: 'hideOthers' },
        { role: 'unhide' },
        { type: 'separator' },
        { role: 'quit', label: 'Quit Kodex' }
      ]
    })
  }
  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
}

function readProviderApiKey() {
  try {
    const encrypted = fs.readFileSync(credentialPath())
    if (!safeStorage.isEncryptionAvailable()) return ''
    return safeStorage.decryptString(encrypted)
  } catch {
    return ''
  }
}

function writeProviderApiKey(apiKey) {
  fs.mkdirSync(path.dirname(credentialPath()), { recursive: true })
  if (!apiKey) {
    fs.rmSync(credentialPath(), { force: true })
    return
  }
  if (!safeStorage.isEncryptionAvailable()) {
    throw new Error('Secure credential storage is unavailable; the API key was not saved')
  }
  const payload = safeStorage.encryptString(apiKey)
  fs.writeFileSync(credentialPath(), payload, { mode: 0o600 })
}

function lifecyclePath() {
  return path.join(app.getPath('userData'), 'core-lifecycle.json')
}

function writeCoreLifecycle() {
  fs.mkdirSync(path.dirname(lifecyclePath()), { recursive: true })
  fs.writeFileSync(lifecyclePath(), JSON.stringify({
    parentPid: process.pid,
    backgroundCore: desktopSettings.backgroundCore,
    updatedAt: new Date().toISOString()
  }))
}

function readDesktopSettings() {
  try {
    return { ...desktopSettings, ...JSON.parse(fs.readFileSync(settingsPath(), 'utf8')) }
  } catch {
    return { ...desktopSettings }
  }
}

function writeDesktopSettings(settings) {
  desktopSettings = { ...desktopSettings, ...settings }
  fs.mkdirSync(path.dirname(settingsPath()), { recursive: true })
  fs.writeFileSync(settingsPath(), JSON.stringify(desktopSettings, null, 2))
  writeCoreLifecycle()
  return desktopSettings
}

function readOrCreateCoreToken() {
  try {
    const existing = fs.readFileSync(tokenPath(), 'utf8').trim()
    if (existing) return existing
  } catch {
    // The token is created below on first launch.
  }
  const token = crypto.randomBytes(32).toString('hex')
  fs.mkdirSync(path.dirname(tokenPath()), { recursive: true })
  fs.writeFileSync(tokenPath(), token, { mode: 0o600 })
  return token
}

function readRecentWorkspaces() {
  try {
    const payload = JSON.parse(fs.readFileSync(workspaceStatePath(), 'utf8'))
    return Array.isArray(payload)
      ? payload.filter((workspace) => typeof workspace === 'string' && fs.existsSync(workspace))
      : []
  } catch {
    return []
  }
}

function writeRecentWorkspaces(workspaces) {
  fs.mkdirSync(path.dirname(workspaceStatePath()), { recursive: true })
  fs.writeFileSync(workspaceStatePath(), JSON.stringify(workspaces.slice(0, 10), null, 2))
}

function recordRecentWorkspace(workspace) {
  const resolved = path.resolve(workspace)
  const recents = [resolved, ...readRecentWorkspaces().filter((item) => item !== resolved)]
  writeRecentWorkspaces(recents)
  currentWorkspace = resolved
  return recents
}

function workspaceSummary(workspace) {
  return {
    path: workspace,
    name: path.basename(workspace) || workspace
  }
}

function startCore(workspace = activeCoreWorkspace()) {
  const coreRoot = path.join(__dirname, '..', '..', 'core', 'src')
  const bundledCore = path.join(
    process.resourcesPath,
    'core',
    process.platform === 'win32' ? 'kodex-core.exe' : 'kodex-core'
  )
  const executable = app.isPackaged ? bundledCore : 'python3'
  const args = app.isPackaged
    ? []
    : ['-m', 'uvicorn', 'codex_core.main:app', '--app-dir', coreRoot, '--host', '127.0.0.1', '--port', corePort]
  return spawn(executable, args, {
    cwd: repoRoot,
    env: cleanChildEnvironment({
      CODEX_WORKSPACE: workspace,
      CODEX_CORE_PORT: corePort,
      CODEX_CORE_TOKEN: coreToken,
      CODEX_SESSION_ID: crypto.randomBytes(8).toString('hex'),
      CODEX_PARENT_PID: String(process.pid),
      CODEX_DESKTOP_PID: String(process.pid),
      CODEX_DISABLE_DOCS: app.isPackaged ? '1' : '0',
      CODEX_RENDERER_ORIGINS: `http://127.0.0.1:${rendererPort},http://localhost:${rendererPort}`,
      CODEX_LIFECYCLE_FILE: lifecyclePath(),
      CODEX_CLI_PATH: codexExecutable(),
      CODEX_OPENAI_API_KEY: readProviderApiKey(),
      CODEX_OPENAI_BASE_URL: desktopSettings.openaiBaseUrl || 'https://api.openai.com/v1',
      CODEX_OPENAI_MODEL: desktopSettings.openaiModel || 'gpt-4.1-mini',
      CODEX_LM_STUDIO_BASE_URL: desktopSettings.lmStudioBaseUrl || 'http://127.0.0.1:1234/v1'
    }),
    stdio: 'inherit',
    detached: true
  })
}

function startRenderer() {
  return spawn('npm', ['run', 'dev', '-w', 'apps/desktop', '--', '--host', '127.0.0.1', '--port', rendererPort], {
    cwd: repoRoot,
    env: cleanChildEnvironment(),
    stdio: 'inherit',
    detached: true
  })
}

function configureUpdates() {
  if (!app.isPackaged || !process.env.KODEX_UPDATE_URL) return
  autoUpdater.autoDownload = false
  autoUpdater.setFeedURL({ provider: 'generic', url: process.env.KODEX_UPDATE_URL })
  for (const event of ['checking-for-update', 'update-available', 'update-not-available', 'download-progress', 'update-downloaded', 'error']) {
    autoUpdater.on(event, (payload) => {
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('update:status', { event, payload })
      }
    })
  }
  void autoUpdater.checkForUpdates().catch((error) => console.error('Update check failed', error))
}

async function isAvailable(url, authenticated = false) {
  try {
    const response = await fetch(url, {
      headers: authenticated ? { Authorization: `Bearer ${coreToken}` } : undefined
    })
    return response.ok
  } catch {
    return false
  }
}

async function waitFor(url, timeoutMs = 30000) {
  const started = Date.now()
  while (Date.now() - started < timeoutMs) {
    if (await isAvailable(url)) return true
    await new Promise((resolve) => setTimeout(resolve, 500))
  }
  return false
}

async function waitForUnavailable(url, timeoutMs = 10000) {
  const started = Date.now()
  while (Date.now() - started < timeoutMs) {
    if (!(await isAvailable(url))) return true
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  return false
}

async function ensureServices() {
  const coreUrl = `http://127.0.0.1:${corePort}/v1/health`
  if (!(await isAvailable(coreUrl))) {
    coreProc = startCore()
    if (!(await waitFor(coreUrl))) throw new Error('Kodex Core did not become ready')
  } else if (!(await isAvailable(`http://127.0.0.1:${corePort}/v1/auth/session`, true))) {
    throw new Error(`Port ${corePort} is occupied by a Kodex Core with a different local session`)
  }

  if (!app.isPackaged && !(await isAvailable(devUrl))) {
    rendererProc = startRenderer()
  }
}

function stopOwnedProcess(proc) {
  if (!proc || proc.killed) return
  try {
    process.kill(-proc.pid, 'SIGTERM')
  } catch {
    proc.kill('SIGTERM')
  }
}

function createTerminal(options = {}) {
  terminalManager.setWorkspace(currentWorkspace)
  return terminalManager.create(options)
}

function killTerminal(id) {
  return terminalManager.kill(id)
}

function assertTrustedSender(event) {
  if (!mainWindow || event.sender !== mainWindow.webContents) {
    throw new Error('Blocked IPC request from an untrusted renderer')
  }
}

function stopAllTerminals() {
  terminalManager.stopAll()
}

async function switchWorkspace(workspace, options = {}) {
  const resolved = path.resolve(workspace)
  if (!fs.existsSync(resolved) || !fs.statSync(resolved).isDirectory()) {
    throw new Error('Workspace folder no longer exists')
  }
  if (!coreProc) {
    throw new Error('The running Core is externally managed; restart Kodex before switching workspaces')
  }
  const coreUrl = `http://127.0.0.1:${corePort}/v1/health`
  stopAllTerminals()
  stopOwnedProcess(coreProc)
  coreProc = null
  if (!(await waitForUnavailable(coreUrl))) {
    throw new Error('The previous Kodex Core did not stop')
  }
  currentWorkspace = resolved
  coreProc = startCore(resolved)
  if (!(await waitFor(coreUrl))) {
    throw new Error('Kodex Core did not start for the selected workspace')
  }
  if (options.recordRecent !== false) recordRecentWorkspace(resolved)
  await mainWindow.reload()
  return workspaceSummary(resolved)
}

async function startUntitledWorkspace() {
  const parent = path.join(app.getPath('userData'), 'Untitled Workspaces')
  fs.mkdirSync(parent, { recursive: true })
  const workspace = fs.mkdtempSync(path.join(parent, 'Untitled-'))
  fs.writeFileSync(path.join(workspace, 'Untitled.txt'), '', 'utf8')
  return switchWorkspace(workspace, { recordRecent: false })
}

async function restartCore() {
  if (!coreProc) {
    throw new Error('The running Core is externally managed and cannot be restarted by this window')
  }
  const coreUrl = `http://127.0.0.1:${corePort}/v1/health`
  stopOwnedProcess(coreProc)
  coreProc = null
  if (!(await waitForUnavailable(coreUrl))) throw new Error('Kodex Core did not stop')
  coreProc = startCore(activeCoreWorkspace())
  if (!(await waitFor(coreUrl))) throw new Error('Kodex Core did not restart')
  return { restarted: true, owned: true }
}

function coreDiagnostics() {
  return {
    owned: Boolean(coreProc),
    pid: coreProc?.pid ?? null,
    rendererPid: rendererProc?.pid ?? null,
    workspace: currentWorkspace,
    corePort: Number(corePort),
    rendererPort: Number(rendererPort),
    backgroundCore: desktopSettings.backgroundCore
  }
}

function registerWorkspaceIpc() {
  ipcMain.on('core:connection-sync', (event) => {
    assertTrustedSender(event)
    event.returnValue = { baseUrl: `http://127.0.0.1:${corePort}`, token: coreToken }
  })
  ipcMain.handle('workspace:current', () => currentWorkspace ? workspaceSummary(currentWorkspace) : null)
  ipcMain.handle('workspace:recent', () => readRecentWorkspaces().map(workspaceSummary))
  ipcMain.handle('workspace:new-file', startUntitledWorkspace)
  ipcMain.handle('workspace:create', async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
      title: 'Create or choose an empty coding workspace',
      defaultPath: currentWorkspace || app.getPath('documents'),
      properties: ['openDirectory', 'createDirectory']
    })
    if (result.canceled || !result.filePaths[0]) return null
    const workspace = path.resolve(result.filePaths[0])
    const visibleEntries = fs.readdirSync(workspace).filter((entry) => entry !== '.DS_Store')
    if (visibleEntries.length) {
      throw new Error('New Coding Workspace requires an empty folder. Use Open Folder for an existing project.')
    }
    return switchWorkspace(workspace)
  })
  ipcMain.handle('workspace:open-dialog', async () => {
    const recent = readRecentWorkspaces()
    const result = await dialog.showOpenDialog(mainWindow, {
      title: 'Open Workspace',
      defaultPath: currentWorkspace || recent[0] || app.getPath('home'),
      properties: ['openDirectory', 'createDirectory']
    })
    if (result.canceled || !result.filePaths[0]) return null
    return switchWorkspace(result.filePaths[0])
  })
  ipcMain.handle('workspace:open-recent', async (_event, workspace) => {
    if (typeof workspace !== 'string' || !readRecentWorkspaces().includes(workspace)) {
      throw new Error('Workspace is not in the recent list')
    }
    return switchWorkspace(workspace)
  })
  ipcMain.handle('workspace:choose-files', async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
      title: 'Add Photos and Files',
      defaultPath: currentWorkspace || app.getPath('home'),
      properties: ['openFile', 'multiSelections']
    })
    if (result.canceled) return []
    const imageCount = result.filePaths.filter((filePath) =>
      Boolean(imageMimeTypes[path.extname(filePath).toLowerCase()])
    ).length
    if (imageCount > 10) throw new Error('You can attach up to 10 images at once.')
    return result.filePaths.map(readAttachment)
  })
  ipcMain.handle('core:restart', restartCore)
  ipcMain.handle('core:diagnostics', coreDiagnostics)
  ipcMain.handle('settings:get', () => desktopSettings)
  ipcMain.handle('settings:update', (event, settings) => {
    assertTrustedSender(event)
    return writeDesktopSettings(settings)
  })
  ipcMain.handle('credentials:set-provider-key', (event, apiKey) => {
    assertTrustedSender(event)
    writeProviderApiKey(typeof apiKey === 'string' ? apiKey : '')
    return { configured: Boolean(apiKey) }
  })
  ipcMain.handle('credentials:provider-status', () => ({ configured: Boolean(readProviderApiKey()) }))
  ipcMain.handle('codex:login', () => runCodexAccountCommand(['login']))
  ipcMain.handle('codex:logout', () => runCodexAccountCommand(['logout']))
  ipcMain.handle('claude:login', () => runClaudeAccountCommand(['auth', 'login']))
  ipcMain.handle('claude:logout', () => runClaudeAccountCommand(['auth', 'logout']))
  ipcMain.handle('plugins:list', () => listKodexPlugins())
  ipcMain.handle('plugins:install', async (event, pluginId) => {
    assertTrustedSender(event)
    if (pluginId === 'openai-codex-kodex') {
      await runCodexAccountCommand(['plugin', 'add', 'openai-codex-kodex@personal'])
    } else if (pluginId === 'anthropic-claude-code') {
      try {
        await runClaudeAccountCommand(['--version'])
      } catch {
        await shell.openExternal('https://code.claude.com/docs/en/setup')
      }
    } else {
      throw new Error('Unknown plugin')
    }
    return (await listKodexPlugins()).find((plugin) => plugin.id === pluginId)
  })
  ipcMain.handle('plugins:set-enabled', async (event, pluginId, enabled) => {
    assertTrustedSender(event)
    const plugin = (await listKodexPlugins()).find((item) => item.id === pluginId)
    if (!plugin || !plugin.installed) throw new Error('Install the plugin before enabling it')
    const pluginStates = { ...(desktopSettings.pluginStates || {}), [pluginId]: Boolean(enabled) }
    writeDesktopSettings({ pluginStates })
    return (await listKodexPlugins()).find((item) => item.id === pluginId)
  })
  ipcMain.handle('plugins:uninstall', async (event, pluginId) => {
    assertTrustedSender(event)
    if (pluginId === 'openai-codex-kodex') {
      try { await runCodexAccountCommand(['plugin', 'remove', 'openai-codex-kodex@personal']) } catch {
        await runCodexAccountCommand(['plugin', 'disable', 'openai-codex-kodex@personal']).catch(() => undefined)
      }
    } else if (pluginId !== 'anthropic-claude-code') {
      throw new Error('Unknown plugin')
    }
    const pluginStates = { ...(desktopSettings.pluginStates || {}), [pluginId]: false }
    writeDesktopSettings({ pluginStates })
    return { id: pluginId, deleted: true }
  })
  ipcMain.handle('desktop:open-external', (event, url) => {
    assertTrustedSender(event)
    const target = new URL(String(url))
    if (!['http:', 'https:'].includes(target.protocol)) throw new Error('Unsupported external URL')
    if (target.protocol === 'http:' && !['127.0.0.1', 'localhost', '::1'].includes(target.hostname)) {
      throw new Error('Unencrypted external URLs are blocked')
    }
    return shell.openExternal(target.toString())
  })
  ipcMain.handle('update:check', () => {
    if (!app.isPackaged || !process.env.KODEX_UPDATE_URL) {
      return { configured: false }
    }
    return autoUpdater.checkForUpdates().then(() => ({ configured: true }))
  })
  ipcMain.handle('update:download', () => autoUpdater.downloadUpdate())
  ipcMain.handle('update:install', () => autoUpdater.quitAndInstall())
  ipcMain.handle('terminal:list', () => terminalManager.list())
  ipcMain.handle('terminal:create', (event, options) => {
    assertTrustedSender(event)
    return createTerminal(options)
  })
  ipcMain.handle('terminal:rename', (event, id, name) => {
    assertTrustedSender(event)
    return terminalManager.rename(id, name)
  })
  ipcMain.handle('terminal:duplicate', (event, id) => {
    assertTrustedSender(event)
    return terminalManager.duplicate(id)
  })
  ipcMain.handle('terminal:write', (event, id, data) => {
    assertTrustedSender(event)
    return terminalManager.write(id, data)
  })
  ipcMain.handle('terminal:resize', (event, id, cols, rows) => {
    assertTrustedSender(event)
    return terminalManager.resize(id, cols, rows)
  })
  ipcMain.handle('terminal:kill', (event, id) => {
    assertTrustedSender(event)
    return killTerminal(id)
  })
}

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1500,
    height: 980,
    minWidth: 1024,
    minHeight: 720,
    title: 'Kodex',
    icon: appIcon,
    titleBarStyle: 'hiddenInset',
    trafficLightPosition: { x: 14, y: 12 },
    show: true,
    backgroundColor: '#050814',
    webPreferences: {
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.join(__dirname, 'preload.cjs')
    }
  })
  mainWindow.on('closed', () => {
    mainWindow = null
  })
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    try {
      const target = new URL(url)
      if (target.protocol === 'https:') void shell.openExternal(target.toString())
    } catch {}
    return { action: 'deny' }
  })
  mainWindow.webContents.on('will-navigate', (event, url) => {
    const current = mainWindow?.webContents.getURL()
    if (!current || new URL(url).origin !== new URL(current).origin) event.preventDefault()
  })
  if (app.isPackaged) {
    await mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'))
  } else {
    const rendererReady = await waitFor(devUrl)
    if (rendererReady) {
      await mainWindow.loadURL(devUrl)
    } else {
      await mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'))
    }
  }
  mainWindow.maximize()
  mainWindow.focus()
  app.focus({ steal: true })
  buildApplicationMenu()
}

app.whenReady().then(async () => {
  if (process.platform === 'darwin') {
    app.dock.setIcon(nativeImage.createFromPath(appIcon))
  }
  coreToken = readOrCreateCoreToken()
  desktopSettings = readDesktopSettings()
  writeCoreLifecycle()
  currentWorkspace = process.env.CODEX_WORKSPACE
    ? path.resolve(process.env.CODEX_WORKSPACE)
    : null
  if (currentWorkspace) recordRecentWorkspace(currentWorkspace)
  else fs.mkdirSync(welcomeWorkspacePath(), { recursive: true })
  registerWorkspaceIpc()
  await ensureServices()
  await createWindow()
  configureUpdates()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
}).catch((error) => {
  console.error(error)
  app.quit()
})

app.on('window-all-closed', () => {
  if (desktopSettings.backgroundCore) {
    stopOwnedProcess(rendererProc)
    rendererProc = null
    return
  }
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', () => {
  if (isQuitting) return
  isQuitting = true
  stopOwnedProcess(rendererProc)
  stopAllTerminals()
  if (!desktopSettings.backgroundCore) stopOwnedProcess(coreProc)
})
