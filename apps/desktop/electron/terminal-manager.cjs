const fs = require('node:fs')
const path = require('node:path')

const DEFAULT_BUFFER_LIMIT = 256_000

function terminalError(code, message) {
  const error = new Error(message)
  error.code = code
  return error
}

function isExecutable(file) {
  try {
    fs.accessSync(file, fs.constants.X_OK)
    return true
  } catch {
    return false
  }
}

function resolveShell(requested, platform = process.platform, env = process.env) {
  const candidates = []
  if (requested) candidates.push(requested)
  if (env.SHELL) candidates.push(env.SHELL)
  if (platform === 'win32') {
    if (env.COMSPEC) candidates.push(env.COMSPEC)
    candidates.push('powershell.exe', 'cmd.exe')
  } else {
    candidates.push('/bin/zsh', '/bin/bash', '/bin/sh')
  }
  for (const candidate of candidates) {
    if (!candidate) continue
    if (!path.isAbsolute(candidate) || isExecutable(candidate)) return candidate
  }
  throw terminalError('shell_unavailable', 'No usable terminal shell was found.')
}

function shellArguments(shell, platform = process.platform) {
  if (platform === 'win32') return []
  const name = path.basename(shell)
  return name === 'zsh' || name === 'bash' || name === 'sh' ? ['-l'] : []
}

function commandArguments(shell, command, platform = process.platform) {
  if (platform === 'win32') {
    const name = path.basename(shell).toLowerCase()
    return name.includes('powershell') ? ['-NoLogo', '-Command', command] : ['/d', '/s', '/c', command]
  }
  return ['-lc', command]
}

function resolveTerminalCwd(workspace, requested) {
  if (!workspace) {
    throw terminalError('workspace_required', 'Open a workspace before starting a terminal.')
  }
  const root = path.resolve(workspace)
  const cwd = requested ? path.resolve(root, requested) : root
  if (cwd !== root && !cwd.startsWith(`${root}${path.sep}`)) {
    throw terminalError('cwd_outside_workspace', 'Terminal directory is outside the workspace.')
  }
  if (!fs.existsSync(cwd) || !fs.statSync(cwd).isDirectory()) {
    throw terminalError('cwd_unavailable', 'Terminal directory does not exist.')
  }
  return cwd
}

class TerminalManager {
  constructor({ pty, workspace, emit, bufferLimit = DEFAULT_BUFFER_LIMIT }) {
    this.pty = pty
    this.workspace = workspace
    this.emit = emit
    this.bufferLimit = bufferLimit
    this.counter = 0
    this.terminals = new Map()
  }

  setWorkspace(workspace) {
    this.workspace = workspace
  }

  snapshot(record) {
    return {
      id: record.id,
      name: record.name,
      cwd: record.cwd,
      shell: record.shell,
      pid: record.process?.pid ?? 0,
      status: record.status,
      cols: record.cols,
      rows: record.rows,
      output: record.output,
      sequence: record.sequence,
      exitCode: record.exitCode,
      signal: record.signal,
      error: record.error,
      command: record.command,
      createdAt: record.createdAt
    }
  }

  list() {
    return [...this.terminals.values()].map((record) => this.snapshot(record))
  }

  create(options = {}) {
    const shell = resolveShell(options.shell)
    const cwd = resolveTerminalCwd(this.workspace, options.cwd)
    const cols = Math.max(20, Number(options.cols) || 100)
    const rows = Math.max(5, Number(options.rows) || 30)
    const id = ++this.counter
    const record = {
      id,
      name: options.name || `Terminal ${id}`,
      cwd,
      shell,
      process: null,
      status: 'starting',
      cols,
      rows,
      output: '',
      sequence: 0,
      exitCode: null,
      signal: null,
      error: '',
      command: '',
      startedAt: Date.now(),
      createdAt: new Date().toISOString()
    }
    this.terminals.set(id, record)
    try {
      const command = typeof options.command === 'string' ? options.command.trim() : ''
      record.command = command
      record.process = this.pty.spawn(shell, command ? commandArguments(shell, command) : shellArguments(shell), {
        name: 'xterm-256color',
        cols,
        rows,
        cwd,
        env: { ...process.env, TERM: 'xterm-256color', COLORTERM: 'truecolor' }
      })
      record.status = 'running'
      if (command) {
        record.sequence += 1
        record.output = `\u001b[90m${record.cwd}\u001b[0m\r\n\u001b[36m$ ${command}\u001b[0m\r\n`
      }
      record.process.onData((data) => {
        record.sequence += 1
        record.output = `${record.output}${data}`.slice(-this.bufferLimit)
        this.emit('terminal:data', { id, sequence: record.sequence, data })
      })
      record.process.onExit(({ exitCode, signal }) => {
        record.status = 'exited'
        record.exitCode = exitCode
        record.signal = signal
        this.emit('terminal:exit', { id, exitCode, signal, durationMs: Date.now() - record.startedAt })
      })
    } catch (error) {
      record.status = 'error'
      record.error = error instanceof Error ? error.message : String(error)
      this.emit('terminal:error', { id, code: 'spawn_failed', message: record.error })
    }
    return this.snapshot(record)
  }

  rename(id, name) {
    const terminal = this.terminals.get(Number(id))
    if (!terminal) throw terminalError('terminal_not_found', 'Terminal not found.')
    const next = String(name || '').trim().slice(0, 80)
    if (!next) throw terminalError('terminal_name_required', 'Enter a terminal name.')
    terminal.name = next
    return this.snapshot(terminal)
  }

  duplicate(id) {
    const terminal = this.terminals.get(Number(id))
    if (!terminal) throw terminalError('terminal_not_found', 'Terminal not found.')
    return this.create({
      name: `${terminal.name} copy`,
      cwd: path.relative(this.workspace, terminal.cwd) || '.',
      shell: terminal.shell,
      command: terminal.command || undefined,
      cols: terminal.cols,
      rows: terminal.rows
    })
  }

  requireRunning(id) {
    const terminal = this.terminals.get(Number(id))
    if (!terminal) throw terminalError('terminal_not_found', 'Terminal not found.')
    if (terminal.status !== 'running' || !terminal.process) {
      throw terminalError('terminal_not_running', terminal.error || 'Terminal is not running.')
    }
    return terminal
  }

  write(id, data) {
    const terminal = this.requireRunning(id)
    try {
      terminal.process.write(String(data))
    } catch (error) {
      terminal.error = error instanceof Error ? error.message : String(error)
      throw terminalError('write_failed', terminal.error)
    }
  }

  resize(id, cols, rows) {
    const terminal = this.requireRunning(id)
    terminal.cols = Math.max(20, Number(cols) || terminal.cols)
    terminal.rows = Math.max(5, Number(rows) || terminal.rows)
    try {
      terminal.process.resize(terminal.cols, terminal.rows)
    } catch (error) {
      terminal.error = error instanceof Error ? error.message : String(error)
      throw terminalError('resize_failed', terminal.error)
    }
    return this.snapshot(terminal)
  }

  kill(id) {
    const terminal = this.terminals.get(Number(id))
    if (!terminal) throw terminalError('terminal_not_found', 'Terminal not found.')
    if (terminal.status === 'running' && terminal.process) terminal.process.kill()
    terminal.status = 'exited'
    return this.snapshot(terminal)
  }

  stopAll() {
    for (const terminal of this.terminals.values()) {
      if (terminal.status === 'running' && terminal.process) terminal.process.kill()
    }
    this.terminals.clear()
  }
}

module.exports = {
  TerminalManager,
  commandArguments,
  resolveShell,
  resolveTerminalCwd,
  shellArguments
}
