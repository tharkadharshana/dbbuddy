import React, { useState, useEffect } from 'react'
import { ComposedChart, Area, Line, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, BarChart } from 'recharts'
import { Card, Btn, Spinner, Spinner2, Empty, ErrorBox, KPICard } from '../components/UI'
import { runAutoForecast, runForecast, fetchTables, fetchTableColumns } from '../utils/api'

const TT = { background:'#1c1e2e', border:'1px solid rgba(255,255,255,0.08)', borderRadius:8, fontSize:12, color:'#f0f1fa' }

export default function ForecastPage() {
  const [mode, setMode]       = useState('auto')
  const [periods, setPeriods] = useState(90)
  const [loading, setLoading] = useState(false)
  const [result, setResult]   = useState(null)
  const [error, setError]     = useState(null)
  const [tables, setTables]         = useState([])
  const [columns, setColumns]       = useState([])
  const [colsLoading, setColsLoading] = useState(false)
  const [manForm, setManForm]       = useState({ table:'', date_column:'', value_column:'' })

  useEffect(() => {
    fetchTables().then(d => setTables(d.tables||[])).catch(()=>{})
  }, [])

  useEffect(() => {
    if (!manForm.table) { setColumns([]); return }
    setColsLoading(true)
    fetchTableColumns(manForm.table)
      .then(d => {
        const cols = d.columns || []
        setColumns(cols)
        // Auto-select sensible defaults
        const dateCol = cols.find(c => /datetime|date|timestamp/i.test(c.type))
        const numCol  = cols.find(c => /decimal|float|double|numeric/i.test(c.type))
        setManForm(f => ({
          ...f,
          date_column:  dateCol?.name || f.date_column,
          value_column: numCol?.name  || f.value_column,
        }))
      })
      .catch(() => setColumns([]))
      .finally(() => setColsLoading(false))
  }, [manForm.table])

  async function runIt() {
    setLoading(true); setError(null); setResult(null)
    try {
      const data = mode==='auto'
        ? await runAutoForecast(periods)
        : await runForecast(manForm.table, manForm.date_column, manForm.value_column, periods)
      setResult(data)
    } catch(e) { setError(e.response?.data?.detail || e.message) }
    finally { setLoading(false) }
  }

  const combined = result ? [
    ...result.historical.map(h => ({ date: h.date, actual: h.value })),
    ...result.forecast.map(f => ({ date: f.date, forecast: f.yhat, upper: f.yhat_upper, lower: f.yhat_lower }))
  ] : []

  const weeklyData = result ? Object.entries(result.weekly_seasonality||{}).map(([day,val]) => ({ day: day.slice(0,3), value:+val.toFixed(2) })) : []
  const { summary } = result || {}

  return (
    <div style={{ padding:20, display:'flex', flexDirection:'column', gap:14, height:'100%', overflowY:'auto' }}>
      {/* Config */}
      <Card style={{ padding:18 }}>
        <div style={{ fontSize:11, fontWeight:600, color:'var(--text3)', textTransform:'uppercase', letterSpacing:'.08em', marginBottom:14 }}>Forecast Configuration</div>

        {/* Mode toggle */}
        <div style={{ display:'flex', background:'var(--bg3)', borderRadius:'var(--r-md)', padding:4, gap:3, marginBottom:16, width:'fit-content' }}>
          {[['auto','⚡ Auto (Revenue)'],['manual','⚙ Manual']].map(([m,l]) => (
            <button key={m} onClick={()=>setMode(m)} style={{ padding:'6px 18px', borderRadius:'var(--r-sm)', fontSize:12, fontWeight:500, background: mode===m ? 'var(--blue)' : 'transparent', color: mode===m ? '#fff' : 'var(--text3)', border:'none', cursor:'pointer' }}>{l}</button>
          ))}
        </div>

        {mode==='manual' && (
          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:10, marginBottom:14 }}>
            {/* Table */}
            <div>
              <label style={{ fontSize:11, color:'var(--text3)', display:'block', marginBottom:5 }}>Table</label>
              <select value={manForm.table} onChange={e=>setManForm(f=>({...f,table:e.target.value,date_column:'',value_column:''}))}
                style={{ width:'100%', padding:'8px 10px', borderRadius:'var(--r-md)', fontSize:13, background:'var(--bg2)', border:'1px solid var(--border)', color:'var(--text)' }}>
                <option value="">Select table…</option>
                {tables.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
            {/* Date column */}
            <div>
              <label style={{ fontSize:11, color:'var(--text3)', display:'block', marginBottom:5 }}>
                Date Column {colsLoading && <span style={{opacity:.5}}>loading…</span>}
              </label>
              <select value={manForm.date_column} onChange={e=>setManForm(f=>({...f,date_column:e.target.value}))}
                disabled={!manForm.table || colsLoading}
                style={{ width:'100%', padding:'8px 10px', borderRadius:'var(--r-md)', fontSize:13, background:'var(--bg2)', border:'1px solid var(--border)', color:'var(--text)', opacity: (!manForm.table||colsLoading)?0.5:1 }}>
                <option value="">Select column…</option>
                {columns.map(c => <option key={c.name} value={c.name}>{c.name} ({c.type})</option>)}
              </select>
            </div>
            {/* Value column */}
            <div>
              <label style={{ fontSize:11, color:'var(--text3)', display:'block', marginBottom:5 }}>Value Column</label>
              <select value={manForm.value_column} onChange={e=>setManForm(f=>({...f,value_column:e.target.value}))}
                disabled={!manForm.table || colsLoading}
                style={{ width:'100%', padding:'8px 10px', borderRadius:'var(--r-md)', fontSize:13, background:'var(--bg2)', border:'1px solid var(--border)', color:'var(--text)', opacity: (!manForm.table||colsLoading)?0.5:1 }}>
                <option value="">Select column…</option>
                {columns.map(c => <option key={c.name} value={c.name}>{c.name} ({c.type})</option>)}
              </select>
            </div>
          </div>
        )}

        <div style={{ display:'flex', alignItems:'center', gap:12 }}>
          <div>
            <label style={{ fontSize:11, color:'var(--text3)', display:'block', marginBottom:5 }}>Forecast horizon</label>
            <select value={periods} onChange={e=>setPeriods(Number(e.target.value))} style={{ padding:'8px 10px', borderRadius:'var(--r-md)', fontSize:13 }}>
              {[30,60,90,180].map(p => <option key={p} value={p}>{p} days</option>)}
            </select>
          </div>
          <Btn onClick={runIt} disabled={loading} style={{ alignSelf:'flex-end' }}>
            {loading ? <><Spinner size={13} color="#fff" /> Running Prophet…</> : 'Run Forecast ↗'}
          </Btn>
        </div>
      </Card>

      {error && <ErrorBox message={error} />}
      {loading && <Spinner2 label="Fitting Prophet model — this takes a few seconds…" />}
      {!result && !loading && !error && (
        <div style={{ background:'var(--blue-dim)', border:'1px solid rgba(79,142,247,0.2)', borderRadius:'var(--r-md)', padding:'12px 16px', fontSize:13, color:'var(--blue)' }}>
          ✦ Powered by Meta's Prophet. Auto mode forecasts daily revenue from your invoices table.
        </div>
      )}

      {result && !loading && (
        <div className="fade-up" style={{ display:'flex', flexDirection:'column', gap:12 }}>
          {/* Summary KPIs */}
          <div style={{ display:'grid', gridTemplateColumns:'repeat(3,1fr)', gap:10 }}>
            <KPICard label="Historical Points" value={result.historical.length} icon="📊" />
            <KPICard label="Next 30-day Avg" value={summary?.next_30_avg?.toLocaleString()} icon="📈" />
            <KPICard label="Predicted Growth" value={`${summary?.predicted_growth_pct > 0 ? '+' : ''}${summary?.predicted_growth_pct}%`} color={summary?.predicted_growth_pct >= 0 ? 'var(--green)' : 'var(--red)'} icon="🚀" />
          </div>

          {/* Main chart */}
          <Card style={{ padding:18 }}>
            <div style={{ fontSize:12, color:'var(--text3)', marginBottom:14 }}>Historical + {periods}-day forecast · 80% confidence band</div>
            <ResponsiveContainer width="100%" height={280}>
              <ComposedChart data={combined}>
                <defs>
                  <linearGradient id="confBand" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#4f8ef7" stopOpacity={0.15}/>
                    <stop offset="100%" stopColor="#4f8ef7" stopOpacity={0.02}/>
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
                <XAxis dataKey="date" tick={{fontSize:10,fill:'#5a5f7d'}} axisLine={false} tickLine={false} interval={Math.floor(combined.length/8)} />
                <YAxis tick={{fontSize:10,fill:'#5a5f7d'}} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={TT} />
                <Area dataKey="upper" fill="url(#confBand)" stroke="none" />
                <Area dataKey="lower" fill="var(--bg)" stroke="none" />
                <Line dataKey="actual" stroke="var(--green)" strokeWidth={1.8} dot={false} connectNulls={false} />
                <Line dataKey="forecast" stroke="var(--blue)" strokeWidth={2} strokeDasharray="5 3" dot={false} connectNulls={false} />
              </ComposedChart>
            </ResponsiveContainer>
            <div style={{ display:'flex', gap:18, marginTop:10, fontSize:11, color:'var(--text3)' }}>
              <span><span style={{ display:'inline-block', width:18, height:2, background:'var(--green)', marginRight:5, verticalAlign:'middle' }}/>Historical</span>
              <span><span style={{ display:'inline-block', width:18, height:2, background:'var(--blue)', marginRight:5, verticalAlign:'middle', borderTop:'2px dashed var(--blue)' }}/>Forecast</span>
              <span><span style={{ display:'inline-block', width:18, height:8, background:'rgba(79,142,247,0.15)', marginRight:5, verticalAlign:'middle', borderRadius:2 }}/>80% Confidence</span>
            </div>
          </Card>

          {/* Weekly seasonality */}
          {weeklyData.length > 0 && (
            <Card style={{ padding:18 }}>
              <div style={{ fontSize:12, color:'var(--text3)', marginBottom:14 }}>Weekly seasonality pattern</div>
              <ResponsiveContainer width="100%" height={160}>
                <BarChart data={weeklyData} barSize={28}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
                  <XAxis dataKey="day" tick={{fontSize:11,fill:'#5a5f7d'}} axisLine={false} tickLine={false} />
                  <YAxis tick={{fontSize:10,fill:'#5a5f7d'}} axisLine={false} tickLine={false} />
                  <Tooltip contentStyle={TT} />
                  <Bar dataKey="value" fill="var(--purple)" radius={[4,4,0,0]} />
                </BarChart>
              </ResponsiveContainer>
            </Card>
          )}
        </div>
      )}
    </div>
  )
}
