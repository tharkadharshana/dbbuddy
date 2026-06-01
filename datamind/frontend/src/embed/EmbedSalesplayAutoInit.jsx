/**
 * EmbedSalesplayAutoInit.jsx
 *
 * Salesplay-specific onboarding for the DataMind iframe embed.
 * No manual API token entry — uses the Salesplay session token (aat) passed
 * as a URL param by the Salesplay website.
 *
 * Flow:
 *   consent  → user reads what data will be accessed and clicks Accept
 *   profile  → fetch Salesplay user profile (auto)
 *   account  → create/locate DataMind account (auto)
 *   sync     → first-time data sync with progress bar (auto)
 *   error    → something went wrong, with retry
 */
import React, { useState } from 'react'
import { salesplayCheckUser, salesplayAutoInit, embedGetProviderStatus } from './embedApi'
import { notifyParent } from './EmbedApp'

const SALESPLAY_API = 'https://predev5api.nvision.lk/v2.0/public/app'

// ── Shared styles (mirror EmbedOnboarding) ────────────────────────────────────
const primaryBtn = (disabled) => ({
  width: '100%', padding: '11px', borderRadius: 8, fontSize: 13, fontWeight: 600,
  background: disabled ? 'var(--bg3)' : 'linear-gradient(135deg,#4f8ef7,#7c6af7)',
  color: disabled ? 'var(--text3)' : '#fff',
  border: disabled ? '1px solid var(--border2)' : 'none',
  cursor: disabled ? 'not-allowed' : 'pointer',
  marginTop: 6,
  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
})

function Spin() {
  return (
    <div style={{
      width: 13, height: 13,
      border: '2px solid rgba(255,255,255,0.3)',
      borderTopColor: '#fff',
      borderRadius: '50%',
      animation: 'spin 0.7s linear infinite',
    }} />
  )
}

