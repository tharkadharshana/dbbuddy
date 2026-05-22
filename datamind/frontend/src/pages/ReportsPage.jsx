import React, { useState } from 'react'
import { BarChart, Bar, LineChart, Line, PieChart, Pie, Cell, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { Card, Btn, UsageMeter, Spinner, ErrorBox, Badge, COLORS, AIQuotaWall } from '../components/UI'
import { generateReport } from '../utils/api'

const TT = { background:'#1c1e2e', border:'1px solid rgba(255,255,255,0.08)', borderRadius:8, fontSize:12, color:'#f0f1fa' }

// ── Markdown → JSX renderer ───────────────────────────────────────────────────
function renderInline(text) {
  const parts = []
  let remaining = text, key = 0
  while (remaining.length) {
    const boldIdx = remaining.indexOf('**')
    if (boldIdx === -1) { parts.push(<span key={key++}>{remaining}</span>); break }
    if (boldIdx > 0) parts.push(<span key={key++}>{remaining.slice(0, boldIdx)}</span>)
    const endIdx = remaining.indexOf('**', boldIdx + 2)
    if (endIdx === -1) { parts.push(<span key={key++}>{remaining.slice(boldIdx)}</span>); break }
    parts.push(<strong key={key++} style={{ color:'var(--text)', fontWeight:700 }}>{remaining.slice(boldIdx + 2, endIdx)}</strong>)
    remaining = remaining.slice(endIdx + 2)
  }
  return parts
}

function NarrativeRenderer({ text }) {
  if (!text) return null
  const blocks = text.split(/\n\n+/)
  return (
    <div style={{ fontSize:14, color:'var(--text2)', lineHeight:1.9 }}>
      {blocks.map((block, i) => {
        const trimmed = block.trim()
        if (!trimmed) return null
        // Bold-only line → section heading
        if (/^\*\*[^*]+\*\*$/.test(trimmed)) {
          return <h3 key={i} style={{ fontSize:15, fontWeight:700, color:'var(--text)', marginTop: i===0?0:22, marginBottom:8 }}>{trimmed.slice(2,-2)}</h3>
        }
        // Numbered or bulleted list items
        if (/^(\d+\.|[-•])\s/.test(trimmed)) {
          const lines = trimmed.split('\n').filter(Boolean)
          return (
            <ul key={i} style={{ margin:'8px 0', paddingLeft:20, listStyleType:'disc' }}>
              {lines.map((line, j) => (
                <li key={j} style={{ marginBottom:6 }}>{renderInline(line.replace(/^(\d+\.|[-•])\s+/, ''))}</li>
              ))}
            </ul>
          )
        }
        return <p key={i} style={{ margin:'0 0 12px' }}>{renderInline(trimmed)}</p>
      })}
    </div>
  )
}

const ALL_SECTIONS = [
  { id:'revenue_trend',       label:'Revenue Trend',         category:'Revenue' },
  { id:'revenue_by_category', label:'Category Breakdown',    category:'Revenue' },
  { id:'revenue_by_location', label:'Location Performance',  category:'Revenue' },
  { id:'growth_metrics',      label:'MoM Growth',            category:'Revenue' },
  { id:'discount_analysis',   label:'Discount Analysis',     category:'Revenue' },
  { id:'top_products',        label:'Top Products',          category:'Products' },
  { id:'margin_by_category',  label:'Margin by Category',    category:'Products' },
  { id:'slow_products',       label:'Slow Movers',           category:'Products' },
  { id:'top_customers',       label:'Top Customers',         category:'Customers' },
  { id:'customer_rfm',        label:'RFM Segmentation',      category:'Customers' },
  { id:'customer_retention',  label:'Retention Rate',        category:'Customers' },
  { id:'loyalty_tiers',       label:'Loyalty Tiers',         category:'Customers' },
  { id:'cashier_performance', label:'Staff Performance',     category:'People' },
  { id:'payment_methods',     label:'Payment Methods',       category:'Finance' },
  { id:'credit_outstanding',  label:'Credit & Collections',  category:'Finance' },
]

const PRESETS = [
  { label:'Executive Summary', sections:['revenue_trend','growth_metrics','top_products','top_customers','cashier_performance'] },
  { label:'Revenue Deep Dive', sections:['revenue_trend','revenue_by_category','revenue_by_location','discount_analysis','growth_metrics'] },
  { label:'Customer Report',   sections:['top_customers','customer_rfm','customer_retention','loyalty_tiers'] },
  { label:'Operations Report', sections:['cashier_performance','payment_methods','credit_outstanding','slow_products'] },
]

// ── Mini chart embedded in report ────────────────────────────────────────────
function MiniChart({ data, columns, title }) {
  if (!data?.length) return null
  const numCols = columns.filter(c => typeof data[0]?.[c] === 'number')
  const strCols = columns.filter(c => typeof data[0]?.[c] === 'string')
  if (!numCols.length || !strCols.length) return null
  const xKey = strCols[0], yKey = numCols[0]
  const chartData = data.slice(0, 12).map(r => ({ name: String(r[xKey] ?? '').slice(0, 14), value: r[yKey] ?? 0 }))

  if (columns.includes('payment_method') || title?.includes('Loyalty') || title?.includes('Tier')) {
    return (
      <div style={{ marginTop:12 }}>
        <ResponsiveContainer width="100%" height={160}>
          <PieChart><Pie data={chartData} dataKey="value" nameKey="name" outerRadius={65} fontSize={10} label={({name,percent})=>`${name} ${(percent*100).toFixed(0)}%`} labelLine={false}>
            {chartData.map((_,i) => <Cell key={i} fill={COLORS[i%COLORS.length]} />)}
          </Pie><Tooltip contentStyle={TT} /></PieChart>
        </ResponsiveContainer>
      </div>
    )
  }
  return (
    <div style={{ marginTop:12 }}>
      <ResponsiveContainer width="100%" height={160}>
        {data.length > 10
          ? <LineChart data={chartData}><CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" /><XAxis dataKey="name" tick={{fontSize:9,fill:'#5a5f7d'}} axisLine={false} tickLine={false} /><YAxis tick={{fontSize:9,fill:'#5a5f7d'}} axisLine={false} tickLine={false} /><Tooltip contentStyle={TT} /><Line dataKey="value" stroke="var(--blue)" strokeWidth={2} dot={false} /></LineChart>
          : <BarChart data={chartData} barSize={18}><CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" /><XAxis dataKey="name" tick={{fontSize:9,fill:'#5a5f7d'}} axisLine={false} tickLine={false} /><YAxis tick={{fontSize:9,fill:'#5a5f7d'}} axisLine={false} tickLine={false} /><Tooltip contentStyle={TT} /><Bar dataKey="value" fill="var(--blue)" radius={[3,3,0,0]} /></BarChart>
        }
      </ResponsiveContainer>
    </div>
  )
}

// ── Rendered Report ───────────────────────────────────────────────────────────
function RenderedReport({ report }) {
  const { title, kpis, sections, narrative } = report
  const reportRef = React.useRef(null)
  const kpiKeys = ['total_revenue','total_invoices','avg_ticket','unique_customers']
  const kpiLabels = { total_revenue:'Total Revenue', total_invoices:'Total Invoices', avg_ticket:'Avg Ticket', unique_customers:'Unique Customers' }
  const kpiColors = ['var(--blue)','var(--green)','var(--purple)','var(--amber)']

  function copyReport() {
    const text = `${title}\n\n${Object.entries(kpis||{}).map(([k,v])=>`${k}: ${v}`).join('\n')}\n\n${narrative}`
    navigator.clipboard.writeText(text)
  }

  function downloadPDF() {
    const node = reportRef.current
    if (!node) return
    const win = window.open('', '_blank')
    const styles = Array.from(document.styleSheets).map(ss => {
      try { return Array.from(ss.cssRules).map(r => r.cssText).join('\n') } catch { return '' }
    }).join('\n')
    win.document.write(`<!DOCTYPE html><html><head>
      <meta charset="utf-8"><title>${title}</title>
      <style>
        ${styles}
        body { background:#fff !important; color:#111 !important; font-family:system-ui,sans-serif; margin:32px; }
        * { print-color-adjust:exact; -webkit-print-color-adjust:exact; }
        @media print { body { margin:16px; } }
      </style>
    </head><body>${node.innerHTML}</body></html>`)
    win.document.close()
    win.focus()
    setTimeout(() => { win.print(); win.close() }, 400)
  }

  return (
    <div ref={reportRef} style={{ background:'var(--bg1)', border:'1px solid var(--border)', borderRadius:'var(--r-xl)', overflow:'hidden' }}>
      {/* Report header */}
      <div style={{ padding:'24px 28px', borderBottom:'1px solid var(--border)', background:'linear-gradient(135deg, rgba(79,142,247,0.08), rgba(167,139,250,0.06))' }}>
        <div style={{ display:'flex', alignItems:'flex-start', justifyContent:'space-between' }}>
          <div>
            <div style={{ fontSize:11, color:'var(--text3)', textTransform:'uppercase', letterSpacing:'.1em', fontWeight:600, marginBottom:6 }}>Analytics Report</div>
            <div style={{ fontSize:22, fontWeight:700, color:'var(--text)', marginBottom:4 }}>{title}</div>
            <div style={{ fontSize:12, color:'var(--text3)' }}>
              {kpis?.from_date && `Period: ${kpis.from_date} → ${kpis.to_date}`}
            </div>
          </div>
          <div style={{ display:'flex', gap:8 }}>
            <Btn size="sm" variant="ghost" onClick={copyReport}>Copy Text</Btn>
            <Btn size="sm" variant="ghost" onClick={downloadPDF}>Download PDF</Btn>
          </div>
        </div>

        {/* KPI row */}
        <div style={{ display:'grid', gridTemplateColumns:'repeat(4,1fr)', gap:12, marginTop:20 }}>
          {kpiKeys.map((k,i) => kpis?.[k] != null && (
            <div key={k} style={{ background:'rgba(255,255,255,0.04)', borderRadius:'var(--r-md)', padding:'12px 14px', border:'1px solid rgba(255,255,255,0.06)' }}>
              <div style={{ fontSize:10, color:'var(--text3)', textTransform:'uppercase', letterSpacing:'.07em', marginBottom:6 }}>{kpiLabels[k]}</div>
              <div style={{ fontSize:20, fontWeight:700, color:kpiColors[i] }}>
                {k.includes('revenue')||k.includes('ticket') ? `$${Number(kpis[k]).toLocaleString()}` : Number(kpis[k]).toLocaleString()}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Narrative */}
      <div style={{ padding:'24px 28px', borderBottom:'1px solid var(--border)' }}>
        <div style={{ fontSize:12, color:'var(--text3)', fontWeight:600, textTransform:'uppercase', letterSpacing:'.08em', marginBottom:14 }}>Executive Analysis</div>
        <NarrativeRenderer text={narrative} />
      </div>

      {/* Data sections with mini charts */}
      <div style={{ padding:'20px 28px 28px' }}>
        <div style={{ fontSize:12, color:'var(--text3)', fontWeight:600, textTransform:'uppercase', letterSpacing:'.08em', marginBottom:16 }}>Data Sections</div>
        <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:14 }}>
          {Object.entries(sections||{}).map(([sid, data]) => (
            <div key={sid} style={{ background:'var(--bg2)', borderRadius:'var(--r-lg)', padding:'16px', border:'1px solid var(--border)' }}>
              <div style={{ fontSize:13, fontWeight:600, color:'var(--text)', marginBottom:2 }}>{data.title}</div>
              <div style={{ fontSize:11, color:'var(--text3)', marginBottom:8 }}>{data.row_count} rows</div>
              <MiniChart data={data.data} columns={data.columns} title={data.title} />
              {/* Top 3 rows preview */}
              {data.data?.slice(0,3).map((row,i) => (
                <div key={i} style={{ display:'flex', gap:8, fontSize:11, color:'var(--text3)', marginTop:4, flexWrap:'wrap' }}>
                  {data.columns.slice(0,3).map(c => (
                    <span key={c}><span style={{ color:'var(--text3)' }}>{c}: </span><span style={{ color: typeof row[c]==='number' ? 'var(--blue)' : 'var(--text2)', fontFamily: typeof row[c]==='number' ? 'var(--mono)' : 'inherit' }}>{row[c]}</span></span>
                  ))}
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}


// ── Main Page ─────────────────────────────────────────────────────────────────
export default function ReportsPage({ llm, setLlm, sub, onNavigate, onQueryComplete }) {
  const [title, setTitle]         = useState('')
  const [selected, setSelected]   = useState([])
  const [format, setFormat]       = useState('full')
  const [loading, setLoading]     = useState(false)
  const [error, setError]         = useState(null)
  const [reports, setReports]     = useState([])
  const [activeReport, setActiveReport] = useState(null)

  function applyPreset(p) {
    setTitle(p.label)
    setSelected(p.sections)
  }

  function toggleSection(id) {
    setSelected(s => s.includes(id) ? s.filter(x=>x!==id) : [...s, id])
  }

  async function handleGenerate() {
    if (!title.trim() || !selected.length) return
    setLoading(true); setError(null)
    try {
      const data = await generateReport(title, selected, llm, format)
      const report = { ...data, id: Date.now(), ts: new Date().toLocaleString() }
      setReports(r => [report, ...r])
      setActiveReport(report)
    } catch(e) { setError(e.response?.data?.detail || e.message) }
    finally { setLoading(false); onQueryComplete?.() }
  }

  const categories = [...new Set(ALL_SECTIONS.map(s => s.category))]

  if (sub && !sub.can_use_ai) return <AIQuotaWall sub={sub} onNavigate={onNavigate} />

  return (
    <div style={{ display:'flex', height:'100%', overflow:'hidden' }}>
      {/* Left config panel */}
      <div style={{ width:320, flexShrink:0, borderRight:'1px solid var(--border)', display:'flex', flexDirection:'column', overflow:'hidden' }}>
        <div style={{ flex:1, overflowY:'auto', padding:16 }}>
          <div style={{ fontSize:15, fontWeight:700, marginBottom:4 }}>Report Builder</div>
          <div style={{ fontSize:12, color:'var(--text3)', marginBottom:16 }}>Select sections and the AI generates a professional report with charts and narrative.</div>

          {/* LLM + Format */}
          <div style={{ marginBottom:14 }}>
            <div style={{ fontSize:11, color:'var(--text3)', marginBottom:6, fontWeight:500, textTransform:'uppercase', letterSpacing:'.07em' }}>LLM</div>
            <UsageMeter sub={sub} />
          </div>
          <div style={{ marginBottom:16 }}>
            <div style={{ fontSize:11, color:'var(--text3)', marginBottom:6, fontWeight:500, textTransform:'uppercase', letterSpacing:'.07em' }}>Report Style</div>
            <div style={{ display:'flex', gap:4 }}>
              {[['full','Detailed'],['executive','Executive'],['quick','Quick']].map(([v,l]) => (
                <button key={v} onClick={()=>setFormat(v)} style={{ flex:1, padding:'6px 0', borderRadius:'var(--r-sm)', fontSize:11, fontWeight:500, background:format===v?'var(--blue)':'var(--bg3)', color:format===v?'#fff':'var(--text3)', border:'none', cursor:'pointer' }}>{l}</button>
              ))}
            </div>
          </div>

          {/* Presets */}
          <div style={{ fontSize:11, color:'var(--text3)', marginBottom:6, fontWeight:500, textTransform:'uppercase', letterSpacing:'.07em' }}>Quick Presets</div>
          <div style={{ display:'flex', gap:5, flexWrap:'wrap', marginBottom:16 }}>
            {PRESETS.map(p => (
              <button key={p.label} onClick={()=>applyPreset(p)} style={{ padding:'4px 12px', borderRadius:20, fontSize:11, background:'var(--bg3)', color:'var(--text2)', border:'1px solid var(--border)', cursor:'pointer' }}>{p.label}</button>
            ))}
          </div>

          {/* Report Title */}
          <div style={{ marginBottom:14 }}>
            <div style={{ fontSize:11, color:'var(--text3)', marginBottom:6, fontWeight:500, textTransform:'uppercase', letterSpacing:'.07em' }}>Report Title</div>
            <input value={title} onChange={e=>setTitle(e.target.value)} placeholder="e.g. Q2 2024 Business Report" style={{ width:'100%', padding:'9px 12px', borderRadius:'var(--r-md)', fontSize:13 }} />
          </div>

          {/* Section picker */}
          <div style={{ fontSize:11, color:'var(--text3)', marginBottom:8, fontWeight:500, textTransform:'uppercase', letterSpacing:'.07em' }}>Include Sections ({selected.length})</div>
          {categories.map(cat => (
            <div key={cat} style={{ marginBottom:12 }}>
              <div style={{ fontSize:10, color:'var(--text3)', fontWeight:600, marginBottom:5, textTransform:'uppercase', letterSpacing:'.07em' }}>{cat}</div>
              <div style={{ display:'flex', flexDirection:'column', gap:3 }}>
                {ALL_SECTIONS.filter(s=>s.category===cat).map(s => (
                  <div key={s.id} onClick={()=>toggleSection(s.id)} style={{
                    display:'flex', alignItems:'center', gap:8, padding:'7px 10px', borderRadius:'var(--r-sm)',
                    background: selected.includes(s.id) ? 'var(--blue-dim)' : 'var(--bg2)',
                    border:`1px solid ${selected.includes(s.id) ? 'rgba(79,142,247,0.3)' : 'var(--border)'}`,
                    cursor:'pointer', fontSize:12,
                    color: selected.includes(s.id) ? 'var(--blue)' : 'var(--text2)'
                  }}>
                    <div style={{ width:14, height:14, borderRadius:4, border:`1.5px solid ${selected.includes(s.id)?'var(--blue)':'var(--border2)'}`, background:selected.includes(s.id)?'var(--blue)':'transparent', display:'flex', alignItems:'center', justifyContent:'center', flexShrink:0, fontSize:9, color:'#fff' }}>
                      {selected.includes(s.id) && '✓'}
                    </div>
                    {s.label}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>

        {/* Generate button */}
        <div style={{ padding:14, borderTop:'1px solid var(--border)' }}>
          {error && <div style={{ marginBottom:10 }}><ErrorBox message={error} /></div>}
          <Btn onClick={handleGenerate} disabled={loading||!title.trim()||!selected.length} style={{ width:'100%', justifyContent:'center' }}>
            {loading ? <><Spinner size={13} color="#fff" /> Generating Report…</> : `Generate Report (${selected.length} sections) ↗`}
          </Btn>
        </div>
      </div>

      {/* Right — report view */}
      <div style={{ flex:1, overflowY:'auto', padding:20 }}>
        {/* Report history tabs */}
        {reports.length > 0 && (
          <div style={{ display:'flex', gap:6, flexWrap:'wrap', marginBottom:16 }}>
            {reports.map(r => (
              <button key={r.id} onClick={()=>setActiveReport(r)} style={{
                padding:'4px 12px', borderRadius:20, fontSize:11, cursor:'pointer', fontWeight:500,
                background: activeReport?.id===r.id ? 'var(--blue)' : 'var(--bg2)',
                color: activeReport?.id===r.id ? '#fff' : 'var(--text3)',
                border:`1px solid ${activeReport?.id===r.id ? 'var(--blue)' : 'var(--border)'}`,
              }}>{r.title} <span style={{ opacity:.6, fontWeight:400 }}>· {r.ts}</span></button>
            ))}
          </div>
        )}

        {loading && (
          <Card style={{ padding:'36px 24px', textAlign:'center' }}>
            <Spinner size={28} />
            <div style={{ fontSize:14, color:'var(--text2)', marginTop:14, fontWeight:500 }}>Generating your report…</div>
            <div style={{ fontSize:12, color:'var(--text3)', marginTop:6 }}>Fetching {selected.length} data sections and writing AI narrative</div>
          </Card>
        )}

        {!activeReport && !loading && (
          <div style={{ display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', height:'80%', color:'var(--text3)', textAlign:'center' }}>
            <div style={{ fontSize:48, marginBottom:16, opacity:.2 }}>📋</div>
            <div style={{ fontSize:15, fontWeight:600, color:'var(--text2)', marginBottom:8 }}>No report yet</div>
            <div style={{ fontSize:13, maxWidth:340 }}>Pick a preset or select sections on the left, give your report a title, and click Generate.</div>
          </div>
        )}

        {activeReport && !loading && <RenderedReport report={activeReport} />}
      </div>
    </div>
  )
}
