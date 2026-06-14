import { useEffect, useMemo, useState } from 'react'

export type DesignerMode = 'design' | 'source' | 'split' | 'preview'

export type DesignerNode = {
  id: string
  type: string
  name: string
  parent_id: string | null
  children: string[]
  properties: Record<string, unknown>
  source: {
    path: string
    start_line: number
    start_column: number
    end_line: number
    end_column: number
    start_offset: number
    end_offset: number
  }
}

export type DesignerDocument = {
  path: string
  adapter: { id: string; name: string; tier: string }
  hash: string
  nodes: DesignerNode[]
  root_ids: string[]
  property_schemas: Record<string, Array<{ name: string; label: string; type: string; group: string }>>
  diagnostics: Array<{ severity: string; message: string }>
}

type ToolboxItem = {
  id: string
  label: string
  category: string
  source_preview: string
}

type Device = {
  id: string
  name: string
  width: number
  height: number
  platform: string
}

type Props = {
  path: string
  source: string
  mode: DesignerMode
  request: <T>(path: string, init?: RequestInit) => Promise<T>
  onModeChange: (mode: DesignerMode) => void
  onOpenSource: (node: DesignerNode) => void
  onDocumentChange: (document: DesignerDocument, content?: string) => void
  onAskAI: (node: DesignerNode | null) => void
}

function nodeLabel(node: DesignerNode) {
  const text = node.properties.text || node.properties.Text || node.properties['aria-label']
  return typeof text === 'string' && text.trim() ? text : node.name
}

function CanvasNode({
  node,
  selected,
  onSelect,
  onOpenSource,
}: {
  node: DesignerNode
  selected: boolean
  onSelect: () => void
  onOpenSource: () => void
}) {
  const type = node.type.toLowerCase()
  const label = nodeLabel(node)
  const className = `designer-canvas-node ${selected ? 'selected' : ''} designer-kind-${type}`
  if (type.includes('button')) {
    return <button className={className} onClick={(event) => { event.stopPropagation(); onSelect() }} onDoubleClick={onOpenSource}>{label}</button>
  }
  if (type.includes('input') || type.includes('entry') || type.includes('textfield')) {
    return <div className={className} onClick={onSelect} onDoubleClick={onOpenSource}><span>{label}</span></div>
  }
  if (type.includes('image')) {
    return <div className={`${className} designer-image-placeholder`} onClick={onSelect} onDoubleClick={onOpenSource}>Image</div>
  }
  return <div className={className} onClick={(event) => { event.stopPropagation(); onSelect() }} onDoubleClick={onOpenSource}><span>{label}</span><small>{node.type}</small></div>
}

