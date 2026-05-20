# Phase 2 — Embed Frontend

## Goal

Build a completely separate, lean React application that loads inside the iframe. It shares zero code with the sidebar, billing pages, or any other main-app page. It communicates with the backend using the same API endpoints — just with a direct base URL instead of the Vite proxy.

**When complete:** You can open `http://localhost:5173/embed.html?pk=sp_dev_test` in a browser, go through the 3-step onboarding wizard, have your data sync, and ask questions in the chat — all within a single compact panel.

---

## Step 2.1 — Update `vite.config.js`

Open `datamind/frontend/vite.config.js`. Current content:

```js
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: path => path.replace(/^\/api/, '')
      }
    }
  }
})
```

Replace with:

```js
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: path => path.replace(/^\/api/, '')
      }
    }
  },
  build: {
    rollupOptions: {
      input: {
        main:  'index.html',
        embed: 'src/embed/embed.html',
      }
    }
  }
})
```

**Why:** This tells Vite to build two separate HTML entry points. The `embed` bundle will only include `EmbedApp.jsx` and its imports — no sidebar, no billing, no heavy analytics pages. The final bundle will be significantly smaller.

---

## Step 2.2 — Create `datamind/frontend/src/embed/embed.html`

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>DataMind — Ask Your Data</title>
    <link rel="stylesheet" href="./embed.css" />
  </head>
  <body>
    <div id="embed-root"></div>
    <script type="module" src="./EmbedApp.jsx"></script>
  </body>
</html>
```

---

## Step 2.3 — Create `datamind/frontend/src/embed/embed.css`

This is the minimal CSS for the iframe. It inherits nothing from the main app's `index.css`.

```css
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

html, body, #embed-root {
  height: 100%;
  width: 100%;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: #0f1117;
  color: #e8eaf6;
}

/* CSS variables — matches main app dark theme */
:root {
  --bg:       #0f1117;
  --bg1:      #14161f;
  --bg2:      #1a1d2e;
  --bg3:      #22263a;
  --bg4:      #2a2f47;
  --border:   rgba(255,255,255,0.08);
  --border2:  rgba(255,255,255,0.12);
  --text:     #e8eaf6;
  --text2:    #9ca3c8;
  --text3:    #5a6080;
  --blue:     #4f8ef7;
  --green:    #34d17a;
  --red:      #f05050;
  --red-dim:  rgba(240,80,80,0.1);
  --green-dim:rgba(52,209,122,0.1);
  --font:     -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  --mono:     'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace;
}

button { cursor: pointer; font-family: var(--font); }
input, textarea { font-family: var(--font); }

