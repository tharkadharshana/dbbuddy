import React from 'react'

/**
 * Brand logo lockup: the 4-square mark + "AI" wordmark.
 *
 * This is the single source of truth for the app logo so every surface
 * (sidebar, auth, onboarding, embed widget, …) stays in sync.
 *
 * Props:
 *   size       – height of the badge in px (the "AI" wordmark scales with it)
 *   wordmark   – show the "AI" wordmark next to the badge (default true)
 *   wordColor  – color of the "AI" wordmark (default brand blue)
 *   style      – extra wrapper styles
 */
export default function Logo({ size = 32, radius, wordmark = false, wordColor = '#4f8ef7', shadow = false, style = {} }) {
  const r = radius != null ? radius : Math.round(size * 0.28)
  const inner  = Math.round(size * 0.5)
  const fontSize = Math.round(size * 0.92)

  return (
    <div style={{ display: 'inline-flex', alignItems: 'center', gap: Math.round(size * 0.3), ...style }}>
      <div style={{
        width: size, height: size, borderRadius: r, flexShrink: 0,
        background: 'linear-gradient(135deg,#4f8ef7,#a78bfa)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        boxShadow: shadow || 'none',
      }}>
        <svg width={inner} height={inner} viewBox="0 0 16 16" fill="none">
          <rect x="2" y="2" width="5" height="5" rx="1" fill="rgba(255,255,255,0.95)" />
          <rect x="9" y="2" width="5" height="5" rx="1" fill="rgba(255,255,255,0.5)" />
          <rect x="2" y="9" width="5" height="5" rx="1" fill="rgba(255,255,255,0.5)" />
          <rect x="9" y="9" width="5" height="5" rx="1" fill="rgba(255,255,255,0.95)" />
        </svg>
      </div>
      {wordmark && (
        <span style={{
          fontWeight: 800, fontSize, lineHeight: 1, letterSpacing: '-0.04em',
          color: wordColor, fontFamily: 'inherit',
        }}>
          AI
        </span>
      )}
    </div>
  )
}
