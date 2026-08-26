import React from 'react'
import { ResponsiveContainer, BarChart, Bar, LineChart, Line, PieChart, Pie, Cell, XAxis, YAxis, CartesianGrid, Tooltip, Legend } from 'recharts'
import { formatCurrency, isCurrencyColumn, formatNumber } from '../utils/locale'
import { fmtTok as _fmtTok } from '../formatTokens'

export const TT_STYLE = { background:'#1c1e2e', border:'1px solid rgba(255,255,255,0.1)', borderRadius:8, fontSize:12, color:'#f0f1fa', boxShadow:'0 8px 24px rgba(0,0,0,0.4)' }
export const COLORS = ['#4f8ef7','#34d17a','#a78bfa','#f5a623','#f05050','#2dd4bf','#f472b6','#facc15','#60a5fa','#fb923c']

// ── Primitives ───────────────────────────────────────────────────────────────

export function Btn({ children, onClick, variant='primary', size='md', disabled, style={} }) {
  const base = { display:'inline-flex', alignItems:'center', gap:6, fontWeight:500, borderRadius:'var(--r-md)', opacity: disabled ? 0.4 : 1, cursor: disabled ? 'not-allowed' : 'pointer' }
  const sizes = { sm:{padding:'5px 12px',fontSize:12}, md:{padding:'9px 18px',fontSize:13}, lg:{padding:'12px 24px',fontSize:14} }
  const variants = {
    primary: { background:'var(--blue)', color:'#fff' },
    ghost:   { background:'var(--bg3)', color:'var(--text2)', border:'1px solid var(--border)' },
    danger:  { background:'var(--red-dim)', color:'var(--red)', border:'1px solid rgba(240,80,80,0.2)' },
    success: { background:'var(--green-dim)', color:'var(--green)', border:'1px solid rgba(52,209,122,0.2)' },
  }
  return <button onClick={!disabled ? onClick : undefined} style={{...base,...sizes[size],...variants[variant],...style}}>{children}</button>
}

export function Card({ children, style={}, onClick }) {
  return <div onClick={onClick} style={{ background:'var(--bg1)', border:'1px solid var(--border)', borderRadius:'var(--r-lg)', ...style }}>{children}</div>
}

export function Badge({ children, color='gray', style={} }) {
  const colors = {
    blue:   { background:'var(--blue-dim)',   color:'var(--blue)' },
    green:  { background:'var(--green-dim)',  color:'var(--green)' },
    amber:  { background:'var(--amber-dim)',  color:'var(--amber)' },
    red:    { background:'var(--red-dim)',    color:'var(--red)' },
    purple: { background:'var(--purple-dim)', color:'var(--purple)' },
    teal:   { background:'var(--teal-dim)',   color:'var(--teal)' },
    pink:   { background:'var(--pink-dim)',   color:'var(--pink)' },
    gray:   { background:'var(--bg3)',        color:'var(--text2)' },
  }
  return <span style={{ display:'inline-flex', alignItems:'center', padding:'2px 10px', borderRadius:20, fontSize:11, fontWeight:500, ...colors[color]||colors.gray, ...style }}>{children}</span>
}

export function Spinner({ size=18, color='var(--blue)' }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" style={{animation:'spin .8s linear infinite'}}><circle cx="12" cy="12" r="9" stroke={color} strokeWidth="2.5" strokeDasharray="40 20" strokeLinecap="round"/></svg>
}


export function UsageMeter({ sub }) {
  if (!sub || sub.status === 'no_subscription') return null
  const used  = sub.tokens_used  || 0
  const total = sub.tokens_total_available || sub.tokens_limit || 1
  const pct   = Math.min(100, Math.round((used / total) * 100))
  const color = pct >= 100 ? 'var(--red)' : pct >= 80 ? 'var(--amber)' : 'var(--blue)'
  return (
    <div style={{ display:'flex', flexDirection:'column', gap:4, padding:'7px 12px', borderRadius:'var(--r-md)', border:'1px solid var(--border)', background:'var(--bg2)', minWidth:160 }}>
      <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', gap:6 }}>
        <span style={{ fontSize:12, fontWeight:600, color:'var(--text2)' }}>⚡ Tokens</span>
        <span style={{ fontSize:10, color, fontFamily:'var(--mono)' }}>{pct}%</span>
      </div>
      <div style={{ height:3, borderRadius:99, background:'var(--bg3)', overflow:'hidden' }}>
        <div style={{ height:'100%', width:`${pct}%`, borderRadius:99, background:color, transition:'width .3s' }} />
      </div>
      <div style={{ fontSize:9, color:'var(--text3)', fontFamily:'var(--mono)' }}>{_fmtTok(used)} / {_fmtTok(total)}</div>
    </div>
  )
}

