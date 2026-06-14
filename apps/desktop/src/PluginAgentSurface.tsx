import type { CSSProperties } from 'react'
import ProviderBrandMark, { type ProviderBrand } from './ProviderBrandMark'

export type PluginAppearance = {
  accent: string
  brand?: ProviderBrand
  icon: string
  title: string
  subtitle: string
  emptyState: 'minimal' | 'grid' | 'gradient'
  controls: string[]
}

type Props = {
  appearance: PluginAppearance
  connected: boolean
  onConnect?: () => void
  actionLabel?: string
}

export default function PluginAgentSurface({ appearance, connected, onConnect, actionLabel = 'Connect account' }: Props) {
  return (
    <div
      className={`plugin-agent-idle ${appearance.emptyState}`}
      style={{ '--plugin-accent': appearance.accent } as CSSProperties}
    >
      <div className="plugin-agent-center">
        <ProviderBrandMark brand={appearance.brand ?? 'generic'} size="large" />
        <strong>{appearance.title}</strong>
        <p>{appearance.subtitle}</p>
        {!connected && onConnect && <button onClick={onConnect}>{actionLabel}</button>}
      </div>
    </div>
  )
}
