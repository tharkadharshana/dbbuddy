// The small "BETA" chip next to the product name.
//
// Shown while the backend's SUBSCRIPTION_FREE is on: during the free launch
// period the product is explicitly a beta, and the badge is what tells a
// merchant why nothing costs anything yet. It disappears on its own when the
// flag flips, so nobody has to remember to remove it.
//
// Callers pass the flag; this component only draws. Sizing is a prop because
// it sits beside headings of quite different sizes (14px sidebar title, 26px
// embed consent heading).
import React from 'react'

export default function BetaBadge({ size = 9, style }) {
  return (
    <span style={{
      fontSize: size,
      fontWeight: 700,
      letterSpacing: '0.06em',
      background: 'var(--blue-dim, rgba(79,142,247,0.15))',
      color: 'var(--blue, #4F8EF7)',
      borderRadius: 4,
      padding: '1px 5px',
      verticalAlign: 'middle',
      flexShrink: 0,
      whiteSpace: 'nowrap',
      ...style,
    }}>
      BETA
    </span>
  )
}
