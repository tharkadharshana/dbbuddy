import React, { useState, useEffect, useRef } from 'react'
import AuthPage       from './pages/AuthPage'
import DiscoverPage   from './pages/DiscoverPage'
import QueryPage      from './pages/QueryPage'
import ForecastPage   from './pages/ForecastPage'
import AnomalyPage    from './pages/AnomalyPage'
import ReportsPage    from './pages/ReportsPage'
import SettingsPage   from './pages/SettingsPage'
import Sidebar        from './components/Sidebar'
import { fetchTables, fetchCacheStatus } from './utils/api'

const PAGE_TITLES = {
  discover: 'Analytics Hub',
  query:    'Natural Language Query',
  forecast: 'Forecasting',
  anomaly:  'Anomaly Detection',
  reports:  'Report Builder',
  settings: 'Settings',
}

// ── Cache status pill ─────────────────────────────────────────────────────────
function CachePill({ status, onClick }) {
  if (!status) return null

  if (status?.build?.status === 'building') {
    const logCount = status?.build?.progress?.length || 0
    const pct = Math.min(95, Math.round((logCount / 28) * 100))
    return (
      <div onClick={onClick} style={{ display:'flex', alignItems:'center', gap:7, padding:'4px 12px', background:'var(--amber-dim)', borderRadius:20, border:'1px solid rgba(245,166,35,0.2)', cursor:'pointer' }}>
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" style={{animation:'spin .8s linear infinite',flexShrink:0}}>
          <style>{'@keyframes spin{to{transform:rotate(360deg)}}'}</style>
          <circle cx="12" cy="12" r="9" stroke="#f5a623" strokeWidth="2.5" strokeDasharray="40 20" strokeLinecap="round"/>
        </svg>
        <span style={{ fontSize:11, color:'var(--amber)', fontWeight:500 }}>Building cache {pct}%</span>
      </div>
    )
  }

  if (status?.cached) {
    return (
      <div style={{ display:'flex', alignItems:'center', gap:6, padding:'4px 12px', background:'var(--green-dim)', borderRadius:20, border:'1px solid rgba(52,209,122,0.2)' }}>
        <div style={{ width:6, height:6, borderRadius:'50%', background:'var(--green)', boxShadow:'0 0 6px var(--green)' }} />
        <span style={{ fontSize:11, color:'var(--green)', fontWeight:500 }}>⚡ {status.template_count} templates cached</span>
      </div>
    )
  }

  return (
    <div onClick={onClick} style={{ display:'flex', alignItems:'center', gap:6, padding:'4px 12px', background:'var(--blue-dim)', borderRadius:20, border:'1px solid rgba(79,142,247,0.2)', cursor:'pointer' }}>
      <div style={{ width:6, height:6, borderRadius:'50%', background:'var(--blue)' }} />
      <span style={{ fontSize:11, color:'var(--blue)', fontWeight:500 }}>No cache · click to build</span>
    </div>
  )
}

