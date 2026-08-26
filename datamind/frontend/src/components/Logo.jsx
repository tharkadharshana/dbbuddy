import React from 'react'

/**
 * Brand logo: the SalesPlay AI wordmark (public/brand/salesplay-ai-logo.svg).
 *
 * This is the single source of truth for the app logo so every surface
 * (sidebar, auth, onboarding, chat, …) stays in sync. To change the logo
 * app-wide, replace that file — nothing here needs editing.
 *
 * The artwork is a WORDMARK: it already contains the words "SalesPlay AI".
 * Call sites must therefore not print the product name next to it, or the
 * name appears twice. The embed widget does the same (see embed/BrandLogo.jsx).
 *
 * `size` is a HEIGHT, not a box. The wordmark is roughly 4:1, so constraining
 * both dimensions to the same value would squash it to a sliver. Width follows
 * the artwork.
 *
 * Props:
 *   size    – rendered height in px
 *   mark    – render the square icon instead of the wordmark, for placements
 *             too narrow for words (kept for the avatar-style call sites)
 *   style   – extra wrapper styles
 *
 *   `color`/`radius`/`shadow`/`wordmark`/`wordColor` are accepted for
 *   call-site compatibility but are not used: the artwork carries its own
 *   colours and has no tile.
 */
export default function Logo({ size = 32, mark = false, style = {}, color, radius, shadow, wordmark, wordColor }) {
  const src = mark ? '/brand/salesplay-mark.svg' : '/brand/salesplay-ai-logo.svg'
  return (
    <img
      src={src}
      alt="SalesPlay AI"
      style={{
        height: size,
        width: mark ? size : 'auto',
        maxWidth: '100%',
        flexShrink: 0,
        objectFit: 'contain',
        ...style,
      }}
    />
  )
}
