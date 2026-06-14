import { useEffect, useMemo, useState } from 'react'

type Metrics = {
  timestamp: number
  system: {
    cpuPercent: number | null
    memoryUsedBytes: number | null
    memoryTotalBytes: number | null
    diskUsedBytes: number
    diskTotalBytes: number
    gpuName: string | null
    gpuPercent: number | null
  }
  kodex: { cpuPercent: number; memoryBytes: number; processCount: number }
  history?: Metrics[]
}

type Props = {
  agentActive: boolean
  provider: string
  model: string
  terminalActive: boolean
  onFocusAgent: () => void
}

const percent = (used: number | null, total: number | null) =>
  used === null || total === null || total <= 0 ? null : Math.min(100, (used / total) * 100)
const label = (value: number | null) => value === null ? 'N/A' : `${Math.round(value)}%`
const bytes = (value: number) => `${(value / 1024 ** 3).toFixed(value >= 10 * 1024 ** 3 ? 0 : 1)} GB`

function Meter({ name, value }: { name: string; value: number | null }) {
  return (
    <span className="system-meter" title={`${name}: ${label(value)}`}>
      <b>{name}</b>
      <i><em style={{ width: `${value ?? 0}%` }} /></i>
      <small>{label(value)}</small>
    </span>
  )
}

export default function KodexSystemIsland({ agentActive, provider, model, terminalActive, onFocusAgent }: Props) {
  const [metrics, setMetrics] = useState<Metrics | null>(null)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    let disposed = false
    const refresh = async () => {
      if (!window.codex) return
      try {
        const response = await fetch(`${window.codex.coreBase}/v1/system/metrics`, {
          headers: { Authorization: `Bearer ${window.codex.coreToken}` },
        })
        if (response.ok && !disposed) setMetrics(await response.json() as Metrics)
      } catch {
        if (!disposed) setMetrics(null)
      }
    }
    void refresh()
    const timer = window.setInterval(() => void refresh(), 1000)
    return () => {
      disposed = true
      window.clearInterval(timer)
    }
  }, [])

  const values = useMemo(() => ({
    cpu: metrics?.system.cpuPercent ?? null,
    ram: percent(metrics?.system.memoryUsedBytes ?? null, metrics?.system.memoryTotalBytes ?? null),
    disk: percent(metrics?.system.diskUsedBytes ?? null, metrics?.system.diskTotalBytes ?? null),
    gpu: metrics?.system.gpuPercent ?? null,
  }), [metrics])

  return (
    <div className="system-island-wrap">
      <button className={`system-island ${agentActive ? 'working' : ''}`} onClick={() => setOpen((value) => !value)}>
        <span className={`status-island-dot ${agentActive ? 'working' : ''}`} />
        <Meter name="CPU" value={values.cpu} />
        <Meter name="RAM" value={values.ram} />
        <Meter name="DSK" value={values.disk} />
        <Meter name="GPU" value={values.gpu} />
      </button>
      {open && (
        <div className="system-popover">
          <header>
            <span><strong>Kodex system monitor</strong><small>Local, one-second samples</small></span>
            <button onClick={() => setOpen(false)}>×</button>
          </header>
          <div className="system-popover-grid">
            <div><small>System CPU</small><strong>{label(values.cpu)}</strong></div>
            <div><small>System memory</small><strong>{metrics?.system.memoryUsedBytes ? bytes(metrics.system.memoryUsedBytes) : 'N/A'}</strong></div>
            <div><small>Kodex CPU</small><strong>{label(metrics?.kodex.cpuPercent ?? null)}</strong></div>
            <div><small>Kodex memory</small><strong>{metrics ? bytes(metrics.kodex.memoryBytes) : 'N/A'}</strong></div>
            <div><small>GPU</small><strong>{metrics?.system.gpuName || 'N/A'}</strong></div>
            <div><small>Processes</small><strong>{metrics?.kodex.processCount ?? 'N/A'}</strong></div>
          </div>
          <div className="system-status-row"><span className="online" />Core connected</div>
          <div className="system-status-row"><span className={agentActive ? 'busy' : 'online'} />Agent {agentActive ? 'working' : 'ready'}</div>
          <div className="system-status-row"><span className={terminalActive ? 'online' : ''} />Terminal {terminalActive ? 'active' : 'collapsed'}</div>
          <button className="system-agent-link" onClick={() => { setOpen(false); onFocusAgent() }}>
            <span><strong>{provider}</strong><small>{model}</small></span><b>Open agent</b>
          </button>
        </div>
      )}
    </div>
  )
}
