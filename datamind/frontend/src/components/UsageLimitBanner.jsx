import React, { useState, useEffect, useRef } from 'react'
import { fetchSubscription } from '../utils/api'

export default function UsageLimitBanner({ onNavigate }) {
  const [sub, setSub]           = useState(null)
  const [dismissed, setDismissed] = useState(false)
  const intervalRef = useRef(null)

  useEffect(() => {
    load()
    intervalRef.current = setInterval(load, 5 * 60 * 1000)
    return () => clearInterval(intervalRef.current)
  }, [])

  async function load() {
    try {
      const data = await fetchSubscription()
      setSub(data)
      setDismissed(false)
    } catch { /* silent */ }
  }

  if (!sub || dismissed) return null

  const { status, usage_pct_ai, usage_pct_db, trial_days_remaining } = sub

  let message = null
  let color   = 'var(--red)'
  let bg      = 'rgba(239,68,68,0.12)'
  let border  = 'rgba(239,68,68,0.3)'

  if (status === 'expired' || status === 'cancelled') {
    message = 'Your subscription has expired. Subscribe to restore access.'
  } else if (status === 'no_subscription') {
    message = 'No active plan. Choose a plan to get started.'
  } else if (usage_pct_ai >= 100) {
    message = "You've used all your AI credits for this billing period."
  } else if (usage_pct_db >= 100) {
    message = "You've reached your DB row limit for this billing period."
  } else if (status === 'trial' && trial_days_remaining <= 2) {
    message = `Your free trial ends in ${trial_days_remaining} day${trial_days_remaining === 1 ? '' : 's'}.`
    color  = 'var(--amber)'
    bg     = 'rgba(245,166,35,0.1)'
    border = 'rgba(245,166,35,0.3)'
  } else if (usage_pct_ai >= 80) {
    message = `You've used ${usage_pct_ai}% of your AI credits.`
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
