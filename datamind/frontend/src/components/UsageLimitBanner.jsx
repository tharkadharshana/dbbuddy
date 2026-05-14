import React, { useState, useEffect, useRef } from 'react'
import { fetchSubscription } from '../utils/api'

const POLL_MS = 5 * 60 * 1000  // 5 minutes

export default function UsageLimitBanner({ onNavigate }) {
  const [message, setMessage] = useState(null)  // { text, level: 'warn'|'error' }
  const [dismissed, setDismissed] = useState(false)
  const timerRef = useRef(null)

  useEffect(() => {
    check()
    timerRef.current = setInterval(() => {
      setDismissed(false)
      check()
    }, POLL_MS)
    return () => clearInterval(timerRef.current)
  }, [])

  async function check() {
    try {
      const sub = await fetchSubscription()
      const status = sub?.status
      const pctAi  = sub?.usage_pct_ai ?? 0
      const pctDb  = sub?.usage_pct_db ?? 0
      const days   = sub?.trial_days_remaining ?? 99

      if (status === 'expired' || status === 'cancelled') {
        setMessage({ text: 'Your subscription has expired. Subscribe to restore access.', level: 'error' })
      } else if (status === 'no_subscription') {
        setMessage({ text: 'No active plan found. Choose a plan to get started.', level: 'error' })
      } else if (pctAi >= 100) {
        setMessage({ text: 'You have used all your AI credits for this period.', level: 'error' })
      } else if (pctDb >= 100) {
        setMessage({ text: 'You have reached your DB row limit for this period.', level: 'error' })
      } else if (status === 'trial' && days <= 2) {
        setMessage({ text: `Your free trial ends in ${days} day${days !== 1 ? 's' : ''}.`, level: 'warn' })
      } else if (pctAi >= 80) {
        setMessage({ text: `You've used ${pctAi}% of your AI credits this period.`, level: 'warn' })
      } else if (pctDb >= 80) {
        setMessage({ text: `You've used ${pctDb}% of your DB row quota this period.`, level: 'warn' })
      } else {
        setMessage(null)
      }
    } catch {
      // Silently ignore — don't block the app if billing is down
    }
  }

  if (!message || dismissed) return null

  const isError = message.level === 'error'
  const bg    = isError ? 'rgba(255,69,58,0.12)'  : 'rgba(245,166,35,0.12)'
  const border= isError ? 'rgba(255,69,58,0.35)'  : 'rgba(245,166,35,0.35)'
  const color = isError ? 'var(--red)'             : 'var(--amber)'

  return (
    <div style={{ background: bg, borderBottom: `1px solid ${border}`, padding: '9px 18px', display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0 }}>
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
        <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>
        <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
      </svg>
      <span style={{ flex: 1, fontSize: 13, color, fontWeight: 500 }}>{message.text}</span>
      <button
        onClick={() => onNavigate?.('billing')}
        style={{ padding: '4px 12px', borderRadius: 'var(--r-sm)', border: `1px solid ${border}`, background: 'transparent', color, fontSize: 12, fontWeight: 600, cursor: 'pointer', flexShrink: 0 }}
      >
        Manage Plan
      </button>
      <button
        onClick={() => setDismissed(true)}
        style={{ background: 'transparent', border: 'none', color, cursor: 'pointer', padding: 4, lineHeight: 1, fontSize: 16, opacity: 0.7, flexShrink: 0 }}
        title="Dismiss"
      >
        ×
      </button>
    </div>
  )
}
