import React, { useState, useRef, useEffect, useCallback } from 'react'
import { ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { runNLQuery, createConversation, getConversationMessages } from '../utils/api'
import { Spinner, UsageMeter, AIQuotaWall } from '../components/UI'
import { formatCurrency, formatNumber } from '../utils/locale'

const TT = { background:'#1c1e2e', border:'1px solid rgba(255,255,255,0.08)', borderRadius:8, fontSize:12, color:'#f0f1fa' }

const SUGGESTIONS = [
  { icon:'💰', text:'What was my total revenue last month?' },
  { icon:'📦', text:'Which products are selling the fastest?' },
  { icon:'👥', text:'Who are my top 10 customers by spend?' },
  { icon:'📍', text:'Compare performance across all locations' },
  { icon:'🕐', text:'What time of day has the most sales?' },
  { icon:'⚠️', text:'Are there any unusual patterns in my data?' },
]

function TypingDots() {
  return (
    <div style={{ display:'flex', gap:4, alignItems:'center', padding:'4px 0' }}>
      {[0,1,2].map(i => (
        <div key={i} style={{ width:7, height:7, borderRadius:'50%', background:'var(--blue)', opacity:.7, animation:`bounce 1.2s ${i*0.2}s ease-in-out infinite` }} />
      ))}
      <style>{`@keyframes bounce{0%,80%,100%{transform:scale(0.7);opacity:.5}40%{transform:scale(1);opacity:1}}`}</style>
    </div>
  )
}

function ResultChart({ columns, data }) {
  if (!data?.length || !columns?.length) return null
  const numCols = columns.filter(c => typeof data[0]?.[c] === 'number')
  const strCols = columns.filter(c => typeof data[0]?.[c] === 'string')
  if (!numCols.length || !strCols.length || data.length < 2) return null
  const xKey = strCols[0], y1 = numCols[0], y2 = numCols[1]
  const chartData = data.slice(0,20).map(r => ({ name: String(r[xKey]||'').slice(0,16), [y1]: r[y1], ...(y2 ? {[y2]: r[y2]} : {}) }))

  return (
    <div style={{ marginTop:14, background:'rgba(255,255,255,0.02)', borderRadius:10, padding:14, border:'1px solid var(--border)' }}>
      <ResponsiveContainer width="100%" height={180}>
        <ComposedChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
          <XAxis dataKey="name" tick={{fontSize:10,fill:'#5a5f7d'}} axisLine={false} tickLine={false} />
          <YAxis tick={{fontSize:10,fill:'#5a5f7d'}} axisLine={false} tickLine={false} />
          <Tooltip contentStyle={TT} />
          <Bar dataKey={y1} fill="var(--blue)" radius={[4,4,0,0]} barSize={data.length > 10 ? 8 : 20} />
          {y2 && <Line dataKey={y2} stroke="var(--green)" strokeWidth={2} dot={false} />}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}

function ResultTable({ columns, data, rowCount }) {
  const [expanded, setExpanded] = useState(false)
  const visible = expanded ? data : data.slice(0,5)
  const isNum = v => typeof v === 'number'
  const fmt = (col, v) => {
    if (v === null || v === undefined) return <span style={{color:'var(--text3)'}}>—</span>
    if (isNum(v)) {
      if (col.includes('revenue')||col.includes('total')||col.includes('amount')||col.includes('price')||col.includes('value')||col.includes('spent'))
        return <span style={{color:'var(--blue)',fontFamily:'var(--mono)'}}>{formatCurrency(v)}</span>
      if (col.includes('pct')||col.includes('rate')||col.includes('percent'))
        return <span style={{color:v>0?'var(--green)':'var(--red)',fontFamily:'var(--mono)'}}>{v > 0 ? '+' : ''}{v}%</span>
      return <span style={{fontFamily:'var(--mono)',color:'var(--blue)'}}>{formatNumber(v, null, 0)}</span>
    }
    return String(v)
  }

  return (
    <div style={{ marginTop:14, borderRadius:10, overflow:'hidden', border:'1px solid var(--border)' }}>
      <div style={{ overflowX:'auto' }}>
        <table style={{ width:'100%', borderCollapse:'collapse', fontSize:12 }}>
          <thead>
            <tr>
              {columns.map(c => <th key={c} style={{ padding:'8px 12px', textAlign:'left', color:'var(--text3)', fontWeight:500, fontSize:11, textTransform:'uppercase', letterSpacing:'.06em', borderBottom:'1px solid var(--border)', background:'rgba(255,255,255,0.02)', whiteSpace:'nowrap' }}>{c.replace(/_/g,' ')}</th>)}
            </tr>
          </thead>
          <tbody>
            {visible.map((row,i) => (
              <tr key={i} style={{ borderBottom:'1px solid var(--border)' }}>
                {columns.map(c => <td key={c} style={{ padding:'8px 12px', color:'var(--text2)', whiteSpace:'nowrap' }}>{fmt(c, row[c])}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data.length > 5 && (
        <div onClick={() => setExpanded(e => !e)} style={{ padding:'8px 12px', textAlign:'center', fontSize:11, color:'var(--blue)', cursor:'pointer', borderTop:'1px solid var(--border)', background:'rgba(255,255,255,0.01)' }}>
          {expanded ? `▲ Show less` : `▼ Show all ${rowCount} rows`}
        </div>
      )}
    </div>
  )
}

function Message({ msg, llm }) {
  const [showSQL, setShowSQL] = useState(false)
  const isUser = msg.role === 'user'

  if (isUser) return (
    <div style={{ display:'flex', justifyContent:'flex-end', marginBottom:20 }}>
      <div style={{ maxWidth:'75%', background:'var(--blue)', color:'#fff', borderRadius:'18px 18px 4px 18px', padding:'11px 16px', fontSize:14, lineHeight:1.6 }}>
        {msg.content}
      </div>
    </div>
  )

  return (
    <div style={{ display:'flex', gap:12, marginBottom:24, alignItems:'flex-start' }}>
      <div style={{ width:30, height:30, borderRadius:'50%', background:'linear-gradient(135deg,#4f8ef7,#a78bfa)', display:'flex', alignItems:'center', justifyContent:'center', flexShrink:0, marginTop:2 }}>
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none"><rect x="2" y="2" width="5" height="5" rx="1" fill="rgba(255,255,255,0.9)"/><rect x="9" y="2" width="5" height="5" rx="1" fill="rgba(255,255,255,0.5)"/><rect x="2" y="9" width="5" height="5" rx="1" fill="rgba(255,255,255,0.5)"/><rect x="9" y="9" width="5" height="5" rx="1" fill="rgba(255,255,255,0.9)"/></svg>
      </div>
      <div style={{ flex:1, minWidth:0 }}>
        {msg.loading ? (
          <TypingDots />
        ) : msg.error ? (
          <div style={{ background:'var(--red-dim)', border:'1px solid rgba(240,80,80,0.2)', borderRadius:10, padding:'10px 14px', fontSize:13, color:'var(--red)' }}>
            ⚠ {msg.error}
          </div>
        ) : (
          <>
            {/* Think Mode analysis */}
            {msg.analysis && (
              <div style={{
                marginBottom:12, padding:'12px 16px', borderRadius:10,
                background:'var(--bg2)', border:'1px solid var(--border2)',
                borderLeft:'3px solid var(--blue)', fontSize:14, lineHeight:1.7,
                color:'var(--text)',
              }}>
                <div style={{ fontSize:11, fontWeight:600, color:'var(--blue)', textTransform:'uppercase', letterSpacing:'.06em', marginBottom:6 }}>
                  🧠 Think Mode
                </div>
                {msg.analysis.replace(/\*\*/g, '').replace(/\*/g, '').replace(/_{2}/g, '').replace(/_/g, '')}
              </div>
            )}

            <div style={{ fontSize:14, color:'var(--text)', lineHeight:1.7, marginBottom: msg.data ? 4 : 0 }}>
              {msg.content}
            </div>
            {msg.data && (
              <>
                {/* TODO: Re-enable "View SQL" toggle when ready
                {msg.data.sql && (
                  <div style={{ marginTop:10 }}>
                    <button onClick={() => setShowSQL(v => !v)} style={{ fontSize:11, color:'var(--text3)', background:'var(--bg3)', border:'1px solid var(--border)', borderRadius:6, padding:'3px 10px', cursor:'pointer' }}>
                      {showSQL ? '▲ Hide SQL' : '▼ View SQL'}
                    </button>
                    {showSQL && (
                      <pre style={{ marginTop:6, padding:'10px 14px', background:'var(--bg2)', borderRadius:8, fontFamily:'var(--mono)', fontSize:11, color:'var(--green)', overflowX:'auto', whiteSpace:'pre-wrap', border:'1px solid var(--border)' }}>
                        {msg.data.sql}
                      </pre>
                    )}
                  </div>
                )}
                */}
                {msg.data.data?.length > 0 && <>
                  <ResultChart columns={msg.data.columns} data={msg.data.data} />
                  <ResultTable columns={msg.data.columns} data={msg.data.data} rowCount={msg.data.row_count} />
                </>}
                {msg.data.row_count === 0 && <div style={{ fontSize:12, color:'var(--text3)', marginTop:8 }}>No results returned.</div>}
              </>
            )}
          </>
        )}
      </div>
    </div>
  )
}

export default function ChatPage({
  llm, setLlm, connection, sub, onNavigate,
  onQueryComplete,
  activeConvId,      // UUID of the selected conversation (null = new)
  onConvCreated,     // called with new convId after first message
  onConversationChange, // called after each exchange to refresh sidebar
}) {
  const [messages, setMessages]   = useState([])
  const [input, setInput]         = useState('')
  const [loading, setLoading]     = useState(false)
  const [thinkMode, setThinkMode] = useState(false)
  const [convId, setConvId]       = useState(activeConvId || null)
  const bottomRef = useRef(null)
  const inputRef  = useRef(null)
  // Track the convId that this component owns internally (created during send).
  // Used to distinguish "sidebar selected a different conversation" (should reload
  // messages) from "send() just created this conversation" (must NOT reload —
  // that would wipe the in-flight thinking bubble).
  const localConvIdRef = useRef(convId)

  // When the parent navigates to a different conversation (sidebar click), load it.
  // Guard: skip if activeConvId matches the conversation we already own locally —
  // this prevents send() → onConvCreated() → setActiveConvId() from re-triggering
  // a message reload that wipes the in-flight result.
  useEffect(() => {
    if (activeConvId === localConvIdRef.current) return  // we own this conv — don't reset

    localConvIdRef.current = activeConvId || null
    setConvId(activeConvId || null)

    if (!activeConvId) {
      setMessages([])
      return
    }
    getConversationMessages(activeConvId)
      .then(res => {
        const loaded = (res.messages || []).map((m, i) => ({
          id:      m.id || i,
          role:    m.role === 'user' ? 'user' : 'ai',
          content: m.content,
        }))
        setMessages(loaded)
      })
      .catch(() => setMessages([]))
  }, [activeConvId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  async function send(text) {
    const q = (text || input).trim()
    if (!q || loading) return
    setInput('')

    // Lazy conversation creation: generate a UUID on the first send if none exists.
    let currentConvId = convId
    if (!currentConvId) {
      const newId = crypto.randomUUID
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2)}`
      try {
        await createConversation(newId)
        currentConvId = newId
        localConvIdRef.current = newId  // claim ownership before notifying parent
        setConvId(newId)
        onConvCreated?.()  // parent only refreshes list — does NOT setActiveConvId
      } catch {
        // If creation fails, continue without conversation memory (graceful degradation)
        currentConvId = null
      }
    }

    const userMsg  = { role: 'user', content: q, id: Date.now() }
    const thinkMsg = { role: 'ai', loading: true, id: Date.now() + 1 }
    setMessages(m => [...m, userMsg, thinkMsg])
    setLoading(true)
    try {
      const data = await runNLQuery(q, llm, thinkMode, currentConvId)
      const rowCount = data.row_count
      const numCol   = data.columns?.find(c => typeof data.data?.[0]?.[c] === 'number')
      let summary = `Found ${rowCount} result${rowCount !== 1 ? 's' : ''}`
      if (numCol && data.data?.[0]) {
        const total = data.data.reduce((s, r) => s + (r[numCol] || 0), 0)
        summary += ` · Total ${numCol.replace(/_/g, ' ')}: ${formatNumber(total)}`
      }
      if (rowCount === 0) summary = 'No matching records found for your query.'

      setMessages(m => m.map(msg =>
        msg.id === thinkMsg.id
          ? { role: 'ai', content: summary, data, analysis: data.analysis || null, id: thinkMsg.id }
          : msg
      ))
      onConversationChange?.()
    } catch(e) {
      const err = e.response?.data?.error || e.response?.data?.detail || e.message
      setMessages(m => m.map(msg =>
        msg.id === thinkMsg.id ? { role: 'ai', error: err, id: thinkMsg.id } : msg
      ))
    } finally {
      setLoading(false)
      onQueryComplete?.()
    }
  }

  const hasMessages = messages.length > 0

  if (sub && !sub.can_use_ai) return <AIQuotaWall sub={sub} onNavigate={onNavigate} />

  return (
    <div style={{ display:'flex', flexDirection:'column', height:'100%', overflow:'hidden' }}>

      {/* LLM selector with token meter */}
      <div style={{ display:'flex', justifyContent:'flex-end', padding:'10px 20px 0', flexShrink:0 }}>
        <UsageMeter sub={sub} />
      </div>

      {/* Messages area */}
      <div style={{ flex:1, overflowY:'auto', padding:'20px 0' }}>
        {!hasMessages ? (
          /* Empty state — ChatGPT style welcome */
          <div style={{ display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', height:'100%', padding:'0 24px', textAlign:'center' }}>
            <div style={{ width:56, height:56, borderRadius:16, background:'linear-gradient(135deg,#4f8ef7,#a78bfa)', display:'flex', alignItems:'center', justifyContent:'center', marginBottom:20, boxShadow:'0 8px 24px rgba(79,142,247,0.25)' }}>
              <svg width="26" height="26" viewBox="0 0 16 16" fill="none"><rect x="2" y="2" width="5" height="5" rx="1" fill="rgba(255,255,255,0.95)"/><rect x="9" y="2" width="5" height="5" rx="1" fill="rgba(255,255,255,0.5)"/><rect x="2" y="9" width="5" height="5" rx="1" fill="rgba(255,255,255,0.5)"/><rect x="9" y="9" width="5" height="5" rx="1" fill="rgba(255,255,255,0.95)"/></svg>
            </div>
            <h2 style={{ fontSize:22, fontWeight:700, color:'var(--text)', marginBottom:8 }}>
              {connection ? `Ask your ${connection.display_name || connection.name} data` : 'Ask your data anything'}
            </h2>
            <p style={{ fontSize:14, color:'var(--text3)', maxWidth:400, lineHeight:1.7, marginBottom:32 }}>
              Type a question in plain English. DataMind translates it into SQL and gives you an answer with charts and data.
            </p>
            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:8, width:'100%', maxWidth:520 }}>
              {SUGGESTIONS.map(s => (
                <button key={s.text} onClick={() => send(s.text)} style={{
                  display:'flex', alignItems:'flex-start', gap:10, padding:'12px 14px',
                  background:'var(--bg1)', border:'1px solid var(--border)',
                  borderRadius:12, cursor:'pointer', textAlign:'left', transition:'all .15s',
                  color:'var(--text2)', fontSize:13, lineHeight:1.5,
                }}>
                  <span style={{ fontSize:18, flexShrink:0 }}>{s.icon}</span>
                  {s.text}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div style={{ maxWidth:800, margin:'0 auto', padding:'0 24px' }}>
            {messages.map(msg => <Message key={msg.id} msg={msg} llm={llm} />)}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {/* Input box */}
      <div style={{ flexShrink:0, padding:'12px 20px 20px', borderTop: hasMessages ? '1px solid var(--border)' : 'none' }}>
        <div style={{ maxWidth:800, margin:'0 auto' }}>
          <div style={{ display:'flex', gap:10, background:'var(--bg1)', border:'1px solid var(--border2)', borderRadius:16, padding:'8px 8px 8px 16px', boxShadow:'0 4px 20px rgba(0,0,0,0.2)' }}>
            <textarea
              ref={inputRef}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
              placeholder={connection ? `Ask about your ${connection.display_name || connection.name} data…` : 'Ask anything about your data…'}
              rows={1}
              style={{
                flex:1, background:'transparent', border:'none', color:'var(--text)', fontSize:14,
                resize:'none', outline:'none', lineHeight:1.6, padding:'4px 0',
                maxHeight:120, overflowY:'auto', fontFamily:'var(--font)',
              }}
            />
            {/* Think Mode toggle */}
            <button
              onClick={() => setThinkMode(m => !m)}
              title={thinkMode ? 'Think Mode ON — click to turn off (Uses X2 Tokens)' : 'Think Mode OFF — click to enable AI analysis of results'}
              style={{
                width:38, height:38, borderRadius:10, flexShrink:0, alignSelf:'flex-end',
                background: thinkMode ? 'rgba(79,142,247,0.15)' : 'var(--bg2)',
                color: thinkMode ? 'var(--blue)' : 'var(--text3)',
                border: `1px solid ${thinkMode ? 'var(--blue)' : 'var(--border)'}`,
                display:'flex', alignItems:'center', justifyContent:'center',
                fontSize:16, cursor:'pointer', transition:'all .15s',
              }}
            >
              🧠
            </button>
            <button onClick={() => send()} disabled={loading || !input.trim()} style={{
              width:38, height:38, borderRadius:10, flexShrink:0, alignSelf:'flex-end',
              background: loading || !input.trim() ? 'var(--bg3)' : 'var(--blue)',
              color: loading || !input.trim() ? 'var(--text3)' : '#fff',
              border:'none', cursor: loading || !input.trim() ? 'not-allowed' : 'pointer',
              display:'flex', alignItems:'center', justifyContent:'center', transition:'all .15s',
            }}>
              {loading
                ? <Spinner size={14} color="var(--text3)" />
                : <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
              }
            </button>
          </div>
          {hasMessages && (
            <div style={{ textAlign:'center', marginTop:8 }}>
              <button
                onClick={() => {
                  setMessages([])
                  setConvId(null)
                  localConvIdRef.current = null
                  onConvCreated?.()
                }}
                style={{ fontSize:11, color:'var(--text3)', background:'none', border:'none', cursor:'pointer' }}
              >
                New conversation
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
