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

function projectCreationPath() {
  return path.join(app.getPath('userData'), 'project-creation.json')
}

function writeProjectCreation(payload) {
  fs.mkdirSync(path.dirname(projectCreationPath()), { recursive: true })
  fs.writeFileSync(projectCreationPath(), JSON.stringify(payload, null, 2))
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('project:progress', payload)
  return payload
}

function sanitizeProjectName(value) {
  const name = String(value || '').trim().replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '')
  if (!name || name === '.' || name === '..') throw new Error('Enter a valid project name')
  return name
}

function projectFiles(template, name, prompt) {
  const title = name.replace(/[-_]+/g, ' ')
  const description = String(prompt || `A ${template} application`).trim()
  const sharedReadme = `# ${title}\n\n${description}\n\nCreated with Kodex Visual App Studio.\n`
  const templates = {
    'react-web': {
      'package.json': JSON.stringify({ name, private: true, version: '0.1.0', type: 'module', scripts: { dev: 'vite', build: 'vite build' }, dependencies: { '@vitejs/plugin-react': '^5.0.0', vite: '^7.0.0', typescript: '^5.9.0', react: '^19.0.0', 'react-dom': '^19.0.0' }, devDependencies: {} }, null, 2),
      'index.html': '<!doctype html><html><body><div id="root"></div><script type="module" src="/src/main.tsx"></script></body></html>\n',
      'src/main.tsx': "import React from 'react'\nimport { createRoot } from 'react-dom/client'\nimport './styles.css'\nimport App from './App'\n\ncreateRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>)\n",
      'src/App.tsx': `export default function App() {\n  return <main className="app"><section className="card"><p className="eyebrow">Kodex project</p><h1>${title}</h1><p>${description}</p><button type="button">Get started</button></section></main>\n}\n`,
      'src/styles.css': ':root { font-family: Inter, system-ui, sans-serif; color: #f5f7fb; background: #0b1020; } * { box-sizing: border-box; } body { margin: 0; } .app { min-height: 100vh; display: grid; place-items: center; padding: 32px; } .card { width: min(680px, 100%); padding: 48px; border: 1px solid #28314d; border-radius: 24px; background: #121a30; } .eyebrow { color: #78a8ff; text-transform: uppercase; letter-spacing: .14em; } button { padding: 12px 18px; border: 0; border-radius: 10px; background: #4d83ff; color: white; }\n',
      'README.md': sharedReadme
    },
    electron: {
      'package.json': JSON.stringify({ name, private: true, version: '0.1.0', main: 'electron/main.cjs', scripts: { dev: 'concurrently "vite" "electron ."', build: 'vite build' }, dependencies: { electron: '^42.0.0', concurrently: '^9.0.0', '@vitejs/plugin-react': '^5.0.0', vite: '^7.0.0', typescript: '^5.9.0', react: '^19.0.0', 'react-dom': '^19.0.0' } }, null, 2),
      'electron/main.cjs': "const { app, BrowserWindow } = require('electron')\napp.whenReady().then(() => new BrowserWindow({ width: 1100, height: 760 }).loadURL('http://localhost:5173'))\n",
      'index.html': '<!doctype html><html><body><div id="root"></div><script type="module" src="/src/main.tsx"></script></body></html>\n',
      'src/main.tsx': "import { createRoot } from 'react-dom/client'\nimport App from './App'\nimport './styles.css'\ncreateRoot(document.getElementById('root')!).render(<App />)\n",
      'src/App.tsx': `export default function App() {\n  return <main className="window"><aside>Navigation</aside><section><h1>${title}</h1><p>${description}</p><button type="button">New window</button></section></main>\n}\n`,
      'src/styles.css': 'body { margin: 0; font-family: system-ui; background: #15171b; color: #eee; } .window { display: grid; grid-template-columns: 220px 1fr; min-height: 100vh; } aside, section { padding: 28px; } aside { background: #202329; }\n',
      'README.md': sharedReadme
    },
    flutter: {
      'pubspec.yaml': `name: ${name.replace(/-/g, '_')}\ndescription: ${description}\nenvironment:\n  sdk: ">=3.3.0 <4.0.0"\ndependencies:\n  flutter:\n    sdk: flutter\n`,
      'lib/main.dart': `import 'package:flutter/material.dart';\n\nvoid main() => runApp(const App());\nclass App extends StatelessWidget {\n  const App({super.key});\n  @override Widget build(BuildContext context) => MaterialApp(home: Scaffold(appBar: AppBar(title: const Text('${title}')), body: const SafeArea(child: Center(child: Text('${description.replace(/'/g, "\\'")}')))));\n}\n`,
      'README.md': sharedReadme
    },
    maui: {
      'MainPage.xaml': `<?xml version="1.0" encoding="utf-8" ?>\n<ContentPage xmlns="http://schemas.microsoft.com/dotnet/2021/maui" xmlns:x="http://schemas.microsoft.com/winfx/2009/xaml" x:Class="${name}.MainPage">\n  <VerticalStackLayout Padding="32" Spacing="16"><Label Text="${title}" FontSize="32" /><Label Text="${description}" /><Button Text="Get started" /></VerticalStackLayout>\n</ContentPage>\n`,
      'README.md': sharedReadme
    },
    compose: {
      'app/src/main/java/MainActivity.kt': `import android.os.Bundle\nimport androidx.activity.ComponentActivity\nimport androidx.activity.compose.setContent\nimport androidx.compose.material3.*\nimport androidx.compose.runtime.Composable\n\nclass MainActivity : ComponentActivity() { override fun onCreate(state: Bundle?) { super.onCreate(state); setContent { App() } } }\n@Composable fun App() { Scaffold { Column { Text("${title}"); Text("${description.replace(/"/g, '\\"')}"); Button(onClick = {}) { Text("Get started") } } } }\n`,
      'README.md': sharedReadme
    },
    swiftui: {
      'Sources/App/ContentView.swift': `import SwiftUI\n\nstruct ContentView: View {\n  var body: some View { NavigationStack { VStack(spacing: 16) { Text("${title}").font(.largeTitle); Text("${description.replace(/"/g, '\\"')}"); Button("Get started") {} }.padding() } }\n}\n`,
      'Package.swift': `// swift-tools-version: 5.9\nimport PackageDescription\nlet package = Package(name: "${name}", platforms: [.macOS(.v13)], products: [.executable(name: "${name}", targets: ["App"])], targets: [.executableTarget(name: "App")])\n`,
      'README.md': sharedReadme
    },
    api: {
      'main.py': `from fastapi import FastAPI\n\napp = FastAPI(title=${JSON.stringify(title)})\n\n@app.get("/")\ndef root():\n    return {"name": ${JSON.stringify(title)}, "description": ${JSON.stringify(description)}}\n`,
      'requirements.txt': 'fastapi\nuvicorn\n',
      'README.md': sharedReadme
    },
    custom: { 'README.md': sharedReadme }
  }
  return templates[template] || templates.custom
}

