import React, { useState } from 'react'
import { BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { Btn, Card, LLMToggle, MetricCard, Spinner, Empty } from '../components/UI'
import { runNLQuery } from '../utils/api'

const SUGGESTIONS = [
  'Show total revenue by category this year',
  'Top 10 customers by order value',
  'Monthly order count for the last 12 months',
  'Which products have the lowest inventory?',
  'Average order value by region',
  'Sales rep performance ranked by revenue',
]

const TOOLTIP_STYLE = {
  background: '#1a1e28', border: '1px solid rgba(255,255,255,0.1)',
  borderRadius: 8, fontSize: 12, color: '#eef0f6',
}

export default function QueryPage({ llm, setLlm }) {
  const [question, setQuestion] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [showSQL, setShowSQL] = useState(false)

  async function handleRun() {
    if (!question.trim()) return
    setLoading(true); setError(null); setResult(null); setShowSQL(false)
    try {
      const data = await runNLQuery(question, llm)
      setResult(data)
    } catch (e) {
      setError(e.response?.data?.detail || e.message)
    } finally {
      setLoading(false)
    }
  }

  const isNumeric = (v) => typeof v === 'number'

  // Pick first string col as X axis, first numeric col as Y
  const cols = result?.columns || []
  const xCol = cols.find(c => typeof result?.data?.[0]?.[c] === 'string') || cols[0]
  const yCol = cols.find(c => isNumeric(result?.data?.[0]?.[c]))
  const chartData = result?.data?.slice(0, 30).map(r => ({ name: String(r[xCol] ?? ''), value: r[yCol] ?? 0 })) || []

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, height: '100%' }}>

      {/* Input area */}
      <Card style={{ padding: 20 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <span style={{ fontSize: 11, fontWeight: 500, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Natural Language Query</span>
          <LLMToggle value={llm} onChange={setLlm} />
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <textarea
            value={question}
            onChange={e => setQuestion(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleRun() } }}
            placeholder="Ask anything about your data… e.g. Show me total revenue by product category for the last 6 months"
            rows={2}
            style={{ flex: 1, padding: '10px 14px', resize: 'none', lineHeight: 1.6 }}
          />
          <Btn onClick={handleRun} disabled={loading || !question.trim()} style={{ alignSelf: 'flex-end', height: 42 }}>
            {loading ? <Spinner size={14} color="#fff" /> : 'Run ↗'}
          </Btn>
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 10 }}>
          {SUGGESTIONS.map(s => (
            <button key={s} onClick={() => setQuestion(s)} style={{
              padding: '3px 12px', borderRadius: 20, fontSize: 11,
              background: 'var(--bg-elevated)', color: 'var(--text-muted)',
              border: '1px solid var(--border)', cursor: 'pointer',
            }}>{s}</button>
          ))}
        </div>
      </Card>

      {/* Error */}
      {error && (
        <Card style={{ padding: '14px 18px', borderColor: 'rgba(240,96,96,0.3)', background: 'var(--red-dim)' }}>
          <span style={{ fontSize: 13, color: 'var(--red)' }}>⚠ {error}</span>
        </Card>
      )}

      {/* Empty */}
      {!result && !loading && !error && (
        <Empty icon="⌕" title="Ask your first question" subtitle="Type a question above and press Run — the AI will generate and execute the SQL for you." />
      )}

      {/* Loading skeleton */}
      {loading && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div className="skeleton" style={{ height: 80 }} />
          <div className="skeleton" style={{ height: 220 }} />
        </div>
      )}

      {/* Results */}
      {result && (
        <div className="fade-up" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>

          {/* SQL toggle */}
          <Card style={{ overflow: 'hidden' }}>
            <div onClick={() => setShowSQL(v => !v)} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 16px', cursor: 'pointer', borderBottom: showSQL ? '1px solid var(--border)' : 'none' }}>
              <span style={{ fontSize: 12, color: 'var(--text-secondary)', fontWeight: 500 }}>Generated SQL</span>
              <span style={{ fontSize: 11, color: 'var(--accent)', fontWeight: 500 }}>{showSQL ? '▲ Hide' : '▼ Show'}</span>
            </div>
            {showSQL && (
              <pre style={{ padding: '12px 16px', fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--green)', overflowX: 'auto', lineHeight: 1.8, whiteSpace: 'pre-wrap' }}>
                {result.sql}
              </pre>
            )}
          </Card>

          {/* Metrics */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10 }}>
            <MetricCard label="Rows returned" value={result.row_count.toLocaleString()} />
            <MetricCard label="Columns" value={result.columns.length} />
            <MetricCard label="LLM used" value={llm.charAt(0).toUpperCase() + llm.slice(1)} />
          </div>

          {/* Chart (only if numeric column exists) */}
          {yCol && chartData.length > 1 && (
            <Card style={{ padding: 18 }}>
              <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 14 }}>{yCol} by {xCol}</div>
              <ResponsiveContainer width="100%" height={220}>
                {chartData.length <= 12 ? (
                  <BarChart data={chartData} barSize={22}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                    <XAxis dataKey="name" tick={{ fontSize: 11, fill: '#555d75' }} axisLine={false} tickLine={false} />
                    <YAxis tick={{ fontSize: 11, fill: '#555d75' }} axisLine={false} tickLine={false} />
                    <Tooltip contentStyle={TOOLTIP_STYLE} />
                    <Bar dataKey="value" fill="var(--accent)" radius={[4,4,0,0]} />
                  </BarChart>
                ) : (
                  <LineChart data={chartData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                    <XAxis dataKey="name" tick={{ fontSize: 11, fill: '#555d75' }} axisLine={false} tickLine={false} />
                    <YAxis tick={{ fontSize: 11, fill: '#555d75' }} axisLine={false} tickLine={false} />
                    <Tooltip contentStyle={TOOLTIP_STYLE} />
                    <Line dataKey="value" stroke="var(--accent)" strokeWidth={2} dot={false} />
                  </LineChart>
                )}
              </ResponsiveContainer>
            </Card>
          )}

          {/* Data table */}
          <Card style={{ overflow: 'hidden' }}>
            <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', fontSize: 12, color: 'var(--text-secondary)', fontWeight: 500 }}>
              Results · {result.row_count} rows
            </div>
            <div style={{ overflowX: 'auto', maxHeight: 340, overflowY: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                <thead>
                  <tr>
                    {result.columns.map(c => (
                      <th key={c} style={{ padding: '10px 16px', textAlign: 'left', color: 'var(--text-muted)', fontWeight: 500, borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap', background: 'var(--bg-elevated)', position: 'sticky', top: 0 }}>
                        {c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.data.map((row, i) => (
                    <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                      {result.columns.map(c => (
                        <td key={c} style={{ padding: '9px 16px', color: isNumeric(row[c]) ? 'var(--accent)' : 'var(--text-primary)', fontFamily: isNumeric(row[c]) ? 'var(--font-mono)' : 'inherit', whiteSpace: 'nowrap' }}>
                          {row[c] === null ? <span style={{ color: 'var(--text-muted)' }}>null</span> : String(row[c])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      )}
    </div>
  )
}
