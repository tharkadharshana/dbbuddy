import React from 'react'

/**
 * Brand logo: a Gemini-style three-sparkle mark in blue.
 *
 * This is the single source of truth for the app logo so every surface
 * (sidebar, auth, onboarding, embed widget, …) stays in sync. To change the
 * logo app-wide, edit only this file.
 *
 * Props:
 *   size    – width/height of the (square) logo box in px
 *   style   – extra wrapper styles
 *
 *   `color`/`radius`/`shadow`/`wordmark`/`wordColor` are accepted for
 *   call-site compatibility but are not used by this mark (it uses its own
 *   blue gradient and has no tile).
 */
export default function Logo({ size = 32, style = {}, color, radius, shadow, wordmark, wordColor }) {
  return (
    <div style={{
      width: size, height: size, flexShrink: 0,
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      ...style,
    }}>
      <svg width={size} height={size} viewBox="0 0 48 48" fill="none" role="img" aria-label="AI logo" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <linearGradient id="ai-sparkle-grad" x1="0" y1="0" x2="48" y2="48" gradientUnits="userSpaceOnUse">
            <stop stopColor="#5f9bff" />
            <stop offset="1" stopColor="#3a7ef5" />
          </linearGradient>
        </defs>
        {/* Big sparkle */}
        <path d="M19 8 C19 16, 20 17, 28 19 C20 21, 19 22, 19 30 C19 22, 18 21, 10 19 C18 17, 19 16, 19 8 Z" fill="url(#ai-sparkle-grad)" />
        {/* Medium sparkle (top-right) */}
        <path d="M35 6 C35 11, 35.6 11.6, 40 13 C35.6 14.4, 35 15, 35 20 C35 15, 34.4 14.4, 30 13 C34.4 11.6, 35 11, 35 6 Z" fill="url(#ai-sparkle-grad)" />
        {/* Small sparkle (bottom-right) */}
        <path d="M34 27 C34 31, 34.5 31.5, 38 32.5 C34.5 33.5, 34 34, 34 38 C34 34, 33.5 33.5, 30 32.5 C33.5 31.5, 34 31, 34 27 Z" fill="url(#ai-sparkle-grad)" />
      </svg>
    </div>
  )
}