export function Spinner2({ label='Loading…' }) {
  return (
    <div style={{ display:'flex', flexDirection:'column', alignItems:'center', gap:12, padding:'48px 0', color:'var(--text3)' }}>
      <Spinner size={28} />
      <span style={{ fontSize:13 }}>{label}</span>
    </div>
  )
}

export function Empty({ icon='⌕', title, subtitle }) {
  return (
    <div style={{ display:'flex', flexDirection:'column', alignItems:'center', gap:8, padding:'56px 24px', color:'var(--text3)', textAlign:'center' }}>
      <div style={{ fontSize:36, marginBottom:4 }}>{icon}</div>
      <div style={{ fontSize:14, fontWeight:500, color:'var(--text2)' }}>{title}</div>
      {subtitle && <div style={{ fontSize:12, maxWidth:360 }}>{subtitle}</div>}
    </div>
  )
}

export function ErrorBox({ message }) {
  return (
    <div style={{ background:'var(--red-dim)', border:'1px solid rgba(240,80,80,0.2)', borderRadius:'var(--r-md)', padding:'12px 16px', fontSize:13, color:'var(--red)', display:'flex', gap:8 }}>
      ⚠ {message}
    </div>
  )
}

export function KPICard({ label, value, sub, color='var(--text)', icon }) {
  return (
    <Card style={{ padding:'16px 18px' }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start' }}>
        <div>
          <div style={{ fontSize:11, color:'var(--text3)', textTransform:'uppercase', letterSpacing:'.07em', marginBottom:8, fontWeight:500 }}>{label}</div>
          <div style={{ fontSize:24, fontWeight:700, color, lineHeight:1.1 }}>{value}</div>
          {sub && <div style={{ fontSize:11, color:'var(--text3)', marginTop:6 }}>{sub}</div>}
        </div>
        {icon && <div style={{ fontSize:20, opacity:.7 }}>{icon}</div>}
      </div>
    </Card>
  )
}

export function SectionTitle({ children, right }) {
  return (
    <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:14 }}>
      <h3 style={{ fontSize:11, fontWeight:600, color:'var(--text3)', textTransform:'uppercase', letterSpacing:'.09em' }}>{children}</h3>
      {right}
    </div>
  )
}

// ── Chart Wrappers ────────────────────────────────────────────────────────────

export function ChartCard({ title, children, style={} }) {
  return (
    <Card style={{ padding:18, ...style }}>
      {title && <div style={{ fontSize:12, color:'var(--text3)', marginBottom:14, fontWeight:500 }}>{title}</div>}
      {children}
    </Card>
  )
}

export function BarChartSimple({ data, xKey, yKey, color='var(--blue)', height=220, horizontal=false }) {
  if (!data?.length) return <Empty icon="📊" title="No data" />
  if (horizontal) {
    return (
      <ResponsiveContainer width="100%" height={Math.max(height, data.length * 32)}>
        <BarChart data={data} layout="vertical" barSize={16} margin={{left:10,right:20}}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" horizontal={false} />
          <XAxis type="number" tick={{fontSize:10,fill:'#5a5f7d'}} axisLine={false} tickLine={false} />
          <YAxis dataKey={xKey} type="category" width={120} tick={{fontSize:10,fill:'#9499b8'}} axisLine={false} tickLine={false} />
          <Tooltip contentStyle={TT_STYLE} />
          <Bar dataKey={yKey} fill={color} radius={[0,4,4,0]} />
        </BarChart>
      </ResponsiveContainer>
    )
  }
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} barSize={22}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
        <XAxis dataKey={xKey} tick={{fontSize:10,fill:'#5a5f7d'}} axisLine={false} tickLine={false} />
        <YAxis tick={{fontSize:11,fill:'#5a5f7d'}} axisLine={false} tickLine={false} />
        <Tooltip contentStyle={TT_STYLE} />
        <Bar dataKey={yKey} fill={color} radius={[4,4,0,0]} />
      </BarChart>
    </ResponsiveContainer>
  )
}

export function LineChartSimple({ data, xKey, yKeys, colors, height=220 }) {
  if (!data?.length) return <Empty icon="📈" title="No data" />
  const _colors = colors || COLORS
  const _yKeys = Array.isArray(yKeys) ? yKeys : [yKeys]
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
        <XAxis dataKey={xKey} tick={{fontSize:10,fill:'#5a5f7d'}} axisLine={false} tickLine={false} />
        <YAxis tick={{fontSize:11,fill:'#5a5f7d'}} axisLine={false} tickLine={false} />
        <Tooltip contentStyle={TT_STYLE} />
        {_yKeys.length > 1 && <Legend wrapperStyle={{fontSize:11,color:'#9499b8'}} />}
        {_yKeys.map((k,i) => <Line key={k} dataKey={k} stroke={_colors[i%_colors.length]} strokeWidth={2} dot={false} />)}
      </LineChart>
    </ResponsiveContainer>
  )
}

