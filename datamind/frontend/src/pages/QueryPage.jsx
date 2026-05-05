import React, { useState } from 'react'
import { BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { Btn, Card, MetricCard, Spinner, Empty } from '../components/UI'
import { runNLQuery } from '../utils/api'

const SUGGESTIONS = [
  'Top 10 customers by revenue',
  'Monthly orders vs returns',
  'Which products have low inventory?',
  'Revenue by region this quarter',
]

const TOOLTIP_STYLE = {
  background: '#ffffff', border: '0.5px solid var(--color-border-secondary)',
  borderRadius: 8, fontSize: 12, color: 'var(--color-text-primary)',
}

export default function QueryPage({ llm }) {
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
      <Card style={{ padding: 16 }}>
        <div style={{ fontSize: 11, fontWeight: 500, color: 'var(--color-text-tertiary)', marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Ask your data anything</div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
          <textarea
            value={question}
            onChange={e => setQuestion(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleRun() } }}
            placeholder="e.g. Show me total revenue by product category for the last 6 months..."
            rows={2}
            style={{ flex: 1, padding: '10px 14px', border: '0.5px solid var(--color-border-secondary)', borderRadius: 'var(--border-radius-md)', fontSize: 14, background: 'var(--color-background-secondary)', color: 'var(--color-text-primary)', resize: 'none' }}
          />
          <Btn onClick={handleRun} disabled={loading || !question.trim()} style={{ padding: '10px 18px', height: 'fit-content' }}>
            {loading ? <Spinner size={14} color="#fff" /> : 'Run ↗'}
          </Btn>
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 10 }}>
          {SUGGESTIONS.map(s => (
            <div key={s} onClick={() => setQuestion(s)} style={{
              padding: '4px 12px', background: 'var(--color-background-secondary)', border: '0.5px solid var(--color-border-tertiary)', borderRadius: 20, fontSize: 12, color: 'var(--color-text-secondary)', cursor: 'pointer', transition: 'all 0.1s'
            }} onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--color-border-secondary)'; e.currentTarget.style.color = 'var(--color-text-primary)'; }} onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--color-border-tertiary)'; e.currentTarget.style.color = 'var(--color-text-secondary)'; }}>{s}</div>
          ))}
        </div>
      </Card>

      {/* Error */}
      {error && (
        <Card style={{ padding: '14px 18px', background: '#FCEBEB', borderColor: 'rgba(163,45,45,0.1)' }}>
          <span style={{ fontSize: 13, color: '#A32D2D' }}>⚠ {error}</span>
        </Card>
      )}

      {/* Empty */}
      {!result && !loading && !error && (
        <Empty icon="⌕" title="Ask your first question" subtitle="Type a question above and press Run — the AI will generate and execute the SQL for you." />
      )}

      {/* Loading skeleton */}
      {loading && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <Card style={{ height: 100, background: 'var(--color-background-secondary)' }} />
          <Card style={{ height: 260, background: 'var(--color-background-secondary)' }} />
        </div>
      )}

      {/* Results */}
      {result && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

          <Card style={{ overflow: 'hidden' }}>
            <div onClick={() => setShowSQL(v => !v)} style={{ display: 'flex', alignItems: 'center', justifyCenter: 'space-between', padding: '12px 16px', cursor: 'pointer', borderBottom: showSQL ? '0.5px solid var(--color-border-tertiary)' : 'none' }}>
              <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--color-text-primary)' }}>{question}</div>
              <div style={{ background: '#f0f0f8', color: '#534AB7', borderRadius: 'var(--border-radius-md)', padding: '2px 10px', fontSize: 11, fontFamily: 'var(--font-mono)', fontWeight: 500 }}>SQL {showSQL ? '↑' : '↓'}</div>
            </div>
            {showSQL && (
              <pre style={{ background: 'var(--color-background-secondary)', padding: '12px 16px', fontSize: 12, fontFamily: 'var(--font-mono)', color: 'var(--color-text-secondary)', borderTop: '0.5px solid var(--color-border-tertiary)', overflowX: 'auto' }}>
                {result.sql}
              </pre>
            )}
            
            <div style={{ padding: 16 }}>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10, marginBottom: 16 }}>
                <MetricCard label="Rows returned" value={result.row_count} />
                <MetricCard label="Columns" value={result.columns.length} />
                <MetricCard label="Avg value" value={isNumeric(result.data[0]?.[yCol]) ? (result.data.reduce((acc, r) => acc + (r[yCol] || 0), 0) / result.row_count).toFixed(1) : 'N/A'} />
                <MetricCard label="Top item" value={result.data[0]?.[xCol] || 'N/A'} />
              </div>

              {yCol && chartData.length > 1 && (
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--color-text-secondary)', marginBottom: 12 }}>Distribution of {yCol}</div>
                    <div style={{ height: 200 }}>
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={chartData}>
                          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="rgba(0,0,0,0.05)" />
                          <XAxis 
                            dataKey="name" 
                            tick={{ fontSize: 10, fill: 'var(--color-text-tertiary)' }} 
                            axisLine={false} 
                            tickLine={false}
                            tickFormatter={(str) => {
                              try {
                                if (str.includes('T')) {
                                  const d = new Date(str);
                                  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
                                }
                                return str;
                              } catch(e) { return str; }
                            }}
                          />
                          <YAxis 
                            tick={{ fontSize: 10, fill: 'var(--color-text-tertiary)' }} 
                            axisLine={false} 
                            tickLine={false} 
                            domain={[0, 'auto']}
                          />
                          <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: 'rgba(0,0,0,0.02)' }} />
                          <Bar dataKey="value" fill="#378ADD" radius={[4,4,0,0]} />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  </div>
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--color-text-secondary)', marginBottom: 12 }}>Trend of {yCol}</div>
                    <div style={{ height: 200 }}>
                      <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={chartData}>
                          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="rgba(0,0,0,0.05)" />
                          <XAxis 
                            dataKey="name" 
                            tick={{ fontSize: 10, fill: 'var(--color-text-tertiary)' }} 
                            axisLine={false} 
                            tickLine={false}
                            tickFormatter={(str) => {
                              try {
                                if (str.includes('T')) {
                                  const d = new Date(str);
                                  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
                                }
                                return str;
                              } catch(e) { return str; }
                            }}
                          />
                          <YAxis 
                            tick={{ fontSize: 10, fill: 'var(--color-text-tertiary)' }} 
                            axisLine={false} 
                            tickLine={false} 
                            domain={[0, 'auto']}
                          />
                          <Tooltip contentStyle={TOOLTIP_STYLE} />
                          <Line type="monotone" dataKey="value" stroke="#1D9E75" strokeWidth={2} dot={false} />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </Card>

          {/* Data table */}
          <Card style={{ overflow: 'hidden' }}>
            <div style={{ padding: '12px 16px', borderBottom: '0.5px solid var(--color-border-tertiary)', fontSize: 12, color: 'var(--color-text-secondary)', fontWeight: 500 }}>
              Full Dataset · {result.row_count} rows
            </div>
            <div style={{ overflowX: 'auto', maxHeight: 340, overflowY: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                <thead style={{ background: 'var(--color-background-secondary)', position: 'sticky', top: 0, zIndex: 1 }}>
                  <tr>
                    {result.columns.map(c => (
                      <th key={c} style={{ padding: '10px 16px', textAlign: 'left', color: 'var(--color-text-tertiary)', fontWeight: 500, borderBottom: '0.5px solid var(--color-border-tertiary)', whiteSpace: 'nowrap' }}>
                        {c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.data.map((row, i) => (
                    <tr key={i} style={{ borderBottom: '0.5px solid var(--color-border-tertiary)' }} onMouseEnter={(e) => e.currentTarget.style.background = 'var(--color-background-secondary)'} onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}>
                      {result.columns.map(c => (
                        <td key={c} style={{ padding: '9px 16px', color: isNumeric(row[c]) ? '#378ADD' : 'var(--color-text-primary)', fontFamily: isNumeric(row[c]) ? 'var(--font-mono)' : 'inherit', whiteSpace: 'nowrap' }}>
                          {row[c] === null ? <span style={{ color: 'var(--color-text-tertiary)' }}>null</span> : String(row[c])}
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