function setupCommands(template) {
  if (['react-web', 'electron'].includes(template)) return [['npm', ['install']], ['npm', ['run', 'build']]]
  if (template === 'flutter') return [['flutter', ['pub', 'get']], ['flutter', ['analyze']]]
  if (template === 'maui') return [['dotnet', ['restore']], ['dotnet', ['build']]]
  if (template === 'compose') return [[process.platform === 'win32' ? 'gradlew.bat' : './gradlew', ['assembleDebug']]]
  if (template === 'swiftui') return [['swift', ['build']]]
  if (template === 'api') return [['python3', ['-m', 'compileall', '.']]]
  return []
}

function runProjectSetup(command, args, cwd) {
  return new Promise((resolve) => {
    const child = spawn(command, args, { cwd, env: cleanChildEnvironment(), stdio: ['ignore', 'pipe', 'pipe'] })
    let output = ''
    child.stdout.on('data', (chunk) => { output += chunk.toString(); writeProjectCreation({ status: 'setting-up', command: [command, ...args], output: output.slice(-8000), project: cwd }) })
    child.stderr.on('data', (chunk) => { output += chunk.toString(); writeProjectCreation({ status: 'setting-up', command: [command, ...args], output: output.slice(-8000), project: cwd }) })
    child.once('error', (error) => resolve({ ok: false, command: [command, ...args], output: error.message }))
    child.once('exit', (code) => resolve({ ok: code === 0, command: [command, ...args], output: output.slice(-12000), code }))
  })
}

