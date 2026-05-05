import React, { useState } from 'react'
import {
  ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceLine
} from 'recharts'
import { Btn, Card, Badge, Spinner, SectionHeader, MetricCard } from '../components/UI'
import { runAnomalies } from '../utils/api'

const TT = { 
  background: '#ffffff', 
  border: '0.5px solid var(--color-border-secondary)', 
  borderRadius: 8, 
  fontSize: 12, 
  color: 'var(--color-text-primary)' 
}

const severityColor = { high: 'red', medium: 'amber', low: 'blue' }

export default function AnomalyPage({ tables, schemas }) {
  const [table, setTable] = useState('')
  const [valueCol, setValueCol] = useState('')
  const [dateCol, setDateCol] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  const columns = table ? (schemas[table] || []) : []

  function handleTableChange(e) {
    setTable(e.target.value)
    setValueCol('')
    setDateCol('')
  }

  async function handleRun() {
    if (!table || !valueCol) return
    setLoading(true); setError(null); setResult(null)
    try {
      const data = await runAnomalies(table, valueCol, dateCol || null)
      setResult(data)
    } catch (e) {
      setError(e.response?.data?.detail || e.message)
    } finally {
      setLoading(false)
    }
  }

  const chartData = result?.series.map(s => ({
    date: s.date,
    score: s.score,
    value: s.value,
    anomaly: s.is_anomaly ? s.score : null,
  })) || []

  const inputStyle = { padding: '9px 12px', width: '100%', borderRadius: 'var(--border-radius-md)', fontSize: 13, background: 'var(--color-background-secondary)', border: '0.5px solid var(--color-border-secondary)', color: 'var(--color-text-primary)', outline: 'none' }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

      <Card style={{ padding: 16 }}>
        <SectionHeader title="Anomaly Detection (Isolation Forest)" />
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr auto', gap: 10, alignItems: 'flex-end' }}>
          <div>
            <label style={{ fontSize: 11, color: 'var(--color-text-tertiary)', display: 'block', marginBottom: 5 }}>Table</label>
            <select value={table} onChange={handleTableChange} style={inputStyle}>
              <option value="">Select table…</option>
              {tables.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div>
            <label style={{ fontSize: 11, color: 'var(--color-text-tertiary)', display: 'block', marginBottom: 5 }}>Value column</label>
            <select value={valueCol} onChange={e => setValueCol(e.target.value)} style={inputStyle} disabled={!table}>
              <option value="">Select column…</option>
              {columns.map(c => <option key={c.name} value={c.name}>{c.name} ({c.type})</option>)}
            </select>
          </div>
          <div>
            <label style={{ fontSize: 11, color: 'var(--color-text-tertiary)', display: 'block', marginBottom: 5 }}>Date column (optional)</label>
            <select value={dateCol} onChange={e => setDateCol(e.target.value)} style={inputStyle} disabled={!table}>
              <option value="">None (Index based)</option>
              {columns.map(c => <option key={c.name} value={c.name}>{c.name} ({c.type})</option>)}
            </select>
          </div>
          <Btn onClick={handleRun} disabled={loading || !table || !valueCol} style={{ padding: '10px 18px', height: 'fit-content' }}>
            {loading ? <Spinner size={14} color="#fff" /> : 'Detect ↗'}
          </Btn>
        </div>
      </Card>

      {error && (
        <Card style={{ padding: '14px 18px', background: '#FCEBEB', borderColor: 'rgba(163,45,45,0.1)' }}>
          <span style={{ fontSize: 13, color: '#A32D2D' }}>⚠ {error}</span>
        </Card>
      )}

      {loading && <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}><Card style={{ height: 240, background: 'var(--color-background-secondary)' }} /><Card style={{ height: 160, background: 'var(--color-background-secondary)' }} /></div>}

      {result && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

          {/* Summary metrics */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
            <MetricCard label="Total points" value={result.total_points.toLocaleString()} />
            <MetricCard label="Anomalies found" value={result.anomaly_count} delta={result.anomaly_count > 0 ? "Potential risk" : "Data clean"} deltaType={result.anomaly_count > 0 ? "down" : "up"} />
            <MetricCard label="Anomaly rate" value={`${((result.anomaly_count / result.total_points) * 100).toFixed(1)}%`} />
            <MetricCard label="Algorithm" value="Isolation Forest" />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1.5fr 1fr', gap: 16 }}>
            {/* Score chart */}
            <Card style={{ padding: 16 }}>
              <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--color-text-secondary)', marginBottom: 16 }}>Anomaly score distribution — red exceeds threshold</div>
              <div style={{ height: 260 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={chartData}>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="rgba(0,0,0,0.05)" />
                  <XAxis 
                    dataKey="date" 
                    hide={false}
                    tick={{ fontSize: 10, fill: 'var(--color-text-tertiary)' }} 
                    axisLine={false} 
                    tickLine={false}
                    interval={Math.floor(chartData.length / 6)}
                    tickFormatter={(str) => {
                      try {
                        const d = new Date(str);
                        return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
                      } catch(e) { return str; }
                    }}
                  />
                  <YAxis 
                    tick={{ fontSize: 10, fill: 'var(--color-text-tertiary)' }} 
                    axisLine={false} 
                    tickLine={false} 
                    domain={[0, 1]} 
                  />
                    <Tooltip contentStyle={TT} />
                    <ReferenceLine y={0.6} stroke="#A32D2D" strokeDasharray="4 3" />
                    <Bar dataKey="score" fill="rgba(55,138,221,0.2)" radius={[2,2,0,0]} />
                    <Bar dataKey="anomaly" fill="#A32D2D" radius={[2,2,0,0]} />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            </Card>

            {/* Anomaly list */}
            <Card style={{ display: 'flex', flexDirection: 'column' }}>
              <div style={{ padding: '12px 16px', borderBottom: '0.5px solid var(--color-border-tertiary)', fontSize: 12, fontWeight: 500, color: 'var(--color-text-secondary)' }}>
                Recent Alerts · {result.anomaly_count}
              </div>
              <div style={{ flex: 1, maxHeight: 300, overflowY: 'auto' }}>
                {result.anomalies.length === 0 ? (
                  <div style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-tertiary)', fontSize: 13 }}>
                    ✓ No anomalies detected
                  </div>
                ) : (
                  result.anomalies.map((a, i) => (
                    <div key={i} style={{ padding: '12px 16px', borderBottom: '0.5px solid var(--color-border-tertiary)', display: 'flex', alignItems: 'center', gap: 12 }} onMouseEnter={(e) => e.currentTarget.style.background = 'var(--color-background-secondary)'} onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}>
                      <div style={{ width: 8, height: 8, borderRadius: '50%', background: a.severity === 'high' ? '#A32D2D' : a.severity === 'medium' ? '#854F0B' : '#378ADD', flexShrink: 0 }}></div>
                      <div style={{ flex: 1 }}>
                        <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--color-text-primary)' }}>{a.date || `Row #${i+1}`}</div>
                        <div style={{ fontSize: 11, color: 'var(--color-text-tertiary)', marginTop: 2 }}>Val: {a.value} · Score: {a.anomaly_score.toFixed(3)}</div>
                      </div>
                      <Badge color={severityColor[a.severity]}>{a.severity}</Badge>
                    </div>
                  ))
                )}
              </div>
            </Card>
          </div>
        </div>
      )}
    </div>
  )
}
