import React, { useState } from 'react'
import { login, register, getErrorMessage } from '../utils/api'
import Logo from '../components/Logo'

export default function AuthPage({ onAuth }) {
  const [mode, setMode] = useState('login') // 'login' | 'register'
  const [form, setForm] = useState({ name:'', email:'', password:'' })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))

  async function handleSubmit(e) {
    e.preventDefault()
    setLoading(true); setError('')
    try {
      let data
      if (mode === 'login') {
        data = await login(form.email, form.password)
      } else {
        if (!form.name.trim()) { setError('Name is required'); setLoading(false); return }
        data = await register(form.name, form.email, form.password)
      }
      localStorage.setItem('dm_token', data.token)
      localStorage.setItem('dm_user', JSON.stringify(data.user))
      onAuth(data.user)
    } catch(e) {
      setError(getErrorMessage(e, 'Something went wrong'))
    } finally {
      setLoading(false)
    }
  }

  const inp = { width:'100%', padding:'11px 14px', borderRadius:10, fontSize:14, background:'rgba(255,255,255,0.06)', border:'1px solid rgba(255,255,255,0.1)', color:'#f0f1fa', outline:'none' }

  return (
    <div style={{ minHeight:'100vh', display:'flex', alignItems:'center', justifyContent:'center', background:'#09090f', position:'relative', overflow:'hidden' }}>

      {/* Animated glow blobs */}
      <div style={{ position:'absolute', width:600, height:600, borderRadius:'50%', background:'radial-gradient(circle, rgba(79,142,247,0.12) 0%, transparent 70%)', top:'-10%', left:'-10%', pointerEvents:'none' }} />
      <div style={{ position:'absolute', width:500, height:500, borderRadius:'50%', background:'radial-gradient(circle, rgba(167,139,250,0.10) 0%, transparent 70%)', bottom:'-5%', right:'-5%', pointerEvents:'none' }} />

      <div style={{ width:'100%', maxWidth:420, margin:'0 16px', animation:'fadeUp .4s ease both' }}>

        {/* Logo */}
        <div style={{ textAlign:'center', marginBottom:32 }}>
          {/* The wordmark already reads "SalesPlay AI" — printing APP_NAME
              beneath it said the same thing twice. */}
          <Logo size={44} style={{ marginBottom:14 }} />
          <div style={{ fontSize:13, color:'#5a5f7d' }}>Transform Data into Actionable Intelligence</div>
        </div>

        {/* Card */}
        <div style={{ background:'#0f1018', border:'1px solid rgba(255,255,255,0.07)', borderRadius:18, padding:'28px 28px 24px', boxShadow:'0 24px 64px rgba(0,0,0,0.5)' }}>

          {/* Tab toggle */}
          <div style={{ display:'flex', background:'rgba(255,255,255,0.04)', borderRadius:10, padding:4, marginBottom:24 }}>
            {['login','register'].map(m => (
              <button key={m} onClick={() => { setMode(m); setError('') }} style={{
                flex:1, padding:'8px 0', borderRadius:7, fontSize:13, fontWeight:500,
                background: mode===m ? 'rgba(79,142,247,0.2)' : 'transparent',
                color: mode===m ? '#4f8ef7' : '#5a5f7d',
                border: mode===m ? '1px solid rgba(79,142,247,0.3)' : '1px solid transparent',
                cursor:'pointer', transition:'all .15s',
                textTransform:'capitalize'
              }}>{m === 'login' ? 'Sign In' : 'Create Account'}</button>
            ))}
          </div>

          <form onSubmit={handleSubmit} style={{ display:'flex', flexDirection:'column', gap:14 }}>
            {mode === 'register' && (
              <div>
                <label style={{ fontSize:12, color:'#5a5f7d', display:'block', marginBottom:6, fontWeight:500 }}>Full Name</label>
                <input value={form.name} onChange={e=>set('name',e.target.value)} placeholder="Jane Smith" required style={inp} />
              </div>
            )}
            <div>
              <label style={{ fontSize:12, color:'#5a5f7d', display:'block', marginBottom:6, fontWeight:500 }}>Email Address</label>
              <input type="email" value={form.email} onChange={e=>set('email',e.target.value)} placeholder="you@company.com" required style={inp} />
            </div>
            <div>
              <label style={{ fontSize:12, color:'#5a5f7d', display:'block', marginBottom:6, fontWeight:500 }}>Password</label>
              <input type="password" value={form.password} onChange={e=>set('password',e.target.value)} placeholder={mode==='register' ? 'Min 6 characters' : '••••••••'} required minLength={6} style={inp} />
            </div>

            {error && (
              <div style={{ background:'rgba(240,80,80,0.1)', border:'1px solid rgba(240,80,80,0.2)', borderRadius:8, padding:'10px 12px', fontSize:12, color:'#f05050' }}>
                ⚠ {error}
              </div>
            )}

            <button type="submit" disabled={loading} style={{
              width:'100%', padding:'12px', borderRadius:10, fontSize:14, fontWeight:600,
              background: loading ? 'rgba(79,142,247,0.5)' : 'linear-gradient(135deg,#4f8ef7,#7c6af7)',
              color:'#fff', border:'none', cursor: loading ? 'not-allowed' : 'pointer',
              marginTop:4, boxShadow: loading ? 'none' : '0 4px 16px rgba(79,142,247,0.35)',
              transition:'all .15s', display:'flex', alignItems:'center', justifyContent:'center', gap:8
            }}>
              {loading ? (
                <><svg width="14" height="14" viewBox="0 0 24 24" fill="none" style={{animation:'spin .8s linear infinite'}}><style>{'@keyframes spin{to{transform:rotate(360deg)}}'}</style><circle cx="12" cy="12" r="9" stroke="#fff" strokeWidth="2.5" strokeDasharray="40 20" strokeLinecap="round"/></svg>Please wait…</>
              ) : mode === 'login' ? 'Sign In →' : 'Create Account →'}
            </button>
          </form>

          {mode === 'login' && (
            <div style={{ textAlign:'center', marginTop:16, fontSize:12, color:'#5a5f7d' }}>
              No account?{' '}
              <span onClick={() => { setMode('register'); setError('') }} style={{ color:'#4f8ef7', cursor:'pointer', fontWeight:500 }}>Create one for free</span>
            </div>
          )}
          {mode === 'register' && (
            <div style={{ textAlign:'center', marginTop:16, fontSize:12, color:'#5a5f7d' }}>
              Already have an account?{' '}
              <span onClick={() => { setMode('login'); setError('') }} style={{ color:'#4f8ef7', cursor:'pointer', fontWeight:500 }}>Sign in</span>
            </div>
          )}
        </div>

        <div style={{ textAlign:'center', marginTop:20, fontSize:11, color:'#2a2e42' }}>
          Your data stays in your own database. We never store it.
        </div>
      </div>
    </div>
  )
}
