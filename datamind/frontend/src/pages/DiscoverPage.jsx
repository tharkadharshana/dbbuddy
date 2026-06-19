import React, { useState, useEffect, useRef } from 'react'
import { fetchDiscover, runAnalytics, fetchCacheProgress, rebuildCache,
         fetchConnectedProviders, fetchIntegrationTemplates, runIntegrationAnalytics,
         syncProvider, fetchProviderStatus, getErrorMessage } from '../utils/api'
import { Card, Badge, Spinner, Spinner2, ErrorBox, KPICard, ChartCard, DataTable,
         BarChartSimple, LineChartSimple, PieChartSimple, UsageMeter, COLORS, Btn } from '../components/UI'
import { ComposedChart, Bar, Line, Area, XAxis, YAxis, CartesianGrid, Tooltip,
         ResponsiveContainer } from 'recharts'
import { formatCurrency, formatNumber, isCurrencyColumn } from '../utils/locale'

const TT = { background:'#1c1e2e', border:'1px solid rgba(255,255,255,0.08)', borderRadius:8, fontSize:12, color:'#f0f1fa' }

const CATEGORY_COLORS = {
  Revenue:'var(--blue)', Products:'var(--green)', Customers:'var(--purple)',
  Employees:'var(--amber)', Payments:'var(--teal)', Growth:'var(--pink)',
  Inventory:'var(--red)', Other:'var(--text3)',
}
const COMPLEXITY_COLOR = { simple:'green', medium:'amber', advanced:'purple' }

