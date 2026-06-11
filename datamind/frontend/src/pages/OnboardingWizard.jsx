import React, { useState, useEffect } from 'react'
import { onboardingValidateKey, onboardingTestDB, onboardingConnectDB, patchSettings, fetchProviders, validateProviderCreds, connectProvider, fetchProviderStatus, fetchConnectedProviders, fetchBillingPlans, subscribeToPlan, getErrorMessage } from '../utils/api'
import { Spinner } from '../components/UI'

// ── Step indicator ────────────────────────────────────────────────────────────
function StepDots({ total, current }) {
  return (
    <div style={{ display:'flex', gap:8, justifyContent:'center', marginBottom:28 }}>
      {Array.from({length:total}).map((_,i) => (
        <div key={i} style={{
          width: i === current ? 24 : 8, height:8, borderRadius:99, transition:'all .25s',
          background: i < current ? 'var(--green)' : i === current ? 'var(--blue)' : 'var(--bg3)',
        }} />
      ))}
    </div>
  )
}

// ── Shared input style ─────────────────────────────────────────────────────────
const inp = (extra={}) => ({
  width:'100%', padding:'10px 14px', borderRadius:10, fontSize:14,
  background:'var(--bg3)', border:'1px solid var(--border)',
  color:'var(--text)', outline:'none', fontFamily:'var(--font)', ...extra
})

// ── Status box ─────────────────────────────────────────────────────────────────
function StatusBox({ ok, message }) {
  return (
    <div style={{
      padding:'11px 14px', borderRadius:10, fontSize:13, marginBottom:14,
      background: ok ? 'var(--green-dim)' : 'var(--red-dim)',
      border: `1px solid ${ok ? 'rgba(52,209,122,0.25)' : 'rgba(240,80,80,0.25)'}`,
      color: ok ? 'var(--green)' : 'var(--red)',
    }}>
      {ok ? '✓' : '✗'} {message}
    </div>
  )
}

const TOTAL_STEPS = 4

const PLAN_HIGHLIGHTS = {
  Starter: { tokens: '100 Tokens / mo',   price: '$5' },
  Growth:  { tokens: '250 Tokens / mo',   price: '$10' },
  Pro:     { tokens: '1,000 Tokens / mo', price: '$25' },
}