export default function App() {
  const [user, setUser]           = useState(() => {
    try { return JSON.parse(localStorage.getItem('dm_user')) } catch { return null }
  })
  const [page, setPage]           = useState('discover')
  const [llm, setLlm]             = useState('gemini')
  const [tables, setTables]       = useState([])
  const [cacheStatus, setCacheStatus] = useState(null)
  const pollRef = useRef(null)

  // Load tables + cache status when user logs in
  useEffect(() => {
    if (!user) return
    fetchTables()
      .then(d => setTables(d.tables || []))
      .catch(() => setTables([]))
    pollCacheStatus()
  }, [user])

  function pollCacheStatus() {
    // Clear any existing poll
    if (pollRef.current) clearInterval(pollRef.current)

    const check = async () => {
      try {
        const s = await fetchCacheStatus()
        setCacheStatus(s)
        // Stop polling once done or no active build
        if (s?.build?.status !== 'building') {
          clearInterval(pollRef.current)
        }
      } catch(e) {}
    }
    check()
    // Poll every 3s while building
    pollRef.current = setInterval(check, 3000)
  }

  // Restart poll when page changes to discover (cache may have been triggered)
  useEffect(() => {
    if (user && page === 'discover') pollCacheStatus()
  }, [page])

  function handleAuth(u) {
    setUser(u)
  }

  function handleLogout() {
    localStorage.removeItem('dm_token')
    localStorage.removeItem('dm_user')
    if (pollRef.current) clearInterval(pollRef.current)
    setUser(null); setTables([]); setCacheStatus(null); setPage('discover')
  }

  if (!user) return <AuthPage onAuth={handleAuth} />

  const fillHeight = ['discover', 'query', 'reports'].includes(page)

  const pageEl = {
    discover: <DiscoverPage llm={llm} setLlm={setLlm} />,
    query:    <QueryPage    llm={llm} setLlm={setLlm} />,
    forecast: <ForecastPage />,
    anomaly:  <AnomalyPage />,
    reports:  <ReportsPage  llm={llm} setLlm={setLlm} />,
    settings: <SettingsPage user={user} onLogout={handleLogout} />,
  }[page] ?? <DiscoverPage llm={llm} setLlm={setLlm} />

  return (
    <div style={{ display:'flex', height:'100vh', overflow:'hidden', background:'var(--bg)' }}>
      <Sidebar active={page} setActive={setPage} tables={tables} cacheStatus={cacheStatus} />

      <div style={{ flex:1, display:'flex', flexDirection:'column', overflow:'hidden', minWidth:0 }}>
        {/* Top bar */}
        <header style={{
          height:'var(--topbar)', flexShrink:0,
          background:'var(--bg1)', borderBottom:'1px solid var(--border)',
          display:'flex', alignItems:'center', justifyContent:'space-between',
          padding:'0 20px',
        }}>
          <div style={{ fontSize:14, fontWeight:600, color:'var(--text2)' }}>
            {PAGE_TITLES[page]}
          </div>

          <div style={{ display:'flex', alignItems:'center', gap:10 }}>
            {/* Cache status pill */}
            <CachePill status={cacheStatus} onClick={() => setPage('discover')} />

            {/* DB connection status */}
            {tables.length > 0
              ? <div style={{ display:'flex', alignItems:'center', gap:6, padding:'4px 12px', background:'rgba(52,209,122,0.07)', borderRadius:20, border:'1px solid rgba(52,209,122,0.15)' }}>
                  <div style={{ width:6, height:6, borderRadius:'50%', background:'var(--green)', boxShadow:'0 0 6px var(--green)' }} />
                  <span style={{ fontSize:11, color:'var(--green)', fontWeight:500 }}>MySQL · {tables.length} tables</span>
                </div>
              : <div onClick={() => setPage('settings')} style={{ display:'flex', alignItems:'center', gap:6, padding:'4px 12px', background:'var(--amber-dim)', borderRadius:20, border:'1px solid rgba(245,166,35,0.2)', cursor:'pointer' }}>
                  <div style={{ width:6, height:6, borderRadius:'50%', background:'var(--amber)' }} />
                  <span style={{ fontSize:11, color:'var(--amber)', fontWeight:500 }}>No DB · Add in Settings</span>
                </div>
            }

            {/* Active LLM */}
            <div style={{ padding:'4px 12px', background:'var(--blue-dim)', borderRadius:20, border:'1px solid rgba(79,142,247,0.2)', fontSize:11, color:'var(--blue)', fontWeight:500 }}>
              {llm === 'gemini' ? '✦ Gemini' : '◈ DeepSeek'}
            </div>

            {/* User avatar → settings */}
            <div onClick={() => setPage('settings')} title="Settings" style={{ width:30, height:30, borderRadius:'50%', background:'linear-gradient(135deg,#4f8ef7,#a78bfa)', display:'flex', alignItems:'center', justifyContent:'center', fontWeight:700, fontSize:13, color:'#fff', cursor:'pointer', flexShrink:0 }}>
              {user?.name?.[0]?.toUpperCase() || '?'}
            </div>
          </div>
        </header>

        {/* Page content */}
        <div style={{ flex:1, overflow: fillHeight ? 'hidden' : 'auto' }}>
          {pageEl}
        </div>
      </div>
    </div>
  )
}
