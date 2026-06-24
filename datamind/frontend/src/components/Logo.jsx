import React from 'react'

/**
 * Brand logo: the "AI" wordmark with the 4-square brand mark as a top-right
 * accent (option9 combined mark).
 *
 * This is the single source of truth for the app logo so every surface
 * (sidebar, auth, onboarding, embed widget, …) stays in sync. To change the
 * logo app-wide, edit only this file.
 *
 * Props:
 *   size    – width/height of the (square) logo box in px
 *   shadow  – optional CSS box-shadow on the logo
 *   style   – extra wrapper styles
 *
 *   `radius`/`wordmark`/`wordColor` are accepted for call-site compatibility
 *   but are not needed by this mark.
 */
export default function Logo({ size = 32, shadow = false, style = {}, radius, wordmark, wordColor }) {
  return (
    <div style={{
      width: size, height: size, flexShrink: 0,
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      boxShadow: shadow || 'none',
      ...style,
    }}>
      <svg width={size} height={size} viewBox="0 0 128 128" fill="none" role="img" aria-label="AI logo" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <linearGradient id="ai-logo-grad" x1="0" y1="0" x2="128" y2="128" gradientUnits="userSpaceOnUse">
            <stop stopColor="#4f8ef7" />
            <stop offset="1" stopColor="#a78bfa" />
          </linearGradient>
        </defs>
        {/* AI wordmark */}
        <text x="48" y="74" textAnchor="middle" dominantBaseline="central"
              fontFamily="Segoe UI, Inter, system-ui, sans-serif" fontSize="60"
              fontWeight="800" fill="#4f8ef7" letterSpacing="-3">AI</text>
        {/* 4-square mark in a gradient rounded square */}
        <rect x="86" y="20" width="34" height="34" rx="9" fill="url(#ai-logo-grad)" />
        <g transform="translate(86,20) scale(2.125)">
          <rect x="2" y="2" width="5" height="5" rx="1" fill="rgba(255,255,255,0.95)" />
          <rect x="9" y="2" width="5" height="5" rx="1" fill="rgba(255,255,255,0.5)" />
          <rect x="2" y="9" width="5" height="5" rx="1" fill="rgba(255,255,255,0.5)" />
          <rect x="9" y="9" width="5" height="5" rx="1" fill="rgba(255,255,255,0.95)" />
        </g>
      </svg>
    </div>
  )
}