export default function OnboardingWizard({ onComplete, theme, setTheme }) {
  const [step, setStep]           = useState(0)

  // Step 0 — LLM choice
  const [llm, setLlm]             = useState('openai')
  const [apiKey, setApiKey]       = useState('')
  const [keyTesting, setKeyTesting]   = useState(false)
  const [keyResult, setKeyResult]     = useState(null)

  // Step 0.5 — data source type
  const [sourceType, setSourceType]   = useState('') // 'db' | 'provider'
  const [providers, setProviders]     = useState([])
  const [selProvider, setSelProvider] = useState(null)
  const [providerCreds, setProvCreds] = useState({})
  const [provTesting, setProvTesting] = useState(false)
  const [provResult, setProvResult]   = useState(null)

  // Step 1 — DB config
  const [dbForm, setDbForm]       = useState({ name:'My Database', host:'localhost', port:3306, database:'', user:'root', password:'' })
  const [dbTesting, setDbTesting]     = useState(false)
  const [dbResult, setDbResult]       = useState(null)

  // Step 2 — connecting & building
  const [connecting, setConnecting]   = useState(false)
  const [connectDone, setConnectDone] = useState(false)
  const [connectErr, setConnectErr]   = useState('')

  // Provider sync progress
  const [syncConnId, setSyncConnId]     = useState(null)
  const [syncProgress, setSyncProgress] = useState(null)

  const [error, setError]         = useState('')

  // Plan selection — now happens BEFORE sync so the backend uses the right
  // data_months when computing the since-date for the initial full sync.
  const [plans, setPlans]              = useState([])
  const [selectedPlanId, setSelectedPlanId] = useState(null)
  const [planSubscribing, setPlanSubscribing] = useState(null)
  const [planSubscribed, setPlanSubscribed]   = useState(false)

  useEffect(() => {
    fetchBillingPlans().then(d => setPlans(d.plans || [])).catch(() => {})
  }, [])

  async function handleSelectPlan(plan) {
    setPlanSubscribing(plan.id)
    try {
      await subscribeToPlan(plan.id)
      setSelectedPlanId(plan.id)
      setPlanSubscribed(true)
    } catch {
      // Already on trial or plan — treat as success
      setSelectedPlanId(plan.id)
      setPlanSubscribed(true)
    }
    setPlanSubscribing(null)
  }

  // Final step — called from Step 3 "Go to Analytics Hub" for own-DB users
  // or from Step 4 (fallback plan selection) for provider users
  async function handleFinalPlanSelect(plan) {
    setPlanSubscribing(plan.id)
    try { await subscribeToPlan(plan.id) } catch { /* ok */ }
    setPlanSubscribing(null)
    onComplete()
  }

  // Poll sync status after provider connect
  useEffect(() => {
    if (!syncConnId) return
    let active = true
    let tid
    let errCount = 0

    async function poll() {
      if (!active) return
      try {
        const s = await fetchProviderStatus(syncConnId)
        if (!active) return
        errCount = 0
        setSyncProgress(s)
        if (s.status === 'syncing' || s.status === 'pending') {
          tid = setTimeout(poll, 2000)
        } else {
          // connected / error / any terminal state → advance
          tid = setTimeout(() => setStep(3), 800)
        }
      } catch {
        errCount++
        // After 5 consecutive failures (~15s) assume sync finished on the backend
        if (errCount >= 5) {
          if (active) tid = setTimeout(() => setStep(3), 800)
        } else {
          tid = setTimeout(poll, 3000)
        }
      }
    }
    poll()
    return () => { active = false; clearTimeout(tid) }
  }, [syncConnId])

  // ── Step 0: Validate LLM key ────────────────────────────────────────────────
  async function handleValidateKey() {
    if (!apiKey.trim()) { setError('Please enter your API key.'); return }
    setKeyTesting(true); setKeyResult(null); setError('')
    try {
      const r = await onboardingValidateKey(llm, apiKey.trim())
      setKeyResult(r)
      if (r.ok) {
        // Save key immediately
        await patchSettings({ [`${llm}_api_key`]: apiKey.trim(), default_llm: llm })
      }
    } catch(e) {
      setKeyResult({ ok:false, error: getErrorMessage(e) })
    } finally { setKeyTesting(false) }
  }

  // ── Step 1: Test DB connection ──────────────────────────────────────────────
  async function handleTestDB() {
    if (!dbForm.database || !dbForm.host) { setError('Host and database name are required.'); return }
    setDbTesting(true); setDbResult(null); setError('')
    try {
      const r = await onboardingTestDB({ ...dbForm, llm })
      setDbResult(r)
    } catch(e) {
      setDbResult({ ok:false, error: getErrorMessage(e) })
    } finally { setDbTesting(false) }
  }

  // ── Step 2: Connect DB + trigger cache build ────────────────────────────────
  async function handleConnect() {
    setConnecting(true); setConnectErr('')
    try {
      await onboardingConnectDB({ ...dbForm, llm })
      setConnectDone(true)
      setTimeout(() => setStep(3), 600)
    } catch(e) {
      setConnectErr(getErrorMessage(e))
    } finally { setConnecting(false) }
  }

  const setDb = (k,v) => setDbForm(f => ({...f, [k]:v}))

  // ── Shared card wrapper ─────────────────────────────────────────────────────
  const Card = ({children}) => (
    <div style={{ background:'var(--bg1)', border:'1px solid var(--border)', borderRadius:18, padding:'28px 32px', boxShadow:'0 24px 64px rgba(0,0,0,0.35)', width:'100%', maxWidth:480 }}>
      {children}
    </div>
  )

  const NextBtn = ({onClick, disabled, children}) => (
    <button onClick={onClick} disabled={disabled} style={{
      width:'100%', padding:'12px', borderRadius:10, fontSize:14, fontWeight:600,
      background: disabled ? 'rgba(79,142,247,0.4)' : 'linear-gradient(135deg,#4f8ef7,#7c6af7)',
      color:'#fff', border:'none', cursor: disabled ? 'not-allowed' : 'pointer',
      marginTop:16, display:'flex', alignItems:'center', justifyContent:'center', gap:8,
      boxShadow: disabled ? 'none' : '0 4px 16px rgba(79,142,247,0.3)',
    }}>{children}</button>
  )

  const Label = ({children}) => (
    <div style={{ fontSize:12, color:'var(--text2)', marginBottom:6, fontWeight:500 }}>{children}</div>
  )

  // ── Background decoration ───────────────────────────────────────────────────
  const Bg = () => (
    <>
      <div style={{ position:'fixed', width:600, height:600, borderRadius:'50%', background:'radial-gradient(circle,rgba(79,142,247,0.10) 0%,transparent 70%)', top:'-10%', left:'-10%', pointerEvents:'none' }} />
      <div style={{ position:'fixed', width:500, height:500, borderRadius:'50%', background:'radial-gradient(circle,rgba(167,139,250,0.08) 0%,transparent 70%)', bottom:'-5%', right:'-5%', pointerEvents:'none' }} />
    </>
  )

  // ── Render ──────────────────────────────────────────────────────────────────
  return (
    <div style={{ minHeight:'100vh', display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', background:'var(--bg)', padding:'24px 16px', fontFamily:'var(--font)', position:'relative', overflow:'hidden' }}>
      <Bg />

      {/* Theme toggle */}
      {setTheme && (
        <button
          onClick={() => setTheme(t => t === 'dark' ? 'light' : 'dark')}
          title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
          style={{
            position:'fixed', top:16, right:16, zIndex:100,
            width:36, height:36, borderRadius:'50%',
            background:'var(--bg3)', border:'1px solid var(--border)',
            cursor:'pointer', display:'flex', alignItems:'center', justifyContent:'center',
            fontSize:16, transition:'background .15s',
          }}
          onMouseEnter={e => e.currentTarget.style.background='var(--bg4)'}
          onMouseLeave={e => e.currentTarget.style.background='var(--bg3)'}
        >
          {theme === 'dark' ? '☀️' : '🌙'}
        </button>
      )}

      {/* Logo */}
      <div style={{ textAlign:'center', marginBottom:28, zIndex:1 }}>
        <div style={{ display:'inline-flex', alignItems:'center', justifyContent:'center', width:48, height:48, borderRadius:13, background:'linear-gradient(135deg,#4f8ef7,#a78bfa)', marginBottom:12, boxShadow:'0 8px 24px rgba(79,142,247,0.3)' }}>
          <svg width="22" height="22" viewBox="0 0 16 16" fill="none">
            <rect x="2" y="2" width="5" height="5" rx="1" fill="rgba(255,255,255,0.95)"/>
            <rect x="9" y="2" width="5" height="5" rx="1" fill="rgba(255,255,255,0.5)"/>
            <rect x="2" y="9" width="5" height="5" rx="1" fill="rgba(255,255,255,0.5)"/>
            <rect x="9" y="9" width="5" height="5" rx="1" fill="rgba(255,255,255,0.95)"/>
          </svg>
        </div>
        <div style={{ fontSize:22, fontWeight:700, color:'var(--text)' }}>Welcome to DataMind</div>
        <div style={{ fontSize:13, color:'var(--text2)', marginTop:4 }}>Let's get you set up in 3 quick steps</div>
      </div>

      <div style={{ zIndex:1, width:'100%', maxWidth:480 }}>
        <StepDots total={TOTAL_STEPS} current={step} />

        {/* ── STEP 0: Auto-setup (hidden from user) ─────────────────────────── */}
        {step === 0 && (
          <Card>
            {(() => {
              // Auto-set default LLM and skip to next step
              setTimeout(async () => {
                try {
                  await patchSettings({ default_llm: 'openai' })
                  const p = await fetchProviders()
                  setProviders(p.providers || [])
                  setStep(1)
                } catch(e) {
                  setError(e.message)
                }
              }, 500)
              return null
            })()}
            <div style={{ textAlign:'center', padding:'60px 20px', color:'var(--text3)' }}>
              <Spinner size={32} />
              <div style={{ marginTop:16, fontSize:14 }}>Setting up DataMind AI...</div>
            </div>
          </Card>
        )}

        {/* ── STEP 0.5: Choose data source type ─────────────────────────── */}
        {step === 1 && sourceType === '' && !syncConnId && (
          <Card>
            <div style={{ fontSize:11, color:'var(--text3)', textTransform:'uppercase', letterSpacing:'.1em', marginBottom:6 }}>Step 2 of 3</div>
            <div style={{ fontSize:19, fontWeight:700, color:'var(--text)', marginBottom:4 }}>How do you want to connect your data?</div>
            <div style={{ fontSize:13, color:'var(--text2)', marginBottom:22, lineHeight:1.6 }}>
              Choose whether you have a MySQL database you control, or you want to sync from a business tool like Loyverse.
            </div>
            <div style={{ display:'flex', flexDirection:'column', gap:10, marginBottom:20 }}>
              <button onClick={async () => {
                setSourceType('provider')
                const r = await fetchProviders().catch(() => ({providers:[]}))
                setProviders(r.providers || [])
              }} style={{
                padding:'16px 18px', borderRadius:12, border:'1px solid var(--border)',
                background:'var(--bg2)', color:'var(--text)', textAlign:'left', cursor:'pointer',
                display:'flex', alignItems:'center', gap:14, transition:'all .15s',
              }}
                onMouseEnter={e => e.currentTarget.style.borderColor='rgba(167,139,250,0.4)'}
                onMouseLeave={e => e.currentTarget.style.borderColor='rgba(255,255,255,0.1)'}
              >
                <span style={{ fontSize:32 }}>🔌</span>
                <div>
                  <div style={{ fontWeight:600, fontSize:14, marginBottom:3 }}>Connect Any API-Enabled Platform</div>
                  <div style={{ fontSize:12, color:'var(--text2)' }}>POS, eCommerce, CRM, ERP & custom systems</div>
                </div>
              </button>
              <button onClick={() => setSourceType('db')} style={{
                padding:'16px 18px', borderRadius:12, border:'1px solid var(--border)',
                background:'var(--bg2)', color:'var(--text)', textAlign:'left', cursor:'pointer',
                display:'flex', alignItems:'center', gap:14, transition:'all .15s',
              }}
                onMouseEnter={e => e.currentTarget.style.borderColor='rgba(79,142,247,0.4)'}
                onMouseLeave={e => e.currentTarget.style.borderColor='rgba(255,255,255,0.1)'}
              >
                <span style={{ fontSize:32 }}>🗄</span>
                <div>
                  <div style={{ fontWeight:600, fontSize:14, marginBottom:3 }}>Bring Your Own Database</div>
                  <div style={{ fontSize:12, color:'var(--text2)' }}>Connect directly to your MySQL database</div>
                </div>
              </button>
            </div>
            <button onClick={() => setStep(0)} style={{ fontSize:12, color:'var(--text3)', background:'none', border:'none', cursor:'pointer' }}>← Back</button>
          </Card>
        )}

        {/* ── STEP 1b: Choose provider ──────────────────────────────────── */}
        {step === 1 && sourceType === 'provider' && !selProvider && !syncConnId && (
          <Card>
            <div style={{ fontSize:11, color:'var(--text3)', textTransform:'uppercase', letterSpacing:'.1em', marginBottom:6 }}>Step 2 of 3</div>
            <div style={{ fontSize:19, fontWeight:700, color:'var(--text)', marginBottom:16 }}>Choose your integration</div>
            <div style={{ display:'flex', flexDirection:'column', gap:8, marginBottom:16 }}>
              {providers.map(p => (
                <button key={p.provider_id} onClick={() => setSelProvider(p)} style={{
                  padding:'14px 16px', borderRadius:12, border:'1px solid rgba(255,255,255,0.08)',
                  background:'var(--bg2)', color:'var(--text)', textAlign:'left', cursor:'pointer',
                  display:'flex', alignItems:'center', gap:12,
                }}>
                  <span style={{ fontSize:28 }}>{p.logo_emoji}</span>
                  <div>
                    <div style={{ fontWeight:600, fontSize:13 }}>{p.display_name}</div>
                    <div style={{ fontSize:11, color:'var(--text3)' }}>{p.description}</div>
                  </div>
                </button>
              ))}
            </div>
            <button onClick={() => setSourceType('')} style={{ fontSize:12, color:'var(--text3)', background:'none', border:'none', cursor:'pointer' }}>← Back</button>
          </Card>
        )}

        {/* ── STEP 1c: Provider credentials ────────────────────────────── */}
        {step === 1 && sourceType === 'provider' && selProvider && !syncConnId && (
          <Card>
            <div style={{ fontSize:11, color:'var(--text3)', textTransform:'uppercase', letterSpacing:'.1em', marginBottom:6 }}>Step 2 of 3</div>
            <div style={{ display:'flex', alignItems:'center', gap:10, marginBottom:16 }}>
              <span style={{ fontSize:30 }}>{selProvider.logo_emoji}</span>
              <div>
                <div style={{ fontSize:17, fontWeight:700, color:'var(--text)' }}>Connect {selProvider.display_name}</div>
                <div style={{ fontSize:12, color:'var(--text2)' }}>{selProvider.description}</div>
                {selProvider.docs_url && (
                  <a href={selProvider.docs_url} target="_blank" rel="noopener noreferrer" style={{ fontSize:12, color:'var(--blue)', textDecoration:'none', display:'inline-flex', alignItems:'center', gap:4, marginTop:4 }}>
                    📄 View API Setup Guide ↗
                  </a>
                )}
              </div>
            </div>
            {selProvider.credential_fields?.map(f => (
              <div key={f.key} style={{ marginBottom:14 }}>
                <Label>{f.label}</Label>
                <input type={f.type === 'password' ? 'password' : 'text'}
                  value={providerCreds[f.key] || ''}
                  onChange={e => setProvCreds(c => ({...c, [f.key]: e.target.value}))}
                  placeholder={f.placeholder || ''}
                  style={{ ...inp({fontFamily:'var(--mono)'}), marginBottom:4 }}
                />
                {f.hint && <div style={{ fontSize:11, color:'var(--text3)' }}>{f.hint}</div>}
              </div>
            ))}
            {provResult && (
              <StatusBox ok={provResult.ok} message={provResult.ok
                ? `✓ Connected to ${provResult.details?.merchant_name || selProvider.display_name}`
                : provResult.error} />
            )}

            {/* Plan selection — shown AFTER test succeeds, BEFORE sync starts.
                The backend reads the plan to determine how many months to sync. */}
            {provResult?.ok && (
              <div style={{ marginTop:14 }}>
                <div style={{ fontSize:12, color:'var(--text2)', marginBottom:8, fontWeight:600 }}>
                  Choose your plan — this sets how much data we sync:
                </div>
                <div style={{ display:'flex', flexDirection:'column', gap:6, marginBottom:6 }}>
                  {plans.map(plan => {
                    const dataMonths = plan.name === 'Starter' ? '1 month' : plan.name === 'Growth' ? '3 months' : '12 months'
                    const isSelected = selectedPlanId === plan.id
                    return (
                      <button key={plan.id} onClick={() => handleSelectPlan(plan)}
                        disabled={planSubscribing === plan.id}
                        style={{
                          padding:'10px 14px', borderRadius:10, textAlign:'left', cursor:'pointer',
                          background: isSelected ? 'rgba(79,142,247,0.12)' : 'var(--bg2)',
                          border: `1px solid ${isSelected ? 'rgba(79,142,247,0.4)' : 'var(--border)'}`,
                          color:'var(--text)', display:'flex', justifyContent:'space-between',
                          alignItems:'center',
                        }}>
                        <span style={{ fontSize:13, fontWeight: isSelected ? 600 : 400 }}>
                          {isSelected ? '✓ ' : ''}{plan.name} — {dataMonths} of data
                        </span>
                        <span style={{ fontSize:12, color:'var(--text2)' }}>
                          ${plan.price_usd}/mo
                        </span>
                      </button>
                    )
                  })}
                </div>
                {planSubscribed && (
                  <div style={{ fontSize:11, color:'var(--green)', marginBottom:4 }}>
                    ✓ Plan saved — sync will import the right amount of data
                  </div>
                )}
              </div>
            )}

            <div style={{ display:'flex', gap:8, marginTop: provResult?.ok ? 6 : 0 }}>
              <button onClick={() => setSelProvider(null)} style={{ padding:'9px 14px', borderRadius:10, fontSize:13, background:'transparent', border:'1px solid var(--border)', color:'var(--text2)', cursor:'pointer' }}>← Back</button>
              <button onClick={async () => {
                setProvTesting(true); setProvResult(null)
                try {
                  const r = await validateProviderCreds(selProvider.provider_id, providerCreds)
                  setProvResult(r)
                } catch(e) { setProvResult({ok:false, error:e.message}) }
                finally { setProvTesting(false) }
              }} disabled={provTesting} style={{ flex:1, padding:'10px', borderRadius:10, fontSize:13, fontWeight:600, background:'rgba(79,142,247,0.12)', color:'var(--blue)', border:'1px solid rgba(79,142,247,0.2)', cursor: provTesting ? 'not-allowed' : 'pointer', display:'flex', alignItems:'center', justifyContent:'center', gap:7 }}>
                {provTesting ? <><span style={{fontSize:12}}>⟳</span> Testing…</> : '⚡ Test Connection'}
              </button>
            </div>
            <NextBtn onClick={async () => {
              setConnecting(true)
              try {
                const result = await connectProvider(selProvider.provider_id, providerCreds)
                const connId = result?.connection_id
                if (connId) {
                  setSyncConnId(connId)
                } else {
                  const connected = await fetchConnectedProviders().catch(() => ({connections:[]}))
                  const latest = connected.connections?.[0]
                  if (latest?.connection_id) setSyncConnId(latest.connection_id)
                  else setTimeout(() => setStep(3), 600)
                }
              } catch(e) { setConnectErr(e.message) }
              finally { setConnecting(false) }
            }} disabled={!provResult?.ok || !planSubscribed || connecting}>
              {connecting ? 'Connecting…' : `Connect ${selProvider.display_name} & Sync Data →`}
            </NextBtn>
          </Card>
        )}

        {/* ── STEP 1 (DB): Add database ─────────────────────────────────── */}
        {step === 1 && sourceType === 'db' && !syncConnId && (
          <Card>
            <div style={{ fontSize:11, color:'var(--text3)', textTransform:'uppercase', letterSpacing:'.1em', marginBottom:6 }}>Step 2 of 3</div>
            <div style={{ fontSize:19, fontWeight:700, color:'var(--text)', marginBottom:4 }}>Connect your MySQL database</div>
            <div style={{ fontSize:13, color:'var(--text2)', marginBottom:22, lineHeight:1.6 }}>
              Enter your database credentials. We'll test the connection before saving.
            </div>

            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:10 }}>
              {[
                {k:'name',     label:'Connection Name', col:'1/-1', ph:'e.g. Production DB'},
                {k:'host',     label:'Host',            col:'',     ph:'localhost or IP'},
                {k:'port',     label:'Port',            col:'',     ph:'3306', mono:true},
                {k:'database', label:'Database Name',   col:'1/-1', ph:'your_database', mono:true},
                {k:'user',     label:'MySQL Username',  col:'',     ph:'root', mono:true},
                {k:'password', label:'Password',        col:'',     ph:'••••••••', type:'password'},
              ].map(({k,label,col,ph,mono,type}) => (
                <div key={k} style={{ gridColumn: col || 'auto' }}>
                  <Label>{label}</Label>
                  <input
                    type={type||'text'} value={dbForm[k]}
                    onChange={e => setDb(k, k==='port' ? Number(e.target.value) : e.target.value)}
                    placeholder={ph}
                    style={{ ...inp(mono ? {fontFamily:'var(--mono)'} : {}), marginBottom:2 }}
                  />
                </div>
              ))}
            </div>

            {error && <div style={{ marginTop:10 }}><StatusBox ok={false} message={error} /></div>}
            {dbResult && <div style={{ marginTop:10 }}><StatusBox ok={dbResult.ok} message={dbResult.ok ? `Connected! Found ${dbResult.table_count} tables: ${dbResult.tables?.slice(0,4).join(', ')}${dbResult.table_count > 4 ? '…' : ''}` : dbResult.error} /></div>}

            <div style={{ display:'flex', gap:8, marginTop:14 }}>
              <button onClick={() => setSourceType('')} style={{ padding:'10px 18px', borderRadius:10, fontSize:13, background:'transparent', border:'1px solid var(--border)', color:'var(--text2)', cursor:'pointer' }}>← Back</button>
              <button onClick={handleTestDB} disabled={dbTesting} style={{
                flex:1, padding:'10px', borderRadius:10, fontSize:13, fontWeight:600,
                background:'rgba(79,142,247,0.12)', color:'var(--blue)',
                border:'1px solid rgba(79,142,247,0.2)', cursor: dbTesting ? 'not-allowed' : 'pointer',
                display:'flex', alignItems:'center', justifyContent:'center', gap:7,
                opacity: dbTesting ? 0.6 : 1,
              }}>
                {dbTesting ? <><Spinner size={13} color="var(--blue)"/>Testing…</> : '⚡ Test Connection'}
              </button>
            </div>

            <NextBtn onClick={() => { setStep(2); setError('') }} disabled={!dbResult?.ok}>
              Continue → Set Up Analytics
            </NextBtn>
          </Card>
        )}

        {/* ── STEP 2: Confirm + build analytics ────────────────────────── */}
        {step === 2 && !syncConnId && (
          <Card>
            <div style={{ fontSize:11, color:'var(--text3)', textTransform:'uppercase', letterSpacing:'.1em', marginBottom:6 }}>Step 3 of 3</div>
            <div style={{ fontSize:19, fontWeight:700, color:'var(--text)', marginBottom:4 }}>Set up your analytics</div>
            <div style={{ fontSize:13, color:'var(--text2)', marginBottom:22, lineHeight:1.6 }}>
              DataMind will now look at your database and build custom analytics for you. This only runs <strong style={{color:'rgba(255,255,255,.6)'}}>once</strong> and everything is saved for future use.
            </div>

            {/* Summary */}
            <div style={{ background:'var(--bg2)', border:'1px solid var(--border)', borderRadius:10, padding:'14px 16px', marginBottom:18 }}>
              {[
                ['Database', `${dbForm.database} @ ${dbForm.host}:${dbForm.port}`],
                ['Tables found', dbResult?.table_count || '?'],
              ].map(([k,v]) => (
                <div key={k} style={{ display:'flex', justifyContent:'space-between', padding:'5px 0', borderBottom:'1px solid var(--border)', fontSize:13 }}>
                  <span style={{ color:'var(--text2)' }}>{k}</span>
                  <span style={{ color:'var(--text)', fontWeight:500 }}>{v}</span>
                </div>
              ))}
            </div>

            {/* What happens */}
            <div style={{ marginBottom:18 }}>
              {[
                { icon:'🔍', text:'Read your full schema and foreign keys' },
                { icon:'🧠', text:'Generate SQL for 21 analytics templates using AI' },
                { icon:'✅', text:'Validate each query with EXPLAIN' },
                { icon:'⚡', text:'Save everything — your insights load instantly, no extra Credits needed' },
              ].map(({icon,text},i) => (
                <div key={i} style={{ display:'flex', gap:10, alignItems:'flex-start', marginBottom:10 }}>
                  <span style={{ fontSize:16, flexShrink:0 }}>{icon}</span>
                  <span style={{ fontSize:13, color:'var(--text2)', lineHeight:1.5 }}>{text}</span>
                </div>
              ))}
            </div>

            {connectErr && <StatusBox ok={false} message={connectErr} />}
            {connectDone && <StatusBox ok={true} message="Database connected! Setting up your analytics in the background…" />}

            <div style={{ display:'flex', gap:8 }}>
              <button onClick={() => setStep(1)} style={{ padding:'10px 18px', borderRadius:10, fontSize:13, background:'transparent', border:'1px solid var(--border)', color:'var(--text2)', cursor:'pointer' }}>← Back</button>
              <button onClick={handleConnect} disabled={connecting || connectDone} style={{
                flex:1, padding:'12px', borderRadius:10, fontSize:14, fontWeight:600,
                background: connectDone ? 'var(--green-dim)' : 'linear-gradient(135deg,#4f8ef7,#7c6af7)',
                color: connectDone ? 'var(--green)' : '#fff',
                border: connectDone ? '1px solid rgba(52,209,122,0.3)' : 'none',
                cursor: connecting || connectDone ? 'not-allowed' : 'pointer',
                display:'flex', alignItems:'center', justifyContent:'center', gap:8,
                boxShadow: connectDone ? 'none' : '0 4px 16px rgba(79,142,247,0.3)',
                opacity: connecting ? 0.7 : 1,
              }}>
                {connecting ? <><Spinner size={14} color="#fff" />Connecting…</> : connectDone ? '✓ Connected!' : '🚀 Connect & Set Up Analytics'}
              </button>
            </div>
          </Card>
        )}

        {/* ── SYNC LOADING: Provider data syncing ───────────────────────── */}
        {syncConnId && step !== 3 && (() => {
          const prog    = syncProgress?.progress
          const pct     = prog?.percent ?? 0
          const rows    = prog?.rows_synced ?? 0
          const elapsed = prog?.elapsed_s ?? 0
          const eta     = pct > 8 ? Math.round((elapsed / pct) * (100 - pct)) : null
          const etaStr  = eta == null ? '' : eta < 60 ? `~${eta}s left` : `~${Math.round(eta/60)}m left`
          const msg     = prog?.message || 'Fetching your data…'
          return (
            <Card>
              <style>{`@keyframes obSlide{0%{transform:translateX(-100%)}100%{transform:translateX(400%)}}`}</style>
              <div style={{ textAlign:'center', paddingBottom:8 }}>
                <div style={{ fontSize:48, marginBottom:10 }}>{selProvider?.logo_emoji || '🔌'}</div>
                <div style={{ fontSize:18, fontWeight:700, color:'var(--text)', marginBottom:4 }}>
                  Syncing {selProvider?.display_name}…
                </div>
                <div style={{ fontSize:13, color:'var(--text2)', marginBottom:20, minHeight:18 }}>{msg}</div>

                {/* Progress bar */}
                <div style={{ height:4, background:'var(--bg3)', borderRadius:2, overflow:'hidden', marginBottom:8 }}>
                  {pct > 0
                    ? <div style={{ height:'100%', width:`${pct}%`, background:'#f59e0b', borderRadius:2, transition:'width .6s ease' }} />
                    : <div style={{ height:'100%', width:'30%', background:'#f59e0b', borderRadius:2, animation:'obSlide 1.4s linear infinite' }} />
                  }
                </div>

                {/* Stats */}
                <div style={{ display:'flex', justifyContent:'center', gap:16, fontSize:12, color:'var(--text2)', marginBottom:20 }}>
                  {rows > 0 && <span>{rows.toLocaleString()} rows synced</span>}
                  {etaStr  && <span>{etaStr}</span>}
                  {pct > 0 && <span>{pct}%</span>}
                </div>

                {/* What's happening list */}
                <div style={{ textAlign:'left', background:'var(--bg2)', border:'1px solid var(--border)', borderRadius:10, padding:'12px 14px', marginBottom:16 }}>
                  {[
                    { icon:'🔗', text:'Authenticating with your account' },
                    { icon:'📥', text:'Downloading and indexing your records' },
                    { icon:'🧠', text:'Setting up your analytics' },
                    { icon:'⚡', text:'Future questions will load instantly' },
                  ].map(({ icon, text }, i) => (
                    <div key={i} style={{ display:'flex', gap:10, alignItems:'center', padding:'5px 0', borderBottom: i < 3 ? '1px solid var(--border)' : 'none' }}>
                      <span style={{ fontSize:15, flexShrink:0 }}>{icon}</span>
                      <span style={{ fontSize:12, color:'var(--text2)' }}>{text}</span>
                    </div>
                  ))}
                </div>

                <div style={{ fontSize:12, color:'var(--text3)' }}>
                  You'll be taken to Analytics Hub automatically when ready.
                </div>
              </div>
            </Card>
          )
        })()}

        {/* ── STEP 3: Done ──────────────────────────────────────────────── */}
        {step === 3 && (
          <Card>
            <div style={{ textAlign:'center', padding:'12px 0 8px' }}>
              <div style={{ fontSize:56, marginBottom:16 }}>🎉</div>
              <div style={{ fontSize:22, fontWeight:700, color:'var(--text)', marginBottom:8 }}>You're all set!</div>
              <div style={{ fontSize:13, color:'var(--text2)', lineHeight:1.7, marginBottom:24 }}>
                Your database is connected and your analytics are being set up in the background.
                Head to the <strong style={{color:'var(--blue)'}}>Analytics Hub</strong> — your custom templates will appear shortly.
              </div>

              <div style={{ background:'rgba(79,142,247,0.06)', border:'1px solid rgba(79,142,247,0.15)', borderRadius:10, padding:'12px 14px', marginBottom:20, textAlign:'left' }}>
                {[
                  '⬡  Analytics Hub — click any card to run an analysis instantly',
                  '⌕  Ask a Question — type anything in plain English',
                  '📈  Forecasting — predict future revenue automatically',
                  '📋  Report Builder — generate professional reports',
                ].map((t,i) => <div key={i} style={{ fontSize:12, color:'var(--text2)', padding:'4px 0' }}>{t}</div>)}
              </div>

              {/* Provider users already selected a plan before sync — go straight to app.
                  Own-DB users select plan here (no sync timing dependency). */}
              {planSubscribed ? (
                <button onClick={onComplete} style={{
                  width:'100%', padding:'13px', borderRadius:10, fontSize:15, fontWeight:700,
                  background:'linear-gradient(135deg,#4f8ef7,#a78bfa)', color:'#fff', border:'none',
                  cursor:'pointer', boxShadow:'0 6px 20px rgba(79,142,247,0.35)',
                }}>
                  Go to Analytics Hub →
                </button>
              ) : (
                <button onClick={() => setStep(4)} style={{
                  width:'100%', padding:'13px', borderRadius:10, fontSize:15, fontWeight:700,
                  background:'linear-gradient(135deg,#4f8ef7,#a78bfa)', color:'#fff', border:'none',
                  cursor:'pointer', boxShadow:'0 6px 20px rgba(79,142,247,0.35)',
                }}>
                  Choose Your Plan →
                </button>
              )}
            </div>
          </Card>
        )}

        {/* ── STEP 4: Plan selection ─────────────────────────────────────── */}
        {step === 4 && (
          <Card>
            <div style={{ textAlign:'center', marginBottom:20 }}>
              <div style={{ fontSize:13, fontWeight:600, color:'var(--green)', background:'rgba(52,209,122,0.1)', border:'1px solid rgba(52,209,122,0.2)', borderRadius:8, padding:'6px 14px', display:'inline-block', marginBottom:14 }}>
                ✓ Your 14-day free trial has started
              </div>
              <div style={{ fontSize:20, fontWeight:700, color:'var(--text)', marginBottom:6 }}>Choose a plan</div>
              <div style={{ fontSize:13, color:'var(--text2)' }}>Lock in your plan now or continue exploring with your trial.</div>
            </div>

            <div style={{ display:'flex', flexDirection:'column', gap:10, marginBottom:20 }}>
              {plans.map(plan => {
                const h = PLAN_HIGHLIGHTS[plan.name] || {}
                const isBusy = planSubscribing === plan.id
                return (
                  <div key={plan.id} style={{
                    display:'flex', alignItems:'center', gap:14,
                    background:'var(--bg2)', border:'1px solid var(--border)',
                    borderRadius:10, padding:'12px 16px',
                  }}>
                    <div style={{ flex:1 }}>
                      <div style={{ fontWeight:700, fontSize:14, marginBottom:2 }}>{plan.name}</div>
                      <div style={{ fontSize:12, color:'var(--text3)' }}>{h.tokens}</div>
                    </div>
                    <div style={{ fontWeight:800, fontSize:16, color:'var(--text)', minWidth:36, textAlign:'right' }}>{h.price}<span style={{ fontSize:11, fontWeight:400, color:'var(--text3)' }}>/mo</span></div>
                    <button
                      onClick={() => handleFinalPlanSelect(plan)}
                      disabled={!!planSubscribing}
                      style={{
                        padding:'7px 16px', borderRadius:8, border:'none', cursor: planSubscribing ? 'wait' : 'pointer',
                        background:'var(--blue)', color:'#fff', fontWeight:600, fontSize:13,
                        opacity: planSubscribing ? 0.6 : 1, flexShrink:0,
                      }}
                    >
                      {isBusy ? <Spinner /> : 'Select'}
                    </button>
                  </div>
                )
              })}
            </div>

            <button
              onClick={onComplete}
              disabled={!!planSubscribing}
              style={{
                width:'100%', padding:'11px', borderRadius:10, fontSize:14,
                background:'transparent', color:'var(--text3)', border:'1px solid var(--border)',
                cursor:'pointer',
              }}
            >
              Continue with free trial →
            </button>
          </Card>
        )}
      </div>
    </div>
  )
}