export function PieChartSimple({ data, nameKey, valueKey, height=220 }) {
  if (!data?.length) return <Empty icon="🥧" title="No data" />
  return (
    <ResponsiveContainer width="100%" height={height}>
      <PieChart>
        <Pie data={data} dataKey={valueKey} nameKey={nameKey} cx="50%" cy="50%" outerRadius={80} label={({name,percent})=>`${name} ${(percent*100).toFixed(0)}%`} labelLine={false} fontSize={10}>
          {data.map((_,i) => <Cell key={i} fill={COLORS[i%COLORS.length]} />)}
        </Pie>
        <Tooltip contentStyle={TT_STYLE} />
      </PieChart>
    </ResponsiveContainer>
  )
}

// ── AI Quota Wall ─────────────────────────────────────────────────────────────

const SUPPORT_EMAIL = 'support@datamind.ai'

// BILLING HIDDEN — AIQuotaWall commented out
// export function AIQuotaWall({ sub }) {
//   const isNoSub     = !sub || sub.status === 'no_subscription'
//   const isExpired   = sub?.status === 'expired' || sub?.status === 'cancelled'
//   const isExhausted = sub?.tokens_pct >= 100
//   if (!isNoSub && !isExpired && !isExhausted) return null
//   let title, body
//   if (isNoSub) {
//     title = 'No active plan'
//     body  = "We're in beta — email us and we'll get you set up with access."
//   } else if (isExpired) {
//     title = 'Your trial has ended'
//     body  = "We're in beta — email us if you'd like continued access to AI features."
//   } else {
//     const resetDate = sub?.period_end
//       ? new Date(sub.period_end).toLocaleDateString('en-US', { month: 'long', day: 'numeric' })
//       : 'next month'
//     title = 'AI credits exhausted'
//     body  = `You've used all ${sub.ai_total_available} AI credits for this billing period. Your quota resets on ${resetDate}.`
//   }
//   return (
//     <div style={{ display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', flex:1, padding:40, textAlign:'center' }}>
//       <div style={{ fontSize:48, marginBottom:16 }}>🔒</div>
//       <div style={{ fontSize:18, fontWeight:700, color:'var(--text)', marginBottom:10 }}>{title}</div>
//       <div style={{ fontSize:13, color:'var(--text2)', lineHeight:1.7, maxWidth:400, marginBottom:24 }}>{body}</div>
//       <a href={`mailto:${SUPPORT_EMAIL}`} style={{ padding:'10px 24px', borderRadius:8, border:'none', background:'var(--blue)', color:'#fff', fontWeight:600, fontSize:14, textDecoration:'none', display:'inline-block' }}>Contact Us</a>
//     </div>
//   )
// }
export function AIQuotaWall() { return null }

// ── Data Table ────────────────────────────────────────────────────────────────

export function DataTable({ columns, data, maxHeight=320 }) {
  if (!data?.length) return <Empty icon="📋" title="No records returned" />
  const isNum = v => typeof v === 'number'
  const isPos = (col, v) => isNum(v) && (col.includes('growth') || col.includes('pct')) && v > 0
  const isNeg = (col, v) => isNum(v) && (col.includes('growth') || col.includes('pct')) && v < 0
  const numCols = new Set(columns.filter(c => isNum(data[0]?.[c])))
  const fmt = (col, v) => {
    if (v === null || v === undefined) return <span style={{color:'var(--text3)'}}>—</span>
    if (isNum(v)) {
      if (col.includes('pct') || col.includes('rate') || col.includes('growth')) return `${v > 0 ? '+' : ''}${v}%`
      // Currency symbol hidden in Analytics Hub reports — single-currency tenants don't need it
      // if (isCurrencyColumn(col))
      //   return formatCurrency(v)
      if (isCurrencyColumn(col))
        return formatNumber(v, null, 2)
      return formatNumber(v, null, 0)
    }
    return String(v)
  }
  return (
    <div style={{ overflowX:'auto', overflowY:'auto', maxHeight }}>
      <table className="data-table">
        <thead><tr>{columns.map(c => <th key={c} style={numCols.has(c) ? {textAlign:'right'} : {}}>{c.replace(/_/g,' ')}</th>)}</tr></thead>
        <tbody>
          {data.map((row,i) => (
            <tr key={i}>
              {columns.map(c => {
                const v = row[c]
                const cls = isPos(c,v) ? 'pos' : isNeg(c,v) ? 'neg' : isNum(v) ? 'num' : ''
                return <td key={c} className={cls}>{fmt(c,v)}</td>
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
