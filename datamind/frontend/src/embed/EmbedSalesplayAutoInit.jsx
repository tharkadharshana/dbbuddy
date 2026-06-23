/**
 * EmbedSalesplayAutoInit.jsx
 *
 * Salesplay-specific onboarding for the DataMind iframe embed.
 * No manual API token entry — uses the Salesplay session token (aat) passed
 * as a URL param by the Salesplay website.
 *
 * Flow:
 *   consent  → user reads what they can do with DataMind and clicks Accept
 *   loading  → single backend call handles everything (profile, token, account)
 *   sync     → first-time workspace setup with progress bar (auto)
 *   error    → something went wrong, with retry
 */
import React, { useState, useEffect } from 'react'
import { salesplayOnboard, embedGetProviderStatus, embedGetPlans } from './embedApi'
import { notifyParent } from './EmbedApp'

// ── SalesPlay visual language (mirrors EmbedChat's isSalesplay branch) ───────
const SP = {
  bg:        'linear-gradient(180deg, #F0F4F8 0%, #F7F9FB 100%)',
  card:      '#FFFFFF',
  heading:   '#191C1E',
  text:      '#545F73',
  text3:     '#8B93A7',
  blue:      '#0058BE',
  blueLight: '#D8E2FF',
  blueDark:  '#001A42',
  outline:   '#C2C6D6',
  green:     '#006947',
  shadow:    '0px 4px 20px 0px rgba(84,95,115,0.12)',
}

// Backend stores small "AI credit" values (e.g. 50); the Billing page displays
// these multiplied out as "Tokens" (50 -> 500K). Mirrors PLAN_FEATURES/fmtTok
// in pages/BillingPage.jsx so the embed consent screen matches exactly.
const TDM = 10_000
const fmtTok = (raw) => {
  const n = (raw || 0) * TDM
  if (n >= 1_000_000) return `${parseFloat((n / 1_000_000).toFixed(2))}M`
  if (n >= 1_000)     return `${parseFloat((n / 1_000).toFixed(1))}K`
  return Math.round(n).toLocaleString()
}

const PLAN_FEATURES = {
  Starter: [
    { text: 'Ask Your Data (AI)',        ok: true  },
    { text: 'All Analytics',             ok: true  },
    { text: 'All Reports',               ok: true  },
    { text: 'Forecasting & Anomalies',   ok: false },
    { text: 'Priority Support',          ok: false },
    { text: '1 Month data history',      ok: true  },
  ],
  Growth: [
    { text: 'Ask Your Data (AI)',        ok: true  },
    { text: 'All Analytics',             ok: true  },
    { text: 'All Reports',               ok: true  },
    { text: 'Forecasting & Anomalies',   ok: true  },
    { text: 'Priority Support',          ok: false },
    { text: '3 Months data history',     ok: true  },
  ],
  Pro: [
    { text: 'Ask Your Data (AI)',        ok: true  },
    { text: 'All Analytics',             ok: true  },
    { text: 'All Reports',               ok: true  },
    { text: 'Forecasting & Anomalies',   ok: true  },
    { text: 'Priority Support',          ok: true  },
    { text: 'External API integrations', ok: true  },
    { text: 'Web widget',                ok: true  },
    { text: '1 Year data history',       ok: true  },
  ],
}

// ── Shared styles ──────────────────────────────────────────────────────────
const primaryBtn = (disabled, sp) => ({
  width: '100%', padding: sp ? '14px' : '12px', borderRadius: sp ? 9999 : 8, fontSize: 13, fontWeight: 700,
  background: disabled
    ? (sp ? '#CBD5E1' : 'var(--bg3)')
    : (sp ? SP.blue : 'linear-gradient(135deg,#4f8ef7,#7c6af7)'),
  color: disabled ? (sp ? '#fff' : 'var(--text3)') : '#fff',
  border: disabled ? (sp ? 'none' : '1px solid var(--border2)') : 'none',
  cursor: disabled ? 'not-allowed' : 'pointer',
  marginTop: 6,
  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
  boxShadow: sp && !disabled ? '0 4px 12px rgba(0,88,190,0.35)' : 'none',
  transition: 'background .15s, box-shadow .15s',
})

const cardStyle = (sp) => ({
  background: sp ? SP.card : 'var(--bg2)',
  border: sp ? 'none' : '1px solid var(--border)',
  borderRadius: sp ? 14 : 8,
  padding: '14px',
  textAlign: 'left',
  marginBottom: 16,
  boxShadow: sp ? SP.shadow : 'none',
})

