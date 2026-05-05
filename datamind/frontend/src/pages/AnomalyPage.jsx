import React, { useState } from 'react'
import {
  ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceLine
} from 'recharts'
import { Btn, Card, Badge, Spinner, Empty, SectionHeader } from '../components/UI'
import { runAnomalies } from '../utils/api'

const TT = { background: '#1a1e28', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, fontSize: 12, color: '#eef0f6' }

const severityColor = { high: 'red', medium: 'amber', low: 'blue' }

export default function AnomalyPage({ tables }) {
  const [table, setTable] = useState('')
  const [valueCol, setValueCol] = useState('')
  const [dateCol, setDateCol] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

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

  const inputStyle = { padding: '8px 12px', width: '100%', borderRadius: 'var(--radius-md)', fontSize: 13 }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

      <Card style={{ padding: 20 }}>
        <SectionHeader title="Anomaly Detection (Isolation Forest)" />
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr auto', gap: 10, alignItems: 'flex-end' }}>
          <div>
            <label style={{ fontSize: 11, color: 'var(--text-muted)', display: 'block', marginBottom: 5 }}>Table</label>
            <select value={table} onChange={e => setTable(e.target.value)} style={inputStyle}>
              <option value="">Select table…</option>
              {tables.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div>
            <label style={{ fontSize: 11, color: 'var(--text-muted)', display: 'block', marginBottom: 5 }}>Value column</label>
            <input value={valueCol} onChange={e => setValueCol(e.target.value)} placeholder="e.g. revenue, quantity" style={inputStyle} />
          </div>
          <div>
            <label style={{ fontSize: 11, color: 'var(--text-muted)', display: 'block', marginBottom: 5 }}>Date column (optional)</label>
            <input value={dateCol} onChange={e => setDateCol(e.target.value)} placeholder="e.g. created_at" style={inputStyle} />
          </div>
          <Btn onClick={handleRun} disabled={loading || !table || !valueCol} style={{ alignSelf: 'flex-end' }}>
            {loading ? <><Spinner size={14} color="#fff" /> Scanning…</> : 'Detect ↗'}
          </Btn>
        </div>
      </Card>

      {!result && !loading && !error && (
        <Empty icon="⬡" title="No scan run yet" subtitle="Select a table and numeric column to detect statistical outliers with Isolation Forest." />
      )}

      {error && (
        <Card style={{ padding: '14px 18px', borderColor: 'rgba(240,96,96,0.3)', background: 'var(--red-dim)' }}>
          <span style={{ fontSize: 13, color: 'var(--red)' }}>⚠ {error}</span>
        </Card>
      )}

      {loading && <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}><div className="skeleton" style={{ height: 240 }} /><div className="skeleton" style={{ height: 160 }} /></div>}

      {result && (
        <div className="fade-up" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>

          {/* Summary metrics */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10 }}>
            <div style={{ background: 'var(--bg-elevated)', borderRadius: 'var(--radius-md)', padding: '14px 16px' }}>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.07em' }}>Total points</div>
              <div style={{ fontSize: 22, fontWeight: 600 }}>{result.total_points.toLocaleString()}</div>
            </div>
            <div style={{ background: 'var(--bg-elevated)', borderRadius: 'var(--radius-md)', padding: '14px 16px' }}>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.07em' }}>Anomalies found</div>
              <div style={{ fontSize: 22, fontWeight: 600, color: result.anomaly_count > 0 ? 'var(--amber)' : 'var(--green)' }}>
                {result.anomaly_count}
              </div>
            </div>
            <div style={{ background: 'var(--bg-elevated)', borderRadius: 'var(--radius-md)', padding: '14px 16px' }}>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.07em' }}>Anomaly rate</div>
              <div style={{ fontSize: 22, fontWeight: 600 }}>{((result.anomaly_count / result.total_points) * 100).toFixed(1)}%</div>
            </div>
          </div>

          {/* Score chart */}
          <Card style={{ padding: 18 }}>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 14 }}>Anomaly score over time — red bars exceed threshold</div>
            <ResponsiveContainer width="100%" height={220}>
              <ComposedChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
                <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#555d75' }} axisLine={false} tickLine={false} interval={Math.floor(chartData.length / 8)} />
                <YAxis tick={{ fontSize: 11, fill: '#555d75' }} axisLine={false} tickLine={false} domain={[0, 1]} />
                <Tooltip contentStyle={TT} />
                <ReferenceLine y={0.6} stroke="rgba(240,96,96,0.4)" strokeDasharray="4 3" />
                <Bar dataKey="score" fill="rgba(91,138,240,0.4)" radius={[2,2,0,0]} />
                <Bar dataKey="anomaly" fill="var(--red)" radius={[2,2,0,0]} />
              </ComposedChart>
            </ResponsiveContainer>
          </Card>

          {/* Anomaly list */}
          {result.anomalies.length === 0 ? (
            <Card style={{ padding: '20px', textAlign: 'center' }}>
              <span style={{ color: 'var(--green)', fontSize: 13 }}>✓ No anomalies detected — your data looks clean.</span>
            </Card>
          ) : (
            <Card style={{ overflow: 'hidden' }}>
              <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', fontSize: 12, color: 'var(--text-secondary)', fontWeight: 500 }}>
                Detected Anomalies · {result.anomaly_count}
              </div>
              <div style={{ maxHeight: 320, overflowY: 'auto' }}>
                {result.anomalies.map((a, i) => (
                  <div key={i} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 16px', borderBottom: '1px solid var(--border)', borderLeft: `3px solid ${a.severity === 'high' ? 'var(--red)' : a.severity === 'medium' ? 'var(--amber)' : 'var(--accent)'}` }}>
                    <div>
                      <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-primary)' }}>{a.date}</div>
                      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
                        Value: <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>{a.value}</span>
                        &nbsp;·&nbsp; z-score: <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--amber)' }}>{a.zscore}</span>
                        &nbsp;·&nbsp; IF score: <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--red)' }}>{a.anomaly_score}</span>
                      </div>
                    </div>
                    <Badge color={severityColor[a.severity]}>{a.severity}</Badge>
                  </div>
                ))}
              </div>
            </Card>
          )}
        </div>
      )}
    </div>
  )
}