// ── Build Progress Panel ──────────────────────────────────────────────────────
function BuildProgress({ onDone }) {
  const [logs, setLogs]     = useState([])
  const [status, setStatus] = useState('building')
  const [pct, setPct]       = useState(0)
  const logRef = useRef(null)
  const pollRef = useRef(null)

  useEffect(() => {
    pollRef.current = setInterval(async () => {
      try {
        const data = await fetchCacheProgress()
        setLogs(data.progress || [])
        setStatus(data.status || 'building')
        // Estimate progress from log count (we know ~24 templates)
        const logCount = (data.progress || []).length
        setPct(Math.min(95, Math.round((logCount / 28) * 100)))
        if (data.status === 'done' || data.status === 'error') {
          clearInterval(pollRef.current)
          setPct(100)
          setTimeout(() => onDone(), 1200)
        }
      } catch(e) {}
    }, 1500)
    return () => clearInterval(pollRef.current)
  }, [])

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [logs])

  return (
    <div style={{ display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', height:'100%', padding:32 }}>
      <div style={{ width:'100%', maxWidth:520 }}>
        {/* Icon + title */}
        <div style={{ textAlign:'center', marginBottom:24 }}>
          <div style={{ fontSize:48, marginBottom:12 }}>🧠</div>
          <div style={{ fontSize:20, fontWeight:700, marginBottom:6 }}>
            {status === 'done' ? 'Analytics Ready!' : status === 'error' ? 'Build Failed' : 'Learning Your Database…'}
          </div>
          <div style={{ fontSize:13, color:'var(--text3)', maxWidth:360, margin:'0 auto' }}>
            {status === 'done'
              ? 'All SQL queries have been generated and cached. Loading your analytics…'
              : status === 'error'
              ? 'Something went wrong. Check your API keys and try rebuilding.'
              : 'The AI is reading your schema and generating custom SQL for every analytics template. This runs once and is then cached forever.'}
          </div>
        </div>

        {/* Progress bar */}
        <div style={{ background:'var(--bg3)', borderRadius:99, height:6, marginBottom:20, overflow:'hidden' }}>
          <div style={{
            height:'100%', borderRadius:99,
            background: status === 'error' ? 'var(--red)' : status === 'done' ? 'var(--green)' : 'var(--blue)',
            width:`${pct}%`, transition:'width .4s ease'
          }} />
        </div>

        {/* Log */}
        <div ref={logRef} style={{ background:'var(--bg2)', border:'1px solid var(--border)', borderRadius:'var(--r-lg)', padding:'14px 16px', maxHeight:280, overflowY:'auto', fontFamily:'var(--mono)', fontSize:11, color:'var(--text2)', lineHeight:1.8 }}>
          {logs.length === 0 && <div style={{ color:'var(--text3)' }}>Starting…</div>}
          {logs.map((l, i) => (
            <div key={i} style={{
              color: l.startsWith('✅') ? 'var(--green)' : l.startsWith('❌') ? 'var(--red)' : l.startsWith('  ⚠') ? 'var(--amber)' : 'var(--text2)'
            }}>{l}</div>
          ))}
          {status === 'building' && <div style={{ color:'var(--text3)' }}>▋</div>}
        </div>

        <div style={{ textAlign:'center', marginTop:14, fontSize:11, color:'var(--text3)' }}>
          {status === 'done' ? `✓ Done` : status === 'error' ? '' : `This runs once per database — future loads are instant.`}
        </div>
      </div>
    </div>
  )
}

// ── Analytics Card ────────────────────────────────────────────────────────────
function AnalyticsCard({ item, isSelected, onRun, disabled }) {
  return (
    <div onClick={disabled ? undefined : onRun} style={{
      padding:'12px 14px', borderRadius:'var(--r-md)', marginBottom:5,
      cursor: disabled ? 'not-allowed' : 'pointer',
      opacity: disabled ? 0.55 : 1,
      background: isSelected ? 'var(--blue-dim)' : 'var(--bg2)',
      border:`1px solid ${isSelected ? 'rgba(79,142,247,0.3)' : 'var(--border)'}`,
      transition:'all .1s'
    }}>
      <div style={{ display:'flex', alignItems:'flex-start', gap:10 }}>
        <div style={{ fontSize:18, flexShrink:0, marginTop:1 }}>{item.icon}</div>
        <div style={{ flex:1, minWidth:0 }}>
          <div style={{ fontSize:13, fontWeight:600, color: isSelected ? 'var(--blue)' : 'var(--text)', marginBottom:4 }}>{item.title}</div>
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

// ── Source badge ──────────────────────────────────────────────────────────────
function SourceBadge({ source }) {
  if (!source) return null
  const info = {
    cache:    { label:'⚡ From cache',    color:'green' },
    python:   { label:'🐍 Python analytics', color:'blue' },
    fallback: { label:'⚠ Fallback SQL',  color:'amber' },
  }[source] || { label: source, color:'gray' }
  return <Badge color={info.color} style={{ fontSize:10 }}>{info.label}</Badge>
}

// ── Result Panel ──────────────────────────────────────────────────────────────
function ResultPanel({ result, templateId }) {
  const { title, columns, data, row_count, source } = result

  const renderChart = () => {
    if (!data?.length) return null
    const numCols = columns.filter(c => typeof data[0]?.[c] === 'number')
    const strCols = columns.filter(c => typeof data[0]?.[c] === 'string')

    if (templateId === 'customer_rfm') {
      return <ChartCard title="Customer Segments"><BarChartSimple data={data} xKey="segment" yKey="customers" color="var(--purple)" horizontal /></ChartCard>
    }
    if (templateId === 'revenue_trend') {
      const chartData = data
      return (
        <ChartCard title="Daily Revenue & Transactions">
          <ResponsiveContainer width="100%" height={240}>
            <ComposedChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
              <XAxis dataKey={strCols[0]||'date'} tick={{fontSize:10,fill:'#5a5f7d'}} axisLine={false} tickLine={false} />
              <YAxis yAxisId="l" tick={{fontSize:10,fill:'#5a5f7d'}} axisLine={false} tickLine={false} />
              <YAxis yAxisId="r" orientation="right" tick={{fontSize:10,fill:'#5a5f7d'}} axisLine={false} tickLine={false} />
              <Tooltip contentStyle={TT} />
              {numCols[0] && <Bar yAxisId="l" dataKey={numCols[0]} fill="var(--blue)" radius={[4,4,0,0]} opacity={.8} />}
              {numCols[1] && <Line yAxisId="r" dataKey={numCols[1]} stroke="var(--green)" strokeWidth={2} dot={false} />}
            </ComposedChart>
          </ResponsiveContainer>
        </ChartCard>
      )
    }
    if (templateId === 'payment_methods' || templateId === 'payment_breakdown') {
      const nameKey = strCols[0] || 'payment_method'
      const valueKey = numCols.find(c => c.includes('revenue') || c.includes('total')) || numCols[0]
      return <ChartCard title="Revenue by Payment Method"><PieChartSimple data={data} nameKey={nameKey} valueKey={valueKey} height={220} /></ChartCard>
    }
    if (templateId === 'customer_analysis') {
      const yKey = numCols.find(c => c.includes('lifetime')) || numCols[1] || numCols[0]
      return <ChartCard title="Lifetime Value by Customer"><BarChartSimple data={data.slice(0,15)} xKey={strCols[0]||'customer'} yKey={yKey} color="var(--purple)" horizontal /></ChartCard>
    }
    if (templateId === 'loyalty_tiers') {
      return <ChartCard title="Customers per Tier"><PieChartSimple data={data} nameKey={strCols[0]||'tier'} valueKey={numCols[0]||'customers'} height={220} /></ChartCard>
    }
    if (['growth_metrics','customer_retention'].includes(templateId)) {
      const yKey = numCols.find(c => c.includes('growth')||c.includes('pct')||c.includes('rate')) || numCols[0]
      return <ChartCard title={yKey?.replace(/_/g,' ')}><LineChartSimple data={data} xKey={strCols[0]||'month'} yKeys={[yKey]} colors={['var(--green)']} height={200} /></ChartCard>
    }
    // Default: bar/horizontal bar
    if (numCols.length && strCols.length) {
      const horizontal = data.length > 8 || strCols[0]?.toLowerCase().includes('name') || strCols[0]?.toLowerCase().includes('product')
      return (
        <ChartCard title={`${numCols[0].replace(/_/g,' ')} by ${strCols[0].replace(/_/g,' ')}`}>
          <BarChartSimple data={data.slice(0,15)} xKey={strCols[0]} yKey={numCols[0]} color="var(--blue)" horizontal={horizontal} />
        </ChartCard>
      )
    }
    return null
  }

  const numCols = columns?.filter(c => typeof data[0]?.[c] === 'number') || []
  // Exclude pure dimension columns that happen to be numeric (hour number, day index, etc.)
  const _KPI_DIMENSIONS = /^(hour_of_day|hour|day_of_week|week_num|month_num|year_num)$/i
  const kpiCols = numCols.filter(c => !_KPI_DIMENSIONS.test(c)).slice(0, 4)

  return (
    <div className="fade-up" style={{ display:'flex', flexDirection:'column', gap:14 }}>
      <div style={{ display:'flex', alignItems:'center', gap:8, flexWrap:'wrap' }}>
        <SourceBadge source={source} />
        {source === 'fallback' && (
          <span style={{ fontSize:11, color:'var(--amber)' }}>Using generic fallback SQL — connect a DB and rebuild cache for schema-specific queries</span>
        )}
      </div>

      {kpiCols.length > 0 && (
        <div style={{ display:'grid', gridTemplateColumns:`repeat(${Math.min(kpiCols.length,4)},1fr)`, gap:10 }}>
          {kpiCols.map((col, i) => {
            const vals = data.map(r => r[col]||0)
            const total = vals.reduce((a,b)=>a+b,0)
            const isAvg = col.includes('avg')||col.includes('pct')||col.includes('rate')||col.includes('margin')
            const val = data.length===1 ? data[0][col] : (isAvg ? total/vals.length : total)
            const colors = ['var(--blue)','var(--green)','var(--purple)','var(--amber)']
            const decimals = isCurrencyColumn(col) ? 2 : (Number.isInteger(val) ? 0 : 1)
            // Currency symbol hidden in Analytics Hub reports — single-currency tenants don't need it
            // const kpiVal = typeof val==='number'?(isCurrencyColumn(col)?formatCurrency(val):formatNumber(val,null,decimals)):val
            const kpiVal = typeof val==='number' ? formatNumber(val, null, decimals) : val
            return <KPICard key={col} label={col.replace(/_/g,' ')} value={kpiVal} color={colors[i%4]} />
          })}
        </div>
      )}

      {renderChart()}

      <Card style={{ overflow:'hidden' }}>
        <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', padding:'12px 16px', borderBottom:'1px solid var(--border)' }}>
          <span style={{ fontSize:12, fontWeight:500, color:'var(--text2)' }}>Data · {row_count} rows</span>
        </div>
        <DataTable columns={columns} data={data} maxHeight={320} />
      </Card>
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────
export default function DiscoverPage({ llm, setLlm, sub, hasDB, onNavigate, onQueryComplete }) {
  const [catalogue, setCatalogue]   = useState([])
  const [loading, setLoading]       = useState(true)
  const [building, setBuilding]     = useState(false)
  const [needsBuild, setNeedsBuild] = useState(false)
  const [cacheInfo, setCacheInfo]   = useState(null)
  const [error, setError]           = useState(null)
  const [selected, setSelected]     = useState(null)
  const [running, setRunning]       = useState(false)
  const [result, setResult]         = useState(null)
  const [runError, setRunError]     = useState(null)
  const [filter, setFilter]         = useState('All')
  const [rateLimitUntil, setRateLimitUntil] = useState(null)
  const [rlSecsLeft, setRlSecsLeft]         = useState(0)
  const [syncPhase, setSyncPhase]           = useState(null)  // null | 'syncing' | 'querying'
  const [lastSyncAt, setLastSyncAt]         = useState(null)
  const syncPollRef                         = useRef(null)

  useEffect(() => {
    if (!rateLimitUntil) return
    const tick = () => {
      const secs = Math.ceil((rateLimitUntil - Date.now()) / 1000)
      if (secs <= 0) { setRateLimitUntil(null); setRlSecsLeft(0) }
      else setRlSecsLeft(secs)
    }
    tick()
    const id = setInterval(tick, 500)
    return () => clearInterval(id)
  }, [rateLimitUntil])

  const load = async () => {
    setLoading(true); setError(null)
    try {
      const d = await fetchDiscover()
      if (d.building) { setBuilding(true); setCatalogue([]); return }

      // /discover returned catalogue (own-DB cache or merged) — use it directly
      if (!d.needs_build) {
        setCatalogue(d.catalogue || [])
        setCacheInfo({ built_at: d.built_at, template_count: d.template_count })
        setBuilding(false); setNeedsBuild(false)
        return
      }

      // /discover says needs_build — but user may have API integrations.
      // Fetch connected providers and pull their pre-built templates directly.
      const providers = await fetchConnectedProviders().catch(() => ({ connections: [] }))
      const connections = providers.connections || []
      if (connections.length > 0) {
        const all = await Promise.all(
          connections.map(c =>
            fetchIntegrationTemplates(c.provider_id)
              .then(r => (r.templates || []).map(t => ({
                ...t,
                category: c.display_name || c.provider_id,
                provider: c.provider_id,
                connection_id: c.connection_id,
                last_sync_at: c.last_sync_at,
              })))
              .catch(() => [])
          )
        )
        const integrationCatalogue = all.flat()
        if (integrationCatalogue.length > 0) {
          setCatalogue(integrationCatalogue)
          setCacheInfo(null)
          setBuilding(false); setNeedsBuild(false)
          return
        }
      }

      // Truly nothing available
      setNeedsBuild(true); setCatalogue([])
    } catch(e) { setError(getErrorMessage(e)) }
    finally { setLoading(false) }
  }

  useEffect(() => { load() }, [])
  useEffect(() => () => { if (syncPollRef.current) clearInterval(syncPollRef.current) }, [])

  async function handleRefresh(item) {
    if (running || syncPhase || (rateLimitUntil && Date.now() < rateLimitUntil)) return
    setSelected(item); setResult(null); setRunError(null)

    if (item.provider) {
      // Integration template — sync first (if we have connection_id), then query
      setRunning(true)

      if (item.connection_id) {
        setSyncPhase('syncing')
        try {
          await syncProvider(item.connection_id)
        } catch { /* sync trigger failed — proceed to query anyway */ }

        // Poll until sync finishes (max 3 min)
        const deadline = Date.now() + 180_000
        await new Promise(resolve => {
          syncPollRef.current = setInterval(async () => {
            try {
              const s = await fetchProviderStatus(item.connection_id)
              if (s.status !== 'syncing' || Date.now() > deadline) {
                clearInterval(syncPollRef.current)
                if (s.last_sync_at) setLastSyncAt(s.last_sync_at)
                resolve()
              }
            } catch { clearInterval(syncPollRef.current); resolve() }
          }, 2500)
        })
      }

      setSyncPhase('querying')
      try {
        const data = await runIntegrationAnalytics(item.provider, item.id)
        if (data.ok === false || data.success === false) {
          setRateLimitUntil(Date.now() + (data.retry_after_seconds || 60) * 1000)
        } else {
          setResult(data)
          if (item.connection_id) setRateLimitUntil(Date.now() + 60_000)
        }
      } catch(e) { setRunError(getErrorMessage(e)) }
      finally { setRunning(false); setSyncPhase(null); onQueryComplete?.() }

    } else {
      // Own-DB: just run the query
      setRunning(true)
      try {
        const data = await runAnalytics(item.id, llm, {})
        if (data.ok === false || data.success === false) {
          setRateLimitUntil(Date.now() + (data.retry_after_seconds || 60) * 1000)
        } else {
          setResult(data)
        }
      } catch(e) { setRunError(getErrorMessage(e)) }
      finally { setRunning(false); onQueryComplete?.() }
    }
  }

  async function handleRebuild() {
    try {
      await rebuildCache()
      setBuilding(true); setCatalogue([]); setNeedsBuild(false)
    } catch(e) {
      setError(getErrorMessage(e, 'Failed to start cache build.'))
    }
  }

  const categories = ['All', ...new Set(catalogue.map(c => c.category))]
  const visible = filter === 'All' ? catalogue : catalogue.filter(c => c.category === filter)


  // Show build progress screen
  if (building) return <BuildProgress onDone={() => { setBuilding(false); load() }} />

  // Show "needs build" prompt
  if (needsBuild && !loading) return (
    <div style={{ display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', height:'100%', padding:32, textAlign:'center' }}>
      <div style={{ fontSize:48, marginBottom:16 }}>🔌</div>
      <div style={{ fontSize:18, fontWeight:700, marginBottom:8 }}>No analytics cache yet</div>
      <div style={{ fontSize:13, color:'var(--text3)', maxWidth:380, marginBottom:24 }}>
        Connect a database in Settings, then click below to let the AI analyse your schema and generate all SQL queries. This runs <strong style={{color:'var(--text2)'}}>once</strong> and is cached forever.
      </div>
      <Btn onClick={handleRebuild}>🧠 Build Analytics Cache</Btn>
    </div>
  )

  return (
    <div style={{ display:'flex', gap:0, height:'100%', overflow:'hidden' }}>
      {/* Left — catalogue */}
      <div style={{ width:440, flexShrink:0, display:'flex', flexDirection:'column', borderRight:'1px solid var(--border)', overflow:'hidden' }}>
        <div style={{ padding:'16px 16px 0' }}>
          <div style={{ display:'flex', alignItems:'flex-start', justifyContent:'space-between', marginBottom:10 }}>
            <div>
              <div style={{ fontSize:17, fontWeight:700, marginBottom:2 }}>Analytics Hub</div>
              <div style={{ fontSize:11, color:'var(--text3)' }}>
                {cacheInfo
                  ? `⚡ ${cacheInfo.template_count} templates cached · built ${new Date(cacheInfo.built_at).toLocaleDateString()}`
                  : 'Select any analysis — results load from cache instantly.'}
              </div>
            </div>
            <div style={{ display:'flex', gap:6 }}>
              {hasDB && <button onClick={handleRebuild} title="Rebuild cache" style={{ padding:'5px 10px', background:'var(--bg3)', border:'1px solid var(--border)', borderRadius:'var(--r-sm)', fontSize:11, color:'var(--text3)', cursor:'pointer' }}>↺ Rebuild</button>}
            </div>
          </div>
          <div style={{ marginBottom:12 }}><UsageMeter sub={sub} /></div>
          <div style={{ display:'flex', gap:4, flexWrap:'wrap', marginBottom:10 }}>
            {categories.map(cat => (
              <button key={cat} onClick={() => setFilter(cat)} style={{
                padding:'3px 10px', borderRadius:20, fontSize:11, fontWeight:500,
                background: filter===cat ? (CATEGORY_COLORS[cat]||'var(--blue)') : 'var(--bg2)',
                color: filter===cat ? '#fff' : 'var(--text3)',
                border:'none', cursor:'pointer'
              }}>{cat}</button>
            ))}
          </div>
        </div>

        {loading && <Spinner2 label="Loading analytics catalogue…" />}
        {error && <div style={{ padding:16 }}><ErrorBox message={error} /></div>}

        <div style={{ flex:1, overflowY:'auto', padding:'0 10px 16px' }}>
          {visible.map(item => (
            <AnalyticsCard key={item.id} item={item} isSelected={selected?.id===item.id} onRun={() => handleRefresh(item)} disabled={running || !!syncPhase} />
          ))}
        </div>
      </div>

      {/* Right — result */}
      <div style={{ flex:1, overflowY:'auto', padding:20, display:'flex', flexDirection:'column', gap:14 }}>
        {!selected && (
          <div style={{ display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', height:'100%', color:'var(--text3)', textAlign:'center' }}>
            <div style={{ fontSize:48, marginBottom:16, opacity:.2 }}>⬡</div>
            <div style={{ fontSize:15, fontWeight:600, color:'var(--text2)', marginBottom:8 }}>Pick an analysis</div>
            <div style={{ fontSize:13, maxWidth:320 }}>Click any card on the left. Results load from the pre-generated cache — no LLM tokens used.</div>
          </div>
        )}

        {selected && (
          <>
            <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between' }}>
              <div>
                <div style={{ fontSize:17, fontWeight:700, marginBottom:2 }}>{selected.title}</div>
                <div style={{ fontSize:12, color:'var(--text3)' }}>{selected.description}</div>
              </div>
              <div style={{ display:'flex', flexDirection:'column', alignItems:'flex-end', gap:5 }}>
                <button onClick={() => handleRefresh(selected)} disabled={running || !!syncPhase || !!rateLimitUntil} style={{
                  padding:'8px 16px', borderRadius:'var(--r-md)', fontSize:13, fontWeight:500,
                  background:'var(--blue)', color:'#fff', opacity:(running || syncPhase || rateLimitUntil) ? 0.6 : 1,
                  cursor:(running || syncPhase || rateLimitUntil) ?'not-allowed':'pointer', display:'flex', alignItems:'center', gap:7, border:'none'
                }}>
                  {syncPhase === 'syncing'  ? <><Spinner size={13} color="#fff"/>Syncing data…</>
                  : syncPhase === 'querying' ? <><Spinner size={13} color="#fff"/>Refreshing…</>
                  : running                  ? <><Spinner size={13} color="#fff"/>Loading…</>
                  : rateLimitUntil          ? `Cooling down… ${rlSecsLeft}s`
                  : '↻ Refresh'}
                </button>
                {(lastSyncAt || selected?.last_sync_at) && !syncPhase && (
                  <div style={{ fontSize:10, color:'var(--text3)' }}>
                    Last sync: {new Date(lastSyncAt || selected.last_sync_at).toLocaleString()}
                  </div>
                )}
              </div>
            </div>

            {syncPhase === 'syncing' && (
              <div style={{ padding:'12px 14px', borderRadius:'var(--r-md)', background:'rgba(99,102,241,0.08)', border:'1px solid rgba(99,102,241,0.2)', fontSize:12, color:'var(--text2)', display:'flex', alignItems:'center', gap:10 }}>
                <Spinner size={14} color="var(--blue)" />
                <div>
                  <div style={{ fontWeight:600, color:'var(--text)', marginBottom:2 }}>Syncing latest data from your integration…</div>
                  <div style={{ color:'var(--text3)' }}>This may take a moment. The report will load automatically once sync is complete.</div>
                </div>
              </div>
            )}

            {syncPhase === 'querying' && (
              <div style={{ display:'flex', flexDirection:'column', gap:10 }}>
                {[160,40,220].map((h,i) => <div key={i} className="skeleton" style={{ height:h }} />)}
              </div>
            )}

            {rateLimitUntil && !syncPhase && (
              <div style={{ padding:'10px 12px', borderRadius:'var(--r-md)', background:'rgba(251,146,60,0.1)', border:'1px solid rgba(251,146,60,0.25)', fontSize:12, color:'var(--text2)', lineHeight:1.5 }}>
                Refresh complete — please wait before syncing again.{' '}
                <span style={{ fontWeight:600, color:'#f97316' }}>Available in {rlSecsLeft}s</span>
              </div>
            )}

            {runError && <ErrorBox message={runError} />}

            {running && !syncPhase && <div style={{ display:'flex', flexDirection:'column', gap:10 }}>
              {[160,40,220].map((h,i) => <div key={i} className="skeleton" style={{ height:h }} />)}
            </div>}

            {result && !running && <ResultPanel result={result} templateId={selected.id} />}
          </>
        )}
      </div>
    </div>
  )
}
