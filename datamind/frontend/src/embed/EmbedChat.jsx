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
import { embedRunQuery, embedGetSSOHandoff } from './embedApi'
import { notifyParent } from './EmbedApp'

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

// ── Chart ─────────────────────────────────────────────────────────────────────
function ResultChart({ columns, data, theme }) {
  if (!data?.length || !columns?.length) return null
  const numCols = columns.filter(c => typeof data[0]?.[c] === 'number')
  const strCols = columns.filter(c => typeof data[0]?.[c] === 'string')
  if (!numCols.length || !strCols.length || data.length < 2) return null
  const xKey = strCols[0], y1 = numCols[0], y2 = numCols[1]
  const isLight    = theme === 'light'
  const gridColor  = isLight ? 'rgba(0,0,0,0.06)' : 'rgba(255,255,255,0.06)'
  const tickColor  = isLight ? '#6b7280' : '#5a5f7d'
  const chartData = data.slice(0, 15).map(r => ({
    name: String(r[xKey] || '').slice(0, 14),
    [y1]: r[y1],
    ...(y2 ? { [y2]: r[y2] } : {}),
  }))
  return (
    <div style={{ marginTop:10, background:'var(--bg2)', borderRadius:8, padding:10, border:'1px solid var(--border)' }}>
      <ResponsiveContainer width="100%" height={140}>
        <ComposedChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" stroke={gridColor} />
          <XAxis dataKey="name" tick={{ fontSize:9, fill:tickColor }} axisLine={false} tickLine={false} />
          <YAxis tick={{ fontSize:9, fill:tickColor }} axisLine={false} tickLine={false} />
          <Tooltip contentStyle={TT} />
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
      if (col.includes('revenue') || col.includes('total') || col.includes('amount') ||
          col.includes('price') || col.includes('value') || col.includes('spent'))
        return <span style={{ color:'var(--blue)', fontFamily:'var(--mono)' }}>${Number(v).toLocaleString()}</span>
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
      <div style={{ width:24, height:24, borderRadius:'50%', background:'linear-gradient(135deg,#4f8ef7,#a78bfa)', display:'flex', alignItems:'center', justifyContent:'center', flexShrink:0, marginTop:2 }}>
        <svg width="11" height="11" viewBox="0 0 16 16" fill="none">
          <rect x="2" y="2" width="5" height="5" rx="1" fill="rgba(255,255,255,0.9)"/>
          <rect x="9" y="2" width="5" height="5" rx="1" fill="rgba(255,255,255,0.5)"/>
          <rect x="2" y="9" width="5" height="5" rx="1" fill="rgba(255,255,255,0.5)"/>
          <rect x="9" y="9" width="5" height="5" rx="1" fill="rgba(255,255,255,0.9)"/>
        </svg>
      </div>
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
            {msg.data?.row_count === 0 && (
              <div style={{ fontSize:11, color:'var(--text3)', marginTop:6 }}>No results found.</div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────
export default function EmbedChat({ context, onExpired, onLogout }) {
  const productTitle = context?.branding?.product_name || 'Ask Your Data'
  const [messages, setMessages] = useState([])
  const [input, setInput]       = useState('')
  const [loading, setLoading]   = useState(false)
  const [thinkMode, setThinkMode] = useState(false)
  const [hoveredSuggestion, setHoveredSuggestion] = useState(null)
  const [inputFocused, setInputFocused] = useState(false)
  const bottomRef = useRef(null)
  const inputRef  = useRef(null)

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

    const userMsg  = { role:'user', content:q,           id: Date.now() }
    const thinkMsg = { role:'ai',  loading:true,
                       loadingText: thinkMode ? 'Querying data…' : null,
                       id: Date.now() + 1 }
    setMessages(m => [...m, userMsg, thinkMsg])
    setLoading(true)

    notifyParent('dm:query', { question: q })

    try {
      const data = await embedRunQuery(q, 'default', thinkMode)
      const rowCount = data.row_count
      const numCol   = data.columns?.find(c => typeof data.data?.[0]?.[c] === 'number')
      let summary = `Found ${rowCount} result${rowCount !== 1 ? 's' : ''}`
      if (numCol && data.data?.[0]) {
        const total = data.data.reduce((s, r) => s + (r[numCol] || 0), 0)
        summary += ` · ${numCol.replace(/_/g, ' ')}: ${total.toLocaleString(undefined, { maximumFractionDigits:2 })}`
      }
      if (rowCount === 0) summary = 'No matching records found for your query.'

      setMessages(m => m.map(msg =>
        msg.id === thinkMsg.id
          ? { role:'ai', content: summary, data, analysis: data.analysis || null, id: thinkMsg.id }
          : msg
      ))
    } catch (e) {
      if (e.response?.status === 401) {
        // Token expired — send user back to onboarding (can't redirect in iframe)
        onExpired()
        return
      }
      const err = e.response?.data?.detail || e.message
      setMessages(m => m.map(msg =>
        msg.id === thinkMsg.id ? { role:'ai', error:err, id:thinkMsg.id } : msg
      ))
    } finally {
      setLoading(false)
    }
  }

  const hasMessages = messages.length > 0

  return (
    <div style={{ display:'flex', flexDirection:'column', height:'100%', overflow:'hidden' }}>

      {/* Header */}
      <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', padding:'10px 14px', borderBottom:'1px solid var(--border)', flexShrink:0 }}>
        <div style={{ display:'flex', alignItems:'center', gap:8 }}>
          <div style={{ width:22, height:22, borderRadius:6, background:'linear-gradient(135deg,#4f8ef7,#a78bfa)', display:'flex', alignItems:'center', justifyContent:'center' }}>
            <svg width="11" height="11" viewBox="0 0 16 16" fill="none">
              <rect x="2" y="2" width="5" height="5" rx="1" fill="rgba(255,255,255,0.9)"/>
              <rect x="9" y="2" width="5" height="5" rx="1" fill="rgba(255,255,255,0.5)"/>
              <rect x="2" y="9" width="5" height="5" rx="1" fill="rgba(255,255,255,0.5)"/>
              <rect x="9" y="9" width="5" height="5" rx="1" fill="rgba(255,255,255,0.9)"/>
            </svg>
          </div>
          <span style={{ fontSize:15, fontWeight:600, color:'var(--text)', letterSpacing:'-0.01em' }}>{productTitle}</span>
        </div>
        <div style={{ display:'flex', alignItems:'center', gap:2 }}>
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
            <span style={{ fontSize:12 }}>↗</span> Open in DataMind
          </button>
          {/* Light / dark toggle — icon-only so it doesn't compete with primary actions */}
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
          {/* <button
            onClick={onLogout}
            title="Disconnect account"
            style={{ background:'none', border:'none', color:'var(--text3)', fontSize:11, cursor:'pointer', padding:'2px 6px' }}
          >
            ⏏ Disconnect
          </button> */}
        </div>
      </div>

      {/* Messages area */}
      <div style={{ flex:1, overflowY:'auto', padding:'14px 0' }}>
        {!hasMessages ? (
          <div style={{ display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', height:'100%', padding:'0 18px', textAlign:'center' }}>
            <div style={{ fontSize:18, fontWeight:600, color:'var(--text)', marginBottom:6, letterSpacing:'-0.01em' }}>
              Ask Your {context?.partner_name || 'Salesplay'} Data
            </div>
            <div style={{ fontSize:13, color:'var(--text2)', marginBottom:20, lineHeight:1.6, maxWidth:300 }}>
              Ask anything about your data in plain English — revenue, products, customers, and more.
            </div>
            <div style={{ width:'100%', textAlign:'left', fontSize:10, fontWeight:600, color:'var(--text3)', textTransform:'uppercase', letterSpacing:'.07em', marginBottom:8 }}>
              Popular questions
            </div>
            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:8, width:'100%' }}>
              {SUGGESTIONS.map((s, i) => {
                const featured = i === 0
                const hovered = hoveredSuggestion === i
                return (
                  <button
                    key={s.text}
                    onClick={() => send(s.text)}
                    onMouseEnter={() => setHoveredSuggestion(i)}
                    onMouseLeave={() => setHoveredSuggestion(null)}
                    style={{
                      gridColumn: featured ? '1 / -1' : 'auto',
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
            <div style={{ marginTop:18, display:'flex', alignItems:'center', gap:6, fontSize:10, color:'var(--text3)' }}>
              <span style={{ width:6, height:6, borderRadius:'50%', background:'var(--green)', display:'inline-block', flexShrink:0 }} />
              Real-time data · Powered by DataMind
            </div>
          </div>
        ) : (
          <div style={{ padding:'0 14px' }}>
            {messages.map(msg => <Message key={msg.id} msg={msg} theme={theme} />)}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {/* Input */}
      <div style={{ flexShrink:0, padding:'10px 12px', borderTop: hasMessages ? '1px solid var(--border)' : 'none' }}>
        {/* Think Mode toggle */}
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
            placeholder="Ask about your data…"
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
        {hasMessages && (
          <div style={{ textAlign:'center', marginTop:6 }}>
            <button onClick={() => setMessages([])} style={{ fontSize:10, color:'var(--text3)', background:'none', border:'none', cursor:'pointer' }}>
              Clear conversation
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
