/**
 * EmbedOnboarding.jsx — 4-step first-time setup wizard for iframe users.
 *
 * Step 0: Validate provider API token     (POST /embed/validate-token)
 * Step 1: Create / log in to DataMind account
 * Step 2: Choose a plan (or continue free trial)
 * Step 3: Connect + sync                  (POST /embed/init → polls status)
 */
import React, { useState, useEffect } from 'react'
import {
  embedValidateToken, embedInit, embedLogin, embedConnectProvider,
  embedGetProviderStatus, embedGetPlans, embedSubscribePlan,
} from './embedApi'
import { notifyParent } from './EmbedApp'

// ── Shared styles ─────────────────────────────────────────────────────────────
const inp = {
  width:'100%', padding:'10px 12px', borderRadius:8, fontSize:13,
  background:'var(--bg3)', border:'1px solid var(--border)',
  color:'var(--text)', outline:'none', marginBottom:10,
}

const primaryBtn = (disabled) => ({
  width:'100%', padding:'11px', borderRadius:8, fontSize:13, fontWeight:600,
  background: disabled ? 'var(--bg3)' : 'linear-gradient(135deg,#4f8ef7,#7c6af7)',
  color: disabled ? 'var(--text3)' : '#fff',
  border: disabled ? '1px solid var(--border2)' : 'none',
  cursor: disabled ? 'not-allowed' : 'pointer',
  marginTop:6,
  display:'flex', alignItems:'center', justifyContent:'center', gap:6,
})

const ghostBtn = {
  background:'none', border:'none', color:'var(--text3)',
  fontSize:12, cursor:'pointer', marginTop:10, width:'100%',
}

// ── Step progress bar (4 steps) ───────────────────────────────────────────────
function StepBar({ step }) {
  return (
    <div style={{ display:'flex', gap:6, marginBottom:20 }}>
      {[0,1,2,3].map(i => (
        <div key={i} style={{
          flex:1, height:3, borderRadius:2, transition:'background .2s',
          background: i <= step ? 'var(--blue)' : 'var(--bg3)',
        }} />
      ))}
    </div>
  )
}

// ── Status pill ───────────────────────────────────────────────────────────────
function StatusPill({ ok, message }) {
  return (
    <div style={{
      padding:'9px 12px', borderRadius:8, fontSize:12, marginBottom:10,
      background: ok ? 'var(--green-dim)' : 'var(--red-dim)',
      color: ok ? 'var(--green)' : 'var(--red)',
      border: `1px solid ${ok ? 'rgba(52,209,122,0.25)' : 'rgba(240,80,80,0.25)'}`,
    }}>
      {ok ? '✓' : '✗'} {message}
    </div>
  )
}

// ── Spinner ───────────────────────────────────────────────────────────────────
function Spin() {
  return <div style={{ width:13, height:13, border:'2px solid rgba(255,255,255,0.3)', borderTopColor:'#fff', borderRadius:'50%', animation:'spin 0.7s linear infinite' }} />
}

