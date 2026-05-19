import React, { useState, useEffect, useRef } from 'react'
import AuthPage          from './pages/AuthPage'
import OnboardingWizard  from './pages/OnboardingWizard'
import ChatPage          from './pages/ChatPage'
import DiscoverPage      from './pages/DiscoverPage'
import ForecastPage      from './pages/ForecastPage'
import AnomalyPage       from './pages/AnomalyPage'
import ReportsPage       from './pages/ReportsPage'
import SettingsPage      from './pages/SettingsPage'
import ConnectionsPage   from './pages/ConnectionsPage'
import BillingPage       from './pages/BillingPage'
import UsagePage         from './pages/UsagePage'
import Sidebar           from './components/Sidebar'
import UsageLimitBanner  from './components/UsageLimitBanner'
import { fetchTables, fetchCacheStatus, fetchSettings, fetchConnectedProviders, fetchProviderStats, fetchSubscription } from './utils/api'

export default function App() {
  const [user, setUser]       = useState(() => {
    try { return JSON.parse(localStorage.getItem('dm_user')) } catch { return null }
  })
  const [page, setPage]       = useState('chat')
  const [llm, setLlm]         = useState('gemini')
  const [cacheStatus, setCacheStatus] = useState(null)
  const [connection, setConnection]   = useState(null) // active connection summary
  const [showOnboarding, setShowOnboarding] = useState(false)
  const [theme, setTheme]       = useState(() => localStorage.getItem('dm_theme') || 'dark')
  const [totalRows, setTotalRows] = useState(0)
  const [sub, setSub]             = useState(null)
  const subIntervalRef = useRef(null)
  const pollRef = useRef(null)

  // Apply theme to document root
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('dm_theme', theme)
  }, [theme])

  useEffect(() => {
    if (!user) return
    checkSetup()
    pollCacheStatus()
    loadSub()
    subIntervalRef.current = setInterval(loadSub, 5 * 60 * 1000)
    return () => clearInterval(subIntervalRef.current)
  }, [user])

  async function loadSub() {
    try { setSub(await fetchSubscription()) } catch { /* silent */ }
  }

  async function checkSetup({ suppressOnboarding = false } = {}) {
    try {
      const [s, providers, stats] = await Promise.all([
        fetchSettings(),
        fetchConnectedProviders().catch(() => ({connections:[]})),
        fetchProviderStats().catch(() => ({total_rows:0})),
      ])
      const hasDB       = s.db_configs?.length > 0
      const hasKey      = !!(s.gemini_api_key || s.deepseek_api_key)
      const hasProvider = providers.connections?.length > 0

      setTotalRows(stats.total_rows || 0)
      if (s.default_llm) setLlm(s.default_llm)

      // Always update connection state — independent of whether a key is configured
      if (hasProvider) {
        const c = providers.connections[0]
        setConnection({ display_name: c.display_name, type:'provider', logo: c.logo_emoji })
      } else if (hasDB) {
        const cfg = s.db_configs[s.active_db_index || 0]
        setConnection({ display_name: cfg?.name || cfg?.database, type:'db', logo:'🗄' })
      } else {
        setConnection({ display_name: 'DataMind DB', type: 'db', logo: '🗄' })
      }

      if (!suppressOnboarding) {
        // Only force onboarding if they have no LLM key AND no connected provider.
        // Integration users can use pre-built analytics without an LLM key.
        if (!hasKey && !hasProvider && !hasDB) { setShowOnboarding(true); return }
      }
    } catch(e) {}
  }

  function pollCacheStatus() {
    if (pollRef.current) clearInterval(pollRef.current)
    const check = async () => {
      try {
        const s = await fetchCacheStatus()
        setCacheStatus(s)
        if (s?.build?.status !== 'building') clearInterval(pollRef.current)
      } catch(e) {}
    }
    check()
    pollRef.current = setInterval(check, 3000)
  }

  function handleAuth(u) { setUser(u) }

  function handleLogout() {
    localStorage.removeItem('dm_token')
    localStorage.removeItem('dm_user')
    if (pollRef.current) clearInterval(pollRef.current)
    setUser(null); setConnection(null); setCacheStatus(null); setPage('chat')
  }

  if (!user) return <AuthPage onAuth={handleAuth} />
  if (showOnboarding) return (
    <OnboardingWizard
      onComplete={() => {
        setShowOnboarding(false)
        checkSetup({ suppressOnboarding: true })
        pollCacheStatus()
      }}
      theme={theme}
      setTheme={setTheme}
    />
  )

  const noScroll = ['chat', 'discover', 'reports'].includes(page)

  const pageEl = {
    chat:        <ChatPage llm={llm} setLlm={setLlm} connection={connection} sub={sub} onNavigate={setPage} />,
    discover:    <DiscoverPage llm={llm} setLlm={setLlm} sub={sub} onNavigate={setPage} />,
    forecast:    <ForecastPage sub={sub} onNavigate={setPage} />,
    anomaly:     <AnomalyPage sub={sub} onNavigate={setPage} />,
    reports:     <ReportsPage llm={llm} setLlm={setLlm} sub={sub} onNavigate={setPage} />,
    connections: <ConnectionsPage onConnectionChange={checkSetup} sub={sub} />,
    settings:    <SettingsPage user={user} onLogout={handleLogout} />,
    billing:     <BillingPage onSubChange={loadSub} />,
    usage:       <UsagePage sub={sub} />,
  }[page] ?? <ChatPage llm={llm} setLlm={setLlm} connection={connection} />

  return (
    <div style={{ display:'flex', height:'100vh', overflow:'hidden', background:'var(--bg)' }}>
      <Sidebar
        active={page}
        setActive={setPage}
        connection={connection}
        cacheStatus={cacheStatus}
        totalRows={totalRows}
        theme={theme}
        setTheme={setTheme}
      />
      <main style={{ flex:1, display:'flex', flexDirection:'column', overflow:'hidden', minWidth:0 }}>
        <UsageLimitBanner sub={sub} onNavigate={setPage} />
        <div style={{ flex:1, overflow: noScroll ? 'hidden' : 'auto' }}>
          {pageEl}
        </div>
      </main>
    </div>
  )
}