function Logo() {
  return (
    <div style={{
      width: 40, height: 40, borderRadius: 11,
      background: 'linear-gradient(135deg,#4f8ef7,#a78bfa)',
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      marginBottom: 10,
      boxShadow: '0 4px 16px rgba(79,142,247,0.3)',
    }}>
      <svg width="18" height="18" viewBox="0 0 16 16" fill="none">
        <rect x="2" y="2" width="5" height="5" rx="1" fill="rgba(255,255,255,0.95)" />
        <rect x="9" y="2" width="5" height="5" rx="1" fill="rgba(255,255,255,0.5)" />
        <rect x="2" y="9" width="5" height="5" rx="1" fill="rgba(255,255,255,0.5)" />
        <rect x="9" y="9" width="5" height="5" rx="1" fill="rgba(255,255,255,0.95)" />
      </svg>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────
export default function EmbedSalesplayAutoInit({ context, partnerKey, aatToken, onComplete, onError }) {
  const [phase, setPhase]       = useState('consent')  // 'consent' | 'profile' | 'account' | 'sync' | 'error'
  const [syncMsg, setSyncMsg]   = useState('Syncing your data…')
  const [syncPct, setSyncPct]   = useState(0)
  const [syncRows, setSyncRows] = useState(0)
  const [errorMsg, setErrorMsg] = useState('')
  const [loading, setLoading]   = useState(false)

  const productTitle  = context?.branding?.product_name || 'DataMind AI'
  const providerName  = context?.partner_name || 'Salesplay'

  // Called when the user clicks "Accept & Connect"
  async function handleAccept() {
    setLoading(true)
    await runFlow()
    setLoading(false)
  }

  async function runFlow() {
    setErrorMsg('')

    // Guard: aat is required for the auto-init path
    if (!aatToken) {
      fail('Session token not found. Please access DataMind through the Salesplay backoffice.')
      return
    }

    // ── 1. Fetch Salesplay user profile ───────────────────────────────────────
    setPhase('profile')
    let profile
    try {
      const resp = await fetch(`${SALESPLAY_API}/profile`, {
        headers: { Authorization: `Bearer ${aatToken}` },
      })
      if (!resp.ok) throw new Error(`${resp.status}`)
      const data  = await resp.json()
      const raw   = data?.data || data
      const email = (raw.email || '').trim().toLowerCase()
      const name  = (raw.name || raw.full_name || raw.business_name || email.split('@')[0]).trim()
      if (!email) throw new Error('No email in profile response')
      profile = { email, name }
    } catch {
      fail('Could not connect to Salesplay. Your session may have expired. Please refresh the page.')
      return
    }

    // ── 2. Check DataMind account + credentials ───────────────────────────────
    setPhase('account')
    let checkResult
    try {
      checkResult = await salesplayCheckUser(partnerKey, profile.email)
    } catch {
      fail('Could not reach DataMind servers. Please try again.')
      return
    }

    // ── 3. Create Salesplay API token if needed ───────────────────────────────
    let salesplayApiToken = null
    if (!checkResult.exists || !checkResult.has_credentials) {
      try {
        const tokenResp = await fetch(`${SALESPLAY_API}/integrations/access_tokens`, {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${aatToken}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ name: 'DataMind', expire_enabled: false, expires_at: '' }),
        })
        if (!tokenResp.ok) throw new Error(`${tokenResp.status}`)
        const tokenData   = await tokenResp.json()
        salesplayApiToken = tokenData?.data?.token
        if (!salesplayApiToken) throw new Error('Missing token in response')
      } catch {
        fail('Could not create Salesplay API credentials. Please try again.')
        return
      }
    }

    // ── 4. Auto-init DataMind account ─────────────────────────────────────────
    let initResult
    try {
      initResult = await salesplayAutoInit(
        partnerKey,
        profile.email,
        profile.name,
        salesplayApiToken,
      )
    } catch (e) {
      const detail = e.response?.data?.detail || e.response?.data?.error || e.message || 'Setup failed. Please try again.'
      fail(detail)
      return
    }

    localStorage.setItem('dm_embed_token', initResult.token)

    // ── 5. Sync or go straight to chat ────────────────────────────────────────
    if (initResult.sync === 'started') {
      setPhase('sync')
      notifyParent('dm:onboarding_sync_started')
      pollSync(context.provider_id, initResult.token, profile)
    } else {
      notifyParent('dm:chat_open')
      onComplete(initResult.token, profile)
    }
  }

  function pollSync(connId, token, profile) {
    let attempts   = 0
    let latestRows = 0
    const interval = setInterval(async () => {
      attempts++
      try {
        const r    = await embedGetProviderStatus(connId)
        const prog = r.progress
        if (prog) {
          setSyncMsg(prog.message || 'Syncing…')
          setSyncPct(prog.percent || 0)
          setSyncRows(prog.rows_synced || 0)
          latestRows = prog.rows_synced || latestRows
        }
        if (r.status === 'connected' || r.status === 'active') {
          clearInterval(interval)
          notifyParent('dm:sync_complete', { rows: r.last_sync_rows || latestRows })
          setTimeout(() => onComplete(token, profile), 600)
        } else if (r.status === 'error') {
          clearInterval(interval)
          setTimeout(() => onComplete(token, profile), 1500)
        }
      } catch {
        if (attempts > 90) {
          clearInterval(interval)
          onComplete(token, profile)
        }
      }
    }, 2000)
  }

  function fail(msg) {
    setErrorMsg(msg)
    setPhase('error')
    setLoading(false)
  }

  async function handleRetry() {
    setLoading(true)
    setPhase('consent')
    await runFlow()
    setLoading(false)
  }

  // ── Render ────────────────────────────────────────────────────────────────────
  return (
    <div style={{
      height: '100%', overflowY: 'auto',
      display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center',
      padding: '24px 20px', textAlign: 'center',
    }}>
      <Logo />
      <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text)', marginBottom: 4 }}>
        {productTitle}
      </div>

      {/* ── CONSENT ────────────────────────────────────────────────────────── */}
      {phase === 'consent' && (
        <div style={{ width: '100%', marginTop: 12 }}>
          <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 16, lineHeight: 1.7 }}>
            To get started, DataMind needs access to your {providerName} account data.
          </div>

          <div style={{
            background: 'var(--bg2)', border: '1px solid var(--border)',
            borderRadius: 8, padding: '12px', textAlign: 'left', marginBottom: 16,
          }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)', marginBottom: 8 }}>
              What we'll access
            </div>
            {[
              { icon: '🧾', text: 'Sales receipts and transaction history' },
              { icon: '📦', text: 'Products and inventory data' },
              { icon: '👥', text: 'Customer records' },
            ].map(({ icon, text }, i) => (
              <div key={i} style={{
                display: 'flex', gap: 8, alignItems: 'center',
                padding: '5px 0',
                borderBottom: i < 2 ? '1px solid var(--border)' : 'none',
              }}>
                <span style={{ fontSize: 14, flexShrink: 0 }}>{icon}</span>
                <span style={{ fontSize: 12, color: 'var(--text2)' }}>{text}</span>
              </div>
            ))}
          </div>

          <div style={{
            background: 'var(--bg2)', border: '1px solid var(--border)',
            borderRadius: 8, padding: '12px', textAlign: 'left', marginBottom: 16,
          }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)', marginBottom: 8 }}>
              How we use it
            </div>
            {[
              { icon: '🔒', text: 'Your data is encrypted and stored securely' },
              { icon: '🤖', text: 'Used only to answer your AI queries' },
              { icon: '🚫', text: 'Never shared with third parties' },
            ].map(({ icon, text }, i) => (
              <div key={i} style={{
                display: 'flex', gap: 8, alignItems: 'center',
                padding: '5px 0',
                borderBottom: i < 2 ? '1px solid var(--border)' : 'none',
              }}>
                <span style={{ fontSize: 14, flexShrink: 0 }}>{icon}</span>
                <span style={{ fontSize: 12, color: 'var(--text2)' }}>{text}</span>
              </div>
            ))}
          </div>

          <button onClick={handleAccept} disabled={loading} style={primaryBtn(loading)}>
            {loading ? <><Spin /> Setting up…</> : 'Accept & Connect'}
          </button>

          <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 10, lineHeight: 1.6 }}>
            By continuing, you agree to DataMind's{' '}
            <a href="https://datamind.ai/terms" target="_blank" rel="noopener noreferrer"
              style={{ color: 'var(--blue)', textDecoration: 'none' }}>Terms of Service</a>
            {' '}and{' '}
            <a href="https://datamind.ai/privacy" target="_blank" rel="noopener noreferrer"
              style={{ color: 'var(--blue)', textDecoration: 'none' }}>Privacy Policy</a>.
          </div>
        </div>
      )}

      {/* ── PROFILE / ACCOUNT loading ───────────────────────────────────────── */}
      {(phase === 'profile' || phase === 'account') && (
        <>
          <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 18, marginTop: 10 }}>
            {phase === 'profile' ? 'Connecting to Salesplay…' : 'Setting up your DataMind account…'}
          </div>
          <div style={{ display: 'flex', justifyContent: 'center' }}>
            <div style={{
              width: 20, height: 20,
              border: '2px solid var(--border)',
              borderTopColor: 'var(--blue)',
              borderRadius: '50%',
              animation: 'spin 0.7s linear infinite',
            }} />
          </div>
        </>
      )}

      {/* ── SYNC ───────────────────────────────────────────────────────────── */}
      {phase === 'sync' && (
        <div style={{ width: '100%', marginTop: 14 }}>
          <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 14, minHeight: 18 }}>
            {syncMsg}
          </div>

          <div style={{ height: 4, background: 'var(--bg3)', borderRadius: 2, overflow: 'hidden', marginBottom: 10 }}>
            {syncPct > 0
              ? <div style={{ height: '100%', width: `${syncPct}%`, background: 'var(--blue)', borderRadius: 2, transition: 'width .6s ease' }} />
              : <div style={{ height: '100%', width: '30%', background: 'var(--blue)', borderRadius: 2, animation: 'obSlide 1.4s linear infinite' }} />
            }
          </div>

          <div style={{ display: 'flex', justifyContent: 'center', gap: 14, fontSize: 11, color: 'var(--text3)', marginBottom: 16 }}>
            {syncRows > 0 && <span>{syncRows.toLocaleString()} rows synced</span>}
            {syncPct > 0  && <span>{syncPct}%</span>}
          </div>

          <div style={{ background: 'var(--bg2)', border: '1px solid var(--border)', borderRadius: 8, padding: '12px', textAlign: 'left' }}>
            {[
              { icon: '🔗', text: `Authenticating with ${providerName}` },
              { icon: '📥', text: 'Downloading your receipts, products & customers' },
              { icon: '🧠', text: 'Setting up analytics templates' },
              { icon: '⚡', text: 'Future questions load instantly after this' },
            ].map(({ icon, text }, i) => (
              <div key={i} style={{
                display: 'flex', gap: 8, alignItems: 'center',
                padding: '5px 0',
                borderBottom: i < 3 ? '1px solid var(--border)' : 'none',
              }}>
                <span style={{ fontSize: 14, flexShrink: 0 }}>{icon}</span>
                <span style={{ fontSize: 11, color: 'var(--text2)' }}>{text}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── ERROR ──────────────────────────────────────────────────────────── */}
      {phase === 'error' && (
        <div style={{ width: '100%', marginTop: 10 }}>
          <div style={{ fontSize: 28, marginBottom: 12 }}>⚠️</div>
          <div style={{ fontSize: 13, color: 'var(--text2)', lineHeight: 1.7, maxWidth: 280, margin: '0 auto 20px' }}>
            {errorMsg}
          </div>
          <button onClick={handleRetry} disabled={loading} style={primaryBtn(loading)}>
            {loading ? <><Spin /> Retrying…</> : 'Try Again'}
          </button>
        </div>
      )}
    </div>
  )
}
