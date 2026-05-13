import React, { useState, useEffect } from 'react'
import { ComposedChart, Bar, ReferenceLine, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { Card, Btn, Badge, Spinner, Spinner2, Empty, ErrorBox, KPICard } from '../components/UI'
import { runAutoAnomalies, runAnomalies, fetchTables, fetchTableColumns } from '../utils/api'

const TT = { background:'#1c1e2e', border:'1px solid rgba(255,255,255,0.08)', borderRadius:8, fontSize:12, color:'#f0f1fa' }
const SEV_COLOR = { high:'red', medium:'amber', low:'blue' }

export default function AnomalyPage() {
  const [mode, setMode]       = useState('auto')
  const [loading, setLoading] = useState(false)
  const [result, setResult]   = useState(null)
  const [error, setError]     = useState(null)
  const [tables, setTables]           = useState([])
  const [columns, setColumns]         = useState([])
  const [colsLoading, setColsLoading] = useState(false)
  const [form, setForm]               = useState({ table:'', value_column:'', date_column:'' })

  useEffect(() => { fetchTables().then(d=>setTables(d.tables||[])).catch(()=>{}) }, [])

  useEffect(() => {
    if (!form.table) { setColumns([]); return }
    setColsLoading(true)
    fetchTableColumns(form.table)
      .then(d => {
        const cols = d.columns || []
        setColumns(cols)
        const dateCol = cols.find(c => /datetime|date|timestamp/i.test(c.type))
        const numCol  = cols.find(c => /decimal|float|double|numeric/i.test(c.type))
        setForm(f => ({
          ...f,
          date_column:  dateCol?.name || f.date_column,
          value_column: numCol?.name  || f.value_column,
        }))
      })
      .catch(() => setColumns([]))
      .finally(() => setColsLoading(false))
  }, [form.table])

  async function runIt() {
    setLoading(true); setError(null); setResult(null)
    try {
      const data = mode==='auto'
        ? await runAutoAnomalies()
        : await runAnomalies(form.table, form.value_column, form.date_column||null)
      setResult(data)
    } catch(e) { setError(e.response?.data?.detail || e.message) }
    finally { setLoading(false) }
  }

  const chartData = result?.series.map(s => ({ date:s.date, score:s.score, anomaly: s.is_anomaly ? s.score : null })) || []

  return (
    <div style={{ padding:20, display:'flex', flexDirection:'column', gap:14, height:'100%', overflowY:'auto' }}>
      <Card style={{ padding:18 }}>
        <div style={{ fontSize:11, fontWeight:600, color:'var(--text3)', textTransform:'uppercase', letterSpacing:'.08em', marginBottom:14 }}>Anomaly Detection · Isolation Forest</div>

        <div style={{ display:'flex', background:'var(--bg3)', borderRadius:'var(--r-md)', padding:4, gap:3, marginBottom:16, width:'fit-content' }}>
          {[['auto','⚡ Auto (Revenue)'],['manual','⚙ Manual']].map(([m,l]) => (
            <button key={m} onClick={()=>setMode(m)} style={{ padding:'6px 18px', borderRadius:'var(--r-sm)', fontSize:12, fontWeight:500, background:mode===m?'var(--blue)':'transparent', color:mode===m?'#fff':'var(--text3)', border:'none', cursor:'pointer' }}>{l}</button>
          ))}
        </div>

        {mode==='manual' && (
          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:10, marginBottom:14 }}>
            {/* Table */}
            <div>
              <label style={{ fontSize:11, color:'var(--text3)', display:'block', marginBottom:5 }}>Table</label>
              <select value={form.table} onChange={e=>setForm(f=>({...f,table:e.target.value,date_column:'',value_column:''}))}
                style={{ width:'100%', padding:'8px 10px', borderRadius:'var(--r-md)', fontSize:13, background:'var(--bg2)', border:'1px solid var(--border)', color:'var(--text)' }}>
                <option value="">Select table…</option>
                {tables.map(t=><option key={t} value={t}>{t}</option>)}
              </select>
            </div>
            {/* Value column */}
            <div>
              <label style={{ fontSize:11, color:'var(--text3)', display:'block', marginBottom:5 }}>Value Column</label>
              <select value={form.value_column} onChange={e=>setForm(f=>({...f,value_column:e.target.value}))}
                disabled={!form.table || colsLoading}
                style={{ width:'100%', padding:'8px 10px', borderRadius:'var(--r-md)', fontSize:13, background:'var(--bg2)', border:'1px solid var(--border)', color:'var(--text)', opacity:(!form.table||colsLoading)?0.5:1 }}>
                <option value="">Select column…</option>
                {columns.map(c=><option key={c.name} value={c.name}>{c.name} ({c.type})</option>)}
              </select>
            </div>
            {/* Date column */}
            <div>
              <label style={{ fontSize:11, color:'var(--text3)', display:'block', marginBottom:5 }}>
                Date Column (optional) {colsLoading && <span style={{opacity:.5}}>loading…</span>}
              </label>
              <select value={form.date_column} onChange={e=>setForm(f=>({...f,date_column:e.target.value}))}
                disabled={!form.table || colsLoading}
                style={{ width:'100%', padding:'8px 10px', borderRadius:'var(--r-md)', fontSize:13, background:'var(--bg2)', border:'1px solid var(--border)', color:'var(--text)', opacity:(!form.table||colsLoading)?0.5:1 }}>
                <option value="">None</option>
                {columns.map(c=><option key={c.name} value={c.name}>{c.name} ({c.type})</option>)}
              </select>
            </div>
          </div>
        )}

        <Btn onClick={runIt} disabled={loading || (mode==='manual' && (!form.table||!form.value_column))}>
          {loading ? <><Spinner size={13} color="#fff" /> Scanning…</> : 'Detect Anomalies ↗'}
        </Btn>
      </Card>

      {error && <ErrorBox message={error} />}
      {loading && <Spinner2 label="Running Isolation Forest — scanning for outliers…" />}
      {!result && !loading && !error && <Empty icon="⬡" title="No scan run yet" subtitle="Auto mode scans daily revenue from your invoices table. Switch to Manual to scan any column." />}

      {result && !loading && (
        <div className="fade-up" style={{ display:'flex', flexDirection:'column', gap:12 }}>
          <div style={{ display:'grid', gridTemplateColumns:'repeat(3,1fr)', gap:10 }}>
            <KPICard label="Data Points" value={result.total_points.toLocaleString()} icon="📊" />
            <KPICard label="Anomalies Found" value={result.anomaly_count} color={result.anomaly_count>0?'var(--amber)':'var(--green)'} icon="⚠" />
            <KPICard label="Anomaly Rate" value={`${((result.anomaly_count/result.total_points)*100).toFixed(1)}%`} icon="📉" />
          </div>

          {/* Score chart */}
          <Card style={{ padding:18 }}>
            <div style={{ fontSize:12, color:'var(--text3)', marginBottom:14 }}>Anomaly score over time — red = detected anomaly</div>
            <ResponsiveContainer width="100%" height={200}>
              <ComposedChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
                <XAxis dataKey="date" tick={{fontSize:9,fill:'#5a5f7d'}} axisLine={false} tickLine={false} interval={Math.floor(chartData.length/8)} />
                <YAxis domain={[0,1]} tick={{fontSize:10,fill:'#5a5f7d'}} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={TT} />
                <ReferenceLine y={0.6} stroke="rgba(245,166,35,0.5)" strokeDasharray="4 3" />
                <Bar dataKey="score" fill="rgba(79,142,247,0.35)" radius={[2,2,0,0]} />
                <Bar dataKey="anomaly" fill="var(--red)" radius={[2,2,0,0]} />
              </ComposedChart>
            </ResponsiveContainer>
          </Card>

          {/* Anomaly list */}
          {result.anomaly_count === 0
            ? <Card style={{ padding:'20px', textAlign:'center' }}><span style={{ color:'var(--green)', fontSize:13 }}>✓ No anomalies detected — your data looks clean.</span></Card>
            : (
              <Card style={{ overflow:'hidden' }}>
                <div style={{ padding:'11px 16px', borderBottom:'1px solid var(--border)', fontSize:12, fontWeight:500, color:'var(--text2)' }}>
                  Anomalies · {result.anomaly_count}
                </div>
                <div style={{ maxHeight:320, overflowY:'auto' }}>
                  {result.anomalies.map((a,i) => (
                    <div key={i} style={{ display:'flex', alignItems:'center', justifyContent:'space-between', padding:'11px 16px', borderBottom:'1px solid var(--border)', borderLeft:`3px solid ${a.severity==='high'?'var(--red)':a.severity==='medium'?'var(--amber)':'var(--blue)'}` }}>
                      <div>
                        <div style={{ fontSize:13, fontWeight:500 }}>{a.date}</div>
                        <div style={{ fontSize:11, color:'var(--text3)', marginTop:2, fontFamily:'var(--mono)' }}>
                          value: {a.value} &nbsp;·&nbsp; z-score: {a.zscore} &nbsp;·&nbsp; IF score: {a.anomaly_score}
                        </div>
                      </div>
                      <Badge color={SEV_COLOR[a.severity]||'gray'}>{a.severity}</Badge>
                    </div>
                  ))}
                </div>
              </Card>
            )
          }
        </div>
      )}
    </div>
  )
}
