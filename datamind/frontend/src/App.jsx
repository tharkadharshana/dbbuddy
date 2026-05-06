import React, { useState, useEffect } from 'react'
import AuthPage       from './pages/AuthPage'
import DiscoverPage   from './pages/DiscoverPage'
import QueryPage      from './pages/QueryPage'
import ForecastPage   from './pages/ForecastPage'
import AnomalyPage    from './pages/AnomalyPage'
import ReportsPage    from './pages/ReportsPage'
import SettingsPage   from './pages/SettingsPage'
import Sidebar        from './components/Sidebar'
import { fetchTables } from './utils/api'

const PAGE_TITLES = {
  discover: 'Analytics Hub',
  query:    'Natural Language Query',
  forecast: 'Forecasting',
  anomaly:  'Anomaly Detection',
  reports:  'Report Builder',
  settings: 'Settings',
}

export default function App() {
  const [user, setUser]     = useState(() => {
    try { return JSON.parse(localStorage.getItem('dm_user')) } catch { return null }
  })
  const [page, setPage]     = useState('discover')
  const [llm, setLlm]       = useState('gemini')
  const [tables, setTables] = useState([])

  useEffect(() => {
    if (!user) return
    fetchTables()
      .then(d => setTables(d.tables || []))
      .catch(() => setTables([]))
  }, [user])

  function handleAuth(u) {
    setUser(u)
  }

  function handleLogout() {
    localStorage.removeItem('dm_token')
    localStorage.removeItem('dm_user')
    setUser(null)
    setTables([])
    setPage('discover')
  }

  if (!user) return <AuthPage onAuth={handleAuth} />

  const pageEl = {
    discover: <DiscoverPage llm={llm} setLlm={setLlm} />,
    query:    <QueryPage    llm={llm} setLlm={setLlm} />,
    forecast: <ForecastPage />,
    anomaly:  <AnomalyPage />,
    reports:  <ReportsPage  llm={llm} setLlm={setLlm} />,
    settings: <SettingsPage user={user} onLogout={handleLogout} />,
  }[page] ?? <DiscoverPage llm={llm} setLlm={setLlm} />

  // Pages where we fill the full height (no scroll on outer container)
  const fillHeight = ['discover','query','reports'].includes(page)

  return (
    <div style={{ display:'flex', height:'100vh', overflow:'hidden', background:'var(--bg)' }}>
      <Sidebar active={page} setActive={setPage} tables={tables} />

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
            {/* DB status */}
            {tables.length > 0
              ? <div style={{ display:'flex', alignItems:'center', gap:6, padding:'4px 12px', background:'var(--green-dim)', borderRadius:20, border:'1px solid rgba(52,209,122,0.2)' }}>
                  <div style={{ width:6, height:6, borderRadius:'50%', background:'var(--green)', boxShadow:'0 0 6px var(--green)' }} />
                  <span style={{ fontSize:11, color:'var(--green)', fontWeight:500 }}>MySQL · {tables.length} tables</span>
                </div>
              : <div onClick={() => setPage('settings')} style={{ display:'flex', alignItems:'center', gap:6, padding:'4px 12px', background:'var(--amber-dim)', borderRadius:20, border:'1px solid rgba(245,166,35,0.2)', cursor:'pointer' }}>
                  <div style={{ width:6, height:6, borderRadius:'50%', background:'var(--amber)' }} />
                  <span style={{ fontSize:11, color:'var(--amber)', fontWeight:500 }}>No DB connected · click to add</span>
                </div>
            }
            {/* Active LLM pill */}
            <div style={{ padding:'4px 12px', background:'var(--blue-dim)', borderRadius:20, border:'1px solid rgba(79,142,247,0.2)', fontSize:11, color:'var(--blue)', fontWeight:500 }}>
              {llm === 'gemini' ? '✦ Gemini' : '◈ DeepSeek'}
            </div>
            {/* User avatar */}
            <div onClick={() => setPage('settings')} style={{ width:30, height:30, borderRadius:'50%', background:'linear-gradient(135deg,#4f8ef7,#a78bfa)', display:'flex', alignItems:'center', justifyContent:'center', fontWeight:700, fontSize:13, color:'#fff', cursor:'pointer', flexShrink:0, title:'Settings' }}>
              {user?.name?.[0]?.toUpperCase() || '?'}
            </div>
          </div>
        </header>

        {/* Page */}
        <div style={{ flex:1, overflow: fillHeight ? 'hidden' : 'auto' }}>
          {pageEl}
        </div>
      </div>
    </div>
  )
}
