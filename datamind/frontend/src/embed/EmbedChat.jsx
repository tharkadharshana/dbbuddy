/**
 * EmbedChat.jsx — Chat interface for the iframe embed widget.
 *
 * Adapted from ChatPage.jsx — stripped of sidebar, billing UI, LLM selector,
 * and UsageMeter. Sized for a compact iframe panel.
 *
 * Handles 401 responses by calling onExpired() (cannot redirect in an iframe).
 */
import React, { useState, useRef, useEffect } from 'react'
import { ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { embedRunQuery, embedStreamQuery, embedGetSSOHandoff, embedCreateConversation, embedGetSubscription } from './embedApi'
import { getErrorMessage } from '../utils/api'
import { formatCurrency } from '../utils/locale'
import { notifyParent } from './EmbedApp'
import EmbedHistoryDrawer from './EmbedHistoryDrawer'
import { appName, productTitle as resolveProductTitle } from './embedBranding'
import Logo from '../components/Logo'

const TT = {
  background:'#1c1e2e', border:'1px solid rgba(255,255,255,0.08)',
  borderRadius:8, fontSize:11, color:'#f0f1fa',
}

const SUGGESTIONS = [
  { icon:'💰', text:'What was my total revenue last month?' },
  { icon:'📦', text:'Which products are selling the fastest?' },
  { icon:'👥', text:'Who are my top 10 customers?' },
  { icon:'📍', text:'Compare sales across all my locations' },
]

// ── Beta badge ──────────────────────────────────────────────────────────────
function BetaBadge({ isSalesplay }) {
  if (isSalesplay) {
    return (
      <span style={{
        fontSize: 10, fontWeight: 700, letterSpacing: '.05em', textTransform: 'uppercase',
        color: '#64748B', background: '#E2E8F0',
        borderRadius: 9999, padding: '3px 9px', flexShrink: 0,
      }}>
        Beta
      </span>
    )
  }
  return (
    <span style={{
      fontSize: 9, fontWeight: 700, letterSpacing: '.06em', textTransform: 'uppercase',
      color: 'var(--blue)',
      background: 'rgba(79,142,247,0.12)',
      borderRadius: 6, padding: '2px 6px', marginLeft: 6,
    }}>
      Beta
    </span>
  )
}

// ── Token usage indicator ──────────────────────────────────────────────────────
function TokenUsage({ sub, isSalesplay }) {
  if (!sub || sub.status === 'no_subscription') return null
  const used  = sub.tokens_used || 0
  const total = sub.tokens_total_available || sub.tokens_limit || 1
  const pct   = Math.min(100, Math.round((used / total) * 100))

  if (isSalesplay) {
    if (pct < 90) return null
    const color = pct >= 100 ? '#EF4444' : '#F59E0B'
    return (
      <div title={`${used.toLocaleString()} / ${total.toLocaleString()} tokens used`} style={{ marginTop: 12 }}>
        <div style={{ position:'relative', height:6, borderRadius:99, background:'#E2E8F0' }}>
          <div style={{ position:'absolute', left:0, top:0, height:'100%', width:`${pct}%`, borderRadius:99, background:color, transition:'width .3s' }} />
          <div style={{ position:'absolute', top:'50%', left:`${pct}%`, transform:'translate(-50%, -50%)', width:14, height:14, borderRadius:'50%', background:color, border:'2px solid #fff', boxShadow:'0 1px 3px rgba(0,0,0,0.15)', transition:'left .3s' }} />
        </div>
        <div style={{ textAlign:'right', fontSize:12, fontWeight:700, color, marginTop:6 }}>{pct}% Tokens Used</div>
      </div>
    )
  }

  const color = pct >= 100 ? 'var(--red)' : pct >= 80 ? 'var(--amber)' : 'var(--blue)'
  return (
    <div title={`${used.toLocaleString()} / ${total.toLocaleString()} tokens used`} style={{ display:'flex', flexDirection:'column', gap:3, minWidth:46 }}>
      <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', gap:4 }}>
        <span style={{ fontSize:9, color:'var(--text3)' }}>⚡</span>
        <span style={{ fontSize:9, color, fontFamily:'var(--mono)' }}>{pct}%</span>
      </div>
      <div style={{ height:3, borderRadius:99, background:'var(--bg3)', overflow:'hidden' }}>
        <div style={{ height:'100%', width:`${pct}%`, borderRadius:99, background:color, transition:'width .3s' }} />
      </div>
    </div>
  )
}

// ── Typing indicator ──────────────────────────────────────────────────────────
function TypingDots() {
  return (
    <div style={{ display:'flex', gap:4, alignItems:'center', padding:'4px 0' }}>
      {[0,1,2].map(i => (
        <div key={i} style={{ width:6, height:6, borderRadius:'50%', background:'var(--blue)', opacity:.7, animation:`bounce 1.2s ${i*0.2}s ease-in-out infinite` }} />
      ))}
    </div>
  )
}

const _CODE_COLS = /^(sku|code|customer_code|shop_id|product_code|item_code)$/i

// ── Chart ─────────────────────────────────────────────────────────────────────
function ResultChart({ columns, data, theme }) {
  if (!data?.length || !columns?.length) return null
  const numCols = columns.filter(c => typeof data[0]?.[c] === 'number')
  const strCols = columns.filter(c => typeof data[0]?.[c] === 'string')
  if (!numCols.length || !strCols.length || data.length < 2) return null
  const y1 = numCols[0], y2 = numCols[1]
  const isLight    = theme === 'light'
  const gridColor  = isLight ? 'rgba(0,0,0,0.06)' : 'rgba(255,255,255,0.06)'
  const tickColor  = isLight ? '#6b7280' : '#5a5f7d'
  const labelKey = strCols.find(c => _CODE_COLS.test(c)) || strCols[0]
  const nameKey  = labelKey !== strCols[0] ? strCols[0] : null
  const chartData = data.slice(0, 15).map(r => ({
    name:     String(r[labelKey] || '').slice(0, 14),
    _tooltip: nameKey ? String(r[nameKey] || '') : null,
    [y1]: r[y1],
    ...(y2 ? { [y2]: r[y2] } : {}),
  }))
  const CustomTooltip = ({ active, payload, label }) => {
    if (!active || !payload?.length) return null
    const fullName = payload[0]?.payload?._tooltip
    return (
      <div style={TT}>
        <p style={{ margin:'0 0 4px', color:'var(--text)', fontWeight:500 }}>{fullName || label}</p>
        {payload.map(p => (
          <p key={p.dataKey} style={{ margin:'2px 0', color:p.color }}>{p.name}: {p.value?.toLocaleString()}</p>
        ))}
      </div>
    )
  }
  return (
    <div style={{ marginTop:10, background:'var(--bg2)', borderRadius:8, padding:10, border:'1px solid var(--border)' }}>
      <ResponsiveContainer width="100%" height={140}>
        <ComposedChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" stroke={gridColor} />
          <XAxis dataKey="name" tick={{ fontSize:9, fill:tickColor }} axisLine={false} tickLine={false} />
          <YAxis tick={{ fontSize:9, fill:tickColor }} axisLine={false} tickLine={false} />
          <Tooltip content={<CustomTooltip />} />
          <Bar dataKey={y1} fill="var(--blue)" radius={[3,3,0,0]} barSize={data.length > 10 ? 6 : 16} />
          {y2 && <Line dataKey={y2} stroke="var(--green)" strokeWidth={1.5} dot={false} />}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}

// ── Table ─────────────────────────────────────────────────────────────────────
function ResultTable({ columns, data, rowCount }) {
  const [expanded, setExpanded] = useState(false)
  const visible = expanded ? data : data.slice(0, 4)

  const fmt = (col, v) => {
    if (v === null || v === undefined)
      return <span style={{ color:'var(--text3)' }}>—</span>
    if (typeof v === 'number') {
      const isCount = col.includes('visit') || col.includes('count') || col.includes('qty') || col.includes('quantity') || col.includes('num_')
      if (!isCount && (col.includes('revenue') || col.includes('total') || col.includes('amount') ||
          col.includes('price') || col.includes('value') || col.includes('spent')))
        return <span style={{ color:'var(--blue)', fontFamily:'var(--mono)' }}>{formatCurrency(v)}</span>
      if (col.includes('pct') || col.includes('rate') || col.includes('percent'))
        return <span style={{ color: v > 0 ? 'var(--green)' : 'var(--red)', fontFamily:'var(--mono)' }}>{v > 0 ? '+' : ''}{v}%</span>
      return <span style={{ fontFamily:'var(--mono)', color:'var(--blue)' }}>{Number(v).toLocaleString()}</span>
    }
    return String(v)
  }

  return (
    <div style={{ marginTop:10, borderRadius:8, overflow:'hidden', border:'1px solid var(--border)' }}>
      <div style={{ overflowX:'auto' }}>
        <table style={{ width:'100%', borderCollapse:'collapse', fontSize:11 }}>
          <thead>
            <tr>
              {columns.map(c => (
                <th key={c} style={{ padding:'6px 10px', textAlign:'left', color:'var(--text3)', fontWeight:500, fontSize:10, textTransform:'uppercase', letterSpacing:'.05em', borderBottom:'1px solid var(--border)', background:'var(--bg2)', whiteSpace:'nowrap' }}>
                  {c.replace(/_/g, ' ')}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((row, i) => (
              <tr key={i} style={{ borderBottom:'1px solid var(--border)' }}>
                {columns.map(c => (
                  <td key={c} style={{ padding:'6px 10px', color:'var(--text2)', whiteSpace:'nowrap' }}>
                    {fmt(c, row[c])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data.length > 4 && (
        <div
          onClick={() => setExpanded(e => !e)}
          style={{ padding:'6px 10px', textAlign:'center', fontSize:10, color:'var(--blue)', cursor:'pointer', borderTop:'1px solid var(--border)' }}
        >
          {expanded ? '▲ Show less' : `▼ Show all ${rowCount} rows`}
        </div>
      )}
    </div>
  )
}

// ── Message bubble ────────────────────────────────────────────────────────────
function Message({ msg, theme }) {
  if (msg.role === 'user') return (
    <div style={{ display:'flex', justifyContent:'flex-end', marginBottom:14 }}>
      <div style={{ maxWidth:'80%', background:'var(--blue)', color:'#fff', borderRadius:'14px 14px 4px 14px', padding:'9px 13px', fontSize:13, lineHeight:1.5 }}>
        {msg.content}
      </div>
    </div>
  )

  return (
    <div style={{ display:'flex', gap:8, marginBottom:18, alignItems:'flex-start' }}>
      <Logo size={24} radius={12} style={{ flexShrink:0, marginTop:2 }} />
      <div style={{ flex:1, minWidth:0 }}>
        {msg.loading ? (
          <div style={{ display:'flex', flexDirection:'column', gap:6 }}>
            <TypingDots />
            {msg.loadingText && (
              <span style={{ fontSize:10, color:'var(--text3)' }}>{msg.loadingText}</span>
            )}
          </div>
        ) : msg.error ? (
          <div style={{ background:'var(--red-dim)', border:'1px solid rgba(240,80,80,0.2)', borderRadius:8, padding:'8px 12px', fontSize:12, color:'var(--red)' }}>
            ⚠ {msg.error}
          </div>
        ) : (
          <>
            {/* Think Mode analysis — shown above the data */}
            {msg.analysis && (
              <div style={{
                marginBottom:10, padding:'10px 12px', borderRadius:8, fontSize:13,
                lineHeight:1.65, color:'var(--text)',
                background:'var(--bg2)', border:'1px solid var(--border2)',
                borderLeft:'3px solid var(--blue)',
              }}>
                <div style={{ fontSize:10, fontWeight:600, color:'var(--blue)', textTransform:'uppercase', letterSpacing:'.06em', marginBottom:5 }}>
                  🧠 Think Mode
                </div>
                {msg.analysis.replace(/\*\*/g, '').replace(/\*/g, '').replace(/_{2}/g, '').replace(/_/g, '')}
              </div>
            )}

            <div style={{ fontSize:13, color:'var(--text)', lineHeight:1.6 }}>{msg.content}</div>
            {msg.data?.data?.length > 0 && (
              <>
                <ResultChart columns={msg.data.columns} data={msg.data.data} theme={theme} />
                <ResultTable columns={msg.data.columns} data={msg.data.data} rowCount={msg.data.row_count} />
              </>
            )}
          </>
        )}
      </div>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────
export default function EmbedChat({ context, onExpired, onLogout, onCollapse, initialInput = '' }) {
  const productTitle = resolveProductTitle(context)
  const APP_NAME = appName(context)
  const isSalesplay = context?.provider_id === 'salesplay'
  // Same accent used by the collapsed search bar (EmbedSearchBar) — keeps the
  // "closed" pill and the "open" input bar visually identical.
  const accent = context?.branding?.accent_color || '#3B82F6'
  const [messages, setMessages] = useState([])
  const [input, setInput]       = useState(initialInput)
  const [loading, setLoading]   = useState(false)
  const [thinkMode, setThinkMode] = useState(true) // always on for the SalesPlay embed — toggle UI hidden below
  const [hoveredSuggestion, setHoveredSuggestion] = useState(null)
  const [inputFocused, setInputFocused] = useState(false)
  const [convId, setConvId] = useState(null)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [sub, setSub] = useState(null)
  const bottomRef = useRef(null)
  const inputRef  = useRef(null)

  // Track iframe viewport width so we can tighten layout on narrow phones
  const [windowWidth, setWindowWidth] = useState(() => window.innerWidth)
  useEffect(() => {
    const handler = () => setWindowWidth(window.innerWidth)
    window.addEventListener('resize', handler)
    return () => window.removeEventListener('resize', handler)
  }, [])
  const isNarrow = windowWidth < 370

  // Token usage for the header indicator — non-fatal if it fails.
  useEffect(() => {
    embedGetSubscription().then(setSub).catch(() => setSub(null))
  }, [])

  // Auto-focus the textarea when opened from the collapsed search bar
  useEffect(() => {
    if (inputRef.current) {
      inputRef.current.focus()
      // Place cursor at end if text was carried over from the collapsed bar
      const len = inputRef.current.value.length
      inputRef.current.setSelectionRange(len, len)
    }
  }, [])

  // ── Theme ───────────────────────────────────────────────────────────────────
  const [theme, setTheme] = useState(() => localStorage.getItem('dm_embed_theme') || 'light')

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('dm_embed_theme', theme)
  }, [theme])

  const toggleTheme = () => setTheme(t => t === 'dark' ? 'light' : 'dark')

  // Opens the standalone DataMind app in a new tab, already signed in — the
  // user authenticated once inside the partner iframe and shouldn't have to
  // log in again (their account password is a generated value they never see).
  // We exchange the embed session for a one-time handoff link; the main app
  // redeems it for a normal session token on load. Falls back to a plain
  // (logged-out) link if the handoff call fails for any reason.
  async function openMainApp() {
    const base = import.meta.env.VITE_APP_URL || 'https://app.datamind.ai'
    // Open the tab synchronously (within the click gesture) so Safari/iOS
    // popup blockers don't kill it — we redirect it once the token resolves.
    // (Can't pass noopener here or we'd lose the handle needed to redirect it;
    // the destination is always our own trusted app.)
    const tab = window.open('about:blank', '_blank')
    let url = base
    try {
      const { token } = await embedGetSSOHandoff()
      url = `${base}${base.includes('?') ? '&' : '?'}sso=${encodeURIComponent(token)}`
    } catch {
      // No handoff token — user lands on the main app's login screen instead.
    }
    notifyParent('dm:open_main_app', { url })
    if (tab) tab.location.href = url
    else window.open(url, '_blank')
  }

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior:'smooth' })
  }, [messages])

  async function send(text) {
    const q = (text || input).trim()
    if (!q || loading) return
    setInput('')
    inputRef.current?.focus()

    // Lazy conversation creation — mirrors ChatPage.jsx so this thread shows
    // up in the main app's history (same /conversations data, same user).
    let currentConvId = convId
    if (!currentConvId) {
      const newId = crypto.randomUUID
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2)}`
      try {
        await embedCreateConversation(newId)
        currentConvId = newId
        setConvId(newId)
      } catch {
        currentConvId = null
      }
    }

    const userMsg  = { role:'user', content:q,           id: Date.now() }
    const thinkMsg = { role:'ai',  loading:true,
                       loadingText: thinkMode ? 'Thinking…' : null,
                       id: Date.now() + 1 }
    setMessages(m => [...m, userMsg, thinkMsg])
    setLoading(true)

    notifyParent('dm:query', { question: q })

    // After 10s still loading → surface a hint so user knows it's working
    const slowTimer = setTimeout(() => {
      setMessages(m => m.map(msg =>
        msg.id === thinkMsg.id && msg.loading
          ? { ...msg, loadingText: 'Complex queries can take a moment...' }
          : msg
      ))
    }, 10000)

    try {
      // Streaming first: live progress steps, model thinking, and the answer
      // text as it arrives. Falls back to the plain endpoint when streaming is
      // disabled server-side (404) or the stream dies before producing output.
      const patchMsg = (patch) => setMessages(m => m.map(msg =>
        msg.id === thinkMsg.id ? { ...msg, ...patch } : msg
      ))
      let sawOutput = false
      let data = null
      try {
        data = await embedStreamQuery(q, 'default', thinkMode, currentConvId, {
          onStep:     p => { if (p.label) patchMsg({ loadingText: p.label }) },
          onThinking: p => { if (p.text) patchMsg({ loadingText: p.text.slice(0, 140) }) },
          onToken:    t => {
            sawOutput = true
            setMessages(m => m.map(msg => {
              if (msg.id !== thinkMsg.id) return msg
              const acc = (msg.streamText || '') + t
              // Render the growing answer in the Think Mode box; the data
              // table/summary replace this when the final payload lands.
              return { role:'ai', id: thinkMsg.id, content:'', analysis: acc, streamText: acc }
            }))
          },
        })
      } catch (se) {
        if (se.status === 401) { onExpired(); return }
        data = null   // stream failed
      }
      // Re-run via the plain endpoint only if the stream produced nothing —
      // never after tokens were shown (that would double-charge the question).
      if (!data && !sawOutput) data = await embedRunQuery(q, 'default', thinkMode, currentConvId)
      if (!data) data = { success: false, type: 'error', message: 'The connection dropped mid-answer. Please try again.' }
      const rowCount = data.row_count
      const type     = data.type
      let summary

      if (type === 'conversational' || type === 'clarification') {
        summary = data.message || 'How can I help you with your data?'
      } else if (!data.success || type === 'error') {
        summary = data.message || 'Something went wrong. Please try again.'
      } else {
        const numCol = data.columns?.find(c => typeof data.data?.[0]?.[c] === 'number')
        summary = `Found ${rowCount} result${rowCount !== 1 ? 's' : ''}`
        if (numCol && data.data?.[0]) {
          const total = data.data.reduce((s, r) => s + (r[numCol] || 0), 0)
          summary += ` · ${numCol.replace(/_/g, ' ')}: ${total.toLocaleString(undefined, { maximumFractionDigits:2 })}`
        }
        if (rowCount === 0) summary = "I couldn't find anything matching that. Try rephrasing or broadening your question."
      }

      setMessages(m => m.map(msg =>
        msg.id === thinkMsg.id
          ? { role:'ai', content: summary, data, analysis: data.analysis || null, id: thinkMsg.id }
          : msg
      ))
      embedGetSubscription().then(setSub).catch(() => {})
    } catch (e) {
      if (e.response?.status === 401) {
        // Token expired — send user back to onboarding (can't redirect in iframe)
        onExpired()
        return
      }
      const err = getErrorMessage(e)
      setMessages(m => m.map(msg =>
        msg.id === thinkMsg.id ? { role:'ai', error:err, id:thinkMsg.id } : msg
      ))
    } finally {
      clearTimeout(slowTimer)
      setLoading(false)
    }
  }

  const hasMessages = messages.length > 0

  return (
    <div className={onCollapse ? 'dm-panel-enter' : undefined} style={{
      position: onCollapse ? 'absolute' : 'relative',
      ...(onCollapse ? { left:0, right:0, bottom:0 } : {}),
      display:'flex', flexDirection:'column', height:'100%', overflow:'hidden',
      background: isSalesplay ? 'linear-gradient(180deg, #E6F2FD 0%, #FFFFFF 100%)' : undefined,
    }}>

      {/* Header */}
      {isSalesplay ? (
        <div style={{ padding: isNarrow ? '10px 12px 10px' : '14px 16px 12px', borderBottom:'1px solid rgba(15,23,42,0.05)', flexShrink:0 }}>
          <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', gap: isNarrow ? 6 : 8 }}>
            <div style={{ display:'flex', alignItems:'center', gap: isNarrow ? 6 : 8, minWidth:0 }}>
              <Logo size={50} radius={9} style={{ flexShrink:0 }} />
              <span style={{ fontSize: isNarrow ? 15 : 18, fontWeight:800, color:'#191C1E', letterSpacing:'-0.02em', fontFamily:"'Manrope', 'Plus Jakarta Sans', sans-serif", overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{/* {productTitle} */}Ask AI</span>
              {!isNarrow && <BetaBadge isSalesplay={isSalesplay} />}
            </div>
            <div style={{ display:'flex', alignItems:'center', gap: isNarrow ? 6 : 8, flexShrink:0 }}>
              {/* Minimize back to the collapsed search bar (?layout=bar only) */}
              {onCollapse && (
                <button
                  onClick={onCollapse}
                  title="Minimize"
                  style={{
                    width:34, height:34, borderRadius:'50%',
                    background:'#fff', border:'1.5px solid #191C1E', cursor:'pointer',
                    display:'flex', alignItems:'center', justifyContent:'center',
                    color:'#191C1E', flexShrink:0,
                  }}
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M6 9l6 6 6-6" />
                  </svg>
                </button>
              )}
              {/* Open in main DataMind app — leaves the partner iframe in a new tab */}
              <button
                onClick={openMainApp}
                title={`Open ${productTitle} in a new tab`}
                style={{
                  background:'#fff', border:'1.5px solid #191C1E',
                  borderRadius:9999, cursor:'pointer',
                  padding: isNarrow ? '7px 10px' : '7px 14px',
                  display:'flex', alignItems:'center', gap:5,
                  fontSize:12, fontWeight:700, color:'#191C1E', whiteSpace:'nowrap',
                }}
              >
                {isNarrow ? <span style={{ fontSize:13 }}>↗</span> : <>Open App <span style={{ fontSize:13 }}>↗</span></>}
              </button>
              {/* Chat history toggle */}
              <button
                onClick={() => setHistoryOpen(true)}
                title="Chat history"
                style={{
                  width:34, height:34, borderRadius:'50%',
                  background:'#fff', border:'1.5px solid #191C1E', cursor:'pointer',
                  display:'flex', alignItems:'center', justifyContent:'center',
                  color:'#191C1E', flexShrink:0,
                }}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="12" r="9" />
                  <path d="M12 7v5l3 3" />
                </svg>
              </button>
            </div>
          </div>
          <TokenUsage sub={sub} isSalesplay={isSalesplay} />
        </div>
      ) : (
        <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', padding: isNarrow ? '10px 10px' : '10px 14px', borderBottom:'1px solid var(--border)', flexShrink:0, gap:8 }}>
          <div style={{ display:'flex', alignItems:'center', gap:8, minWidth:0, flex:1 }}>
            {/* Chat history toggle */}
            <button
              onClick={() => setHistoryOpen(true)}
              title="Chat history"
              style={{
                background:'none', border:'none', cursor:'pointer', padding:4,
                display:'flex', alignItems:'center', justifyContent:'center',
                color:'var(--text3)', flexShrink:0,
              }}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="9" />
                <path d="M12 7v5l3 3" />
              </svg>
            </button>
            <Logo size={22} radius={6} style={{ flexShrink:0 }} />
            <span style={{ fontSize:15, fontWeight:600, color:'var(--text)', letterSpacing:'-0.01em', whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>{productTitle}</span>
            {!isNarrow && <BetaBadge isSalesplay={isSalesplay} />}
          </div>
          <div style={{ display:'flex', alignItems:'center', gap: isNarrow ? 4 : 8, flexShrink:0 }}>
            <TokenUsage sub={sub} isSalesplay={isSalesplay} />
            {/* Open in main DataMind app — leaves the partner iframe in a new tab */}
            <button
              onClick={openMainApp}
              title={`Open ${productTitle} in a new tab`}
              style={{
                background:'none', border:'1px solid var(--border2)',
                borderRadius:20, cursor:'pointer', padding:'3px 8px',
                display:'flex', alignItems:'center', gap:5,
                fontSize:11, color:'var(--text2)',
              }}
            >
              {isNarrow ? <span style={{ fontSize:12 }}>↗</span> : <><span style={{ fontSize:12 }}>↗</span> Open in {APP_NAME}</>}
            </button>
            {/* Light / dark toggle */}
            <button
              onClick={toggleTheme}
              title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
              style={{
                background:'none', border:'none',
                borderRadius:8, cursor:'pointer', padding:'4px 6px',
                display:'flex', alignItems:'center', justifyContent:'center',
                fontSize:13, color:'var(--text3)',
              }}
            >
              {theme === 'dark' ? '☀️' : '🌙'}
            </button>
            {/* Minimize back to the collapsed search bar (?layout=bar only) */}
            {onCollapse && (
              <button
                onClick={onCollapse}
                title="Minimize"
                style={{
                  background:'none', border:'none',
                  borderRadius:8, cursor:'pointer', padding:'4px 6px',
                  display:'flex', alignItems:'center', justifyContent:'center',
                  color:'var(--text3)',
                }}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M6 9l6 6 6-6" />
                </svg>
              </button>
            )}
          </div>
        </div>
      )}

      {/* Messages area */}
      <div className={isSalesplay ? 'dm-scroll-hidden' : undefined} style={{ flex:1, overflowY:'auto', padding:'14px 0' }}>
        {!hasMessages ? (
          <div style={{ display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', height:'100%', padding: isSalesplay ? (isNarrow ? '0 16px' : '0 24px') : (isNarrow ? '0 12px' : '0 18px'), textAlign:'center' }}>
            <div style={{
              fontSize: isSalesplay ? 21 : 18, fontWeight: isSalesplay ? 800 : 600,
              color: isSalesplay ? '#0F172A' : 'var(--text)',
              marginBottom: isSalesplay ? 6 : 6, letterSpacing:'-0.02em',
            }}>
              {isSalesplay ? 'Ask Your Sales Data' : `Ask Your ${context?.partner_name || 'Salesplay'} Data`}
            </div>
            <div style={{
              fontSize: isSalesplay ? 12.5 : 13, color: isSalesplay ? '#64748B' : 'var(--text2)',
              marginBottom: isSalesplay ? 16 : 20, lineHeight:1.5, maxWidth: isSalesplay ? 240 : 300,
            }}>
              {isSalesplay
                ? 'Query your business metrics in natural language'
                : 'Ask anything about your data in plain English — revenue, products, customers, and more.'}
            </div>
            <div style={{
              width:'100%', textAlign:'left',
              fontSize: isSalesplay ? 10 : 10, fontWeight: isSalesplay ? 700 : 600,
              color: isSalesplay ? '#94A3B8' : 'var(--text3)',
              textTransform:'uppercase', letterSpacing: isSalesplay ? '.15em' : '.07em',
              marginBottom: isSalesplay ? 10 : 8,
            }}>
              Popular questions
            </div>
            <div style={{ display:'flex', flexDirection:'column', gap: isSalesplay ? 8 : 8, width:'100%' }}>
              {SUGGESTIONS.map((s, i) => {
                const featured = i === 0
                const hovered = hoveredSuggestion === i

                if (isSalesplay) {
                  return (
                    <button
                      key={s.text}
                      onClick={() => send(s.text)}
                      onMouseEnter={() => setHoveredSuggestion(i)}
                      onMouseLeave={() => setHoveredSuggestion(null)}
                      style={{
                        display:'flex', alignItems:'center', justifyContent:'space-between', gap:10,
                        padding:'11px 14px',
                        background:'#FFFFFF', border:'none', borderRadius:14, textAlign:'left',
                        boxShadow: hovered ? '0 6px 24px rgba(0,0,0,0.08)' : '0 4px 20px rgba(0,0,0,0.05)',
                        transform: hovered ? 'translateY(-2px)' : 'none',
                        transition:'transform .15s, box-shadow .15s',
                        cursor:'pointer',
                      }}
                    >
                      <div style={{ display:'flex', alignItems:'center', gap:11 }}>
                        <div style={{ width:30, height:30, display:'flex', alignItems:'center', justifyContent:'center', fontSize:18, flexShrink:0 }}>
                          {s.icon}
                        </div>
                        <span style={{ fontSize:12.5, fontWeight:600, color:'#1E293B', lineHeight:1.35 }}>{s.text}</span>
                      </div>
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#1E293B" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink:0 }}>
                        <path d="M9 5l7 7-7 7"/>
                      </svg>
                    </button>
                  )
                }

                return (
                  <button
                    key={s.text}
                    onClick={() => send(s.text)}
                    onMouseEnter={() => setHoveredSuggestion(i)}
                    onMouseLeave={() => setHoveredSuggestion(null)}
                    style={{
                      display:'flex', alignItems:'center', gap:9,
                      padding: featured ? '13px 14px' : '11px 12px',
                      background: hovered ? 'var(--bg2)' : 'var(--bg1)',
                      border: `1px solid ${hovered ? 'var(--blue)' : 'var(--border)'}`,
                      borderRadius:12, textAlign:'left', color:'var(--text)',
                      fontSize: featured ? 13.5 : 12.5, fontWeight: featured ? 500 : 400, lineHeight:1.4,
                      cursor:'pointer', transition:'transform .15s, box-shadow .15s, border-color .15s, background .15s',
                      transform: hovered ? 'translateY(-2px)' : 'none',
                      boxShadow: hovered ? '0 6px 16px rgba(0,0,0,0.12)' : 'none',
                    }}
                  >
                    <span style={{ fontSize: featured ? 18 : 15, flexShrink:0 }}>{s.icon}</span>
                    <span style={{ flex:1 }}>{s.text}</span>
                    <span style={{ fontSize:13, flexShrink:0, color: hovered ? 'var(--blue)' : 'var(--text3)', transition:'color .15s' }}>→</span>
                  </button>
                )
              })}
            </div>
            <div style={{
              marginTop: isSalesplay ? 14 : 18, display:'flex', alignItems:'center',
              gap: isSalesplay ? 6 : 6, fontSize: isSalesplay ? 11 : 10,
              color: isSalesplay ? '#94A3B8' : 'var(--text3)', fontWeight: isSalesplay ? 500 : 400,
            }}>
              <span style={{
                width: isSalesplay ? 8 : 6, height: isSalesplay ? 8 : 6, borderRadius:'50%',
                background: isSalesplay ? '#4ADE80' : 'var(--green)', display:'inline-block', flexShrink:0,
              }} />
              Real-time data {isSalesplay ? '•' : '·'} Powered by {APP_NAME}
            </div>
          </div>
        ) : (
          <div style={{ padding: isNarrow ? '0 10px' : '0 14px' }}>
            {messages.map(msg => <Message key={msg.id} msg={msg} theme={theme} />)}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {/* Input */}
      <div style={{
        flexShrink:0,
        padding: isSalesplay
          ? (isNarrow ? '10px 12px' : '14px 16px')
          : (isNarrow ? '8px 10px' : '10px 12px'),
        borderTop: !isSalesplay && hasMessages ? '1px solid var(--border)' : 'none',
      }}>
        {/* Think Mode toggle — hidden for the embed; thinkMode is always true above.
        <div style={{ display:'flex', alignItems:'center', gap:6, marginBottom:6 }}>
          <button
            onClick={() => setThinkMode(m => !m)}
            title="Think Mode: runs a second AI call to analyse the data and answer your question directly"
            style={{
              display:'flex', alignItems:'center', gap:5,
              padding:'4px 10px', borderRadius:20, fontSize:11, fontWeight:500,
              background: thinkMode ? 'rgba(79,142,247,0.12)' : 'var(--bg2)',
              color: thinkMode ? 'var(--blue)' : 'var(--text3)',
              border: `1px solid ${thinkMode ? 'var(--blue)' : 'var(--border)'}`,
              cursor:'pointer', transition:'all .15s',
            }}
          >
            🧠 Think Mode {thinkMode ? 'ON' : 'OFF'}
          </button>
          {thinkMode && (
            <span style={{ fontSize:10, color:'var(--text3)' }}>
              Uses X2 Tokens · deducts extra tokens
            </span>
          )}
        </div>
        */}

        {isSalesplay ? (
          <div style={{
            display:'flex', alignItems:'center', gap:10,
            background:'#FFFFFF', borderRadius:9999,
            padding:'8px 8px 8px 14px',
            boxShadow: '0 10px 40px rgba(0,0,0,0.12)',
            border: `1.5px solid ${inputFocused ? accent : 'transparent'}`,
            transition:'border-color .15s',
          }}>
            <span style={{
              width:28, height:28, borderRadius:'50%', flexShrink:0,
              background:`${accent}1A`, display:'flex', alignItems:'center', justifyContent:'center', color:accent,
            }}>
              {/* AI sparkle — matches the collapsed search bar icon */}
              <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor">
                <path d="M12 2l1.7 5.3L19 9l-5.3 1.7L12 16l-1.7-5.3L5 9l5.3-1.7L12 2z" />
                <path d="M19 13.5l.85 2.65L22.5 17l-2.65.85L19 20.5l-.85-2.65L15.5 17l2.65-.85L19 13.5z" />
              </svg>
            </span>
            <textarea
              ref={inputRef}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
              onFocus={() => setInputFocused(true)}
              onBlur={() => setInputFocused(false)}
              placeholder="Ask AI about your data…"
              rows={1}
              style={{ flex:1, background:'transparent', border:'none', color:'#1E293B', fontSize:14, resize:'none', outline:'none', lineHeight:1.5, padding:'8px 0', maxHeight:90, overflowY:'auto', fontFamily:'var(--font)' }}
            />
            <button
              onClick={() => send()}
              disabled={loading || !input.trim()}
              style={{
                width:32, height:32, borderRadius:'50%', flexShrink:0,
                background: loading || !input.trim() ? '#CBD5E1' : accent,
                color:'#fff', border:'none',
                display:'flex', alignItems:'center', justifyContent:'center',
                transition:'background .15s',
              }}
            >
              {loading
                ? <div style={{ width:14, height:14, border:'1.5px solid #fff', borderTopColor:'transparent', borderRadius:'50%', animation:'spin 0.7s linear infinite' }} />
                : <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M2 21l21-9L2 3v7l15 2-15 2v7z" />
                  </svg>
              }
            </button>
          </div>
        ) : (
          <div style={{
            display:'flex', alignItems:'flex-end', gap:8,
            background:'var(--bg1)',
            border: `1.5px solid ${inputFocused ? 'var(--blue)' : 'var(--border2)'}`,
            borderRadius:14, padding:'10px 10px 10px 14px',
            boxShadow: inputFocused ? '0 0 0 3px rgba(79,142,247,0.15)' : 'none',
            transition:'border-color .15s, box-shadow .15s',
          }}>
            <span style={{ fontSize:15, color:'var(--text3)', flexShrink:0, lineHeight:1.4, marginBottom:1 }}>💬</span>
            <textarea
              ref={inputRef}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
              onFocus={() => setInputFocused(true)}
              onBlur={() => setInputFocused(false)}
              placeholder="Ask AI about your data…"
              rows={1}
              style={{ flex:1, background:'transparent', border:'none', color:'var(--text)', fontSize:14, resize:'none', outline:'none', lineHeight:1.5, padding:'3px 0', maxHeight:90, overflowY:'auto', fontFamily:'var(--font)' }}
            />
            <button
              onClick={() => send()}
              disabled={loading || !input.trim()}
              style={{
                width:36, height:36, borderRadius:10, flexShrink:0,
                background: loading || !input.trim() ? 'var(--bg3)' : 'var(--blue)',
                color: loading || !input.trim() ? 'var(--text3)' : '#fff', border:'none',
                display:'flex', alignItems:'center', justifyContent:'center',
                boxShadow: !loading && input.trim() ? '0 2px 8px rgba(79,142,247,0.35)' : 'none',
                transition:'background .15s, box-shadow .15s',
              }}
            >
              {loading
                ? <div style={{ width:13, height:13, border:'1.5px solid var(--text3)', borderTopColor:'transparent', borderRadius:'50%', animation:'spin 0.7s linear infinite' }} />
                : <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
              }
            </button>
          </div>
        )}
        {hasMessages && (
          <div style={{ textAlign:'center', marginTop:6 }}>
            <button onClick={() => { setMessages([]); setConvId(null) }} style={{ fontSize:10, fontWeight:700, textDecoration:'underline', color: isSalesplay ? '#94A3B8' : 'var(--text3)', background:'none', border:'none', cursor:'pointer' }}>
              Clear conversation
            </button>
          </div>
        )}
        <div style={{ textAlign:'center', marginTop:6, fontSize:9, color: isSalesplay ? '#94A3B8' : 'var(--text3)' }}>
          AI can make mistakes.
        </div>
      </div>

      <EmbedHistoryDrawer
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        activeConvId={convId}
        isSalesplay={isSalesplay}
        onNewChat={() => { setConvId(null); setMessages([]); setHistoryOpen(false) }}
        onSelect={(id, loadedMessages) => {
          setConvId(id)
          setMessages(loadedMessages)
          setHistoryOpen(false)
        }}
      />
    </div>
  )
}