const rowStyle = (sp, isLast) => ({
  display: 'flex', gap: 10, alignItems: 'center',
  padding: '7px 0',
  borderBottom: isLast ? 'none' : `1px solid ${sp ? 'rgba(15,23,42,0.06)' : 'var(--border)'}`,
})

function Spin({ sp }) {
  return (
    <div style={{
      width: 13, height: 13,
      border: `2px solid ${sp ? 'rgba(255,255,255,0.3)' : 'rgba(255,255,255,0.3)'}`,
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

// Translates raw backend sync-progress messages (which name specific data
// tables like "Categories" / "Receipts" / "Customers") into generic,
// professional copy — avoids any "we're extracting your data" framing.
function friendlySyncMsg(raw, providerName) {
  const m = (raw || '').toLowerCase()
  if (m.includes('sync complete') || m.includes('✅')) return 'All set!'
  if (m.includes('row limit'))   return 'Finishing up…'
  if (m.includes('refreshing'))  return 'Organizing your insights…'
  if (m.includes('customers') || m.includes('receipts')) return 'Gathering your business information…'
  if (m.includes('shops') || m.includes('categories') || m.includes('payment types') || m.includes('products')) return 'Setting up your workspace…'
  if (m.includes('sync started')) return `Connecting to ${providerName}…`
  return 'Setting up your workspace…'
}

function BetaBadge({ sp, large }) {
  if (sp && large) {
    return (
      <div style={{
        display: 'inline-flex', alignItems: 'center', gap: 6,
        padding: '5px 14px', background: SP.blueLight, color: SP.blueDark,
        borderRadius: 9999, fontSize: 12, fontWeight: 700, letterSpacing: '.04em',
      }}>
        <span style={{ fontSize: 13 }}>⚡</span> BETA
      </div>
    )
  }
  return (
    <span style={{
      fontSize: 9, fontWeight: 700, letterSpacing: '.06em', textTransform: 'uppercase',
      color: sp ? SP.blue : 'var(--blue)',
      background: sp ? 'rgba(0,88,190,0.1)' : 'rgba(79,142,247,0.12)',
      borderRadius: 6, padding: '2px 6px', marginLeft: 6,
    }}>
      Beta
    </span>
  )
}

// ── Main component ────────────────────────────────────────────────────────────
export default function EmbedSalesplayAutoInit({ context, partnerKey, aatToken, onComplete, onError, onClose }) {
  const sp = context?.provider_id === 'salesplay'

  const [phase, setPhase]       = useState('consent')  // 'consent' | 'profile' | 'account' | 'sync' | 'error'
  const [syncMsg, setSyncMsg]   = useState('Setting up your workspace…')
  const [syncPct, setSyncPct]   = useState(0)
  const [syncRows, setSyncRows] = useState(0)
  const [errorMsg, setErrorMsg] = useState('')
  const [loading, setLoading]   = useState(false)
  const [plans, setPlans]       = useState([])
  const [selectedPlan, setSelectedPlan] = useState(null)

  // Load subscription plans for the consent screen pricing section.
  // Non-fatal — if this fails, the consent screen still renders without pricing.
  useEffect(() => {
    embedGetPlans()
      .then(r => setPlans(Array.isArray(r?.plans) ? r.plans : []))
      .catch(() => setPlans([]))
  }, [])

  // Default-select the first plan once plans load (drives the highlighted card).
  useEffect(() => {
    if (plans.length > 0 && selectedPlan === null) {
      setSelectedPlan(plans[0]?.id ?? plans[0]?.name)
    }
  }, [plans, selectedPlan])

  // Read aat directly from URL params — more reliable than the prop which travels
  // through React state and can be empty on the first render in some React versions.
  const aat = aatToken || new URLSearchParams(window.location.search).get('aat') || ''

  const productTitle  = context?.branding?.product_name || import.meta.env.VITE_APP_NAME || 'SalesPlay AI'
  const providerName  = context?.partner_name || 'Salesplay'

  // Called when the user clicks "Accept & Connect"
  async function handleAccept() {
    setLoading(true)
    await runFlow()
    setLoading(false)
  }

  async function runFlow() {
    setErrorMsg('')

    if (!aat) {
      fail('Session token not found. Please access DataMind through the Salesplay backoffice.')
      return
    }

    // Single backend call — profile fetch, token creation, account setup all happen server-side
    setPhase('loading')
    let result
    try {
      result = await salesplayOnboard(partnerKey, aat)
    } catch (e) {
      const detail = e.response?.data?.detail || e.response?.data?.error || e.message || 'Setup failed. Please try again.'
      fail(detail)
      return
    }

    localStorage.setItem('dm_embed_token', result.token)
    if (result.user?.email) localStorage.setItem('dm_sp_email', result.user.email)

    if (result.sync === 'started') {
      setPhase('sync')
      notifyParent('dm:onboarding_sync_started')
      pollSync(context.provider_id, result.token, result.user)
    } else {
      notifyParent('dm:chat_open')
      onComplete(result.token, result.user)
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
          setSyncMsg(friendlySyncMsg(prog.message, providerName))
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
  const canClose = phase === 'consent' || phase === 'error'

  return (
    <div style={{
      height: '100%', overflowY: 'auto',
      display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: phase === 'consent' ? 'flex-start' : 'center',
      padding: '24px 20px', textAlign: 'center',
      background: sp ? SP.bg : undefined,
      position: 'relative',
    }}>
      {canClose && onClose && (
        <button onClick={onClose} style={{
          position: 'absolute', top: 12, right: 12,
          background: 'none', border: 'none', cursor: 'pointer',
          color: sp ? SP.text3 : 'var(--text3)',
          fontSize: 18, lineHeight: 1, padding: 4, borderRadius: 6,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>✕</button>
      )}
      {!(sp && phase === 'consent') && (
        <>
          <Logo />
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 15, fontWeight: 700, color: sp ? SP.heading : 'var(--text)', marginBottom: 4 }}>
            {productTitle}
            <BetaBadge sp={sp} />
          </div>
        </>
      )}

      {/* ── CONSENT ────────────────────────────────────────────────────────── */}
      {phase === 'consent' && (sp ? (
        <div style={{ width: '100%' }}>
          <BetaBadge sp={sp} large />
          <h2 style={{
            fontFamily: "'Manrope', 'Plus Jakarta Sans', sans-serif",
            fontSize: 26, lineHeight: '34px', letterSpacing: '-0.02em', fontWeight: 800,
            color: SP.heading, margin: '14px 0 10px',
          }}>
            Connect Your Data
          </h2>
          <p style={{ fontSize: 14, lineHeight: '22px', color: SP.text, marginBottom: 24 }}>
            To get started, connect your {providerName} account — it only takes a few seconds.
          </p>

          <div style={{ textAlign: 'left', marginBottom: 20 }}>
            <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '.08em', textTransform: 'uppercase', color: SP.text, marginBottom: 10, padding: '0 2px' }}>
              What you can do with DataMind
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {[
                { icon: '💬', text: 'Ask questions in plain English — no spreadsheets, no formulas' },
                { icon: '📊', text: 'Get instant answers about sales, top products & customers' },
                { icon: '🔮', text: 'Spot trends and unusual patterns before they become problems' },
                { icon: '⚡', text: `Insights in seconds, right inside ${providerName}` },
              ].map(({ icon, text }, i) => (
                <div key={i} style={{
                  display: 'flex', alignItems: 'center', gap: 12,
                  background: SP.card, padding: '12px 14px', borderRadius: 14,
                  boxShadow: SP.shadow, border: '1px solid rgba(255,255,255,0.6)',
                }}>
                  <span style={{ fontSize: 20, flexShrink: 0 }}>{icon}</span>
                  <span style={{ flex: 1, fontSize: 12, fontWeight: 500, color: SP.heading }}>{text}</span>
                  <span style={{ fontSize: 18, color: SP.text, flexShrink: 0 }}>›</span>
                </div>
              ))}
            </div>
          </div>

          {/* BILLING HIDDEN — subscription plan selection commented out
          {plans.length > 0 && (
            <div style={{
              textAlign: 'left', background: SP.card, padding: 16, borderRadius: 16,
              boxShadow: SP.shadow, border: '1px solid rgba(255,255,255,0.8)', marginBottom: 20,
            }}>
              <div style={{ fontSize: 14, fontWeight: 700, color: SP.heading, marginBottom: 2 }}>
                Subscription plans
              </div>
              <div style={{ fontSize: 11, color: SP.blue, marginBottom: 14 }}>
                Start with a free {plans[0]?.trial_days || 14}-day trial — no card required
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {plans.map((p, i) => {
                  const pid = p.id ?? p.name
                  const isSelected = selectedPlan === pid
                  const features = PLAN_FEATURES[p.name] || []
                  return (
                    <label key={pid} onClick={() => setSelectedPlan(pid)} style={{
                      position: 'relative', display: 'block',
                      padding: '12px 14px', borderRadius: 12, cursor: 'pointer',
                      border: isSelected ? `2px solid ${SP.blue}` : `1px solid ${SP.outline}`,
                      background: isSelected ? 'rgba(0,88,190,0.05)' : 'transparent',
                      transition: 'border-color .15s, background .15s',
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                        <div style={{ display: 'flex', flexDirection: 'column' }}>
                          <span style={{ fontSize: 12, fontWeight: 700, color: SP.heading }}>{p.name}</span>
                          <span style={{ fontSize: 11, color: SP.text, marginTop: 2 }}>{fmtTok(p.tokens_limit)} Tokens / month</span>
                        </div>
                        <span style={{ fontSize: 14, fontWeight: 700, color: SP.heading }}>
                          ${Number(p.price_usd).toFixed(2)}/mo
                        </span>
                        {i === 0 && (
                          <span style={{
                            position: 'absolute', top: -9, right: 14,
                            background: SP.blue, color: '#fff', fontSize: 9, fontWeight: 700,
                            padding: '2px 8px', borderRadius: 9999, letterSpacing: '.04em',
                          }}>
                            POPULAR
                          </span>
                        )}
                      </div>
                      {isSelected && features.length > 0 && (
                        <div style={{ marginTop: 10, paddingTop: 10, borderTop: `1px solid ${SP.outline}`, display: 'flex', flexDirection: 'column', gap: 5 }}>
                          {features.map(({ text, ok }) => (
                            <div key={text} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                              <span style={{ fontSize: 11, color: ok ? SP.green : SP.text3, flexShrink: 0 }}>{ok ? '✓' : '–'}</span>
                              <span style={{ fontSize: 11, color: ok ? SP.text : SP.text3 }}>{text}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </label>
                  )
                })}
              </div>
            </div>
          )}
          */}

          <button onClick={handleAccept} disabled={loading} style={primaryBtn(loading, sp)}>
            {loading ? <><Spin sp={sp} /> Setting up…</> : 'Start your 14-day trial'}
          </button>

          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, marginTop: 14 }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: SP.green, flexShrink: 0 }} />
            <span style={{ fontSize: 11, color: SP.text }}>Real-time data · Powered by DataMind</span>
          </div>

          <div style={{ fontSize: 11, color: SP.text, marginTop: 10, lineHeight: 1.6 }}>
            By continuing, you agree to DataMind's{' '}
            <a href="https://datamind.ai/terms" target="_blank" rel="noopener noreferrer"
              style={{ color: SP.blue, textDecoration: 'underline' }}>Terms of Service</a>
            {' '}and{' '}
            <a href="https://datamind.ai/privacy" target="_blank" rel="noopener noreferrer"
              style={{ color: SP.blue, textDecoration: 'underline' }}>Privacy Policy</a>.
          </div>
        </div>
      ) : (
        <div style={{ width: '100%', marginTop: 12 }}>
          <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 16, lineHeight: 1.7 }}>
            To get started, connect your {providerName} account — it only takes a few seconds.
          </div>

          <div style={cardStyle(sp)}>
            <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text)', marginBottom: 8 }}>
              What you can do with DataMind
            </div>
            {[
              { icon: '💬', text: 'Ask questions in plain English — no spreadsheets, no formulas' },
              { icon: '📊', text: 'Get instant answers about sales, top products & customers' },
              { icon: '🔮', text: 'Spot trends and unusual patterns before they become problems' },
              { icon: '⚡', text: `Insights in seconds, right inside ${providerName}` },
            ].map(({ icon, text }, i, arr) => (
              <div key={i} style={rowStyle(sp, i === arr.length - 1)}>
                <span style={{ fontSize: 14, flexShrink: 0 }}>{icon}</span>
                <span style={{ fontSize: 12, color: 'var(--text2)' }}>{text}</span>
              </div>
            ))}
          </div>

          {plans.length > 0 && (
            <div style={cardStyle(sp)}>
              <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text)', marginBottom: 4 }}>
                Subscription plans
              </div>
              <div style={{ fontSize: 11, color: 'var(--blue)', marginBottom: 8 }}>
                Start with a free {plans[0]?.trial_days || 14}-day trial — no card required
              </div>
              {plans.map((p, i) => (
                <div key={p.id || p.name} style={{ ...rowStyle(sp, i === plans.length - 1), justifyContent: 'space-between' }}>
                  <span style={{ fontSize: 12, color: 'var(--text2)' }}>{p.name}</span>
                  <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)' }}>
                    ${Number(p.price_usd).toFixed(2)}/mo
                    <span style={{ fontWeight: 400, color: 'var(--text3)', marginLeft: 6 }}>
                      · {fmtTok(p.tokens_limit)} Tokens/mo
                    </span>
                  </span>
                </div>
              ))}
            </div>
          )}

          <button onClick={handleAccept} disabled={loading} style={primaryBtn(loading, sp)}>
            {loading ? <><Spin sp={sp} /> Setting up…</> : 'Start your 14-day trial with Starter'}
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
      ))}

      {/* ── LOADING (single backend call covers everything) ─────────────────── */}
      {phase === 'loading' && (
        <>
          <div style={{ fontSize: 13, color: sp ? SP.text : 'var(--text2)', marginBottom: 18, marginTop: 10 }}>
            Setting up your account…
          </div>
          <div style={{ display: 'flex', justifyContent: 'center' }}>
            <div style={{
              width: 20, height: 20,
              border: `2px solid ${sp ? '#E2E8F0' : 'var(--border)'}`,
              borderTopColor: sp ? SP.blue : 'var(--blue)',
              borderRadius: '50%',
              animation: 'spin 0.7s linear infinite',
            }} />
          </div>
        </>
      )}

      {/* ── SYNC ───────────────────────────────────────────────────────────── */}
      {phase === 'sync' && (
        <div style={{ width: '100%', marginTop: 14 }}>
          <div style={{ fontSize: 13, color: sp ? SP.text : 'var(--text2)', marginBottom: 14, minHeight: 18 }}>
            {syncMsg}
          </div>

          <div style={{ height: 4, background: sp ? '#E2E8F0' : 'var(--bg3)', borderRadius: 2, overflow: 'hidden', marginBottom: 10 }}>
            {syncPct > 0
              ? <div style={{ height: '100%', width: `${syncPct}%`, background: sp ? SP.blue : 'var(--blue)', borderRadius: 2, transition: 'width .6s ease' }} />
              : <div style={{ height: '100%', width: '30%', background: sp ? SP.blue : 'var(--blue)', borderRadius: 2, animation: 'obSlide 1.4s linear infinite' }} />
            }
          </div>

          <div style={{ display: 'flex', justifyContent: 'center', gap: 14, fontSize: 11, color: sp ? SP.text3 : 'var(--text3)', marginBottom: 16 }}>
            {syncRows > 0 && <span>{syncRows.toLocaleString()} rows synced</span>}
            {syncPct > 0  && <span>{syncPct}%</span>}
          </div>

          <div style={cardStyle(sp)}>
            {[
              { icon: '🔗', text: `Connecting to ${providerName}` },
              { icon: '⚙️', text: 'Setting up your workspace' },
              { icon: '🧠', text: 'Preparing your analytics' },
              { icon: '⚡', text: 'This only happens once — future questions load instantly' },
            ].map(({ icon, text }, i, arr) => (
              <div key={i} style={rowStyle(sp, i === arr.length - 1)}>
                <span style={{ fontSize: 14, flexShrink: 0 }}>{icon}</span>
                <span style={{ fontSize: 11, color: sp ? SP.text : 'var(--text2)' }}>{text}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── ERROR ──────────────────────────────────────────────────────────── */}
      {phase === 'error' && (
        <div style={{ width: '100%', marginTop: 10 }}>
          <div style={{ fontSize: 28, marginBottom: 12 }}>⚠️</div>
          <div style={{ fontSize: 13, color: sp ? SP.text : 'var(--text2)', lineHeight: 1.7, maxWidth: 280, margin: '0 auto 20px' }}>
            {errorMsg}
          </div>
          <button onClick={handleRetry} disabled={loading} style={primaryBtn(loading, sp)}>
            {loading ? <><Spin sp={sp} /> Retrying…</> : 'Try Again'}
          </button>
        </div>
      )}
    </div>
  )
}
