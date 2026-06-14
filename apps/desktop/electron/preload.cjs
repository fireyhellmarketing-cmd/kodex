const { contextBridge, ipcRenderer } = require('electron')
const connection = ipcRenderer.sendSync('core:connection-sync')

contextBridge.exposeInMainWorld('codex', {
  coreBase: connection.baseUrl,
  coreToken: connection.token,
  currentWorkspace: () => ipcRenderer.invoke('workspace:current'),
  recentWorkspaces: () => ipcRenderer.invoke('workspace:recent'),
  startUntitledWorkspace: () => ipcRenderer.invoke('workspace:new-file'),
  createCodingWorkspace: () => ipcRenderer.invoke('workspace:create'),
  openWorkspace: () => ipcRenderer.invoke('workspace:open-dialog'),
  openRecentWorkspace: (workspace) => ipcRenderer.invoke('workspace:open-recent', workspace),
  chooseWorkspaceFiles: () => ipcRenderer.invoke('workspace:choose-files'),
  restartCore: () => ipcRenderer.invoke('core:restart'),
  coreDiagnostics: () => ipcRenderer.invoke('core:diagnostics'),
  getSettings: () => ipcRenderer.invoke('settings:get'),
  updateSettings: (settings) => ipcRenderer.invoke('settings:update', settings),
  setProviderApiKey: (apiKey) => ipcRenderer.invoke('credentials:set-provider-key', apiKey),
  providerCredentialStatus: () => ipcRenderer.invoke('credentials:provider-status'),
  loginCodex: () => ipcRenderer.invoke('codex:login'),
  logoutCodex: () => ipcRenderer.invoke('codex:logout'),
  loginClaude: () => ipcRenderer.invoke('claude:login'),
  logoutClaude: () => ipcRenderer.invoke('claude:logout'),
  listPlugins: () => ipcRenderer.invoke('plugins:list'),
  installPlugin: (pluginId) => ipcRenderer.invoke('plugins:install', pluginId),
  setPluginEnabled: (pluginId, enabled) => ipcRenderer.invoke('plugins:set-enabled', pluginId, enabled),
  uninstallPlugin: (pluginId) => ipcRenderer.invoke('plugins:uninstall', pluginId),
  onMenuCommand: (listener) => {
    const handler = (_event, command) => listener(command)
    ipcRenderer.on('menu:command', handler)
    return () => ipcRenderer.removeListener('menu:command', handler)
  },
  openExternal: (url) => ipcRenderer.invoke('desktop:open-external', url),
  checkForUpdates: () => ipcRenderer.invoke('update:check'),
  downloadUpdate: () => ipcRenderer.invoke('update:download'),
  installUpdate: () => ipcRenderer.invoke('update:install'),
  onUpdateStatus: (listener) => {
    const handler = (_event, payload) => listener(payload)
    ipcRenderer.on('update:status', handler)
    return () => ipcRenderer.removeListener('update:status', handler)
  },
  listTerminals: () => ipcRenderer.invoke('terminal:list'),
  createTerminal: (options) => ipcRenderer.invoke('terminal:create', options),
  renameTerminal: (id, name) => ipcRenderer.invoke('terminal:rename', id, name),
  duplicateTerminal: (id) => ipcRenderer.invoke('terminal:duplicate', id),
  writeTerminal: (id, data) => ipcRenderer.invoke('terminal:write', id, data),
  resizeTerminal: (id, cols, rows) => ipcRenderer.invoke('terminal:resize', id, cols, rows),
  killTerminal: (id) => ipcRenderer.invoke('terminal:kill', id),
  onTerminalData: (listener) => {
    const handler = (_event, payload) => listener(payload)
    ipcRenderer.on('terminal:data', handler)
    return () => ipcRenderer.removeListener('terminal:data', handler)
  },
  onTerminalExit: (listener) => {
    const handler = (_event, payload) => listener(payload)
    ipcRenderer.on('terminal:exit', handler)
    return () => ipcRenderer.removeListener('terminal:exit', handler)
  },
  onTerminalError: (listener) => {
    const handler = (_event, payload) => listener(payload)
    ipcRenderer.on('terminal:error', handler)
    return () => ipcRenderer.removeListener('terminal:error', handler)
  }
})
