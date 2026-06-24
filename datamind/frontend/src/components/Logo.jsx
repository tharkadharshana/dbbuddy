import React from 'react'

/**
 * Brand logo: three blue dots — the "AI" three-dot mark (matches the embed
 * widget's typing indicator).
 *
 * This is the single source of truth for the app logo so every surface
 * (sidebar, auth, onboarding, embed widget, …) stays in sync. To change the
 * logo app-wide, edit only this file.
 *
 * Props:
 *   size    – width/height of the (square) logo box in px
 *   color   – dot color (default brand blue)
 *   shadow  – optional CSS box-shadow on the logo box
 *   style   – extra wrapper styles
 *
 *   `radius`/`wordmark`/`wordColor` are accepted for call-site compatibility
 *   but are not used by this mark.
 */
export default function Logo({ size = 32, color = '#4f8ef7', style = {}, radius, shadow, wordmark, wordColor }) {
  // `shadow`/`radius` are accepted from existing call sites but ignored — the
  // bare three-dot mark has no tile to cast a shadow or round.
  return (
    <div style={{
      width: size, height: size, flexShrink: 0,
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      ...style,
    }}>
      {/* Three dots, centered in a square box so the mark drops into every
          existing logo slot without changing layout. */}
      <svg width={size} height={size} viewBox="0 0 48 48" fill="none" role="img" aria-label="AI logo" xmlns="http://www.w3.org/2000/svg">
        <circle cx="10" cy="24" r="5" fill={color} />
        <circle cx="24" cy="24" r="5" fill={color} />
        <circle cx="38" cy="24" r="5" fill={color} />
      </svg>
    </div>
  )
}
