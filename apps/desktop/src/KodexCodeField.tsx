import { useEffect, useRef } from 'react'

type KodexCodeFieldProps = {
  width?: number | string
  height?: number | string
  density?: number
  provider?: 'kodex' | 'openai-codex'
  reducedMotion?: boolean
  interactive?: boolean
}

type Glyph = {
  x: number
  y: number
  speed: number
  alpha: number
  phase: number
  char: string
  accent: boolean
}

const glyphs = '{}[]()<>/\\|=+-_*.:;01abcdefletconstasyncawaitgitnpmtestreadeditplan'

export default function KodexCodeField({
  width = '100%',
  height = '100%',
  density = 1,
  provider = 'kodex',
  reducedMotion,
  interactive = false,
}: KodexCodeFieldProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    const host = canvas?.parentElement
    if (!canvas || !host) return
    const context = canvas.getContext('2d', { alpha: true })
    if (!context) return

    let frame = 0
    let visible = !document.hidden
    let motionReduced = reducedMotion ?? window.matchMedia('(prefers-reduced-motion: reduce)').matches
    let points: Glyph[] = []
    let cssWidth = 0
    let cssHeight = 0
    let lastTime = 0
    let pointerX = -10_000
    let pointerY = -10_000
    let pointerActive = false
    const media = window.matchMedia('(prefers-reduced-motion: reduce)')

    const rebuild = () => {
      const bounds = host.getBoundingClientRect()
      cssWidth = Math.max(1, Math.floor(bounds.width))
      cssHeight = Math.max(1, Math.floor(bounds.height))
      const ratio = Math.min(window.devicePixelRatio || 1, 2)
      canvas.width = Math.floor(cssWidth * ratio)
      canvas.height = Math.floor(cssHeight * ratio)
      canvas.style.width = `${cssWidth}px`
      canvas.style.height = `${cssHeight}px`
      context.setTransform(ratio, 0, 0, ratio, 0, 0)

      const count = Math.min(
        10_000,
        Math.max(1_600, Math.floor(cssWidth * cssHeight * 0.017 * density)),
      )
      points = Array.from({ length: count }, (_, index) => ({
        x: Math.random() * cssWidth,
        y: Math.random() * cssHeight,
        speed: 1.5 + Math.random() * 7,
        alpha: 0.035 + Math.random() * 0.22,
        phase: Math.random() * Math.PI * 2,
        char: glyphs[(index * 17 + Math.floor(Math.random() * glyphs.length)) % glyphs.length],
        accent: index % 53 === 0,
      }))
    }

    const draw = (time: number) => {
      if (!visible) {
        frame = requestAnimationFrame(draw)
        return
      }
      if (!motionReduced && time - lastTime < 34) {
        frame = requestAnimationFrame(draw)
        return
      }
      lastTime = time
      context.clearRect(0, 0, cssWidth, cssHeight)
      context.font = '7px "SF Mono", Menlo, monospace'
      context.textBaseline = 'middle'
      const centerX = cssWidth / 2
      const centerY = cssHeight / 2
      const elapsed = time / 1000

      for (const point of points) {
        const pointerDistance = Math.hypot(point.x - pointerX, point.y - pointerY)
        const pointerInfluence = interactive && pointerActive
          ? Math.max(0, 1 - pointerDistance / 150)
          : 0
        if (!motionReduced) {
          point.y += point.speed * (0.018 + pointerInfluence * 0.018)
          point.x += Math.sin(elapsed * 0.35 + point.phase) * 0.025
          if (point.y > cssHeight + 8) point.y = -8
        }
        const distance = Math.hypot((point.x - centerX) / 1.25, point.y - centerY)
        const centerFade = Math.min(1, Math.max(0.05, (distance - 58) / 105))
        const pulse = motionReduced ? 0.75 : 0.62 + Math.sin(elapsed * 1.3 + point.phase) * 0.22
        context.globalAlpha = Math.min(
          1,
          point.alpha * centerFade * pulse + pointerInfluence * 0.72,
        )
        context.fillStyle = point.accent
          ? provider === 'openai-codex' ? '#d9dddf' : '#e4e7e9'
          : pointerInfluence > 0.08 ? '#d9dde0' : '#777d81'
        context.fillText(point.char, point.x, point.y)
      }

      if (interactive && pointerActive) {
        const glow = context.createRadialGradient(
          pointerX, pointerY, 0, pointerX, pointerY, 170,
        )
        glow.addColorStop(0, 'rgba(232,238,242,.12)')
        glow.addColorStop(.38, 'rgba(190,204,214,.045)')
        glow.addColorStop(1, 'rgba(160,180,194,0)')
        context.globalAlpha = 1
        context.fillStyle = glow
        context.fillRect(pointerX - 170, pointerY - 170, 340, 340)
      }

      const sweep = motionReduced ? cssHeight * 0.34 : (elapsed * 19) % (cssHeight + 80) - 40
      const gradient = context.createLinearGradient(0, sweep - 30, 0, sweep + 30)
      gradient.addColorStop(0, 'rgba(220,225,228,0)')
      gradient.addColorStop(.5, 'rgba(220,225,228,.065)')
      gradient.addColorStop(1, 'rgba(220,225,228,0)')
      context.globalAlpha = 1
      context.fillStyle = gradient
      context.fillRect(0, sweep - 30, cssWidth, 60)

      if (!motionReduced) frame = requestAnimationFrame(draw)
    }

    const observer = new ResizeObserver(() => {
      rebuild()
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(draw)
    })
    const onVisibility = () => {
      visible = !document.hidden
    }
    const onMotion = (event: MediaQueryListEvent) => {
      if (reducedMotion === undefined) motionReduced = event.matches
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(draw)
    }
    const onPointerMove = (event: PointerEvent) => {
      if (!interactive) return
      const bounds = host.getBoundingClientRect()
      pointerX = event.clientX - bounds.left
      pointerY = event.clientY - bounds.top
      pointerActive = true
    }
    const onPointerLeave = () => {
      pointerActive = false
    }

    rebuild()
    observer.observe(host)
    document.addEventListener('visibilitychange', onVisibility)
    media.addEventListener('change', onMotion)
    host.addEventListener('pointermove', onPointerMove)
    host.addEventListener('pointerleave', onPointerLeave)
    frame = requestAnimationFrame(draw)
    return () => {
      observer.disconnect()
      document.removeEventListener('visibilitychange', onVisibility)
      media.removeEventListener('change', onMotion)
      host.removeEventListener('pointermove', onPointerMove)
      host.removeEventListener('pointerleave', onPointerLeave)
      cancelAnimationFrame(frame)
    }
  }, [density, interactive, provider, reducedMotion])

  return (
    <canvas
      ref={canvasRef}
      className="kodex-code-field"
      style={{ width, height }}
      aria-hidden="true"
    />
  )
}
