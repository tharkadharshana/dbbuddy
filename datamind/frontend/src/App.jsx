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
import DocsPage          from './pages/DocsPage'
import Sidebar           from './components/Sidebar'
import UsageLimitBanner  from './components/UsageLimitBanner'
import { fetchTables, fetchCacheStatus, fetchSettings, fetchConnectedProviders, fetchProviderStats, fetchSubscription, listConversations } from './utils/api'

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
  const [conversations, setConversations]   = useState([])
  const [activeConvId, setActiveConvId]     = useState(null)
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
    loadConversations()
    subIntervalRef.current = setInterval(loadSub, 5 * 1 * 1000)
    return () => clearInterval(subIntervalRef.current)
  }, [user])

  async function loadSub() {
    try { setSub(await fetchSubscription()) } catch { /* silent */ }
  }

  async function loadConversations() {
    try {
      const data = await listConversations()
      setConversations(data.conversations || [])
    } catch { /* silent */ }
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

  const handleConvSelect = (convId) => {
    setActiveConvId(convId)
    setPage('chat')
  }

  const handleConvCreated = () => {
    // Only refresh the sidebar list — do NOT setActiveConvId here.
    // ChatPage manages its own convId internally during an active send().
    // Calling setActiveConvId would change the prop, trigger the useEffect
    // inside ChatPage, and wipe the in-flight "thinking…" message.
    loadConversations()
  }

  const handleConvDeleted = (convId) => {
    if (activeConvId === convId) setActiveConvId(null)
    loadConversations()
  }

  const pageEl = {
    chat:        <ChatPage llm={llm} setLlm={setLlm} connection={connection} sub={sub} onNavigate={setPage}
                           onQueryComplete={loadSub} activeConvId={activeConvId}
                           onConvCreated={handleConvCreated} onConversationChange={loadConversations} />,
    discover:    <DiscoverPage llm={llm} setLlm={setLlm} sub={sub} onNavigate={setPage} onQueryComplete={loadSub} />,
    forecast:    <ForecastPage sub={sub} onNavigate={setPage} onQueryComplete={loadSub} />,
    anomaly:     <AnomalyPage sub={sub} onNavigate={setPage} onQueryComplete={loadSub} />,
    reports:     <ReportsPage llm={llm} setLlm={setLlm} sub={sub} onNavigate={setPage} onQueryComplete={loadSub} />,
    connections: <ConnectionsPage onConnectionChange={checkSetup} sub={sub} />,
    settings:    <SettingsPage user={user} onLogout={handleLogout} onNavigate={setPage} />,
    billing:     <BillingPage onSubChange={loadSub} />,
    docs:        <DocsPage />,
  }[page] ?? <ChatPage llm={llm} setLlm={setLlm} connection={connection} />

  return (
    <div style={{ display:'flex', height:'100vh', overflow:'hidden', background:'var(--bg)' }}>
      <Sidebar
        active={page}
        setActive={(p) => { setPage(p); if (p !== 'chat') setActiveConvId(null) }}
        connection={connection}
        cacheStatus={cacheStatus}
        totalRows={totalRows}
        theme={theme}
        setTheme={setTheme}
        conversations={conversations}
        activeConvId={activeConvId}
        onConvSelect={handleConvSelect}
        onConvCreate={() => { setActiveConvId(null); setPage('chat') }}
        onConvDelete={handleConvDeleted}
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
