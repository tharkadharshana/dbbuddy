import React, { useState } from 'react'
import { ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { Card, Btn, LLMToggle, Spinner, Empty, ErrorBox, KPICard, DataTable, COLORS } from '../components/UI'
import { runNLQuery } from '../utils/api'

const SUGGESTIONS = [
  'Show total revenue by product category',
  'Top 10 customers by lifetime value',
  'Monthly order count for the last 12 months',
  'Which products have the lowest inventory?',
  'Revenue by location this year',
  'Cashier performance ranked by sales',
  'Show daily revenue for the last 30 days',
  'What is the average discount per payment method?',
]
const TT = { background:'#1c1e2e', border:'1px solid rgba(255,255,255,0.08)', borderRadius:8, fontSize:12, color:'#f0f1fa' }

export default function QueryPage({ llm, setLlm }) {
  const [q, setQ]           = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult]   = useState(null)
  const [error, setError]     = useState(null)
  const [showSQL, setShowSQL] = useState(false)
  const [history, setHistory] = useState([])

  async function handleRun(question) {
    const qText = question || q
    if (!qText.trim()) return
    setLoading(true); setError(null); setResult(null); setShowSQL(false)
    try {
      const data = await runNLQuery(qText, llm)
      setResult(data)
      setHistory(h => [{ q: qText, ts: new Date().toLocaleTimeString() }, ...h.slice(0,9)])
    } catch(e) {
      setError(e.response?.data?.detail || e.message)
    } finally { setLoading(false) }
  }

  const isNum = v => typeof v === 'number'
  const cols  = result?.columns || []
  const xCol  = cols.find(c => typeof result?.data?.[0]?.[c] === 'string') || cols[0]
  const yCols = cols.filter(c => isNum(result?.data?.[0]?.[c])).slice(0, 2)
  const chartData = result?.data?.slice(0, 30).map(r => ({ name: String(r[xCol] ?? ''), ...Object.fromEntries(yCols.map(k => [k, r[k]])) })) || []
  const numCols = cols.filter(c => isNum(result?.data?.[0]?.[c])).slice(0, 4)

  return (
    <div style={{ display:'flex', height:'100%', overflow:'hidden' }}>
      {/* History panel */}
      <div style={{ width:220, borderRight:'1px solid var(--border)', display:'flex', flexDirection:'column', flexShrink:0 }}>
        <div style={{ padding:'14px 14px 8px', fontSize:10, color:'var(--text3)', fontWeight:600, textTransform:'uppercase', letterSpacing:'.08em' }}>Recent Queries</div>
        <div style={{ flex:1, overflowY:'auto', padding:'0 8px 12px' }}>
          {!history.length && <div style={{ fontSize:11, color:'var(--text3)', padding:'8px 6px' }}>No queries yet</div>}
          {history.map((h, i) => (
            <div key={i} onClick={() => { setQ(h.q); handleRun(h.q) }} style={{ padding:'8px 8px', borderRadius:'var(--r-sm)', cursor:'pointer', marginBottom:2, fontSize:11, color:'var(--text2)', lineHeight:1.4 }}
              onMouseEnter={e => e.currentTarget.style.background='var(--bg3)'}
              onMouseLeave={e => e.currentTarget.style.background='transparent'}>
              <div style={{ color:'var(--text)', fontWeight:500, marginBottom:2, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{h.q}</div>
              <div style={{ color:'var(--text3)', fontSize:10 }}>{h.ts}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Main */}
      <div style={{ flex:1, overflowY:'auto', padding:20, display:'flex', flexDirection:'column', gap:14 }}>
        <Card style={{ padding:18 }}>
          <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:12 }}>
            <span style={{ fontSize:11, fontWeight:600, color:'var(--text3)', textTransform:'uppercase', letterSpacing:'.08em' }}>Ask Your Data Anything</span>
            <LLMToggle value={llm} onChange={setLlm} />
          </div>
          <div style={{ display:'flex', gap:10 }}>
            <textarea value={q} onChange={e=>setQ(e.target.value)}
              onKeyDown={e=>{ if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();handleRun()} }}
              placeholder="e.g. Show total revenue by category for the last 6 months…"
              rows={2} style={{ flex:1, padding:'10px 14px', resize:'none', lineHeight:1.6 }} />
            <Btn onClick={() => handleRun()} disabled={loading||!q.trim()} style={{ alignSelf:'flex-end', height:42 }}>
              {loading ? <Spinner size={13} color="#fff" /> : 'Run ↗'}
            </Btn>
          </div>
          <div style={{ display:'flex', gap:5, flexWrap:'wrap', marginTop:10 }}>
            {SUGGESTIONS.map(s => (
              <button key={s} onClick={() => { setQ(s); handleRun(s) }} style={{ padding:'3px 11px', borderRadius:20, fontSize:11, background:'var(--bg3)', color:'var(--text3)', border:'1px solid var(--border)', cursor:'pointer' }}>{s}</button>
            ))}
          </div>
        </Card>

        {error && <ErrorBox message={error} />}

        {loading && <div style={{ display:'flex', flexDirection:'column', gap:10 }}>
          {[80,220,40].map((h,i) => <div key={i} className="skeleton" style={{ height:h }} />)}
        </div>}

        {!result && !loading && !error && <Empty icon="⌕" title="Ask your first question" subtitle="Type a question above and press Run — the AI writes and executes the SQL automatically." />}

        {result && !loading && (
          <div className="fade-up" style={{ display:'flex', flexDirection:'column', gap:12 }}>
            {/* SQL */}
            <Card style={{ overflow:'hidden' }}>
              <div onClick={() => setShowSQL(v=>!v)} style={{ display:'flex', alignItems:'center', justifyContent:'space-between', padding:'11px 16px', cursor:'pointer' }}>
                <span style={{ fontSize:12, fontWeight:500, color:'var(--text2)' }}>Generated SQL</span>
                <span style={{ fontSize:11, color:'var(--blue)' }}>{showSQL ? '▲ Hide' : '▼ Show'}</span>
              </div>
              {showSQL && <pre style={{ padding:'12px 16px', fontFamily:'var(--mono)', fontSize:12, color:'var(--green)', overflowX:'auto', lineHeight:1.8, whiteSpace:'pre-wrap', borderTop:'1px solid var(--border)' }}>{result.sql}</pre>}
            </Card>

            {/* KPIs */}
            <div style={{ display:'grid', gridTemplateColumns:`repeat(${Math.min(numCols.length||1,4)},1fr)`, gap:10 }}>
              {numCols.length ? numCols.map((col,i) => {
                const vals = result.data.map(r=>r[col]||0)
                const total = vals.reduce((a,b)=>a+b,0)
                const display = result.data.length===1 ? result.data[0][col] : (col.includes('avg')||col.includes('pct')||col.includes('rate') ? (total/vals.length) : total)
                return <KPICard key={col} label={col.replace(/_/g,' ')} value={typeof display==='number'?display.toLocaleString(undefined,{maximumFractionDigits:1}):display} color={['var(--blue)','var(--green)','var(--purple)','var(--amber)'][i%4]} />
              }) : <KPICard label="Rows" value={result.row_count} />}
            </div>

            {/* Chart */}
            {yCols.length > 0 && chartData.length > 1 && (
              <Card style={{ padding:18 }}>
                <div style={{ fontSize:12, color:'var(--text3)', marginBottom:12 }}>{yCols.join(' & ')} by {xCol}</div>
                <ResponsiveContainer width="100%" height={220}>
                  <ComposedChart data={chartData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
                    <XAxis dataKey="name" tick={{fontSize:10,fill:'#5a5f7d'}} axisLine={false} tickLine={false} />
                    <YAxis tick={{fontSize:10,fill:'#5a5f7d'}} axisLine={false} tickLine={false} />
                    <Tooltip contentStyle={TT} />
                    {yCols[0] && <Bar dataKey={yCols[0]} fill="var(--blue)" radius={[4,4,0,0]} barSize={chartData.length > 12 ? 6 : 22} />}
                    {yCols[1] && <Line dataKey={yCols[1]} stroke="var(--green)" strokeWidth={2} dot={false} />}
                  </ComposedChart>
                </ResponsiveContainer>
              </Card>
            )}

            {/* Table */}
            <Card style={{ overflow:'hidden' }}>
              <div style={{ padding:'11px 16px', borderBottom:'1px solid var(--border)', fontSize:12, fontWeight:500, color:'var(--text2)' }}>Results · {result.row_count} rows</div>
              <DataTable columns={result.columns} data={result.data} maxHeight={360} />
            </Card>
          </div>
        )}
      </div>
    </div>
  )
}
