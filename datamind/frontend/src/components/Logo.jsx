import React from 'react'
import { useBrand } from '../brand'

/**
 * Brand logo — whatever the brand's partner row supplies.
 *
 * This used to be a hardcoded SalesPlay wordmark. One build serves every brand,
 * so that put SalesPlay's mark on ai.sellmopos.com. The artwork now comes from
 * branding.logo_url, the same field the widget renders.
 *
 * With no logo configured this falls back to the brand's initial on an accent
 * tile, never to any particular mark: a fallback that drew one brand's logo
 * would put it in another brand's app. Same rule as embed/BrandLogo.jsx.
 *
 * The artwork is usually a WORDMARK containing the product name, so call sites
 * must not print the name beside it or it appears twice.
 *
 * `size` is a HEIGHT, not a box. A wordmark is roughly 4:1, so constraining
 * both dimensions squashes it to a sliver. Width follows the artwork.
 *
 * Props:
 *   size    – rendered height in px
 *   mark    – render the square mark instead of the wordmark, for placements
 *             too narrow for words
 *   style   – extra wrapper styles
 *
 *   `color`/`radius`/`shadow`/`wordmark`/`wordColor` are accepted for call-site
 *   compatibility but unused: the artwork carries its own colours.
 */
export default function Logo({ size = 32, mark = false, style = {}, color, radius, shadow, wordmark, wordColor }) {
  const brand = useBrand()
  const src = mark ? brand.logoMarkUrl : brand.logoUrl

  if (!src) {
    // ponytail: initial-on-a-tile, so a brand with no artwork still renders
    // something of its own rather than someone else's mark.
    return (
      <div
        style={{
          height: size, width: size,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          background: brand.accent, color: '#fff',
          borderRadius: 8, fontWeight: 700, fontSize: size * 0.5,
          flexShrink: 0, ...style,
        }}
        aria-label={brand.productName}
      >
        {(brand.productName || '?').trim().charAt(0).toUpperCase()}
      </div>
    )
  }

  return (
    <img
      src={src}
      alt={brand.productName}
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
