import { useEffect, useRef, useState } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'

type Props = {
  processesRunning: number
  theme: string
  collapsed: boolean
  maximized: boolean
  activePanel: BottomPanelId
  launchRequest: TerminalLaunchRequest | null
  processLogs: Array<{ id: number; name: string; status: string; logs: Array<{ stream: string; line: string; time: string }> }>
  onPanelChange: (panel: BottomPanelId) => void
  onToggleCollapsed: () => void
  onToggleMaximized: () => void
  onOpenSource: (path: string, line: number, column: number) => void
}

export type BottomPanelId = 'terminal' | 'output' | 'debug' | 'problems' | 'ports'
export type TerminalLaunchRequest = { key: number; name: string; command: string; cwd: string; mode: 'run' | 'debug' }

type PortRecord = {
  process: string
  pid: number
  port: number
  address: string
  url: string
  managed_process_id?: number
}

const terminalThemes: Record<string, { background: string; foreground: string; cursor: string; selectionBackground: string }> = {
  black: { background: '#000000', foreground: '#e4e6e8', cursor: '#ffffff', selectionBackground: '#303030' },
  vscode: { background: '#1e1e1e', foreground: '#d4d4d4', cursor: '#aeafad', selectionBackground: '#264f78' },
  'high-contrast': { background: '#000000', foreground: '#ffffff', cursor: '#ffd700', selectionBackground: '#423900' },
  graphite: { background: '#111214', foreground: '#e4e6e9', cursor: '#ffffff', selectionBackground: '#35404f' },
  midnight: { background: '#07111f', foreground: '#dceaff', cursor: '#7db7ff', selectionBackground: '#173a63' },
  aurora: { background: '#071613', foreground: '#dcf5ee', cursor: '#68e0c1', selectionBackground: '#195245' },
  ember: { background: '#190e0a', foreground: '#f6e8df', cursor: '#ff9b62', selectionBackground: '#67321f' },
  ocean: { background: '#06151b', foreground: '#ddf5fa', cursor: '#57d4ef', selectionBackground: '#155268' },
  sakura: { background: '#180f16', foreground: '#f7e8ef', cursor: '#f4a7c1', selectionBackground: '#60384d' },
  forest: { background: '#0b150f', foreground: '#e4f1e6', cursor: '#91d18b', selectionBackground: '#315a3c' },
  solarized: { background: '#07191d', foreground: '#e4dfc7', cursor: '#d6a84b', selectionBackground: '#28515a' },
}