@keyframes bounce {
  0%, 80%, 100% { transform: scale(0.7); opacity: .5; }
  40%           { transform: scale(1);   opacity: 1;  }
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
```

---

## Step 2.4 — Create `datamind/frontend/src/embed/embedApi.js`

The main app's `api.js` uses `/api` as a relative base URL (proxied by Vite). In production that works because the frontend and backend are on the same domain. But the embed loads from `datamind.ai` inside Salesplay — it needs to call the API with a full URL.

```js
// datamind/frontend/src/embed/embedApi.js
import axios from 'axios'

// In production, set VITE_API_URL=https://api.datamind.ai (or your backend URL)
// In development, it falls back to the same Vite proxy as the main app
const BASE_URL = import.meta.env.VITE_API_URL || ''

const api = axios.create({ baseURL: BASE_URL + '/api' })

// Read token from embed-specific localStorage key to avoid collisions
// with a user who also has the main DataMind app open in another tab
api.interceptors.request.use(cfg => {
  const token = localStorage.getItem('dm_embed_token')
  if (token) cfg.headers.Authorization = `Bearer ${token}`
  return cfg
})

// On 401 clear the embed token (do NOT redirect — we're in an iframe)
api.interceptors.response.use(
  r => r,
  err => {
    if (err.response?.status === 401) {
      localStorage.removeItem('dm_embed_token')
    }
    return Promise.reject(err)
  }
)

export const embedValidateContext = (pk) =>
  api.get(`/embed/context?pk=${encodeURIComponent(pk)}`).then(r => r.data)

export const embedInit = (data) =>
  api.post('/embed/init', data).then(r => r.data)

export const embedLogin = (email, password) =>
  api.post('/auth/login', { email, password }).then(r => r.data)

export const embedValidateProviderCreds = (provider_id, credentials) =>
  api.post('/providers/validate', { provider_id, credentials }).then(r => r.data)

export const embedGetProviderStatus = (connection_id) =>
  api.get(`/providers/${connection_id}/status`).then(r => r.data)

export const embedRunQuery = (question, llm = 'gemini') =>
  api.post('/query', { question, llm }).then(r => r.data)

export const embedGetSubscription = () =>
  api.get('/billing/subscription').then(r => r.data)
```

**Important:** Add `VITE_API_URL` to your `.env` file when deploying:

```
VITE_API_URL=https://api.datamind.ai
```

Leave it blank in local development — the Vite proxy handles it.

---

## Step 2.5 — Create `datamind/frontend/src/embed/EmbedApp.jsx`

This is the root component. It owns the state machine: `loading → error → onboarding → chat`.

```jsx
// datamind/frontend/src/embed/EmbedApp.jsx
import React, { useState, useEffect } from 'react'
import { createRoot } from 'react-dom/client'
import './embed.css'
import { embedValidateContext } from './embedApi'
import EmbedOnboarding from './EmbedOnboarding'
import EmbedChat from './EmbedChat'

function EmbedApp() {
  // Read partner_key from URL ?pk=...
  const params     = new URLSearchParams(window.location.search)
  const partnerKey = params.get('pk') || ''

  const [state, setState]     = useState('loading')   // loading | error | onboarding | chat
  const [context, setContext] = useState(null)         // { partner_name, provider_id }
  const [errorMsg, setError]  = useState('')
  const [user, setUser]       = useState(null)

  useEffect(() => {
    if (!partnerKey) {
      setError('No partner key provided in URL.')
      setState('error')
      return
    }

    // Check if user already has a token from a previous session
    const existingToken = localStorage.getItem('dm_embed_token')

    embedValidateContext(partnerKey)
      .then(ctx => {
        setContext(ctx)
        if (existingToken) {
          // Token exists — go straight to chat. If it's expired, EmbedChat
          // will get a 401 and send us back to onboarding via handleExpired().
          setState('chat')
        } else {
          setState('onboarding')
        }
      })
      .catch(err => {
        const msg = err.response?.data?.detail || 'Invalid embed configuration.'
        setError(msg)
        setState('error')
      })
  }, [partnerKey])

  function handleOnboardingComplete(token, userData) {
    localStorage.setItem('dm_embed_token', token)
    setUser(userData)
    setState('chat')
    // Notify parent window that setup is complete
    window.parent.postMessage({ type: 'dm:ready', user: userData }, '*')
  }

  function handleExpired() {
    // Token expired — send back to onboarding
    localStorage.removeItem('dm_embed_token')
    setState('onboarding')
  }

  function handleLogout() {
    localStorage.removeItem('dm_embed_token')
    setState('onboarding')
  }

  if (state === 'loading') {
    return (
      <div style={{ display:'flex', alignItems:'center', justifyContent:'center', height:'100%' }}>
        <div style={{ width:20, height:20, border:'2px solid var(--border)', borderTopColor:'var(--blue)', borderRadius:'50%', animation:'spin 0.7s linear infinite' }} />
      </div>
    )
  }

  if (state === 'error') {
    return (
      <div style={{ display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', height:'100%', padding:24, textAlign:'center' }}>
        <div style={{ fontSize:32, marginBottom:12 }}>⚠️</div>
        <div style={{ fontSize:14, color:'var(--text2)', lineHeight:1.6 }}>{errorMsg}</div>
      </div>
    )
  }

  if (state === 'onboarding') {
    return (
      <EmbedOnboarding
        context={context}
        partnerKey={partnerKey}
        onComplete={handleOnboardingComplete}
      />
    )
  }

  return (
    <EmbedChat
      context={context}
      onExpired={handleExpired}
      onLogout={handleLogout}
    />
  )
}

// Mount the app
const root = createRoot(document.getElementById('embed-root'))
root.render(<EmbedApp />)
```

---

## Step 2.6 — Create `datamind/frontend/src/embed/EmbedOnboarding.jsx`

This is the 3-step wizard. Study how `OnboardingWizard.jsx` works — this is a simplified, iframe-appropriate version of it.

```jsx
// datamind/frontend/src/embed/EmbedOnboarding.jsx
import React, { useState } from 'react'
import { embedValidateProviderCreds, embedInit, embedLogin } from './embedApi'

const inp = {
  width:'100%', padding:'10px 12px', borderRadius:8, fontSize:13,
  background:'var(--bg3)', border:'1px solid var(--border)',
  color:'var(--text)', outline:'none', marginBottom:10,
}

const btn = (disabled) => ({
  width:'100%', padding:'11px', borderRadius:8, fontSize:13, fontWeight:600,
  background: disabled ? 'rgba(79,142,247,0.3)' : 'linear-gradient(135deg,#4f8ef7,#7c6af7)',
  color:'#fff', border:'none', cursor: disabled ? 'not-allowed' : 'pointer',
  marginTop:6, opacity: disabled ? 0.6 : 1,
})

function StepBar({ step }) {
  return (
    <div style={{ display:'flex', gap:6, marginBottom:20 }}>
      {[0,1,2].map(i => (
        <div key={i} style={{
          flex:1, height:3, borderRadius:2,
          background: i <= step ? 'var(--blue)' : 'var(--bg3)',
          transition:'background .2s',
        }} />
      ))}
    </div>
  )
}

export default function EmbedOnboarding({ context, partnerKey, onComplete }) {
  const [step, setStep]         = useState(0)

  // Step 0 — Salesplay API key
  const [apiToken, setApiToken]     = useState('')
  const [validating, setValidating] = useState(false)
  const [tokenResult, setTokenResult] = useState(null)  // null | {ok, error, details}

  // Step 1 — Account creation or login
  const [mode, setMode]             = useState('register')  // 'register' | 'login'
  const [name, setName]             = useState('')
  const [email, setEmail]           = useState('')
  const [password, setPassword]     = useState('')

  // Step 2 — Connecting + syncing
  const [connecting, setConnecting] = useState(false)
  const [syncConnId, setSyncConnId] = useState(null)
  const [syncMsg, setSyncMsg]       = useState('Connecting your account…')
  const [syncPct, setSyncPct]       = useState(0)
  const [error, setError]           = useState('')

  const providerName = context?.partner_name || 'Salesplay'

  // ── Step 0: Validate the Salesplay API token ────────────────────────────────
  async function handleValidateToken() {
    if (!apiToken.trim()) return
    setValidating(true)
    setTokenResult(null)
    try {
      const r = await embedValidateProviderCreds(
        context.provider_id,
        { api_token: apiToken.trim() }
      )
      setTokenResult(r)
    } catch(e) {
      setTokenResult({ ok:false, error: e.response?.data?.detail || e.message })
    } finally {
      setValidating(false)
    }
  }

  // ── Step 2: Create account + connect + sync ─────────────────────────────────
  async function handleConnect() {
    setConnecting(true)
    setError('')

    try {
      if (mode === 'register') {
        // Use /embed/init — creates account, connects Salesplay, starts sync
        const result = await embedInit({
          partner_key: partnerKey,
          api_token:   apiToken.trim(),
          name:        name.trim(),
          email:       email.trim().toLowerCase(),
          password,
        })
        localStorage.setItem('dm_embed_token', result.token)
        setSyncConnId(result.provider_id)
        pollSync(result.provider_id, result.token)

      } else {
        // Existing user — log in, then connect provider separately
        // (They may already have a Salesplay connection; the backend handles duplicates)
        const result = await embedLogin(email.trim().toLowerCase(), password)
        localStorage.setItem('dm_embed_token', result.token)
        // For existing users, trigger connect via standard endpoint
        const connectResult = await fetch('/api/providers/connect', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${result.token}`,
          },
          body: JSON.stringify({
            provider_id: context.provider_id,
            credentials: { api_token: apiToken.trim() },
          }),
        }).then(r => r.json())
        setSyncConnId(context.provider_id)
        pollSync(context.provider_id, result.token)
      }
    } catch(e) {
      setError(e.response?.data?.detail || e.message || 'Connection failed.')
      setConnecting(false)
    }
  }

  function pollSync(connId, token) {
    let attempts = 0
    const interval = setInterval(async () => {
      attempts++
      try {
        const r = await fetch(`/api/providers/${connId}/status`, {
          headers: { 'Authorization': `Bearer ${token}` }
        }).then(res => res.json())

        const prog = r.progress
        if (prog) {
          setSyncMsg(prog.message || 'Syncing…')
          setSyncPct(prog.percent || 0)
        }

        if (r.status === 'connected' || r.status === 'active') {
          clearInterval(interval)
          setTimeout(() => {
            onComplete(token, { email: email.trim().toLowerCase(), name: name.trim() })
          }, 600)
        } else if (r.status === 'error') {
          clearInterval(interval)
          setError(`Sync failed: ${r.last_error || 'Unknown error'}`)
          setConnecting(false)
        }
      } catch {
        // Network hiccup during sync — keep polling
        // After 3 minutes (90 attempts × 2s) assume success and proceed
        if (attempts > 90) {
          clearInterval(interval)
          onComplete(token, { email: email.trim().toLowerCase(), name: name.trim() })
        }
      }
    }, 2000)
  }

  // ── Render ──────────────────────────────────────────────────────────────────
  return (
    <div style={{
      height:'100%', overflowY:'auto', padding:'20px 16px',
      display:'flex', flexDirection:'column',
    }}>
      {/* Header */}
      <div style={{ textAlign:'center', marginBottom:20 }}>
        <div style={{
          width:40, height:40, borderRadius:11,
          background:'linear-gradient(135deg,#4f8ef7,#a78bfa)',
          display:'inline-flex', alignItems:'center', justifyContent:'center',
          marginBottom:10, boxShadow:'0 4px 16px rgba(79,142,247,0.3)',
        }}>
          <svg width="18" height="18" viewBox="0 0 16 16" fill="none">
            <rect x="2" y="2" width="5" height="5" rx="1" fill="rgba(255,255,255,0.95)"/>
            <rect x="9" y="2" width="5" height="5" rx="1" fill="rgba(255,255,255,0.5)"/>
            <rect x="2" y="9" width="5" height="5" rx="1" fill="rgba(255,255,255,0.5)"/>
            <rect x="9" y="9" width="5" height="5" rx="1" fill="rgba(255,255,255,0.95)"/>
          </svg>
        </div>
        <div style={{ fontSize:15, fontWeight:700, color:'var(--text)' }}>DataMind AI</div>
        <div style={{ fontSize:12, color:'var(--text3)', marginTop:2 }}>Ask your {providerName} data anything</div>
      </div>

      <StepBar step={step} />

      {/* ── STEP 0: Enter Salesplay API Token ───────────────────────────────── */}
      {step === 0 && (
        <div>
          <div style={{ fontSize:14, fontWeight:600, color:'var(--text)', marginBottom:6 }}>
            Connect your {providerName} account
          </div>
          <div style={{ fontSize:12, color:'var(--text2)', marginBottom:14, lineHeight:1.6 }}>
            Enter your {providerName} API Access Token. You can find this in your {providerName} Backoffice under Integrations → Access Token.
          </div>
          <input
            type="password"
            placeholder="eyJh..."
            value={apiToken}
            onChange={e => { setApiToken(e.target.value); setTokenResult(null) }}
            style={{ ...inp, fontFamily:'monospace' }}
          />
          {tokenResult && (
            <div style={{
              padding:'9px 12px', borderRadius:8, fontSize:12, marginBottom:10,
              background: tokenResult.ok ? 'var(--green-dim)' : 'var(--red-dim)',
              color: tokenResult.ok ? 'var(--green)' : 'var(--red)',
              border: `1px solid ${tokenResult.ok ? 'rgba(52,209,122,0.25)' : 'rgba(240,80,80,0.25)'}`,
            }}>
              {tokenResult.ok
                ? `✓ Connected to ${tokenResult.details?.merchant_name || providerName}`
                : `✗ ${tokenResult.error}`}
            </div>
          )}
          <button
            onClick={handleValidateToken}
            disabled={validating || !apiToken.trim()}
            style={btn(validating || !apiToken.trim())}
          >
            {validating ? 'Validating…' : 'Verify API Token'}
          </button>
          {tokenResult?.ok && (
            <button onClick={() => setStep(1)} style={{ ...btn(false), marginTop:8, background:'var(--bg3)', color:'var(--text2)', border:'1px solid var(--border)' }}>
              Continue →
            </button>
          )}
        </div>
      )}

      {/* ── STEP 1: Create account or log in ────────────────────────────────── */}
      {step === 1 && (
        <div>
          <div style={{ fontSize:14, fontWeight:600, color:'var(--text)', marginBottom:6 }}>
            {mode === 'register' ? 'Create your DataMind account' : 'Log in to DataMind'}
          </div>
          <div style={{ fontSize:12, color:'var(--text2)', marginBottom:14, lineHeight:1.6 }}>
            {mode === 'register'
              ? 'Your account lets you access full analytics, forecasting, and reports at datamind.ai.'
              : 'Use your existing DataMind credentials.'}
          </div>
          {mode === 'register' && (
            <input
              type="text"
              placeholder="Your name"
              value={name}
              onChange={e => setName(e.target.value)}
              style={inp}
            />
          )}
          <input
            type="email"
            placeholder="Email address"
            value={email}
            onChange={e => setEmail(e.target.value)}
            style={inp}
          />
          <input
            type="password"
            placeholder="Password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            style={inp}
          />
          <button
            onClick={() => setStep(2)}
            disabled={!email.trim() || !password || (mode === 'register' && !name.trim())}
            style={btn(!email.trim() || !password || (mode === 'register' && !name.trim()))}
          >
            Continue →
          </button>
          <div style={{ textAlign:'center', marginTop:12, fontSize:12, color:'var(--text3)' }}>
            {mode === 'register'
              ? <span>Already have an account? <button onClick={() => setMode('login')} style={{ background:'none', border:'none', color:'var(--blue)', cursor:'pointer', fontSize:12 }}>Log in</button></span>
              : <span>Don't have an account? <button onClick={() => setMode('register')} style={{ background:'none', border:'none', color:'var(--blue)', cursor:'pointer', fontSize:12 }}>Sign up free</button></span>
            }
          </div>
          <button onClick={() => setStep(0)} style={{ background:'none', border:'none', color:'var(--text3)', fontSize:12, cursor:'pointer', marginTop:12, width:'100%' }}>← Back</button>
        </div>
      )}

      {/* ── STEP 2: Connecting + syncing ────────────────────────────────────── */}
      {step === 2 && (
        <div style={{ textAlign:'center' }}>
          <div style={{ fontSize:14, fontWeight:600, color:'var(--text)', marginBottom:6 }}>
            {connecting ? 'Syncing your data…' : 'Ready to connect'}
          </div>

          {!connecting && !error && (
            <>
              <div style={{ fontSize:12, color:'var(--text2)', marginBottom:16, lineHeight:1.6 }}>
                DataMind will sync your {providerName} receipts, products, and customers. This takes 1–3 minutes for most accounts.
              </div>
              <div style={{ background:'var(--bg2)', border:'1px solid var(--border)', borderRadius:8, padding:'12px', marginBottom:16, textAlign:'left' }}>
                {[
                  ['Account', mode === 'register' ? `${name} (${email})` : email],
                  ['Data source', providerName],
                  ['Plan', '14-day free trial starts now'],
                ].map(([k,v]) => (
                  <div key={k} style={{ display:'flex', justifyContent:'space-between', padding:'5px 0', borderBottom:'1px solid var(--border)', fontSize:12 }}>
                    <span style={{ color:'var(--text3)' }}>{k}</span>
                    <span style={{ color:'var(--text)', fontWeight:500 }}>{v}</span>
                  </div>
                ))}
              </div>
              {error && (
                <div style={{ padding:'9px 12px', borderRadius:8, fontSize:12, marginBottom:12, background:'var(--red-dim)', color:'var(--red)', border:'1px solid rgba(240,80,80,0.25)' }}>
                  ✗ {error}
                </div>
              )}
              <button onClick={handleConnect} style={btn(false)}>
                Connect & Start Sync →
              </button>
              <button onClick={() => setStep(1)} style={{ background:'none', border:'none', color:'var(--text3)', fontSize:12, cursor:'pointer', marginTop:10, width:'100%' }}>← Back</button>
            </>
          )}

          {connecting && (
            <>
              <div style={{ fontSize:12, color:'var(--text2)', marginBottom:16 }}>{syncMsg}</div>
              <div style={{ height:4, background:'var(--bg3)', borderRadius:2, overflow:'hidden', marginBottom:12 }}>
                {syncPct > 0
                  ? <div style={{ height:'100%', width:`${syncPct}%`, background:'var(--blue)', borderRadius:2, transition:'width .6s ease' }} />
                  : <div style={{ height:'100%', width:'30%', background:'var(--blue)', borderRadius:2, animation:'obSlide 1.4s linear infinite' }} />
                }
              </div>
              <style>{`@keyframes obSlide{0%{transform:translateX(-200%)}100%{transform:translateX(400%)}}`}</style>
              <div style={{ fontSize:11, color:'var(--text3)' }}>Syncing your {providerName} data — this takes 1–3 minutes</div>
            </>
          )}

          {error && !connecting && (
            <>
              <div style={{ padding:'9px 12px', borderRadius:8, fontSize:12, marginBottom:12, background:'var(--red-dim)', color:'var(--red)', border:'1px solid rgba(240,80,80,0.25)' }}>
                ✗ {error}
              </div>
              <button onClick={handleConnect} style={btn(false)}>Retry</button>
            </>
          )}
        </div>
      )}
    </div>
  )
}
```

---

## Step 2.7 — Create `datamind/frontend/src/embed/EmbedChat.jsx`

This is the chat interface. It is adapted from `datamind/frontend/src/pages/ChatPage.jsx` but stripped of everything specific to the full app (no `UsageMeter`, no `AIQuotaWall` — just the input + messages).

```jsx
// datamind/frontend/src/embed/EmbedChat.jsx
import React, { useState, useRef, useEffect } from 'react'
import { ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { embedRunQuery } from './embedApi'

const TT = { background:'#1c1e2e', border:'1px solid rgba(255,255,255,0.08)', borderRadius:8, fontSize:11, color:'#f0f1fa' }

const SUGGESTIONS = [
  { icon:'💰', text:'What was my total revenue last month?' },
  { icon:'📦', text:'Which products are selling the fastest?' },
  { icon:'👥', text:'Who are my top 10 customers?' },
  { icon:'📍', text:'Compare sales across all my locations' },
]

function TypingDots() {
  return (
    <div style={{ display:'flex', gap:4, alignItems:'center', padding:'4px 0' }}>
      {[0,1,2].map(i => (
        <div key={i} style={{ width:6, height:6, borderRadius:'50%', background:'var(--blue)', opacity:.7, animation:`bounce 1.2s ${i*0.2}s ease-in-out infinite` }} />
      ))}
    </div>
  )
}

function ResultChart({ columns, data }) {
  if (!data?.length || !columns?.length) return null
  const numCols = columns.filter(c => typeof data[0]?.[c] === 'number')
  const strCols = columns.filter(c => typeof data[0]?.[c] === 'string')
  if (!numCols.length || !strCols.length || data.length < 2) return null
  const xKey = strCols[0], y1 = numCols[0], y2 = numCols[1]
  const chartData = data.slice(0,15).map(r => ({ name: String(r[xKey]||'').slice(0,14), [y1]: r[y1], ...(y2 ? {[y2]: r[y2]} : {}) }))
  return (
    <div style={{ marginTop:10, background:'rgba(255,255,255,0.02)', borderRadius:8, padding:10, border:'1px solid var(--border)' }}>
      <ResponsiveContainer width="100%" height={140}>
        <ComposedChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
          <XAxis dataKey="name" tick={{fontSize:9,fill:'#5a5f7d'}} axisLine={false} tickLine={false} />
          <YAxis tick={{fontSize:9,fill:'#5a5f7d'}} axisLine={false} tickLine={false} />
          <Tooltip contentStyle={TT} />
          <Bar dataKey={y1} fill="var(--blue)" radius={[3,3,0,0]} barSize={data.length > 10 ? 6 : 16} />
          {y2 && <Line dataKey={y2} stroke="var(--green)" strokeWidth={1.5} dot={false} />}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}

function ResultTable({ columns, data, rowCount }) {
  const [expanded, setExpanded] = useState(false)
  const visible = expanded ? data : data.slice(0,4)
  const fmt = (col, v) => {
    if (v === null || v === undefined) return <span style={{color:'var(--text3)'}}>—</span>
    if (typeof v === 'number') {
      if (col.includes('revenue')||col.includes('total')||col.includes('amount')||col.includes('price')||col.includes('value'))
        return <span style={{color:'var(--blue)',fontFamily:'monospace'}}>${Number(v).toLocaleString()}</span>
      return <span style={{fontFamily:'monospace',color:'var(--blue)'}}>{Number(v).toLocaleString()}</span>
    }
    return String(v)
  }
  return (
    <div style={{ marginTop:10, borderRadius:8, overflow:'hidden', border:'1px solid var(--border)' }}>
      <div style={{ overflowX:'auto' }}>
        <table style={{ width:'100%', borderCollapse:'collapse', fontSize:11 }}>
          <thead>
            <tr>{columns.map(c => <th key={c} style={{ padding:'6px 10px', textAlign:'left', color:'var(--text3)', fontWeight:500, fontSize:10, textTransform:'uppercase', borderBottom:'1px solid var(--border)', background:'rgba(255,255,255,0.02)', whiteSpace:'nowrap' }}>{c.replace(/_/g,' ')}</th>)}</tr>
          </thead>
          <tbody>
            {visible.map((row,i) => (
              <tr key={i} style={{ borderBottom:'1px solid var(--border)' }}>
                {columns.map(c => <td key={c} style={{ padding:'6px 10px', color:'var(--text2)', whiteSpace:'nowrap' }}>{fmt(c, row[c])}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data.length > 4 && (
        <div onClick={() => setExpanded(e => !e)} style={{ padding:'6px 10px', textAlign:'center', fontSize:10, color:'var(--blue)', cursor:'pointer', borderTop:'1px solid var(--border)' }}>
          {expanded ? '▲ Show less' : `▼ Show all ${rowCount} rows`}
        </div>
      )}
    </div>
  )
}

function Message({ msg }) {
  if (msg.role === 'user') return (
    <div style={{ display:'flex', justifyContent:'flex-end', marginBottom:14 }}>
      <div style={{ maxWidth:'80%', background:'var(--blue)', color:'#fff', borderRadius:'14px 14px 4px 14px', padding:'9px 13px', fontSize:13, lineHeight:1.5 }}>
        {msg.content}
      </div>
    </div>
  )
  return (
    <div style={{ display:'flex', gap:8, marginBottom:18, alignItems:'flex-start' }}>
      <div style={{ width:24, height:24, borderRadius:'50%', background:'linear-gradient(135deg,#4f8ef7,#a78bfa)', display:'flex', alignItems:'center', justifyContent:'center', flexShrink:0, marginTop:2 }}>
        <svg width="11" height="11" viewBox="0 0 16 16" fill="none"><rect x="2" y="2" width="5" height="5" rx="1" fill="rgba(255,255,255,0.9)"/><rect x="9" y="2" width="5" height="5" rx="1" fill="rgba(255,255,255,0.5)"/><rect x="2" y="9" width="5" height="5" rx="1" fill="rgba(255,255,255,0.5)"/><rect x="9" y="9" width="5" height="5" rx="1" fill="rgba(255,255,255,0.9)"/></svg>
      </div>
      <div style={{ flex:1, minWidth:0 }}>
        {msg.loading ? <TypingDots /> : msg.error ? (
          <div style={{ background:'var(--red-dim)', border:'1px solid rgba(240,80,80,0.2)', borderRadius:8, padding:'8px 12px', fontSize:12, color:'var(--red)' }}>⚠ {msg.error}</div>
        ) : (
          <>
            <div style={{ fontSize:13, color:'var(--text)', lineHeight:1.6 }}>{msg.content}</div>
            {msg.data?.data?.length > 0 && <>
              <ResultChart columns={msg.data.columns} data={msg.data.data} />
              <ResultTable columns={msg.data.columns} data={msg.data.data} rowCount={msg.data.row_count} />
            </>}
            {msg.data?.row_count === 0 && <div style={{ fontSize:11, color:'var(--text3)', marginTop:6 }}>No results found.</div>}
          </>
        )}
      </div>
    </div>
  )
}

export default function EmbedChat({ context, onExpired, onLogout }) {
  const [messages, setMessages] = useState([])
  const [input, setInput]       = useState('')
  const [loading, setLoading]   = useState(false)
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior:'smooth' })
  }, [messages])

  async function send(text) {
    const q = (text || input).trim()
    if (!q || loading) return
    setInput('')
    const userMsg  = { role:'user',  content:q,     id: Date.now() }
    const thinkMsg = { role:'ai',    loading:true,  id: Date.now()+1 }
    setMessages(m => [...m, userMsg, thinkMsg])
    setLoading(true)
    try {
      const data = await embedRunQuery(q)
      const rowCount = data.row_count
      const numCol   = data.columns?.find(c => typeof data.data?.[0]?.[c] === 'number')
      let summary = `Found ${rowCount} result${rowCount !== 1 ? 's' : ''}`
      if (numCol && data.data?.[0]) {
        const total = data.data.reduce((s, r) => s + (r[numCol] || 0), 0)
        summary += ` · ${numCol.replace(/_/g,' ')}: ${total.toLocaleString(undefined, {maximumFractionDigits:2})}`
      }
      if (rowCount === 0) summary = 'No matching records found.'
      setMessages(m => m.map(msg => msg.id === thinkMsg.id ? { role:'ai', content:summary, data, id:thinkMsg.id } : msg))
    } catch(e) {
      const status = e.response?.status
      if (status === 401) { onExpired(); return }
      const err = e.response?.data?.detail || e.message
      setMessages(m => m.map(msg => msg.id === thinkMsg.id ? { role:'ai', error:err, id:thinkMsg.id } : msg))
    } finally { setLoading(false) }
  }

  const hasMessages = messages.length > 0

  return (
    <div style={{ display:'flex', flexDirection:'column', height:'100%', overflow:'hidden' }}>

      {/* Minimal header */}
      <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', padding:'10px 14px', borderBottom:'1px solid var(--border)', flexShrink:0 }}>
        <div style={{ display:'flex', alignItems:'center', gap:8 }}>
          <div style={{ width:22, height:22, borderRadius:6, background:'linear-gradient(135deg,#4f8ef7,#a78bfa)', display:'flex', alignItems:'center', justifyContent:'center' }}>
            <svg width="11" height="11" viewBox="0 0 16 16" fill="none"><rect x="2" y="2" width="5" height="5" rx="1" fill="rgba(255,255,255,0.9)"/><rect x="9" y="2" width="5" height="5" rx="1" fill="rgba(255,255,255,0.5)"/><rect x="2" y="9" width="5" height="5" rx="1" fill="rgba(255,255,255,0.5)"/><rect x="9" y="9" width="5" height="5" rx="1" fill="rgba(255,255,255,0.9)"/></svg>
          </div>
          <span style={{ fontSize:13, fontWeight:600, color:'var(--text)' }}>Ask Your Data</span>
        </div>
        <button onClick={onLogout} title="Disconnect" style={{ background:'none', border:'none', color:'var(--text3)', fontSize:11, cursor:'pointer', padding:'2px 6px' }}>
          ⏏ Disconnect
        </button>
      </div>

      {/* Messages */}
      <div style={{ flex:1, overflowY:'auto', padding:'14px 0' }}>
        {!hasMessages ? (
          <div style={{ display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', height:'100%', padding:'0 16px', textAlign:'center' }}>
            <div style={{ fontSize:13, color:'var(--text2)', marginBottom:16, lineHeight:1.6 }}>
              Ask anything about your {context?.partner_name || 'Salesplay'} data in plain English.
            </div>
            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:6, width:'100%' }}>
              {SUGGESTIONS.map(s => (
                <button key={s.text} onClick={() => send(s.text)} style={{
                  display:'flex', alignItems:'flex-start', gap:7, padding:'9px 10px',
                  background:'var(--bg1)', border:'1px solid var(--border)',
                  borderRadius:8, textAlign:'left', color:'var(--text2)', fontSize:11, lineHeight:1.4,
                }}>
                  <span style={{ fontSize:14, flexShrink:0 }}>{s.icon}</span>
                  {s.text}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div style={{ padding:'0 14px' }}>
            {messages.map(msg => <Message key={msg.id} msg={msg} />)}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {/* Input */}
      <div style={{ flexShrink:0, padding:'10px 12px', borderTop: hasMessages ? '1px solid var(--border)' : 'none' }}>
        <div style={{ display:'flex', gap:8, background:'var(--bg1)', border:'1px solid var(--border2)', borderRadius:12, padding:'6px 6px 6px 12px' }}>
          <textarea
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
            placeholder="Ask about your data…"
            rows={1}
            style={{ flex:1, background:'transparent', border:'none', color:'var(--text)', fontSize:13, resize:'none', outline:'none', lineHeight:1.5, padding:'3px 0', maxHeight:90, overflowY:'auto', fontFamily:'var(--font)' }}
          />
          <button onClick={() => send()} disabled={loading || !input.trim()} style={{
            width:32, height:32, borderRadius:8, flexShrink:0, alignSelf:'flex-end',
            background: loading || !input.trim() ? 'var(--bg3)' : 'var(--blue)',
            color: loading || !input.trim() ? 'var(--text3)' : '#fff', border:'none',
            display:'flex', alignItems:'center', justifyContent:'center',
          }}>
            {loading
              ? <div style={{ width:12, height:12, border:'1.5px solid var(--text3)', borderTopColor:'transparent', borderRadius:'50%', animation:'spin 0.7s linear infinite' }} />
              : <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
            }
          </button>
        </div>
        {hasMessages && (
          <div style={{ textAlign:'center', marginTop:6 }}>
            <button onClick={() => setMessages([])} style={{ fontSize:10, color:'var(--text3)', background:'none', border:'none', cursor:'pointer' }}>Clear conversation</button>
          </div>
        )}
      </div>
    </div>
  )
}
```

---

## How to Test Phase 2

**1. Start the dev server:**

```
cd datamind/frontend
npm run dev
```

**2. Open the embed directly:**

```
http://localhost:5173/src/embed/embed.html?pk=sp_dev_test
```

You should see the 3-step onboarding wizard.

**3. Test the full onboarding flow:**
- Enter a valid Salesplay API token → click Verify → should show merchant name
- Enter your name, email, password
- Click Connect & Start Sync → should show the progress bar
- Wait for sync to complete → chat should appear

**4. Test returning user flow:**
- Refresh the page — you should go straight to chat (token is in localStorage)
- Click "Disconnect" — clears the token, onboarding wizard appears again

**5. Test inside an actual iframe:**

Create a test HTML file anywhere and open it in a browser:

```html
<!DOCTYPE html>
<html>
<head><title>Salesplay Embed Test</title></head>
<body style="background:#f0f0f0; padding:40px; font-family:sans-serif;">
  <h2>Salesplay Dashboard</h2>
  <p>This simulates the Salesplay app embedding DataMind.</p>
  <iframe
    src="http://localhost:5173/src/embed/embed.html?pk=sp_dev_test"
    width="400"
    height="600"
    frameborder="0"
    style="border-radius:12px; box-shadow: 0 4px 24px rgba(0,0,0,0.15);"
  ></iframe>
</body>
</html>
```

The onboarding and chat should work correctly inside the iframe.

---

## Build for Production

```
cd datamind/frontend
npm run build
```

This produces two separate builds:
- `dist/index.html` — the main DataMind app (unchanged)
- `dist/src/embed/embed.html` — the embed widget

The embed widget URL in production will be:
```
https://datamind.ai/src/embed/embed.html?pk=sp_live_abc123
```

Configure your web server (nginx/caddy) to serve the `dist` folder as static files. Both entry points are served from the same origin.

---

## Key Design Decisions

**Why a separate Vite entry point?**
The main app bundle includes all pages — forecast, anomaly, billing, reports. That's ~400KB+ of JavaScript. The embed only needs the chat + onboarding wizard. A separate entry produces a lean ~100KB bundle that loads fast inside the iframe.

**Why `dm_embed_token` instead of `dm_token`?**
A user might have the full DataMind app open in another browser tab while Salesplay is open. If both used `dm_token`, the token from one session could interfere with the other. Separate keys keep them isolated.

**Why does `EmbedChat` handle 401 explicitly?**
In the main app, `api.js` handles 401 by redirecting to `/login`. In an iframe, you cannot redirect the whole page. Instead, `EmbedChat` detects the 401 and calls `onExpired()`, which sends the user back to the onboarding wizard.

**Why does `pollSync` fall through after 90 attempts?**
Sync for a large Salesplay account (years of receipts) can take 3–5 minutes. After 3 minutes of polling, if no terminal status has been received (could be a network hiccup), we assume sync is running fine and let the user into the chat. They can always ask questions — sync running in the background just means some very recent data may not be there yet.
