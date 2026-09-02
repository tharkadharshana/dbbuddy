import React, { useState, useRef, useEffect, useCallback } from 'react'
import { ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { runNLQuery, createConversation, getConversationMessages, getErrorMessage, voteMessage } from '../utils/api'
import { Spinner, UsageMeter } from '../components/UI'
import Logo from '../components/Logo'
import Markdown from '../components/Markdown'
import ResultChart from '../components/ResultChart'
import DownloadButton from '../components/DownloadButton'
import { formatCurrency, formatNumber, isMoneyColumn, summarySuffix } from '../utils/locale'


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


function ResultTable({ columns, data, rowCount, moneyCols }) {
  const [expanded, setExpanded] = useState(false)
  const visible = expanded ? data : data.slice(0,5)
  const isNum = v => typeof v === 'number'
  const fmt = (col, v) => {
    if (v === null || v === undefined) return <span style={{color:'var(--text3)'}}>—</span>
    if (isNum(v)) {
      if (isMoneyColumn(col, moneyCols))
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
          {expanded ? `▲ Show less` : `▼ Show all ${rowCount} records`}
        </div>
      )}
    </div>
  )
}

function VoteButtons({ vote, onVote }) {
  const [popped, setPopped] = useState(null) // which button (1 | -1) is mid-animation

  const btn = (active, color) => ({
    background: 'none', border: 'none', cursor: 'pointer', padding: 4,
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    color: active ? color : 'var(--text3)', opacity: active ? 1 : 0.6,
  })

  function handleClick(v) {
    setPopped(v)
    onVote(v)
  }

  return (
    <div style={{ display:'flex', gap:2, marginTop:8 }}>
      <style>{`
        .cp-vote-btn { transition: transform .12s ease, color .12s ease, opacity .12s ease; }
        .cp-vote-btn:hover { transform: scale(1.15); }
        .cp-vote-btn.cp-vote-pop { animation: cpVotePop .3s ease; }
        @keyframes cpVotePop { 0%{transform:scale(1)} 40%{transform:scale(1.35)} 100%{transform:scale(1)} }
      `}</style>
      <button
        type="button" title="Good response" onClick={() => handleClick(1)}
        onAnimationEnd={() => setPopped(p => p === 1 ? null : p)}
        className={`cp-vote-btn${popped === 1 ? ' cp-vote-pop' : ''}`}
        style={btn(vote === 1, 'var(--blue)')}
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill={vote === 1 ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3H14z" />
          <path d="M7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3" />
        </svg>
      </button>
      <button
        type="button" title="Bad response" onClick={() => handleClick(-1)}
        onAnimationEnd={() => setPopped(p => p === -1 ? null : p)}
        className={`cp-vote-btn${popped === -1 ? ' cp-vote-pop' : ''}`}
        style={btn(vote === -1, 'var(--red, #e05252)')}
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill={vote === -1 ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3H10z" />
          <path d="M17 2h3a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-3" />
        </svg>
      </button>
    </div>
  )
}

function Message({ msg, llm, onVote, question }) {
  const [showSQL, setShowSQL] = useState(false)
  const chartRef = useRef(null)
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
      <Logo size={30} mark style={{ flexShrink:0, marginTop:2 }} />
      <div style={{ flex:1, minWidth:0 }}>
        {msg.loading ? (
          <div style={{ display:'flex', flexDirection:'column', gap:6 }}>
            <TypingDots />
            {msg.loadingText && (
              <span style={{ fontSize:11, color:'var(--text3)' }}>{msg.loadingText}</span>
            )}
          </div>
        ) : msg.error ? (
          <div style={{ background:'var(--red-dim)', border:'1px solid rgba(240,80,80,0.2)', borderRadius:10, padding:'10px 14px', fontSize:13, color:'var(--red)' }}>
            ⚠ {msg.error}
          </div>
        ) : (
          <>
            {/* agent_answer: `analysis` IS the answer — plain prose, no card.
                Legacy Think Mode commentary keeps its own labelled card. */}
            {msg.analysis && (msg.data?.agent_answer ? (
              <div style={{ fontSize:14, color:'var(--text)', lineHeight:1.7 }}>
                <Markdown text={msg.analysis} />
              </div>
            ) : (
              <div style={{
                marginBottom:12, padding:'12px 16px', borderRadius:10,
                background:'var(--bg2)', border:'1px solid var(--border2)',
                borderLeft:'3px solid var(--blue)', fontSize:14, lineHeight:1.7,
                color:'var(--text)',
              }}>
                <div style={{ fontSize:11, fontWeight:600, color:'var(--blue)', textTransform:'uppercase', letterSpacing:'.06em', marginBottom:6 }}>
                  🧠 Think Mode
                </div>
                <Markdown text={msg.analysis} />
              </div>
            ))}

            {/* Advisory (prose-only) answers set show_data=false — hide the
                "Found N results" summary and the unrelated chart/table. */}
            {msg.content && msg.data?.show_data !== false && (
              <div style={{ fontSize:14, color:'var(--text)', lineHeight:1.7, marginBottom: msg.data ? 4 : 0 }}>
                <Markdown text={msg.content} />
              </div>
            )}
            {msg.data && msg.data?.show_data !== false && (
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
                  <ResultChart columns={msg.data.columns} data={msg.data.data} innerRef={chartRef} />
                  <ResultTable columns={msg.data.columns} data={msg.data.data} rowCount={msg.data.row_count} moneyCols={msg.data.money_cols} />
                </>}
              </>
            )}
            {/* Only present when the merchant asked for a file (agent's
                export_data tool). Outside the show_data block on purpose: the
                agent flow sets show_data=false for every answer. */}
            <DownloadButton payload={msg.data?.export} chartRef={chartRef} question={question} />
            {msg.data?.message_id != null && (
              <VoteButtons vote={msg.vote} onVote={v => onVote(msg.vote === v ? null : v)} />
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
  const [thinkMode, setThinkMode] = useState(true)
  const [convId, setConvId]       = useState(activeConvId || null)
  const bottomRef = useRef(null)
  const inputRef  = useRef(null)
  // Track the convId that this component owns internally (created during send).
  // Used to distinguish "sidebar selected a different conversation" (should reload
  // messages) from "send() just created this conversation" (must NOT reload —
  // that would wipe the in-flight thinking bubble).
  // Starts at null (NOT convId) even when the URL already names a conversation —
  // otherwise the guard below sees activeConvId === ref on the very first render
  // and skips the initial load, leaving messages empty despite a valid deep link
  // (e.g. refreshing on /chat/<uuid>).
  const localConvIdRef = useRef(null)

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
        const loaded = (res.messages || []).map((m, i) => {
          const snap = m.data_snapshot || null
          const analysis = snap?.analysis || null
          const isAssistant = m.role !== 'user'
          // Smart answers stores the analyst prose as BOTH the message content
          // and the snapshot's analysis (so conversation memory has real text) —
          // rendering both would duplicate it. Only show content separately when
          // it differs from the analysis (e.g. legacy "Found N results").
          const content = (analysis && m.content === analysis) ? '' : m.content
          return {
            id:       m.id || i,
            role:     m.role === 'user' ? 'user' : 'ai',
            content,
            data: (snap || isAssistant) ? {
              type:      'data',
              columns:   snap?.columns || [],
              data:      snap?.rows || [],
              row_count: m.row_count || 0,
              message_id: m.id,
              conversation_id: activeConvId,
            } : null,
            analysis,
            vote: m.vote ?? null,
          }
        })
        setMessages(loaded)
      })
      .catch(() => setMessages([]))
  }, [activeConvId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  function handleVote(msg, vote) {
    setMessages(m => m.map(x => x.id === msg.id ? { ...x, vote } : x))
    voteMessage(msg.data.conversation_id, msg.data.message_id, vote).catch(() => {
      // Best-effort — revert on failure so the UI doesn't lie about saved state.
      setMessages(m => m.map(x => x.id === msg.id ? { ...x, vote: msg.vote } : x))
    })
  }

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
        // Ownership (localConvIdRef) is already claimed above, so the parent
        // setting activeConvId=newId (to update the URL for refresh-persistence)
        // matches what we already own — ChatPage's reload-guard effect no-ops.
        onConvCreated?.(newId)
      } catch {
        // If creation fails, continue without conversation memory (graceful degradation)
        currentConvId = null
      }
    }

    const userMsg  = { role: 'user', content: q, id: Date.now() }
    const thinkMsg = { role: 'ai', loading: true, id: Date.now() + 1 }
    setMessages(m => [...m, userMsg, thinkMsg])
    setLoading(true)

    const slowTimer = setTimeout(() => {
      setMessages(m => m.map(msg =>
        msg.id === thinkMsg.id && msg.loading
          ? { ...msg, loadingText: 'Complex queries can take a moment...' }
          : msg
      ))
    }, 10000)

    try {
      // Streaming first: live progress + the analyst answer as it arrives.
      // Falls back to the plain endpoint when streaming is disabled server-side
      // (404) or the stream dies before producing any output.
      const patchMsg = (patch) => setMessages(m => m.map(msg =>
        msg.id === thinkMsg.id ? { ...msg, ...patch } : msg
      ))
      let sawOutput = false
      let data = null
      try {
        data = await streamNLQuery(q, llm, thinkMode, currentConvId, {
          onStep:  p => { if (p.label) patchMsg({ loadingText: p.label }) },
          onToken: t => {
            sawOutput = true
            setMessages(m => m.map(msg => {
              if (msg.id !== thinkMsg.id) return msg
              const acc = (msg.streamText || '') + t
              return { role: 'ai', id: thinkMsg.id, content: '', analysis: acc, streamText: acc }
            }))
          },
        })
      } catch (se) {
        data = null   // stream failed — fall back below
      }
      // Re-run via the plain endpoint only if the stream produced nothing —
      // never after tokens were shown (that would double-charge the question).
      if (!data && !sawOutput) data = await runNLQuery(q, llm, thinkMode, currentConvId)
      if (!data) data = { success: false, type: 'error', message: 'The connection dropped mid-answer. Please try again.' }
      const rowCount = data.row_count
      const type     = data.type
      let summary

      if (type === 'conversational' || type === 'clarification') {
        summary = data.message || 'How can I help you with your data?'
      } else if (!data.success || type === 'error') {
        summary = data.message || 'Something went wrong. Please try again.'
      } else {
        summary = `Found ${rowCount} result${rowCount !== 1 ? 's' : ''}` + summarySuffix(data)
        if (rowCount === 0) summary = "I couldn't find anything matching that. Try rephrasing or broadening your question."
      }

      setMessages(m => m.map(msg =>
        msg.id === thinkMsg.id
          ? { role: 'ai', content: summary, data, analysis: data.analysis || null, id: thinkMsg.id }
          : msg
      ))
      onConversationChange?.()
    } catch(e) {
      const err = getErrorMessage(e)
      setMessages(m => m.map(msg =>
        msg.id === thinkMsg.id ? { role: 'ai', error: err, id: thinkMsg.id } : msg
      ))
    } finally {
      clearTimeout(slowTimer)
      setLoading(false)
      onQueryComplete?.()
    }
  }

  const hasMessages = messages.length > 0


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
            <Logo size={40} style={{ marginBottom:20 }} />
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
            {messages.map((msg, i) => <Message key={msg.id} msg={msg} llm={llm}
              question={messages[i - 1]?.role === 'user' ? messages[i - 1].content : ''}
              onVote={v => handleVote(msg, v)} />)}
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
            {/* Think Mode toggle (brain icon) — temporarily hidden.
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
            */}
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
            <div style={{ textAlign:'center', marginTop:6 }}>
              <button
                onClick={() => {
                  setMessages([])
                  setConvId(null)
                  localConvIdRef.current = null
                  onConvCreated?.()
                }}
                style={{ fontSize:12, fontWeight:700, textDecoration:'underline', color:'var(--text3)', background:'none', border:'none', cursor:'pointer' }}
              >
                Clear conversation
              </button>
            </div>
          )}
          <div style={{ textAlign:'center', marginTop:6, fontSize:12, color:'var(--text3)' }}>
            AI can make mistakes.
          </div>
        </div>
      </div>
    </div>
  )
}