export default function VisualDesigner({
  path,
  source,
  mode,
  request,
  onModeChange,
  onOpenSource,
  onDocumentChange,
  onAskAI,
}: Props) {
  const [document, setDocument] = useState<DesignerDocument | null>(null)
  const [toolbox, setToolbox] = useState<ToolboxItem[]>([])
  const [devices, setDevices] = useState<Device[]>([])
  const [deviceId, setDeviceId] = useState('responsive')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [zoom, setZoom] = useState(80)
  const [propertyDrafts, setPropertyDrafts] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')

  useEffect(() => {
    let active = true
    setMessage('')
    void request<DesignerDocument>(`/v1/designer/document?path=${encodeURIComponent(path)}`)
      .then(async (next) => {
        if (!active) return
        setDocument(next)
        onDocumentChange(next)
        setSelectedId(next.nodes[0]?.id ?? null)
        const [items, profiles] = await Promise.all([
          request<ToolboxItem[]>(`/v1/designer/toolbox?adapter=${encodeURIComponent(next.adapter.id)}`),
          request<Device[]>('/v1/designer/device-profiles'),
        ])
        if (active) {
          setToolbox(items)
          setDevices(profiles)
        }
      })
      .catch((error) => active && setMessage(error instanceof Error ? error.message : 'Unable to open designer'))
    return () => { active = false }
  }, [path, request])

  const selected = document?.nodes.find((node) => node.id === selectedId) ?? null
  const device = devices.find((profile) => profile.id === deviceId) ?? devices[0]
  const schemas = selected && document ? document.property_schemas[selected.id] ?? [] : []
  const canvasNodes = useMemo(() => document?.nodes.slice(0, 80) ?? [], [document])

  async function refreshFromTransaction(payload: { document: DesignerDocument }) {
    setDocument(payload.document)
    onDocumentChange(payload.document)
  }

  async function transact(body: Record<string, unknown>) {
    if (!document) return
    setBusy(true)
    setMessage('')
    try {
      const result = await request<{ document: DesignerDocument }>('/v1/designer/transactions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, expected_hash: document.hash, ...body }),
      })
      await refreshFromTransaction(result)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Designer change failed')
    } finally {
      setBusy(false)
    }
  }

  async function historyAction(action: 'undo' | 'redo') {
    setBusy(true)
    try {
      const result = await request<{ document: DesignerDocument }>(`/v1/designer/transactions/${action}`, { method: 'POST' })
      await refreshFromTransaction(result)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : `Unable to ${action}`)
    } finally {
      setBusy(false)
    }
  }

  function propertyValue(name: string) {
    if (propertyDrafts[name] !== undefined) return propertyDrafts[name]
    const value = selected?.properties[name]
    return value === undefined || value === true ? '' : String(value)
  }

  return (
    <section className="visual-designer">
      <header className="designer-toolbar">
        <div className="designer-modes">
          {(['design', 'source', 'split', 'preview'] as DesignerMode[]).map((item) => (
            <button key={item} className={mode === item ? 'active' : ''} onClick={() => onModeChange(item)}>{item}</button>
          ))}
        </div>
        <div className="designer-toolbar-actions">
          <select value={deviceId} onChange={(event) => setDeviceId(event.target.value)}>
            {devices.map((profile) => <option key={profile.id} value={profile.id}>{profile.name} · {profile.width}×{profile.height}</option>)}
          </select>
          <button onClick={() => void historyAction('undo')} disabled={busy}>Undo</button>
          <button onClick={() => void historyAction('redo')} disabled={busy}>Redo</button>
          <button className="designer-ai-button" onClick={() => onAskAI(selected)}>Ask Kodex</button>
        </div>
      </header>

      <div className={`designer-workspace mode-${mode}`}>
        {mode !== 'preview' && (
          <aside className="designer-toolbox">
            <header><strong>Toolbox</strong><small>{document?.adapter.name ?? 'Loading adapter'}</small></header>
            <div className="designer-toolbox-list">
              {toolbox.map((item) => (
                <button
                  key={item.id}
                  draggable
                  title={item.source_preview}
                  onDragStart={(event) => event.dataTransfer.setData('application/x-kodex-component', item.id)}
                  onDoubleClick={() => void transact({ operation: 'add', node_id: selected?.id ?? '', component: item.id, label: `Add ${item.label}` })}
                >
                  <span>＋</span><div><strong>{item.label}</strong><small>{item.category}</small></div>
                </button>
              ))}
            </div>
          </aside>
        )}

        {mode !== 'preview' && (
          <aside className="designer-hierarchy">
            <header><strong>Hierarchy</strong><small>{canvasNodes.length} elements</small></header>
            <div>
              {canvasNodes.map((node) => (
                <button
                  key={node.id}
                  className={selectedId === node.id ? 'active' : ''}
                  style={{ paddingLeft: `${12 + Math.min(4, node.parent_id ? 1 : 0) * 14}px` }}
                  onClick={() => setSelectedId(node.id)}
                  onDoubleClick={() => onOpenSource(node)}
                >
                  <span>{node.type.slice(0, 2).toUpperCase()}</span>
                  <div><strong>{nodeLabel(node)}</strong><small>Line {node.source.start_line}</small></div>
                </button>
              ))}
            </div>
          </aside>
        )}

        <main className="designer-stage">
          <div className="designer-stage-bar">
            <span>{device?.name ?? 'Canvas'}</span>
            <label>Zoom <input type="range" min="40" max="120" value={zoom} onChange={(event) => setZoom(Number(event.target.value))} /> {zoom}%</label>
          </div>
          <div className="designer-stage-scroll">
            <div
              className={`designer-device ${device?.platform ?? 'web'}`}
              style={{
                width: `${Math.min(device?.width ?? 1180, 1180)}px`,
                minHeight: `${Math.min(device?.height ?? 760, 760)}px`,
                transform: `scale(${zoom / 100})`,
              }}
              onClick={() => setSelectedId(null)}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault()
                const component = event.dataTransfer.getData('application/x-kodex-component')
                if (component) void transact({ operation: 'add', node_id: selected?.id ?? '', component, label: `Add ${component}` })
              }}
            >
              <div className="designer-canvas">
                {canvasNodes.map((node) => (
                  <CanvasNode
                    key={node.id}
                    node={node}
                    selected={node.id === selectedId}
                    onSelect={() => setSelectedId(node.id)}
                    onOpenSource={() => onOpenSource(node)}
                  />
                ))}
                {!canvasNodes.length && <div className="designer-empty-canvas"><strong>Drop a component here</strong><span>The source file remains the source of truth.</span></div>}
              </div>
            </div>
          </div>
          {mode === 'split' && <pre className="designer-source-peek">{source}</pre>}
        </main>

        {mode !== 'preview' && (
          <aside className="designer-properties">
            <header><strong>Properties</strong><small>{selected ? selected.type : 'Select an element'}</small></header>
            {selected && (
              <>
                <div className="designer-source-link" onDoubleClick={() => onOpenSource(selected)}>
                  <span>{selected.source.path}</span><b>Line {selected.source.start_line}</b>
                </div>
                {['identity', 'content', 'layout', 'style', 'events', 'accessibility'].map((group) => {
                  const fields = schemas.filter((schema) => schema.group === group)
                  if (!fields.length) return null
                  return (
                    <section key={group}>
                      <h4>{group}</h4>
                      {fields.map((schema) => (
                        <label key={schema.name}>
                          <span>{schema.label}</span>
                          <input
                            value={propertyValue(schema.name)}
                            placeholder={schema.name}
                            onChange={(event) => setPropertyDrafts((current) => ({ ...current, [schema.name]: event.target.value }))}
                            onBlur={() => {
                              const value = propertyDrafts[schema.name]
                              if (value !== undefined && value !== String(selected.properties[schema.name] ?? '')) {
                                void transact({ operation: 'update', node_id: selected.id, property: schema.name, value, label: `Change ${schema.label}` })
                              }
                            }}
                          />
                        </label>
                      ))}
                    </section>
                  )
                })}
                <button className="designer-delete" onClick={() => void transact({ operation: 'delete', node_id: selected.id, label: `Delete ${selected.type}` })}>Delete element</button>
              </>
            )}
          </aside>
        )}
      </div>
      {(busy || message) && <div className={`designer-status ${message ? 'error' : ''}`}>{message || 'Applying source transaction…'}</div>}
    </section>
  )
}
