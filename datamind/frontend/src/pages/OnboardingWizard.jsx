import React, { useState } from 'react'
import { onboardingValidateKey, onboardingTestDB, onboardingConnectDB, patchSettings, fetchProviders, validateProviderCreds, connectProvider } from '../utils/api'
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
  background:'rgba(255,255,255,0.05)', border:'1px solid rgba(255,255,255,0.1)',
  color:'#f0f1fa', outline:'none', fontFamily:'var(--font)', ...extra
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

export default function OnboardingWizard({ onComplete }) {
  const [step, setStep]           = useState(0)

  // Step 0 — LLM choice
  const [llm, setLlm]             = useState('gemini')
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

  const [error, setError]         = useState('')

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
      setKeyResult({ ok:false, error: e.response?.data?.detail || e.message })
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
      setDbResult({ ok:false, error: e.response?.data?.detail || e.message })
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
      setConnectErr(e.response?.data?.detail || e.message)
    } finally { setConnecting(false) }
  }

  const setDb = (k,v) => setDbForm(f => ({...f, [k]:v}))

  // ── Shared card wrapper ─────────────────────────────────────────────────────
  const Card = ({children}) => (
    <div style={{ background:'#0f1018', border:'1px solid rgba(255,255,255,0.07)', borderRadius:18, padding:'28px 32px', boxShadow:'0 24px 64px rgba(0,0,0,0.5)', width:'100%', maxWidth:480 }}>
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
    <div style={{ fontSize:12, color:'rgba(255,255,255,0.4)', marginBottom:6, fontWeight:500 }}>{children}</div>
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
    <div style={{ minHeight:'100vh', display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', background:'#09090f', padding:'24px 16px', fontFamily:'var(--font)', position:'relative', overflow:'hidden' }}>
      <Bg />

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
        <div style={{ fontSize:22, fontWeight:700, color:'#f0f1fa' }}>Welcome to DataMind</div>
        <div style={{ fontSize:13, color:'rgba(255,255,255,0.35)', marginTop:4 }}>Let's get you set up in 3 quick steps</div>
      </div>

      <div style={{ zIndex:1, width:'100%', maxWidth:480 }}>
        <StepDots total={TOTAL_STEPS} current={step} />

        {/* ── STEP 0: Choose & validate LLM key ─────────────────────────── */}
        {step === 0 && (
          <Card>
            <div style={{ fontSize:11, color:'rgba(255,255,255,0.3)', textTransform:'uppercase', letterSpacing:'.1em', marginBottom:6 }}>Step 1 of 3</div>
            <div style={{ fontSize:19, fontWeight:700, color:'#f0f1fa', marginBottom:4 }}>Choose your AI model</div>
            <div style={{ fontSize:13, color:'rgba(255,255,255,0.4)', marginBottom:22, lineHeight:1.6 }}>
              DataMind uses an LLM to understand your database and generate analytics. Add your API key below — it's stored securely in your account.
            </div>

            {/* LLM toggle */}
            <Label>AI Provider</Label>
            <div style={{ display:'flex', background:'rgba(255,255,255,0.04)', borderRadius:10, padding:4, marginBottom:18, gap:3 }}>
              {[['gemini','✦ Gemini','Free tier · fast · recommended'],['deepseek','◈ DeepSeek','Low cost · very capable']].map(([id,label,hint]) => (
                <button key={id} onClick={() => { setLlm(id); setKeyResult(null); setApiKey('') }} style={{
                  flex:1, padding:'9px 6px', borderRadius:7, border:'none', cursor:'pointer', transition:'all .15s',
                  background: llm===id ? 'rgba(79,142,247,0.2)' : 'transparent',
                  color: llm===id ? 'var(--blue)' : 'rgba(255,255,255,0.35)',
                }}>
                  <div style={{ fontSize:13, fontWeight:600 }}>{label}</div>
                  <div style={{ fontSize:10, opacity:.7, marginTop:2 }}>{hint}</div>
                </button>
              ))}
            </div>

            <Label>
              {llm === 'gemini' ? 'Gemini API Key' : 'DeepSeek API Key'}
              {' — '}
              <a href={llm==='gemini' ? 'https://aistudio.google.com/app/apikey' : 'https://platform.deepseek.com/api_keys'}
                target="_blank" rel="noreferrer" style={{ color:'var(--blue)', textDecoration:'none' }}>
                Get yours free ↗
              </a>
            </Label>
            <input
              type="password" value={apiKey}
              onChange={e => { setApiKey(e.target.value); setKeyResult(null) }}
              onKeyDown={e => e.key === 'Enter' && handleValidateKey()}
              placeholder={llm==='gemini' ? 'AIza…' : 'sk-…'}
              style={{ ...inp({fontFamily:'var(--mono)', letterSpacing:'.04em'}), marginBottom:12 }}
            />

            {error && <StatusBox ok={false} message={error} />}
            {keyResult && <StatusBox ok={keyResult.ok} message={keyResult.ok ? `Key verified! Model responded: "${keyResult.response?.slice(0,40)}"` : keyResult.error} />}

            <button onClick={handleValidateKey} disabled={keyTesting || !apiKey.trim()} style={{
              width:'100%', padding:'11px', borderRadius:10, fontSize:13, fontWeight:600,
              background: 'rgba(79,142,247,0.15)', color:'var(--blue)',
              border:'1px solid rgba(79,142,247,0.25)', cursor: keyTesting || !apiKey.trim() ? 'not-allowed' : 'pointer',
              display:'flex', alignItems:'center', justifyContent:'center', gap:8, marginBottom:10,
              opacity: keyTesting || !apiKey.trim() ? 0.6 : 1
            }}>
              {keyTesting ? <><Spinner size={13} color="var(--blue)" /> Validating…</> : '⚡ Validate Key'}
            </button>

            <NextBtn onClick={() => { setStep(1); setError('') }} disabled={!keyResult?.ok}>
              Continue → Add Database
            </NextBtn>

            <div style={{ textAlign:'center', marginTop:14, fontSize:11, color:'rgba(255,255,255,0.2)' }}>
              Your key is sent only to {llm==='gemini'?'Google':'DeepSeek'}'s API. We never store it in plain text.
            </div>
          </Card>
        )}

        {/* ── STEP 0.5: Choose data source type ─────────────────────────── */}
        {step === 1 && sourceType === '' && (
          <Card>
            <div style={{ fontSize:11, color:'rgba(255,255,255,0.3)', textTransform:'uppercase', letterSpacing:'.1em', marginBottom:6 }}>Step 2 of 3</div>
            <div style={{ fontSize:19, fontWeight:700, color:'#f0f1fa', marginBottom:4 }}>How do you want to connect your data?</div>
            <div style={{ fontSize:13, color:'rgba(255,255,255,0.4)', marginBottom:22, lineHeight:1.6 }}>
              Choose whether you have a MySQL database you control, or you want to sync from a business tool like Loyverse.
            </div>
            <div style={{ display:'flex', flexDirection:'column', gap:10, marginBottom:20 }}>
              <button onClick={() => setSourceType('db')} style={{
                padding:'16px 18px', borderRadius:12, border:'1px solid rgba(255,255,255,0.1)',
                background:'rgba(255,255,255,0.03)', color:'#f0f1fa', textAlign:'left', cursor:'pointer',
                display:'flex', alignItems:'center', gap:14, transition:'all .15s',
              }}
                onMouseEnter={e => e.currentTarget.style.borderColor='rgba(79,142,247,0.4)'}
                onMouseLeave={e => e.currentTarget.style.borderColor='rgba(255,255,255,0.1)'}
              >
                <span style={{ fontSize:32 }}>🗄</span>
                <div>
                  <div style={{ fontWeight:600, fontSize:14, marginBottom:3 }}>Bring Your Own Database</div>
                  <div style={{ fontSize:12, color:'rgba(255,255,255,0.35)' }}>Connect directly to your MySQL database</div>
                </div>
              </button>
              <button onClick={async () => {
                setSourceType('provider')
                const r = await fetchProviders().catch(() => ({providers:[]}))
                setProviders(r.providers || [])
              }} style={{
                padding:'16px 18px', borderRadius:12, border:'1px solid rgba(255,255,255,0.1)',
                background:'rgba(255,255,255,0.03)', color:'#f0f1fa', textAlign:'left', cursor:'pointer',
                display:'flex', alignItems:'center', gap:14, transition:'all .15s',
              }}
                onMouseEnter={e => e.currentTarget.style.borderColor='rgba(167,139,250,0.4)'}
                onMouseLeave={e => e.currentTarget.style.borderColor='rgba(255,255,255,0.1)'}
              >
                <span style={{ fontSize:32 }}>🔌</span>
                <div>
                  <div style={{ fontWeight:600, fontSize:14, marginBottom:3 }}>Connect via API Integration</div>
                  <div style={{ fontSize:12, color:'rgba(255,255,255,0.35)' }}>Loyverse POS, Square, Shopify and more</div>
                </div>
              </button>
            </div>
            <button onClick={() => setStep(0)} style={{ fontSize:12, color:'rgba(255,255,255,0.3)', background:'none', border:'none', cursor:'pointer' }}>← Back</button>
          </Card>
        )}

        {/* ── STEP 1b: Choose provider ──────────────────────────────────── */}
        {step === 1 && sourceType === 'provider' && !selProvider && (
          <Card>
            <div style={{ fontSize:11, color:'rgba(255,255,255,0.3)', textTransform:'uppercase', letterSpacing:'.1em', marginBottom:6 }}>Step 2 of 3</div>
            <div style={{ fontSize:19, fontWeight:700, color:'#f0f1fa', marginBottom:16 }}>Choose your integration</div>
            <div style={{ display:'flex', flexDirection:'column', gap:8, marginBottom:16 }}>
              {providers.map(p => (
                <button key={p.provider_id} onClick={() => setSelProvider(p)} style={{
                  padding:'14px 16px', borderRadius:12, border:'1px solid rgba(255,255,255,0.08)',
                  background:'rgba(255,255,255,0.03)', color:'#f0f1fa', textAlign:'left', cursor:'pointer',
                  display:'flex', alignItems:'center', gap:12,
                }}>
                  <span style={{ fontSize:28 }}>{p.logo_emoji}</span>
                  <div>
                    <div style={{ fontWeight:600, fontSize:13 }}>{p.display_name}</div>
                    <div style={{ fontSize:11, color:'rgba(255,255,255,0.3)' }}>{p.description}</div>
                  </div>
                </button>
              ))}
            </div>
            <button onClick={() => setSourceType('')} style={{ fontSize:12, color:'rgba(255,255,255,0.3)', background:'none', border:'none', cursor:'pointer' }}>← Back</button>
          </Card>
        )}

        {/* ── STEP 1c: Provider credentials ────────────────────────────── */}
        {step === 1 && sourceType === 'provider' && selProvider && (
          <Card>
            <div style={{ fontSize:11, color:'rgba(255,255,255,0.3)', textTransform:'uppercase', letterSpacing:'.1em', marginBottom:6 }}>Step 2 of 3</div>
            <div style={{ display:'flex', alignItems:'center', gap:10, marginBottom:16 }}>
              <span style={{ fontSize:30 }}>{selProvider.logo_emoji}</span>
              <div>
                <div style={{ fontSize:17, fontWeight:700, color:'#f0f1fa' }}>Connect {selProvider.display_name}</div>
                <div style={{ fontSize:12, color:'rgba(255,255,255,0.35)' }}>{selProvider.description}</div>
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
                {f.hint && <div style={{ fontSize:11, color:'rgba(255,255,255,0.3)' }}>{f.hint}</div>}
              </div>
            ))}
            {provResult && (
              <StatusBox ok={provResult.ok} message={provResult.ok
                ? `✓ Connected to ${provResult.details?.merchant_name || selProvider.display_name}`
                : provResult.error} />
            )}
            <div style={{ display:'flex', gap:8 }}>
              <button onClick={() => setSelProvider(null)} style={{ padding:'9px 14px', borderRadius:10, fontSize:13, background:'transparent', border:'1px solid rgba(255,255,255,0.1)', color:'rgba(255,255,255,0.4)', cursor:'pointer' }}>← Back</button>
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
                await connectProvider(selProvider.provider_id, providerCreds)
                setConnectDone(true)
                setTimeout(() => setStep(3), 600)
              } catch(e) { setConnectErr(e.message) }
              finally { setConnecting(false) }
            }} disabled={!provResult?.ok || connecting}>
              {connecting ? 'Connecting…' : `Connect ${selProvider.display_name} & Sync Data →`}
            </NextBtn>
          </Card>
        )}

        {/* ── STEP 1 (DB): Add database ─────────────────────────────────── */}
        {/* ── STEP 1 (DB original): ─────────────────────────────────────── */}
        {step === 1 && sourceType === 'db' && (
          <Card>
            <div style={{ fontSize:11, color:'rgba(255,255,255,0.3)', textTransform:'uppercase', letterSpacing:'.1em', marginBottom:6 }}>Step 2 of 3</div>
            <div style={{ fontSize:19, fontWeight:700, color:'#f0f1fa', marginBottom:4 }}>Connect your MySQL database</div>
            <div style={{ fontSize:13, color:'rgba(255,255,255,0.4)', marginBottom:22, lineHeight:1.6 }}>
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
              <button onClick={() => setStep(0)} style={{ padding:'10px 18px', borderRadius:10, fontSize:13, background:'transparent', border:'1px solid rgba(255,255,255,0.1)', color:'rgba(255,255,255,0.4)', cursor:'pointer' }}>← Back</button>
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
              Continue → Build Analytics Cache
            </NextBtn>
          </Card>
        )}

        {/* ── STEP 2: Confirm + build cache ─────────────────────────────── */}
        {step === 2 && (
          <Card>
            <div style={{ fontSize:11, color:'rgba(255,255,255,0.3)', textTransform:'uppercase', letterSpacing:'.1em', marginBottom:6 }}>Step 3 of 3</div>
            <div style={{ fontSize:19, fontWeight:700, color:'#f0f1fa', marginBottom:4 }}>Build your analytics cache</div>
            <div style={{ fontSize:13, color:'rgba(255,255,255,0.4)', marginBottom:22, lineHeight:1.6 }}>
              DataMind will now read your schema and ask {llm === 'gemini' ? 'Gemini' : 'DeepSeek'} to generate custom SQL for every analytics template. This runs <strong style={{color:'rgba(255,255,255,.6)'}}>once</strong> and is cached forever.
            </div>

            {/* Summary */}
            <div style={{ background:'rgba(255,255,255,0.03)', border:'1px solid rgba(255,255,255,0.06)', borderRadius:10, padding:'14px 16px', marginBottom:18 }}>
              {[
                ['AI Model', llm === 'gemini' ? '✦ Gemini' : '◈ DeepSeek'],
                ['Database', `${dbForm.database} @ ${dbForm.host}:${dbForm.port}`],
                ['Tables found', dbResult?.table_count || '?'],
              ].map(([k,v]) => (
                <div key={k} style={{ display:'flex', justifyContent:'space-between', padding:'5px 0', borderBottom:'1px solid rgba(255,255,255,0.05)', fontSize:13 }}>
                  <span style={{ color:'rgba(255,255,255,0.35)' }}>{k}</span>
                  <span style={{ color:'#f0f1fa', fontWeight:500 }}>{v}</span>
                </div>
              ))}
            </div>

            {/* What happens */}
            <div style={{ marginBottom:18 }}>
              {[
                { icon:'🔍', text:'Read your full schema and foreign keys' },
                { icon:'🧠', text:`Ask ${llm==='gemini'?'Gemini':'DeepSeek'} to generate SQL for 21 analytics templates` },
                { icon:'✅', text:'Validate each query with EXPLAIN' },
                { icon:'⚡', text:'Cache everything — future loads are instant, zero AI tokens' },
              ].map(({icon,text},i) => (
                <div key={i} style={{ display:'flex', gap:10, alignItems:'flex-start', marginBottom:10 }}>
                  <span style={{ fontSize:16, flexShrink:0 }}>{icon}</span>
                  <span style={{ fontSize:13, color:'rgba(255,255,255,0.45)', lineHeight:1.5 }}>{text}</span>
                </div>
              ))}
            </div>

            {connectErr && <StatusBox ok={false} message={connectErr} />}
            {connectDone && <StatusBox ok={true} message="Database connected! Building cache in background…" />}

            <div style={{ display:'flex', gap:8 }}>
              <button onClick={() => setStep(1)} style={{ padding:'10px 18px', borderRadius:10, fontSize:13, background:'transparent', border:'1px solid rgba(255,255,255,0.1)', color:'rgba(255,255,255,0.4)', cursor:'pointer' }}>← Back</button>
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
                {connecting ? <><Spinner size={14} color="#fff" />Connecting…</> : connectDone ? '✓ Connected!' : '🚀 Connect & Build Cache'}
              </button>
            </div>
          </Card>
        )}

        {/* ── STEP 3: Done ──────────────────────────────────────────────── */}
        {step === 3 && (
          <Card>
            <div style={{ textAlign:'center', padding:'12px 0 8px' }}>
              <div style={{ fontSize:56, marginBottom:16 }}>🎉</div>
              <div style={{ fontSize:22, fontWeight:700, color:'#f0f1fa', marginBottom:8 }}>You're all set!</div>
              <div style={{ fontSize:13, color:'rgba(255,255,255,0.4)', lineHeight:1.7, marginBottom:24 }}>
                Your database is connected and the analytics cache is building in the background.
                Head to the <strong style={{color:'var(--blue)'}}>Analytics Hub</strong> — your custom templates will appear once the cache is ready.
              </div>

              <div style={{ background:'rgba(79,142,247,0.06)', border:'1px solid rgba(79,142,247,0.15)', borderRadius:10, padding:'12px 14px', marginBottom:20, textAlign:'left' }}>
                {[
                  '⬡  Analytics Hub — click any card to run an analysis instantly',
                  '⌕  Ask a Question — type anything in plain English',
                  '📈  Forecasting — predict future revenue automatically',
                  '📋  Report Builder — generate professional reports',
                ].map((t,i) => <div key={i} style={{ fontSize:12, color:'rgba(255,255,255,0.45)', padding:'4px 0' }}>{t}</div>)}
              </div>

              <button onClick={onComplete} style={{
                width:'100%', padding:'13px', borderRadius:10, fontSize:15, fontWeight:700,
                background:'linear-gradient(135deg,#4f8ef7,#a78bfa)', color:'#fff', border:'none',
                cursor:'pointer', boxShadow:'0 6px 20px rgba(79,142,247,0.35)',
              }}>
                Open Analytics Hub →
              </button>
            </div>
          </Card>
        )}
      </div>
    </div>
  )
}
