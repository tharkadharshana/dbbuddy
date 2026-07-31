import React from 'react'
import { ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid, Tooltip, PieChart, Pie, Cell } from 'recharts'

// Shared result chart for the chat surfaces (main app + embed). One copy so the
// two can't diverge again. Round 2 Issue A fixes:
//   - plot every row (cap high, not 15) so a 30-day query shows all 30 bars
//   - explicit width per category inside an overflowX wrapper → horizontal scroll
//   - interval={0} + angle={-45} → every x-axis label shown, rotated
//   - full (untruncated) label in the tooltip
const _CODE_COLS = /^(sku|code|customer_code|shop_id|product_code|item_code)$/i
const PER_CAT = 40                 // px of horizontal space per category
const MAX_CATS = 366               // a year of daily points — effectively "all"
const TT = { background: '#1c1e2e', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 8, fontSize: 12, color: '#f0f1fa' }

// `kind` is optional: omitted (the auto-derived table chart) keeps the original
// bar+line composed shape. The agent flow passes 'line' | 'bar' | 'pie' from its
// ```chart block, so the model picks the form that fits what it's showing.
const PIE_COLORS = ['#4f8cff', '#41c99e', '#f2a33c', '#e0607e', '#9a7cf5', '#4bc0d9']

export default function ResultChart({ columns, data, theme, height = 240, kind }) {
  if (!data?.length || !columns?.length) return null
  const numCols = columns.filter(c => typeof data[0]?.[c] === 'number')
  const strCols = columns.filter(c => typeof data[0]?.[c] === 'string')
  if (!numCols.length || !strCols.length || data.length < 2) return null

  const y1 = numCols[0], y2 = numCols[1]
  const isLight   = theme === 'light'
  const gridColor = isLight ? 'rgba(0,0,0,0.06)' : 'rgba(255,255,255,0.06)'
  const tickColor = isLight ? '#6b7280' : '#5a5f7d'
  const labelKey  = strCols.find(c => _CODE_COLS.test(c)) || strCols[0]
  const nameKey   = labelKey !== strCols[0] ? strCols[0] : null

  const chartData = data.slice(0, MAX_CATS).map(r => ({
    name:     String(r[labelKey] || ''),          // full label; rotation gives it room
    _tooltip: nameKey ? String(r[nameKey] || '') : null,
    [y1]: r[y1],
    ...(y2 ? { [y2]: r[y2] } : {}),
  }))
  const chartWidth = Math.max(320, chartData.length * PER_CAT)
  const barSize = Math.min(28, Math.max(6, Math.floor(PER_CAT * 0.55)))

  const CustomTooltip = ({ active, payload, label }) => {
    if (!active || !payload?.length) return null
    const fullName = payload[0]?.payload?._tooltip
    return (
      <div style={TT}>
        <p style={{ margin: '0 0 4px', color: 'var(--text)', fontWeight: 500 }}>{fullName || label}</p>
        {payload.map(p => (
          <p key={p.dataKey} style={{ margin: '2px 0', color: p.color }}>{p.name}: {p.value?.toLocaleString()}</p>
        ))}
      </div>
    )
  }

  const wrap = inner => (
    <div style={{ marginTop: 10, background: 'var(--bg2)', borderRadius: 8, padding: 10, border: '1px solid var(--border)', overflowX: 'auto', overflowY: 'hidden' }}>
      {inner}
    </div>
  )

  if (kind === 'pie') return wrap(
    <PieChart width={Math.max(320, height + 200)} height={height}>
      <Pie data={chartData} dataKey={y1} nameKey="name" outerRadius={height / 2 - 20} label>
        {chartData.map((_, k) => <Cell key={k} fill={PIE_COLORS[k % PIE_COLORS.length]} />)}
      </Pie>
      <Tooltip content={<CustomTooltip />} />
    </PieChart>
  )

  return wrap(
    <ComposedChart width={chartWidth} height={height} data={chartData}
                   margin={{ top: 10, right: 12, left: 0, bottom: 64 }}>
      <CartesianGrid strokeDasharray="3 3" stroke={gridColor} />
      <XAxis dataKey="name" interval={0} angle={-45} textAnchor="end" height={70}
             tick={{ fontSize: 10, fill: tickColor }} tickLine={false} axisLine={false} />
      <YAxis tick={{ fontSize: 9, fill: tickColor }} axisLine={false} tickLine={false} />
      <Tooltip content={<CustomTooltip />} />
      {kind !== 'line' &&
        <Bar dataKey={y1} fill="var(--blue)" radius={[3, 3, 0, 0]} barSize={barSize} />}
      {kind === 'line' &&
        <Line dataKey={y1} stroke="var(--blue)" strokeWidth={1.8} dot={false} />}
      {y2 && <Line dataKey={y2} stroke="var(--green)" strokeWidth={1.5} dot={false} />}
    </ComposedChart>
  )
}
