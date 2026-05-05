import React, { useState } from 'react'
import {
  ComposedChart, Line, Area, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, BarChart, Bar, Legend
} from 'recharts'
import { Btn, Card, Spinner, Empty, SectionHeader } from '../components/UI'
import { runForecast } from '../utils/api'

const TT = { background: '#1a1e28', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, fontSize: 12, color: '#eef0f6' }

export default function ForecastPage({ tables, schemas }) {
  const [table, setTable] = useState('')
  const [dateCol, setDateCol] = useState('')
  const [valueCol, setValueCol] = useState('')
  const [periods, setPeriods] = useState(90)
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  const columns = table ? (schemas[table] || []) : []

  function handleTableChange(e) {
    setTable(e.target.value)
    setDateCol('')
    setValueCol('')
  }

  async function handleRun() {
    if (!table || !dateCol || !valueCol) return
    setLoading(true); setError(null); setResult(null)
    try {
      const data = await runForecast(table, dateCol, valueCol, periods)
      setResult(data)
    } catch (e) {
      setError(e.response?.data?.detail || e.message)
    } finally {
      setLoading(false)
    }
  }

  const chartData = result
    ? [
        ...result.historical.map(h => ({ date: h.date, actual: h.value })),
        ...result.forecast.map(f => ({ date: f.date, forecast: f.yhat, upper: f.yhat_upper, lower: f.yhat_lower }))
      ]
    : []

  const weeklyData = result
    ? Object.entries(result.weekly_seasonality || {}).map(([day, val]) => ({ day: day.slice(0,3), value: +val.toFixed(1) }))
    : []

  const inputStyle = { padding: '8px 12px', width: '100%', borderRadius: 'var(--radius-md)', fontSize: 13, background: 'var(--bg-elevated)', border: '1px solid var(--border)', color: 'var(--text-primary)' }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

      {/* Config */}
      <Card style={{ padding: 20 }}>
        <SectionHeader title="Forecast Configuration" />
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr auto', gap: 10, alignItems: 'flex-end' }}>
          <div>
            <label style={{ fontSize: 11, color: 'var(--text-muted)', display: 'block', marginBottom: 5 }}>Table</label>
            <select value={table} onChange={handleTableChange} style={inputStyle}>
              <option value="">Select table…</option>
              {tables.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div>
            <label style={{ fontSize: 11, color: 'var(--text-muted)', display: 'block', marginBottom: 5 }}>Date column</label>
            <select value={dateCol} onChange={e => setDateCol(e.target.value)} style={inputStyle} disabled={!table}>
              <option value="">Select date column…</option>
              {columns.map(c => <option key={c.name} value={c.name}>{c.name} ({c.type})</option>)}
            </select>
          </div>
          <div>
            <label style={{ fontSize: 11, color: 'var(--text-muted)', display: 'block', marginBottom: 5 }}>Value column</label>
            <select value={valueCol} onChange={e => setValueCol(e.target.value)} style={inputStyle} disabled={!table}>
              <option value="">Select value column…</option>
              {columns.map(c => <option key={c.name} value={c.name}>{c.name} ({c.type})</option>)}
            </select>
          </div>
          <div>
            <label style={{ fontSize: 11, color: 'var(--text-muted)', display: 'block', marginBottom: 5 }}>Days ahead</label>
            <select value={periods} onChange={e => setPeriods(Number(e.target.value))} style={inputStyle}>
              <option value={30}>30 days</option>
              <option value={60}>60 days</option>
              <option value={90}>90 days</option>
              <option value={180}>180 days</option>
            </select>
          </div>
        </div>
        <div style={{ marginTop: 14 }}>
          <Btn onClick={handleRun} disabled={loading || !table || !dateCol || !valueCol}>
            {loading ? <><Spinner size={14} color="#fff" /> Running Prophet…</> : 'Run Forecast ↗'}
          </Btn>
        </div>
      </Card>

      {/* Info banner */}
      {!result && !loading && !error && (
        <div style={{ background: 'var(--accent-dim)', border: '1px solid rgba(91,138,240,0.2)', borderRadius: 'var(--radius-md)', padding: '12px 16px', fontSize: 13, color: 'var(--accent)' }}>
          ✦ Powered by Meta's Prophet. Automatically detects yearly and weekly seasonality from your MySQL data.
        </div>
      )}

      {error && (
        <Card style={{ padding: '14px 18px', borderColor: 'rgba(240,96,96,0.3)', background: 'var(--red-dim)' }}>
          <span style={{ fontSize: 13, color: 'var(--red)' }}>⚠ {error}</span>
        </Card>
      )}

      {loading && <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}><div className="skeleton" style={{ height: 300 }} /><div className="skeleton" style={{ height: 180 }} /></div>}

      {result && (
        <div className="fade-up" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>

          {/* Main forecast chart */}
          <Card style={{ padding: 18 }}>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 14 }}>Historical + {periods}-day forecast with 80% confidence band</div>
            <ResponsiveContainer width="100%" height={280}>
              <ComposedChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
                <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#555d75' }} axisLine={false} tickLine={false} interval={Math.floor(chartData.length / 8)} />
                <YAxis tick={{ fontSize: 11, fill: '#555d75' }} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={TT} />
                <Area dataKey="upper" fill="rgba(91,138,240,0.08)" stroke="none" />
                <Area dataKey="lower" fill="var(--bg)" stroke="none" />
                <Line dataKey="actual" stroke="var(--green)" strokeWidth={1.8} dot={false} connectNulls={false} />
                <Line dataKey="forecast" stroke="var(--accent)" strokeWidth={1.8} strokeDasharray="5 3" dot={false} connectNulls={false} />
              </ComposedChart>
            </ResponsiveContainer>
            <div style={{ display: 'flex', gap: 20, marginTop: 10, fontSize: 11, color: 'var(--text-muted)' }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}><span style={{ width: 18, height: 2, background: 'var(--green)', display: 'inline-block' }} /> Historical</span>
              <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}><span style={{ width: 18, height: 2, background: 'var(--accent)', display: 'inline-block', borderTop: '2px dashed var(--accent)' }} /> Forecast</span>
              <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}><span style={{ width: 18, height: 8, background: 'rgba(91,138,240,0.15)', display: 'inline-block', borderRadius: 2 }} /> 80% confidence</span>
            </div>
          </Card>

          {/* Weekly seasonality */}
          {weeklyData.length > 0 && (
            <Card style={{ padding: 18 }}>
              <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 14 }}>Weekly seasonality pattern</div>
              <ResponsiveContainer width="100%" height={160}>
                <BarChart data={weeklyData} barSize={28}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
                  <XAxis dataKey="day" tick={{ fontSize: 11, fill: '#555d75' }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fontSize: 11, fill: '#555d75' }} axisLine={false} tickLine={false} />
                  <Tooltip contentStyle={TT} />
                  <Bar dataKey="value" fill="var(--purple)" radius={[4,4,0,0]} />
                </BarChart>
              </ResponsiveContainer>
            </Card>
          )}

          {/* Stats */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10 }}>
            <div style={{ background: 'var(--bg-elevated)', borderRadius: 'var(--radius-md)', padding: '14px 16px' }}>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.07em' }}>Historical points</div>
              <div style={{ fontSize: 22, fontWeight: 600 }}>{result.historical.length}</div>
            </div>
            <div style={{ background: 'var(--bg-elevated)', borderRadius: 'var(--radius-md)', padding: '14px 16px' }}>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.07em' }}>Forecast horizon</div>
              <div style={{ fontSize: 22, fontWeight: 600 }}>{periods} days</div>
            </div>
            <div style={{ background: 'var(--bg-elevated)', borderRadius: 'var(--radius-md)', padding: '14px 16px' }}>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.07em' }}>Last forecast value</div>
              <div style={{ fontSize: 22, fontWeight: 600 }}>{result.forecast.at(-1)?.yhat?.toLocaleString() ?? '—'}</div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
