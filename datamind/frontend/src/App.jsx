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
import { fetchTables, fetchCacheStatus, fetchSettings, fetchConnectedProviders, fetchProviderStats, fetchSubscription, listConversations, ssoLogin } from './utils/api'

export default function App() {
  const [user, setUser]       = useState(() => {
    try { return JSON.parse(localStorage.getItem('dm_user')) } catch { return null }
  })
  const [page, setPage]       = useState('chat')
  const [llm, setLlm]         = useState('openai')
  const [cacheStatus, setCacheStatus] = useState(null)
  const [connection, setConnection]   = useState(null) // active connection summary
  const [showOnboarding, setShowOnboarding] = useState(false)
  const [theme, setTheme]       = useState(() => localStorage.getItem('dm_theme') || 'light')
  const [totalRows, setTotalRows] = useState(0)
  const [sub, setSub]             = useState(null)
  const [hasDB, setHasDB]         = useState(false)
  const [conversations, setConversations]   = useState([])
  const [activeConvId, setActiveConvId]     = useState(null)
  const [ssoPending, setSsoPending] = useState(() => new URLSearchParams(window.location.search).has('sso'))
  const ssoHandledRef = useRef(false)
  const subIntervalRef = useRef(null)
  const pollRef = useRef(null)

  // Embed handoff: a user already authenticated inside the Salesplay Web Embed
  // arrives here with ?sso=<one-time token>. Exchange it for a normal session
  // so they land in the app already signed in — they never see (or need) the
  // generated password on their account. Strip the param either way so it
  // can't be reused or bookmarked.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const token = params.get('sso')
    if (!token) return

    // StrictMode runs effects twice in dev — without this guard, the
    // one-time token gets redeemed twice. The second (failing) request
    // resolves faster than the first (which does a DB lookup), so its
    // .finally() flips ssoPending to false while user is still null,
    // briefly/sometimes permanently landing on the login page.
    if (ssoHandledRef.current) return
    ssoHandledRef.current = true

    ssoLogin(token)
      .then(data => {
        localStorage.setItem('dm_token', data.token)
        localStorage.setItem('dm_user', JSON.stringify(data.user))
        setUser(data.user)
      })
      .catch(() => { /* expired/used link — fall through to normal login */ })
      .finally(() => {
        params.delete('sso')
        const rest = params.toString()
        window.history.replaceState({}, '', window.location.pathname + (rest ? `?${rest}` : ''))
        setSsoPending(false)
      })
  }, [])

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
    subIntervalRef.current = setInterval(loadSub, 5 * 60 * 1000)
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
      setHasDB(hasDB)
      const hasKey      = !!s.has_llm_key
      const hasProvider = providers.connections?.length > 0

      setTotalRows(stats.total_rows || 0)

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

  if (ssoPending) {
    return (
      <div style={{ display:'flex', alignItems:'center', justifyContent:'center', height:'100vh', background:'#09090f' }}>
        <div style={{ width:24, height:24, border:'2px solid rgba(255,255,255,0.15)', borderTopColor:'#4f8ef7', borderRadius:'50%', animation:'spin 0.7s linear infinite' }} />
      </div>
    )
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
    discover:    <DiscoverPage llm={llm} setLlm={setLlm} sub={sub} hasDB={hasDB} onNavigate={setPage} onQueryComplete={loadSub} />,
    forecast:    <ForecastPage sub={sub} onNavigate={setPage} onQueryComplete={loadSub} />,
    anomaly:     <AnomalyPage sub={sub} onNavigate={setPage} onQueryComplete={loadSub} />,
    reports:     <ReportsPage llm={llm} setLlm={setLlm} sub={sub} onNavigate={setPage} onQueryComplete={loadSub} />,
    connections: <ConnectionsPage onConnectionChange={checkSetup} sub={sub} />,
    settings:    <SettingsPage user={user} onLogout={handleLogout} onNavigate={setPage} sub={sub} />,
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
        {/* BILLING HIDDEN — <UsageLimitBanner sub={sub} onNavigate={setPage} /> */}
        <div style={{ flex:1, overflow: noScroll ? 'hidden' : 'auto' }}>
          {pageEl}
        </div>
      </main>
    </div>
  )
}