// ── Main component ────────────────────────────────────────────────────────────
export default function EmbedOnboarding({ context, partnerKey, onComplete }) {
  const [step, setStep]               = useState(0)

  // Step 0
  const [apiToken, setApiToken]       = useState('')
  const [validating, setValidating]   = useState(false)
  const [tokenResult, setTokenResult] = useState(null)

  // Step 1
  const [mode, setMode]               = useState('register')
  const [name, setName]               = useState('')
  const [email, setEmail]             = useState('')
  const [password, setPassword]       = useState('')

  // Step 2 — plan selection
  const [plans, setPlans]             = useState([])
  const [selectedPlanId, setSelectedPlanId] = useState(null)

  // Step 3 — connect + sync
  const [connecting, setConnecting]   = useState(false)
  const [syncMsg, setSyncMsg]         = useState('Connecting your account…')
  const [syncPct, setSyncPct]         = useState(0)
  const [syncRows, setSyncRows]       = useState(0)
  const [error, setError]             = useState('')

  const providerName = context?.partner_name || 'Salesplay'
  const productTitle = context?.branding?.product_name || import.meta.env.VITE_APP_NAME || 'SalesPlay AI'

  // Fetch plans when entering step 2
  useEffect(() => {
    if (step === 2 && plans.length === 0) {
      embedGetPlans()
        .then(d => setPlans(d.plans || []))
        .catch(() => {})
    }
  }, [step])

  // ── Step 0: validate API token ───────────────────────────────────────────────
  async function handleValidateToken() {
    if (!apiToken.trim()) return
    setValidating(true); setTokenResult(null)
    try {
      const r = await embedValidateToken(partnerKey, apiToken.trim())
      setTokenResult(r)
    } catch (e) {
      setTokenResult({ ok: false, error: e.response?.data?.error || e.message })
    } finally { setValidating(false) }
  }

  // ── Step 3: create account + optionally subscribe + connect + sync ───────────
  async function handleConnect() {
    setConnecting(true); setError('')
    setSyncMsg('Creating your account…')

    let token
    try {
      if (mode === 'register') {
        const result = await embedInit({
          partner_key: partnerKey,
          api_token:   apiToken.trim(),
          name:        name.trim(),
          email:       email.trim().toLowerCase(),
          password,
        })
        token = result.token
        localStorage.setItem('dm_embed_token', token)
      } else {
        setSyncMsg('Logging in…')
        const loginResult = await embedLogin(email.trim().toLowerCase(), password)
        token = loginResult.token
        localStorage.setItem('dm_embed_token', token)
        setSyncMsg(`Connecting ${providerName}…`)
        await embedConnectProvider(context.provider_id, { api_token: apiToken.trim() }, token)
      }

      // Subscribe to selected plan if the user chose one
      if (selectedPlanId) {
        setSyncMsg('Activating your plan…')
        try { await embedSubscribePlan(selectedPlanId) } catch { /* non-fatal */ }
      }

      setSyncMsg('Syncing your data…')
      notifyParent('dm:onboarding_sync_started')
      pollSync(context.provider_id, token)
    } catch (e) {
      setError(e.response?.data?.error || e.response?.data?.detail || e.message || 'Connection failed. Please try again.')
      setConnecting(false)
    }
  }

  function pollSync(connId, token) {
    let attempts = 0
    const interval = setInterval(async () => {
      attempts++
      try {
        const r = await embedGetProviderStatus(connId)
        const prog = r.progress
        if (prog) {
          setSyncMsg(prog.message || 'Syncing…')
          setSyncPct(prog.percent || 0)
          setSyncRows(prog.rows_synced || 0)
        }
        if (r.status === 'connected' || r.status === 'active') {
          clearInterval(interval)
          notifyParent('dm:sync_complete', { rows: r.last_sync_rows || syncRows })
          setTimeout(() => onComplete(token, { email: email.trim().toLowerCase(), name }), 600)
        } else if (r.status === 'error') {
          clearInterval(interval)
          setError(`Sync failed: ${r.last_error || 'Unknown error'}. You can still use chat — data may be incomplete.`)
          setTimeout(() => onComplete(token, { email: email.trim().toLowerCase(), name }), 1500)
        }
      } catch {
        if (attempts > 90) {
          clearInterval(interval)
          onComplete(token, { email: email.trim().toLowerCase(), name })
        }
      }
    }, 2000)
  }

  // ── Render ───────────────────────────────────────────────────────────────────
  return (
    <div style={{ height:'100%', overflowY:'auto', padding:'20px 16px', display:'flex', flexDirection:'column' }}>

      {/* Header */}
      <div style={{ textAlign:'center', marginBottom:20 }}>
        <div style={{ width:40, height:40, borderRadius:11, background:'linear-gradient(135deg,#4f8ef7,#a78bfa)', display:'inline-flex', alignItems:'center', justifyContent:'center', marginBottom:10, boxShadow:'0 4px 16px rgba(79,142,247,0.3)' }}>
          <svg width="18" height="18" viewBox="0 0 16 16" fill="none">
            <rect x="2" y="2" width="5" height="5" rx="1" fill="rgba(255,255,255,0.95)"/>
            <rect x="9" y="2" width="5" height="5" rx="1" fill="rgba(255,255,255,0.5)"/>
            <rect x="2" y="9" width="5" height="5" rx="1" fill="rgba(255,255,255,0.5)"/>
            <rect x="9" y="9" width="5" height="5" rx="1" fill="rgba(255,255,255,0.95)"/>
          </svg>
        </div>
        <div style={{ fontSize:15, fontWeight:700, color:'var(--text)' }}>{productTitle}</div>
        <div style={{ fontSize:12, color:'var(--text3)', marginTop:2 }}>Ask your {providerName} data anything</div>
      </div>

      <StepBar step={step} />

      {/* ── STEP 0: Provider API token ───────────────────────────────────────── */}
      {step === 0 && (
        <div>
          <div style={{ fontSize:14, fontWeight:600, color:'var(--text)', marginBottom:6 }}>
            Connect your {providerName} account
          </div>
          <div style={{ fontSize:12, color:'var(--text2)', marginBottom:14, lineHeight:1.6 }}>
            Enter your {providerName} API Access Token. Find it in {providerName} Backoffice → Integrations → Access Token.
          </div>

          <input
            type="password"
            placeholder="eyJh..."
            value={apiToken}
            onChange={e => { setApiToken(e.target.value); setTokenResult(null) }}
            onKeyDown={e => e.key === 'Enter' && handleValidateToken()}
            style={{ ...inp, fontFamily:'var(--mono)' }}
          />

          {tokenResult && (
            <StatusPill
              ok={tokenResult.ok}
              message={tokenResult.ok
                ? `Connected to ${tokenResult.details?.merchant_name || providerName}`
                : tokenResult.error}
            />
          )}

          <button
            onClick={handleValidateToken}
            disabled={validating || !apiToken.trim()}
            style={primaryBtn(validating || !apiToken.trim())}
          >
            {validating ? <><Spin /> Validating…</> : 'Verify API Token'}
          </button>

          {tokenResult?.ok && (
            <button onClick={() => setStep(1)} style={{ ...primaryBtn(false), marginTop:8, background:'var(--bg3)', color:'var(--text2)', border:'1px solid var(--border)' }}>
              Continue →
            </button>
          )}
        </div>
      )}

      {/* ── STEP 1: Account ──────────────────────────────────────────────────── */}
      {step === 1 && (
        <div>
          <div style={{ fontSize:14, fontWeight:600, color:'var(--text)', marginBottom:6 }}>
            {mode === 'register' ? 'Create your DataMind account' : 'Log in to DataMind'}
          </div>
          <div style={{ fontSize:12, color:'var(--text2)', marginBottom:14, lineHeight:1.6 }}>
            {mode === 'register'
              ? 'Your account gives you full access at datamind.ai — analytics, forecasting, reports and more.'
              : 'Use your existing DataMind credentials.'}
          </div>

          {mode === 'register' && (
            <input type="text" placeholder="Your name" value={name}
              onChange={e => setName(e.target.value)} style={inp} />
          )}
          <input type="email" placeholder="Email address" value={email}
            onChange={e => setEmail(e.target.value)} style={inp} />
          <input
            type="password" placeholder="Password (min 8 chars)" value={password}
            onChange={e => setPassword(e.target.value)}
            onKeyDown={e => {
              const ok = email.trim() && password.length >= 1 && (mode === 'login' || name.trim())
              if (e.key === 'Enter' && ok) setStep(2)
            }}
            style={inp}
          />

          <button
            onClick={() => setStep(2)}
            disabled={!email.trim() || !password || (mode === 'register' && !name.trim())}
            style={primaryBtn(!email.trim() || !password || (mode === 'register' && !name.trim()))}
          >
            Continue →
          </button>

          <div style={{ textAlign:'center', marginTop:12, fontSize:12, color:'var(--text3)' }}>
            {mode === 'register'
              ? <>Already have an account?{' '}
                  <button onClick={() => setMode('login')} style={{ background:'none', border:'none', color:'var(--blue)', cursor:'pointer', fontSize:12 }}>Log in</button>
                </>
              : <>No account yet?{' '}
                  <button onClick={() => setMode('register')} style={{ background:'none', border:'none', color:'var(--blue)', cursor:'pointer', fontSize:12 }}>Sign up free</button>
                </>
            }
          </div>
          <button onClick={() => setStep(0)} style={ghostBtn}>← Back</button>
        </div>
      )}

      {/* BILLING HIDDEN — plan selection step commented out
      {step === 2 && (
        <div>
          <div style={{
            textAlign:'center', marginBottom:16,
            background:'var(--green-dim)', color:'var(--green)',
            border:'1px solid rgba(52,209,122,0.25)',
            borderRadius:8, padding:'8px 12px', fontSize:12, fontWeight:600,
          }}>
            ✓ Your 14-day free trial has started
          </div>
          <div style={{ fontSize:15, fontWeight:700, color:'var(--text)', textAlign:'center', marginBottom:4 }}>
            Choose a plan
          </div>
          <div style={{ fontSize:12, color:'var(--text3)', textAlign:'center', marginBottom:16 }}>
            Lock in your plan now or continue exploring with your trial.
          </div>
          <div style={{ display:'flex', flexDirection:'column', gap:8, marginBottom:12 }}>
            {plans.length === 0
              ? [
                  { id: 1, name: 'Starter', tokens_limit: 50,  price_cents: 500  },
                  { id: 2, name: 'Growth',  tokens_limit: 125, price_cents: 1000 },
                  { id: 3, name: 'Pro',     tokens_limit: 500, price_cents: 2500 },
                ].map(plan => (
                  <PlanCard key={plan.id} plan={plan} selected={selectedPlanId === plan.id} onSelect={() => setSelectedPlanId(plan.id)} />
                ))
              : plans.map(plan => (
                  <PlanCard key={plan.id} plan={plan} selected={selectedPlanId === plan.id} onSelect={() => setSelectedPlanId(plan.id)} />
                ))
            }
          </div>
          {selectedPlanId && (
            <button onClick={() => setStep(3)} style={primaryBtn(false)}>
              Continue with selected plan →
            </button>
          )}
          <button
            onClick={() => { setSelectedPlanId(null); setStep(3) }}
            style={{
              width:'100%', padding:'11px', borderRadius:8, fontSize:13,
              background:'none', border:'1px solid var(--border)',
              color:'var(--text2)', cursor:'pointer', marginTop: selectedPlanId ? 8 : 6,
            }}
          >
            Continue with free trial →
          </button>
          <button onClick={() => setStep(1)} style={ghostBtn}>← Back</button>
        </div>
      )}
      */}

      {/* ── STEP 3: Connect + sync ───────────────────────────────────────────── */}
      {step === 3 && (
        <div>
          <div style={{ fontSize:14, fontWeight:600, color:'var(--text)', marginBottom:6 }}>
            {connecting ? 'Setting up your account…' : 'Ready to connect'}
          </div>

          {!connecting && !error && (
            <>
              <div style={{ fontSize:12, color:'var(--text2)', marginBottom:14, lineHeight:1.6 }}>
                DataMind will sync your {providerName} receipts, products, and customers.
                This takes 1–3 minutes for most accounts.
              </div>

              <div style={{ background:'var(--bg2)', border:'1px solid var(--border)', borderRadius:8, padding:'12px', marginBottom:16 }}>
                {[
                  ['Account',  mode === 'register' ? `${name} (${email})` : email],
                  ['Data source', providerName],
                  ['Plan', selectedPlanId
                    ? (plans.find(p => p.id === selectedPlanId)?.name || 'Selected plan')
                    : '14-day free trial'],
                ].map(([k, v]) => (
                  <div key={k} style={{ display:'flex', justifyContent:'space-between', padding:'5px 0', borderBottom:'1px solid var(--border)', fontSize:12 }}>
                    <span style={{ color:'var(--text3)' }}>{k}</span>
                    <span style={{ color:'var(--text)', fontWeight:500 }}>{v}</span>
                  </div>
                ))}
              </div>

              <button onClick={handleConnect} style={primaryBtn(false)}>
                Connect & Start Sync →
              </button>
              <button onClick={() => setStep(2)} style={ghostBtn}>← Back</button>
            </>
          )}

          {connecting && (
            <div style={{ textAlign:'center' }}>
              <div style={{ fontSize:12, color:'var(--text2)', marginBottom:14, minHeight:18 }}>{syncMsg}</div>

              <div style={{ height:4, background:'var(--bg3)', borderRadius:2, overflow:'hidden', marginBottom:10 }}>
                {syncPct > 0
                  ? <div style={{ height:'100%', width:`${syncPct}%`, background:'var(--blue)', borderRadius:2, transition:'width .6s ease' }} />
                  : <div style={{ height:'100%', width:'30%', background:'var(--blue)', borderRadius:2, animation:'obSlide 1.4s linear infinite' }} />
                }
              </div>

              <div style={{ display:'flex', justifyContent:'center', gap:14, fontSize:11, color:'var(--text3)', marginBottom:16 }}>
                {syncRows > 0 && <span>{syncRows.toLocaleString()} rows synced</span>}
                {syncPct > 0  && <span>{syncPct}%</span>}
              </div>

              <div style={{ background:'var(--bg2)', border:'1px solid var(--border)', borderRadius:8, padding:'12px', textAlign:'left' }}>
                {[
                  { icon:'🔗', text:`Authenticating with ${providerName}` },
                  { icon:'📥', text:'Downloading your receipts, products & customers' },
                  { icon:'🧠', text:'Setting up analytics templates' },
                  { icon:'⚡', text:'Future questions load instantly after this' },
                ].map(({ icon, text }, i) => (
                  <div key={i} style={{ display:'flex', gap:8, alignItems:'center', padding:'5px 0', borderBottom: i < 3 ? '1px solid var(--border)' : 'none' }}>
                    <span style={{ fontSize:14, flexShrink:0 }}>{icon}</span>
                    <span style={{ fontSize:11, color:'var(--text2)' }}>{text}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {error && !connecting && (
            <>
              <StatusPill ok={false} message={error} />
              <button onClick={handleConnect} style={primaryBtn(false)}>Retry</button>
              <button onClick={() => { setError(''); setConnecting(false) }} style={ghostBtn}>← Back</button>
            </>
          )}
        </div>
      )}
    </div>
  )
}

// ── Plan card subcomponent ─────────────────────────────────────────────────────
const _TDM = 10_000
function _fmtTok(raw) {
  const n = (raw || 0) * _TDM
  if (n >= 1_000_000) return `${parseFloat((n / 1_000_000).toFixed(2))}M`
  if (n >= 1_000)     return `${parseFloat((n / 1_000).toFixed(1))}K`
  return Math.round(n).toLocaleString()
}

function PlanCard({ plan, selected, onSelect }) {
  const price = `$${(plan.price_cents / 100).toFixed(0)}/mo`
  const tokens = _fmtTok(plan.tokens_limit)
  return (
    <div
      onClick={onSelect}
      style={{
        display:'flex', alignItems:'center', justifyContent:'space-between',
        padding:'12px 14px', borderRadius:8, cursor:'pointer',
        border: `1px solid ${selected ? 'var(--blue)' : 'var(--border)'}`,
        background: selected ? 'rgba(79,142,247,0.08)' : 'var(--bg2)',
        transition:'border-color .15s, background .15s',
      }}
    >
      <div>
        <div style={{ fontSize:13, fontWeight:600, color:'var(--text)' }}>{plan.name}</div>
        <div style={{ fontSize:11, color:'var(--text3)', marginTop:2 }}>{tokens} Tokens / mo</div>
      </div>
      <div style={{ display:'flex', alignItems:'center', gap:10 }}>
        <span style={{ fontSize:14, fontWeight:700, color:'var(--text)' }}>{price}</span>
        <div style={{
          width:18, height:18, borderRadius:'50%',
          border: `2px solid ${selected ? 'var(--blue)' : 'var(--border)'}`,
          background: selected ? 'var(--blue)' : 'transparent',
          display:'flex', alignItems:'center', justifyContent:'center',
          transition:'all .15s',
        }}>
          {selected && <div style={{ width:6, height:6, borderRadius:'50%', background:'#fff' }} />}
        </div>
      </div>
    </div>
  )
}