function buildApplicationMenu() {
  const send = (command) => mainWindow && !mainWindow.isDestroyed() && mainWindow.webContents.send('menu:command', command)
  const template = [
    { label: 'File', submenu: [
      { label: 'New Project', accelerator: 'CmdOrCtrl+Shift+N', click: () => send('new-project') },
      { label: 'Open Folder…', accelerator: 'CmdOrCtrl+O', click: () => send('open-folder') },
      { type: 'separator' },
      { label: 'Save', accelerator: 'CmdOrCtrl+S', click: () => send('save') },
      { label: 'Save All', accelerator: 'CmdOrCtrl+Alt+S', click: () => send('save-all') },
      { role: process.platform === 'darwin' ? 'close' : 'quit' }
    ] },
    { label: 'Edit', submenu: [{ role: 'undo' }, { role: 'redo' }, { type: 'separator' }, { role: 'cut' }, { role: 'copy' }, { role: 'paste' }, { role: 'selectAll' }] },
    { label: 'Selection', submenu: [{ label: 'Select All', accelerator: 'CmdOrCtrl+A', click: () => send('designer-select-all') }, { label: 'Delete', accelerator: 'Backspace', click: () => send('designer-delete') }] },
    { label: 'View', submenu: [
      { label: 'Command Palette…', accelerator: 'CmdOrCtrl+Shift+P', click: () => send('command-palette') },
      { label: 'Designer', accelerator: 'CmdOrCtrl+Shift+D', click: () => send('show-designer') },
      { label: 'Explorer', accelerator: 'CmdOrCtrl+Shift+E', click: () => send('show-explorer') },
      { label: 'Terminal', accelerator: 'Ctrl+`', click: () => send('toggle-terminal') },
      { role: 'togglefullscreen' }, { role: 'reload' }, { role: 'toggleDevTools' }
    ] },
    { label: 'Go', submenu: [{ label: 'Back', accelerator: 'Alt+Left', click: () => send('go-back') }, { label: 'Forward', accelerator: 'Alt+Right', click: () => send('go-forward') }] },
    { label: 'Run', submenu: [{ label: 'Run', accelerator: 'F5', click: () => send('run') }, { label: 'Start Debugging', accelerator: 'CmdOrCtrl+F5', click: () => send('debug') }, { label: 'Stop', accelerator: 'Shift+F5', click: () => send('stop') }] },
    { label: 'Terminal', submenu: [{ label: 'New Terminal', accelerator: 'Ctrl+Shift+`', click: () => send('new-terminal') }, { label: 'Kill Terminal', click: () => send('kill-terminal') }] },
    { label: 'Window', submenu: process.platform === 'darwin' ? [{ role: 'minimize' }, { role: 'zoom' }, { type: 'separator' }, { role: 'front' }] : [{ role: 'minimize' }, { role: 'close' }] },
    { label: 'Help', submenu: [{ label: 'Kodex Documentation', click: () => shell.openExternal('https://github.com/openai/codex') }, { label: 'Diagnostics', click: () => send('diagnostics') }] }
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
    return safeStorage.isEncryptionAvailable()
      ? safeStorage.decryptString(encrypted)
      : encrypted.toString('utf8')
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
  const payload = safeStorage.isEncryptionAvailable()
    ? safeStorage.encryptString(apiKey)
    : Buffer.from(apiKey, 'utf8')
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
    event.returnValue = { baseUrl: `http://127.0.0.1:${corePort}`, token: coreToken }
  })
  ipcMain.handle('workspace:current', () => currentWorkspace ? workspaceSummary(currentWorkspace) : null)
  ipcMain.handle('workspace:recent', () => readRecentWorkspaces().map(workspaceSummary))
  ipcMain.handle('workspace:new-file', startUntitledWorkspace)
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
  ipcMain.handle('project:choose-parent', async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
      title: 'Choose where to create the project',
      defaultPath: currentWorkspace || app.getPath('documents'),
      properties: ['openDirectory', 'createDirectory']
    })
    return result.canceled ? null : result.filePaths[0]
  })
  ipcMain.handle('project:creation-state', () => {
    try { return JSON.parse(fs.readFileSync(projectCreationPath(), 'utf8')) } catch { return null }
  })
  ipcMain.handle('project:create', async (_event, options) => {
    const parent = path.resolve(String(options?.parent || ''))
    if (!fs.existsSync(parent) || !fs.statSync(parent).isDirectory()) throw new Error('Choose a valid parent directory')
    const name = sanitizeProjectName(options?.name)
    const destination = path.join(parent, name)
    if (fs.existsSync(destination)) throw new Error(`A folder named "${name}" already exists`)
    const template = String(options?.template || 'custom')
    writeProjectCreation({ status: 'scaffolding', name, template, project: destination })
    fs.mkdirSync(destination)
    for (const [relative, content] of Object.entries(projectFiles(template, name, options?.prompt))) {
      const target = path.join(destination, relative)
      fs.mkdirSync(path.dirname(target), { recursive: true })
      fs.writeFileSync(target, content, 'utf8')
    }
    const blueprint = {
      platform: options?.platform || template,
      framework: options?.framework || template,
      screens: ['Home'],
      features: String(options?.prompt || '').trim() ? [String(options.prompt).trim()] : ['Starter experience'],
      dependencies: setupCommands(template).map(([command]) => command),
      validationCommands: setupCommands(template).map(([command, args]) => [command, ...args].join(' '))
    }
    fs.mkdirSync(path.join(destination, '.kodex-agent', 'designer'), { recursive: true })
    fs.writeFileSync(path.join(destination, '.kodex-agent', 'designer', 'blueprint.json'), JSON.stringify(blueprint, null, 2))
    const setup = []
    if (options?.install !== false) {
      for (const [command, args] of setupCommands(template)) {
        const result = await runProjectSetup(command, args, destination)
        setup.push(result)
        if (!result.ok) break
      }
    }
    writeProjectCreation({ status: setup.some((item) => !item.ok) ? 'needs-setup' : 'complete', name, template, project: destination, blueprint, setup })
    return switchWorkspace(destination)
  })
  ipcMain.handle('core:restart', restartCore)
  ipcMain.handle('core:diagnostics', coreDiagnostics)
  ipcMain.handle('settings:get', () => desktopSettings)
  ipcMain.handle('settings:update', (_event, settings) => writeDesktopSettings(settings))
  ipcMain.handle('credentials:set-provider-key', (_event, apiKey) => {
    writeProviderApiKey(typeof apiKey === 'string' ? apiKey : '')
    return { configured: Boolean(apiKey) }
  })
  ipcMain.handle('credentials:provider-status', () => ({ configured: Boolean(readProviderApiKey()) }))
  ipcMain.handle('codex:login', () => runCodexAccountCommand(['login']))
  ipcMain.handle('codex:logout', () => runCodexAccountCommand(['logout']))
  ipcMain.handle('claude:login', () => runClaudeAccountCommand(['auth', 'login']))
  ipcMain.handle('claude:logout', () => runClaudeAccountCommand(['auth', 'logout']))
  ipcMain.handle('plugins:list', () => listKodexPlugins())
  ipcMain.handle('plugins:install', async (_event, pluginId) => {
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
  ipcMain.handle('plugins:set-enabled', async (_event, pluginId, enabled) => {
    const plugin = (await listKodexPlugins()).find((item) => item.id === pluginId)
    if (!plugin || !plugin.installed) throw new Error('Install the plugin before enabling it')
    const pluginStates = { ...(desktopSettings.pluginStates || {}), [pluginId]: Boolean(enabled) }
    writeDesktopSettings({ pluginStates })
    return (await listKodexPlugins()).find((item) => item.id === pluginId)
  })
  ipcMain.handle('plugins:uninstall', async (_event, pluginId) => {
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
  ipcMain.handle('designer:devices', async () => {
    const checks = await Promise.all([
      runProjectSetup('flutter', ['devices', '--machine'], currentWorkspace || repoRoot),
      runProjectSetup('xcrun', ['simctl', 'list', 'devices', 'available', '-j'], currentWorkspace || repoRoot),
      runProjectSetup('adb', ['devices', '-l'], currentWorkspace || repoRoot)
    ])
    return checks.map((result, index) => ({ runtime: ['flutter', 'ios', 'android'][index], available: result.ok, output: result.output }))
  })
  ipcMain.handle('desktop:open-external', (_event, url) => {
    const target = new URL(String(url))
    if (!['http:', 'https:'].includes(target.protocol)) throw new Error('Unsupported external URL')
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
  ipcMain.handle('terminal:create', (_event, options) => createTerminal(options))
  ipcMain.handle('terminal:write', (_event, id, data) => terminalManager.write(id, data))
  ipcMain.handle('terminal:resize', (_event, id, cols, rows) => terminalManager.resize(id, cols, rows))
  ipcMain.handle('terminal:kill', (_event, id) => killTerminal(id))
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
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.join(__dirname, 'preload.cjs')
    }
  })
  mainWindow.on('closed', () => {
    mainWindow = null
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
