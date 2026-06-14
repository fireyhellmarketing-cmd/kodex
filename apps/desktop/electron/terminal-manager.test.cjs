const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const {
  TerminalManager,
  resolveTerminalCwd,
  shellArguments
} = require('./terminal-manager.cjs')

function fakePty() {
  const state = { data: null, exit: null, writes: [], resize: null, killed: false }
  return {
    state,
    spawn() {
      return {
        pid: 42,
        onData(listener) { state.data = listener },
        onExit(listener) { state.exit = listener },
        write(data) { state.writes.push(data) },
        resize(cols, rows) { state.resize = [cols, rows] },
        kill() { state.killed = true }
      }
    }
  }
}

test('buffers early output and assigns monotonic sequence numbers', () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'kodex-terminal-'))
  const pty = fakePty()
  const events = []
  const manager = new TerminalManager({
    pty,
    workspace,
    emit: (channel, payload) => events.push({ channel, payload })
  })
  const terminal = manager.create({ shell: process.execPath })
  pty.state.data('first')
  pty.state.data(' second')
  const snapshot = manager.list()[0]
  assert.equal(terminal.status, 'running')
  assert.equal(snapshot.output, 'first second')
  assert.equal(snapshot.sequence, 2)
  assert.deepEqual(events.map((event) => event.payload.sequence), [1, 2])
})

test('writes, resizes, and records process exit', () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'kodex-terminal-'))
  const pty = fakePty()
  const manager = new TerminalManager({ pty, workspace, emit: () => {} })
  const terminal = manager.create({ shell: process.execPath })
  manager.write(terminal.id, 'echo ok\n')
  manager.resize(terminal.id, 120, 40)
  pty.state.exit({ exitCode: 0, signal: 0 })
  assert.deepEqual(pty.state.writes, ['echo ok\n'])
  assert.deepEqual(pty.state.resize, [120, 40])
  assert.equal(manager.list()[0].status, 'exited')
  assert.equal(manager.list()[0].exitCode, 0)
})

test('rejects terminal cwd outside the workspace', () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'kodex-terminal-'))
  assert.throws(
    () => resolveTerminalCwd(workspace, '..'),
    (error) => error.code === 'cwd_outside_workspace'
  )
})

test('uses login arguments only for supported unix shells', () => {
  assert.deepEqual(shellArguments('/bin/zsh', 'darwin'), ['-l'])
  assert.deepEqual(shellArguments('/usr/local/bin/fish', 'darwin'), [])
  assert.deepEqual(shellArguments('powershell.exe', 'win32'), [])
})