export default function TerminalPane({
  processesRunning,
  theme,
  collapsed,
  maximized,
  activePanel,
  launchRequest,
  processLogs,
  onPanelChange,
  onToggleCollapsed,
  onToggleMaximized,
  onOpenSource,
}: Props) {
  const hostRef = useRef<HTMLDivElement>(null)
  const xtermRef = useRef<Terminal | null>(null)
  const fitRef = useRef<FitAddon | null>(null)
  const [terminals, setTerminals] = useState<CodexTerminal[]>([])
  const [activeId, setActiveId] = useState<number | null>(null)
  const [ports, setPorts] = useState<PortRecord[]>([])
  const [terminalError, setTerminalError] = useState('')
  const [terminalOutput, setTerminalOutput] = useState<Array<{ terminalId: number; name: string; stream: string; time: string }>>([])
  const buffersRef = useRef(new Map<number, string>())
  const sequencesRef = useRef(new Map<number, number>())
  const terminalsRef = useRef<CodexTerminal[]>([])
  const activeIdRef = useRef<number | null>(null)
  const terminalBootstrapRef = useRef(false)
  activeIdRef.current = activeId
  terminalsRef.current = terminals

  async function createTerminal(options: { name?: string; command?: string; cwd?: string } = {}) {
    if (!window.codex) return
    setTerminalError('')
    try {
      const xterm = xtermRef.current
      const terminal = await window.codex.createTerminal({
        cols: xterm?.cols || 100,
        rows: xterm?.rows || 30,
        ...options,
      })
      buffersRef.current.set(terminal.id, terminal.output || '')
      sequencesRef.current.set(terminal.id, terminal.sequence || 0)
      setTerminals((current) => [...current, terminal])
      setActiveId(terminal.id)
      if (terminal.status === 'error') setTerminalError(terminal.error)
    } catch (error) {
      setTerminalError(error instanceof Error ? error.message : String(error))
    }
  }

  async function renameTerminal(id: number) {
    if (!window.codex) return
    const current = terminals.find((terminal) => terminal.id === id)
    const name = window.prompt('Terminal name', current?.name || '')
    if (!name?.trim()) return
    const renamed = await window.codex.renameTerminal(id, name)
    setTerminals((items) => items.map((item) => item.id === id ? renamed : item))
  }

  async function duplicateTerminal(id: number) {
    if (!window.codex) return
    const terminal = await window.codex.duplicateTerminal(id)
    buffersRef.current.set(terminal.id, terminal.output || '')
    sequencesRef.current.set(terminal.id, terminal.sequence || 0)
    setTerminals((items) => [...items, terminal])
    setActiveId(terminal.id)
  }

  async function refreshPorts() {
    if (!window.codex) return
    const response = await fetch(`${window.codex.coreBase}/v1/ports`, {
      headers: { Authorization: `Bearer ${window.codex.coreToken}` },
    })
    if (response.ok) setPorts(await response.json() as PortRecord[])
  }

  async function stopManagedPort(port: PortRecord) {
    if (!window.codex || !port.managed_process_id) return
    await fetch(`${window.codex.coreBase}/v1/processes/${port.managed_process_id}`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${window.codex.coreToken}` },
    })
    await refreshPorts()
  }

  async function killTerminal(id: number) {
    if (!window.codex) return
    try {
      await window.codex.killTerminal(id)
      setTerminals((current) => {
        const next = current.filter((terminal) => terminal.id !== id)
        setActiveId((active) => active === id ? next.at(-1)?.id ?? null : active)
        return next
      })
      buffersRef.current.delete(id)
      sequencesRef.current.delete(id)
    } catch (error) {
      setTerminalError(error instanceof Error ? error.message : String(error))
    }
  }

  useEffect(() => {
    const xterm = new Terminal({
      cursorBlink: true,
      convertEol: true,
      fontFamily: '"SF Mono", Menlo, Monaco, monospace',
      fontSize: 12,
      lineHeight: 1.3,
      scrollback: 5000,
      theme: {
        ...terminalThemes.vscode,
        black: terminalThemes.vscode.background,
        red: '#d47a72',
        green: '#6eb583',
        yellow: '#d3aa54',
        blue: '#6aa8cb',
        magenta: '#aa86bd',
        cyan: '#70b8b0',
        white: '#d5d5d5',
      },
    })
    const fit = new FitAddon()
    xterm.loadAddon(fit)
    if (hostRef.current) {
      xterm.open(hostRef.current)
      requestAnimationFrame(() => fit.fit())
    }
    xtermRef.current = xterm
    fitRef.current = fit
    const input = xterm.onData((data) => {
      if (activeIdRef.current !== null) void window.codex?.writeTerminal(activeIdRef.current, data)
    })
    const resize = new ResizeObserver(() => {
      requestAnimationFrame(() => {
        if (!hostRef.current || hostRef.current.clientWidth < 20 || hostRef.current.clientHeight < 20) return
        fit.fit()
        if (activeIdRef.current !== null && xterm.cols >= 20 && xterm.rows >= 5) {
          void window.codex?.resizeTerminal(activeIdRef.current, xterm.cols, xterm.rows)
            .catch((error) => setTerminalError(error instanceof Error ? error.message : String(error)))
        }
      })
    })
    if (hostRef.current) resize.observe(hostRef.current)
    return () => {
      input.dispose()
      resize.disconnect()
      xterm.dispose()
    }
  }, [])

  useEffect(() => {
    const xterm = xtermRef.current
    if (!xterm) return
    const palette = terminalThemes[theme] ?? terminalThemes.vscode
    xterm.options.theme = {
      ...xterm.options.theme,
      ...palette,
      black: palette.background,
    }
  }, [theme])

  useEffect(() => {
    if (!window.codex) return
    const removeData = window.codex.onTerminalData(({ id, sequence, data }) => {
      if (sequence <= (sequencesRef.current.get(id) ?? 0)) return
      sequencesRef.current.set(id, sequence)
      buffersRef.current.set(id, `${buffersRef.current.get(id) ?? ''}${data}`.slice(-200_000))
      const terminal = terminalsRef.current.find((item) => item.id === id)
      if (terminal?.command) {
        setTerminalOutput((current) => [
          ...current,
          { terminalId: id, name: terminal.name, stream: data, time: new Date().toISOString() },
        ].slice(-1000))
      }
      if (id === activeIdRef.current) xtermRef.current?.write(data)
    })
    const removeExit = window.codex.onTerminalExit(({ id, exitCode }) => {
      if (id === activeIdRef.current) {
        xtermRef.current?.writeln(`\r\n\x1b[90mProcess exited with code ${exitCode}\x1b[0m`)
      }
      setTerminals((current) =>
        current.map((terminal) =>
          terminal.id === id ? { ...terminal, status: 'exited' } : terminal,
        ),
      )
    })
    const removeError = window.codex.onTerminalError(({ id, message }) => {
      setTerminalError(message)
      setTerminals((current) =>
        current.map((terminal) =>
          terminal.id === id ? { ...terminal, status: 'error', error: message } : terminal,
        ),
      )
    })
    return () => {
      removeData()
      removeExit()
      removeError()
    }
  }, [])

  useEffect(() => {
    if (!window.codex || terminalBootstrapRef.current) return
    terminalBootstrapRef.current = true
    void window.codex.listTerminals().then(async (items) => {
      const visible = items.filter((item) => item.status !== 'exited')
      for (const terminal of visible) {
        if ((terminal.sequence || 0) >= (sequencesRef.current.get(terminal.id) ?? 0)) {
          buffersRef.current.set(terminal.id, terminal.output || '')
          sequencesRef.current.set(terminal.id, terminal.sequence || 0)
        }
      }
      if (visible.length) {
        setTerminals(visible)
        setActiveId(visible.at(-1)?.id ?? null)
        const failed = visible.find((item) => item.status === 'error')
        if (failed) setTerminalError(failed.error)
        return
      }
      await createTerminal()
    }).catch((error) => setTerminalError(error instanceof Error ? error.message : String(error)))
  }, [])

  useEffect(() => {
    const xterm = xtermRef.current
    if (!xterm || activeId === null || activePanel !== 'terminal' || collapsed) return
    xterm.reset()
    const buffered = buffersRef.current.get(activeId)
    if (buffered) xterm.write(buffered)
    requestAnimationFrame(() => {
      fitRef.current?.fit()
      if (xterm.cols >= 20 && xterm.rows >= 5) {
        void window.codex?.resizeTerminal(activeId, xterm.cols, xterm.rows)
          .catch((error) => setTerminalError(error instanceof Error ? error.message : String(error)))
      }
      xterm.focus()
    })
  }, [activeId, activePanel, collapsed])

  useEffect(() => {
    if (activePanel !== 'ports' || collapsed) return
    void refreshPorts()
    const timer = window.setInterval(() => void refreshPorts(), 2000)
    return () => window.clearInterval(timer)
  }, [activePanel, collapsed])

  useEffect(() => {
    if (!launchRequest) return
    void createTerminal({
      name: `${launchRequest.mode === 'debug' ? 'Debug' : 'Run'}: ${launchRequest.name}`,
      command: launchRequest.command,
      cwd: launchRequest.cwd,
    })
  }, [launchRequest?.key])

  const processOutputLines = processLogs.flatMap((process) =>
    process.logs.map((log) => ({ ...log, process: process.name, processId: process.id, status: process.status })),
  )
  const terminalOutputLines = terminalOutput.map((entry) => ({
    line: entry.stream,
    stream: 'stdout',
    time: entry.time,
    process: entry.name,
    processId: entry.terminalId,
    status: 'running',
  }))
  const outputLines = [...processOutputLines, ...terminalOutputLines]
    .filter((entry) => activePanel !== 'debug' || entry.process.startsWith('Debug:'))
    .slice(-1000)
  const problems = outputLines.flatMap((entry) => {
    const match = entry.line.match(/(?:^|\s)([^:\s]+\.[A-Za-z0-9]+):(\d+)(?::(\d+))?[:\s]+(.+)/)
    return match ? [{
      path: match[1],
      line: Number(match[2]),
      column: Number(match[3] || 1),
      message: match[4],
      severity: /error|failed|exception/i.test(entry.line) ? 'error' : 'warning',
    }] : []
  })

  return (
    <>
      <div className="panel-tabs terminal-tabs">
        {(['terminal', 'output', 'debug', 'problems', 'ports'] as BottomPanelId[]).map((panel) => (
          <button
            className={activePanel === panel ? 'active' : ''}
            key={panel}
            onClick={() => activePanel === panel && !collapsed ? onToggleCollapsed() : onPanelChange(panel)}
          >
            {panel.toUpperCase()}{panel === 'problems' && problems.length ? ` ${problems.length}` : ''}
          </button>
        ))}
        <div className="panel-spacer" />
        <span>{processesRunning} running</span>
        <button title={maximized ? 'Restore panel' : 'Maximize panel'} onClick={onToggleMaximized}>{maximized ? '◱' : '□'}</button>
        <button title={collapsed ? 'Expand panel' : 'Collapse panel'} onClick={onToggleCollapsed}>{collapsed ? '⌃' : '⌄'}</button>
      </div>
      <div className={`bottom-panel-content ${collapsed ? 'collapsed' : ''}`}>
      {activePanel === 'terminal' ? (
        <>
          <div className="terminal-toolbar">
            <div className="terminal-tab-list">
              {terminals.map((terminal) => (
                <button
                  key={terminal.id}
                  className={terminal.id === activeId ? 'active' : ''}
                  onClick={() => setActiveId(terminal.id)}
                >
                  <span className={`terminal-state ${terminal.status}`} />
                  {terminal.name}
                </button>
              ))}
            </div>
            <button title="New terminal" onClick={() => void createTerminal()}>＋</button>
            <button title="Rename terminal" disabled={activeId === null} onClick={() => activeId !== null && void renameTerminal(activeId)}>✎</button>
            <button title="Duplicate terminal" disabled={activeId === null} onClick={() => activeId !== null && void duplicateTerminal(activeId)}>◫</button>
            <button title="Clear terminal" onClick={() => xtermRef.current?.clear()}>⌫</button>
            <button
              title="Kill terminal"
              disabled={activeId === null}
              onClick={() => activeId !== null && void killTerminal(activeId)}
            >
              ◼
            </button>
          </div>
          <div className="terminal-stage">
            <div className="xterm-host" ref={hostRef} />
            {terminalError && (
              <div className="terminal-error" role="alert">
                <strong>Terminal could not start</strong>
                <span>{terminalError}</span>
                <button onClick={() => void createTerminal()}>Retry</button>
              </div>
            )}
          </div>
        </>
      ) : activePanel === 'ports' ? (
        <div className="ports-view">
          <header><span>FORWARDED ADDRESS</span><span>PROCESS</span><span>ORIGIN</span><span /></header>
          {ports.map((port) => (
            <div className="port-row" key={`${port.pid}:${port.port}`}>
              <button onClick={() => void window.codex?.openExternal(port.url)}>{port.url}</button>
              <span>{port.process} · PID {port.pid}</span>
              <code>{port.address}</code>
              {port.managed_process_id ? (
                <button className="port-stop" onClick={() => void stopManagedPort(port)}>Stop</button>
              ) : <span className="port-external">external</span>}
            </div>
          ))}
          {!ports.length && <div className="ports-empty">No listening TCP ports detected.</div>}
        </div>
      ) : activePanel === 'problems' ? (
        <div className="problems-view">
          {problems.map((problem, index) => (
            <button key={`${problem.path}:${problem.line}:${index}`} onClick={() => onOpenSource(problem.path, problem.line, problem.column)}>
              <span className={problem.severity}>{problem.severity === 'error' ? '×' : '!'}</span>
              <strong>{problem.message}</strong>
              <code>{problem.path}:{problem.line}:{problem.column}</code>
            </button>
          ))}
          {!problems.length && <div className="ports-empty">No parsed workspace problems.</div>}
        </div>
      ) : (
        <div className="output-view">
          {outputLines.map((entry, index) => (
            <code className={entry.stream} key={`${entry.processId}:${index}`}>
              <span>{new Date(entry.time).toLocaleTimeString()}</span>
              <b>{activePanel === 'debug' ? 'DEBUG' : entry.process}</b>
              {entry.line}
            </code>
          ))}
          {!outputLines.length && <div className="ports-empty">No {activePanel} output yet.</div>}
        </div>
      )}
      </div>
    </>
  )
}
