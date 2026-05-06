import React, { useState, useEffect } from 'react'
import { fetchDiscover, runAnalytics } from '../utils/api'
import { Card, Badge, Spinner, Spinner2, ErrorBox, KPICard, ChartCard, DataTable, BarChartSimple, LineChartSimple, PieChartSimple, LLMToggle, COLORS } from '../components/UI'
import { ComposedChart, Bar, Line, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, RadarChart, Radar, PolarGrid, PolarAngleAxis, ScatterChart, Scatter } from 'recharts'

const TT = { background:'#1c1e2e', border:'1px solid rgba(255,255,255,0.1)', borderRadius:8, fontSize:12, color:'#f0f1fa', boxShadow:'0 8px 24px rgba(0,0,0,0.4)' }

const CATEGORY_COLORS = {
  Revenue:'var(--blue)', Products:'var(--green)', Customers:'var(--purple)',
  Employees:'var(--amber)', Payments:'var(--teal)', Growth:'var(--pink)', Inventory:'var(--red)',
}
const COMPLEXITY_COLOR = { simple:'green', medium:'amber', advanced:'purple' }

export default function DiscoverPage({ llm, setLlm }) {
  const [catalogue, setCatalogue] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [selected, setSelected] = useState(null)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState(null)
  const [runError, setRunError] = useState(null)
  const [filter, setFilter] = useState('All')

  useEffect(() => {
    fetchDiscover()
      .then(d => { setCatalogue(d.catalogue || []); setLoading(false) })
      .catch(e => { setError(e.response?.data?.detail || e.message); setLoading(false) })
  }, [])

  const categories = ['All', ...new Set(catalogue.map(c => c.category))]
  const visible = filter === 'All' ? catalogue : catalogue.filter(c => c.category === filter)

  async function handleRun(item) {
    setSelected(item)
    setRunning(true)
    setResult(null)
    setRunError(null)
    try {
      const data = await runAnalytics(item.id, llm, {})
      setResult(data)
    } catch (e) {
      setRunError(e.response?.data?.detail || e.message)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div style={{ display:'flex', gap:0, height:'100%', overflow:'hidden' }}>

      {/* Left — catalogue */}
      <div style={{ width:440, flexShrink:0, display:'flex', flexDirection:'column', borderRight:'1px solid var(--border)', overflow:'hidden' }}>
        <div style={{ padding:'16px 16px 0' }}>
          <div style={{ marginBottom:14 }}>
            <div style={{ fontSize:18, fontWeight:700, marginBottom:3 }}>Analytics Hub</div>
            <div style={{ fontSize:12, color:'var(--text3)' }}>Select any analysis below — the AI detects what's possible from your database structure.</div>
          </div>
          <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:12 }}>
            <LLMToggle value={llm} onChange={setLlm} />
          </div>
          {/* Category filter tabs */}
          <div style={{ display:'flex', gap:4, flexWrap:'wrap', marginBottom:12 }}>
            {categories.map(cat => (
              <button key={cat} onClick={() => setFilter(cat)} style={{
                padding:'3px 11px', borderRadius:20, fontSize:11, fontWeight:500,
                background: filter===cat ? (CATEGORY_COLORS[cat]||'var(--blue)') : 'var(--bg2)',
                color: filter===cat ? '#fff' : 'var(--text3)',
                border:'none', cursor:'pointer'
              }}>{cat}</button>
            ))}
          </div>
        </div>

        {loading && <Spinner2 label="Discovering analytics from your database…" />}
        {error && <div style={{ padding:16 }}><ErrorBox message={error} /></div>}

        <div style={{ flex:1, overflowY:'auto', padding:'0 12px 16px' }}>
          {visible.map(item => (
            <AnalyticsCard key={item.id} item={item} isSelected={selected?.id===item.id} onRun={() => handleRun(item)} />
          ))}
        </div>
      </div>

      {/* Right — result panel */}
      <div style={{ flex:1, overflowY:'auto', padding:20, display:'flex', flexDirection:'column', gap:14 }}>
        {!selected && (
          <div style={{ display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', height:'100%', color:'var(--text3)', textAlign:'center' }}>
            <div style={{ fontSize:48, marginBottom:16, opacity:.3 }}>⬡</div>
            <div style={{ fontSize:16, fontWeight:600, color:'var(--text2)', marginBottom:8 }}>Pick an analysis</div>
            <div style={{ fontSize:13, maxWidth:320 }}>Choose from the catalogue on the left. Results will appear here with charts, tables, and insights.</div>
          </div>
        )}

        {selected && (
          <>
            <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between' }}>
              <div>
                <div style={{ fontSize:18, fontWeight:700, marginBottom:2 }}>{selected.title}</div>
                <div style={{ fontSize:12, color:'var(--text3)' }}>{selected.description}</div>
              </div>
              <button onClick={() => handleRun(selected)} disabled={running} style={{
                padding:'8px 18px', borderRadius:'var(--r-md)', fontSize:13, fontWeight:500,
                background:'var(--blue)', color:'#fff', opacity: running ? 0.6 : 1, cursor: running ? 'not-allowed' : 'pointer',
                display:'flex', alignItems:'center', gap:7
              }}>
                {running ? <><svg width="13" height="13" viewBox="0 0 24 24" fill="none" style={{animation:'spin .8s linear infinite'}}><circle cx="12" cy="12" r="9" stroke="#fff" strokeWidth="2.5" strokeDasharray="40 20" strokeLinecap="round"/></svg>Running…</> : '↻ Re-run'}
              </button>
            </div>

            {runError && <ErrorBox message={runError} />}

            {running && (
              <div style={{ display:'flex', flexDirection:'column', gap:10 }}>
                {[100,70,85,60].map((w,i) => <div key={i} className="skeleton" style={{ height:i===0?160:40, width:`${w}%` }} />)}
              </div>
            )}

            {result && !running && (
              <ResultPanel result={result} templateId={selected.id} />
            )}
          </>
        )}
      </div>
    </div>
  )
}


function AnalyticsCard({ item, isSelected, onRun }) {
  const catColor = CATEGORY_COLORS[item.category] || 'var(--blue)'
  return (
    <div onClick={onRun} style={{
      padding:'12px 14px', borderRadius:'var(--r-md)', marginBottom:6, cursor:'pointer',
      background: isSelected ? 'var(--blue-dim)' : 'var(--bg2)',
      border: `1px solid ${isSelected ? 'rgba(79,142,247,0.3)' : 'var(--border)'}`,
      transition:'all .1s'
    }}>
      <div style={{ display:'flex', alignItems:'flex-start', gap:10 }}>
        <div style={{ fontSize:20, flexShrink:0, marginTop:1 }}>{item.icon}</div>
        <div style={{ flex:1, minWidth:0 }}>
          <div style={{ display:'flex', alignItems:'center', gap:6, marginBottom:4 }}>
            <div style={{ fontSize:13, fontWeight:600, color: isSelected ? 'var(--blue)' : 'var(--text)' }}>{item.title}</div>
          </div>
          <div style={{ fontSize:11, color:'var(--text3)', marginBottom:6, lineHeight:1.5 }}>{item.description}</div>
          <div style={{ display:'flex', gap:5, flexWrap:'wrap' }}>
            <Badge color={COMPLEXITY_COLOR[item.complexity]||'gray'}>{item.complexity}</Badge>
            <span style={{ fontSize:10, color:'var(--text3)', padding:'2px 8px', background:'var(--bg3)', borderRadius:20 }}>{item.category}</span>
            {item.tables_used?.slice(0,2).map(t => (
              <span key={t} style={{ fontSize:10, color:'var(--text3)', padding:'2px 8px', background:'var(--bg3)', borderRadius:20, fontFamily:'var(--mono)' }}>{t}</span>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}


function ResultPanel({ result, templateId }) {
  const { title, columns, data, row_count } = result

  // Pick the right chart type based on template
  const renderChart = () => {
    if (!data?.length) return null

    // RFM segment chart
    if (templateId === 'customer_rfm') {
      return (
        <ChartCard title="Customer Segment Distribution">
          <BarChartSimple data={data} xKey="segment" yKey="customers" color="var(--purple)" horizontal />
        </ChartCard>
      )
    }

    // Revenue trend — line chart
    if (templateId === 'revenue_trend') {
      return (
        <ChartCard title="Monthly Revenue & Transactions">
          <ResponsiveContainer width="100%" height={240}>
            <ComposedChart data={[...data].reverse()}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
              <XAxis dataKey="month" tick={{fontSize:10,fill:'#5a5f7d'}} axisLine={false} tickLine={false} />
              <YAxis yAxisId="left" tick={{fontSize:10,fill:'#5a5f7d'}} axisLine={false} tickLine={false} />
              <YAxis yAxisId="right" orientation="right" tick={{fontSize:10,fill:'#5a5f7d'}} axisLine={false} tickLine={false} />
              <Tooltip contentStyle={TT} />
              <Bar yAxisId="left" dataKey="revenue" fill="var(--blue)" radius={[4,4,0,0]} opacity={.8} />
              <Line yAxisId="right" dataKey="transactions" stroke="var(--green)" strokeWidth={2} dot={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </ChartCard>
      )
    }

    // Payment methods — pie
    if (templateId === 'payment_methods') {
      return (
        <ChartCard title="Payment Method Share">
          <PieChartSimple data={data} nameKey="payment_method" valueKey="revenue" height={240} />
        </ChartCard>
      )
    }

    // Growth — line chart with MoM
    if (templateId === 'growth_metrics') {
      return (
        <ChartCard title="Monthly Revenue Growth %">
          <LineChartSimple data={data} xKey="month" yKeys={['revenue_growth']} colors={['var(--green)']} height={220} />
        </ChartCard>
      )
    }

    // Category breakdowns — horizontal bar
    if (['revenue_by_category','margin_by_category','top_products'].includes(templateId)) {
      const yKey = data[0]?.revenue !== undefined ? 'revenue' : Object.keys(data[0]||{}).find(k => typeof data[0][k]==='number') || 'value'
      const xKey = data[0]?.category !== undefined ? 'category' : data[0]?.name !== undefined ? 'name' : columns[0]
      return (
        <ChartCard title={`${yKey.replace(/_/g,' ')} by ${xKey}`}>
          <BarChartSimple data={data.slice(0,12)} xKey={xKey} yKey={yKey} color="var(--blue)" horizontal />
        </ChartCard>
      )
    }

    // Hourly pattern — bar chart
    if (templateId === 'hourly_pattern') {
      return (
        <ChartCard title="Hourly Revenue Pattern">
          <BarChartSimple data={data} xKey="hour" yKey="total_revenue" color="var(--teal)" />
        </ChartCard>
      )
    }

    // Customer retention — line
    if (templateId === 'customer_retention') {
      return (
        <ChartCard title="Retention Rate by Cohort Month">
          <LineChartSimple data={data} xKey="cohort" yKeys={['retention_pct']} colors={['var(--purple)']} height={220} />
        </ChartCard>
      )
    }

    // Location — multi bar
    if (['revenue_by_location','location_comparison'].includes(templateId)) {
      return (
        <ChartCard title="Revenue & Avg Ticket by Location">
          <BarChartSimple data={data} xKey="location_name" yKey="revenue" color="var(--blue)" horizontal />
        </ChartCard>
      )
    }

    // Loyalty tiers — pie
    if (templateId === 'loyalty_tiers') {
      return (
        <ChartCard title="Customers per Loyalty Tier">
          <PieChartSimple data={data} nameKey="tier" valueKey="customers" height={220} />
        </ChartCard>
      )
    }

    // Default: first numeric col bar chart
    const numCols = columns.filter(c => typeof data[0]?.[c] === 'number')
    const strCols = columns.filter(c => typeof data[0]?.[c] === 'string')
    if (numCols.length && strCols.length) {
      return (
        <ChartCard title={`${numCols[0].replace(/_/g,' ')} by ${strCols[0].replace(/_/g,' ')}`}>
          <BarChartSimple data={data.slice(0,15)} xKey={strCols[0]} yKey={numCols[0]} color="var(--blue)" horizontal={data.length>6} />
        </ChartCard>
      )
    }
    return null
  }

  const numCols = columns?.filter(c => typeof data[0]?.[c] === 'number') || []
  const stats = numCols.slice(0,4)

  return (
    <div className="fade-up" style={{ display:'flex', flexDirection:'column', gap:14 }}>
      {/* KPI row */}
      {stats.length > 0 && (
        <div style={{ display:'grid', gridTemplateColumns:`repeat(${Math.min(stats.length,4)},1fr)`, gap:10 }}>
          {stats.map((col,i) => {
            const total = data.reduce((s,r)=>s+(r[col]||0),0)
            const avg = total/data.length
            const val = data.length === 1 ? data[0][col] : (col.includes('avg')||col.includes('pct')||col.includes('rate') ? avg : total)
            const colors = ['var(--blue)','var(--green)','var(--purple)','var(--amber)']
            return <KPICard key={col} label={col.replace(/_/g,' ')} value={typeof val==='number'?val.toLocaleString(undefined,{maximumFractionDigits:1}):val} color={colors[i%4]} />
          })}
        </div>
      )}

      {/* Chart */}
      {renderChart()}

      {/* Data table */}
      <Card style={{ overflow:'hidden' }}>
        <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', padding:'12px 16px', borderBottom:'1px solid var(--border)' }}>
          <span style={{ fontSize:12, fontWeight:500, color:'var(--text2)' }}>Data · {row_count} rows</span>
        </div>
        <DataTable columns={columns} data={data} maxHeight={320} />
      </Card>
    </div>
  )
}
