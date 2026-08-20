// Shown instead of the plans screen while SUBSCRIPTION_FREE is on.
//
// During the free launch period nobody is asked to pay, so a merchant we
// can't grant access to must not be routed to a paywall — that screen
// charges a real card, and charging during a period we advertised as free
// is the one outcome worth engineering against. This screen explains the
// block and stops there.
//
// It also covers the "we couldn't check" case: when the access call fails
// after its retry, `reason` is null and this renders a retry button rather
// than claiming the trial ended.
import React, { useState } from 'react'
import { appName } from './embedBranding'

const REASON_COPY = {
  trial_expired:  (days) => `Your ${days}-day free trial has ended.`,
  plan_expired:   () => 'Your subscription has expired.',
  quota_exceeded: () => "You've used up this period's AI usage.",
}

export default function EmbedFreeBlocked({ context, reason, trialDays = 14, onRetry, onClose }) {
  const [retrying, setRetrying] = useState(false)
  const appNm = appName(context)
  const unknown = !reason

  async function handleRetry() {
    if (!onRetry) return
    setRetrying(true)
    try { await onRetry() } catch { /* stay on this screen */ }
    setRetrying(false)
  }

  return (
    <div style={{ width: '100%', padding: '28px 20px', textAlign: 'center' }}>
      <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text)', marginBottom: 10 }}>
        {unknown ? "Couldn't check your access" : `${appNm} is paused for now`}
      </div>

      <div style={{ fontSize: 13, color: 'var(--text2)', lineHeight: 1.7, marginBottom: 20 }}>
        {unknown
          ? 'We could not reach the server to confirm your access. Please try again.'
          : <>
              {REASON_COPY[reason]?.(trialDays) || 'Your access has ended.'}
              {' '}Please contact support and we'll get you going again.
            </>}
      </div>

      {unknown && (
        <button
          onClick={handleRetry}
          disabled={retrying}
          style={{
            width: '100%', padding: '12px 16px', borderRadius: 10, border: 'none',
            background: 'var(--accent, #2563EB)', color: '#fff', fontSize: 13, fontWeight: 700,
            cursor: retrying ? 'not-allowed' : 'pointer', opacity: retrying ? 0.7 : 1,
          }}
        >
          {retrying ? 'Checking…' : 'Try again'}
        </button>
      )}

      {onClose && (
        <button
          onClick={onClose}
          style={{
            marginTop: 12, background: 'transparent', border: 'none',
            color: 'var(--text3)', fontSize: 12, fontWeight: 600, cursor: 'pointer',
          }}
        >
          Close
        </button>
      )}
    </div>
  )
}
