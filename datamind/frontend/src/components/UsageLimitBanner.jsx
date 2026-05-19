import React, { useState } from 'react'

export default function UsageLimitBanner({ sub, onNavigate }) {
  const [dismissed, setDismissed] = useState(false)

  if (!sub || dismissed) return null

  const { status, tokens_pct, trial_days_remaining } = sub

  let message = null
  let color   = 'var(--red)'
  let bg      = 'rgba(239,68,68,0.12)'
  let border  = 'rgba(239,68,68,0.3)'

  if (status === 'expired' || status === 'cancelled') {
    message = 'Your subscription has expired. Subscribe to restore access.'
  } else if (status === 'no_subscription') {
    message = 'No active plan. Choose a plan to get started.'
  } else if (tokens_pct >= 100) {
    message = "You've used all your tokens for this billing period."
  } else if (status === 'trial' && trial_days_remaining <= 2) {
    message = `Your free trial ends in ${trial_days_remaining} day${trial_days_remaining === 1 ? '' : 's'}.`
    color  = 'var(--amber)'
    bg     = 'rgba(245,166,35,0.1)'
    border = 'rgba(245,166,35,0.3)'
  } else if (tokens_pct >= 80) {
    message = `You've used ${tokens_pct}% of your tokens.`
    color  = 'var(--amber)'
    bg     = 'rgba(245,166,35,0.1)'
    border = 'rgba(245,166,35,0.3)'
  }

  if (!message) return null

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12,
      padding: '8px 16px',
      background: bg,
      borderBottom: `1px solid ${border}`,
      fontSize: 13,
      color,
      flexShrink: 0,
    }}>
      <span style={{ flex: 1 }}>{message}</span>
      <button
        onClick={() => onNavigate && onNavigate('billing')}
        style={{
          fontSize: 12, fontWeight: 600, padding: '4px 12px',
          borderRadius: 6, border: `1px solid ${color}`,
          background: 'transparent', color, cursor: 'pointer',
          flexShrink: 0,
        }}
      >
        Manage Plan
      </button>
      <button
        onClick={() => setDismissed(true)}
        aria-label="Dismiss"
        style={{
          background: 'none', border: 'none', cursor: 'pointer',
          color, fontSize: 16, lineHeight: 1, padding: '0 2px',
          flexShrink: 0,
        }}
      >
        ×
      </button>
    </div>
  )
}
