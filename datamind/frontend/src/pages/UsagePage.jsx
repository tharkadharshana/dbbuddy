import React, { useState, useEffect } from 'react'
import { Card, Spinner, Badge } from '../components/UI'

export default function UsagePage() {
  const [credits, setCredits] = useState(null)
  const [history, setHistory] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      fetch('/api/credits', {
        headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` }
      }).then(r => r.ok ? r.json() : null),
      fetch('/api/credits/history?limit=100', {
        headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` }
      }).then(r => r.ok ? r.json() : null)
    ])
      .then(([c, h]) => {
        setCredits(c)
        setHistory(h?.history || [])
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [])

  if (loading) return (
    <div style={{ padding: 32, display: 'flex', alignItems: 'center', gap: 10, color: 'var(--text3)' }}>
      <Spinner /> Loading usage data...
    </div>
  )

  const aiUsage = history.filter(h => h.usage_type === 'ai_tokens')
  const totalCost = history.reduce((sum, h) => sum + (h.amount || 0), 0)
  
  // Group by day
  const byDay = {}
  history.forEach(h => {
    const day = h.created_at?.split('T')[0]
    if (!day) return
    if (!byDay[day]) byDay[day] = { tokens: 0, cost: 0, count: 0 }
    byDay[day].tokens += h.tokens_used || 0
    byDay[day].cost += h.amount || 0
    byDay[day].count += 1
  })

  const dailyData = Object.entries(byDay)
    .map(([date, data]) => ({ date, ...data }))
    .sort((a, b) => b.date.localeCompare(a.date))
    .slice(0, 30)

  return (
    <div style={{ maxWidth: 1000, padding: '24px 24px 60px' }}>
      <div style={{ marginBottom: 28 }}>
        <div style={{ fontSize: 20, fontWeight: 700, marginBottom: 4 }}>Usage & Credits</div>
        <div style={{ fontSize: 13, color: 'var(--text3)' }}>
          Track your AI token usage, database row processing, and credit consumption
        </div>
      </div>

      {/* CREDIT SUMMARY CARDS */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 14, marginBottom: 28 }}>
        <Card style={{ padding: 18, background: 'linear-gradient(135deg, rgba(79,142,247,0.1), rgba(167,139,250,0.1))' }}>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
            Credits Remaining
          </div>
          <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--blue)' }}>
            ${(credits?.credits_remaining || 0).toFixed(2)}
          </div>
        </Card>

        <Card style={{ padding: 18 }}>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
            Total Tokens Used
          </div>
          <div style={{ fontSize: 28, fontWeight: 700 }}>
            {(credits?.total_tokens_used || 0).toLocaleString()}
          </div>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>
            ${totalCost.toFixed(4)} spent
          </div>
        </Card>

        <Card style={{ padding: 18 }}>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
            Database Rows
          </div>
          <div style={{ fontSize: 28, fontWeight: 700 }}>
            {(credits?.total_db_rows || 0).toLocaleString()}
          </div>
        </Card>

        <Card style={{ padding: 18 }}>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
            API Calls
          </div>
          <div style={{ fontSize: 28, fontWeight: 700 }}>
            {aiUsage.length}
          </div>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>
            Last 30 days
          </div>
        </Card>
      </div>

      {/* DAILY USAGE CHART */}
      <Card style={{ padding: 20, marginBottom: 28 }}>
        <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 16 }}>Daily Usage (Last 30 Days)</div>
        <div style={{ height: 200, display: 'flex', alignItems: 'flex-end', gap: 4 }}>
          {dailyData.reverse().map((day, i) => {
            const maxTokens = Math.max(...dailyData.map(d => d.tokens))
            const height = (day.tokens / maxTokens) * 100
            return (
              <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
                <div style={{ fontSize: 10, color: 'var(--text3)', whiteSpace: 'nowrap' }}>
                  {day.tokens.toLocaleString()}
                </div>
                <div
                  style={{
                    width: '100%',
                    height: `${height}%`,
                    background: 'linear-gradient(to top, var(--blue), var(--purple))',
                    borderRadius: '4px 4px 0 0',
                    minHeight: day.tokens > 0 ? 4 : 0
                  }}
                  title={`${day.date}: ${day.tokens.toLocaleString()} tokens, $${day.cost.toFixed(4)}`}
                />
                <div style={{ fontSize: 9, color: 'var(--text3)', transform: 'rotate(-45deg)', transformOrigin: 'top left' }}>
                  {day.date.slice(5)}
                </div>
              </div>
            )
          })}
        </div>
      </Card>

      {/* USAGE BREAKDOWN */}
      <Card style={{ padding: 20, marginBottom: 28 }}>
        <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 16 }}>Usage Breakdown</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 14 }}>
          {[
            { label: 'AI Queries', value: aiUsage.filter(h => h.description?.includes('query')).length, icon: '🤖' },
            { label: 'Reports Generated', value: aiUsage.filter(h => h.description?.includes('report')).length, icon: '📊' },
            { label: 'Forecasts Run', value: aiUsage.filter(h => h.description?.includes('forecast')).length, icon: '📈' },
            { label: 'Cache Builds', value: aiUsage.filter(h => h.description?.includes('cache')).length, icon: '⚡' },
          ].map((item, i) => (
            <div key={i} style={{ padding: 14, background: 'var(--bg2)', borderRadius: 'var(--r-md)', border: '1px solid var(--border)' }}>
              <div style={{ fontSize: 24, marginBottom: 6 }}>{item.icon}</div>
              <div style={{ fontSize: 20, fontWeight: 600, marginBottom: 2 }}>{item.value}</div>
              <div style={{ fontSize: 11, color: 'var(--text3)' }}>{item.label}</div>
            </div>
          ))}
        </div>
      </Card>

      {/* USAGE HISTORY TABLE */}
      <Card style={{ padding: 20 }}>
        <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 16 }}>Recent Usage History</div>
        <div style={{ overflow: 'auto' }}>
          <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)' }}>
                <th style={{ textAlign: 'left', padding: '8px 12px', fontWeight: 600, color: 'var(--text2)' }}>Date & Time</th>
                <th style={{ textAlign: 'left', padding: '8px 12px', fontWeight: 600, color: 'var(--text2)' }}>Type</th>
                <th style={{ textAlign: 'left', padding: '8px 12px', fontWeight: 600, color: 'var(--text2)' }}>Model</th>
                <th style={{ textAlign: 'right', padding: '8px 12px', fontWeight: 600, color: 'var(--text2)' }}>Tokens</th>
                <th style={{ textAlign: 'right', padding: '8px 12px', fontWeight: 600, color: 'var(--text2)' }}>Cost</th>
                <th style={{ textAlign: 'left', padding: '8px 12px', fontWeight: 600, color: 'var(--text2)' }}>Description</th>
              </tr>
            </thead>
            <tbody>
              {history.slice(0, 50).map((item, i) => (
                <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '10px 12px', color: 'var(--text3)', fontFamily: 'var(--mono)', fontSize: 11 }}>
                    {new Date(item.created_at).toLocaleString()}
                  </td>
                  <td style={{ padding: '10px 12px' }}>
                    <Badge color={item.usage_type === 'ai_tokens' ? 'blue' : 'green'}>
                      {item.usage_type === 'ai_tokens' ? 'AI' : 'DB'}
                    </Badge>
                  </td>
                  <td style={{ padding: '10px 12px', fontFamily: 'var(--mono)', fontSize: 11 }}>
                    {item.model || '-'}
                  </td>
                  <td style={{ padding: '10px 12px', textAlign: 'right', fontFamily: 'var(--mono)' }}>
                    {item.tokens_used?.toLocaleString() || '-'}
                  </td>
                  <td style={{ padding: '10px 12px', textAlign: 'right', fontFamily: 'var(--mono)', color: 'var(--blue)' }}>
                    ${(item.amount || 0).toFixed(4)}
                  </td>
                  <td style={{ padding: '10px 12px', color: 'var(--text3)', fontSize: 11 }}>
                    {item.description || '-'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {/* PRICING INFO */}
      <Card style={{ padding: 16, marginTop: 28, background: 'rgba(79,142,247,0.05)', border: '1px solid rgba(79,142,247,0.2)' }}>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, color: 'var(--blue)' }}>Pricing Information</div>
        <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.6 }}>
          • AI Credits: $0.001 per 1,000 tokens (Gemini), $0.0003 per 1,000 tokens (DeepSeek)
          <br />
          • Database Rows: $0.001 per 1,000 rows synced
          <br />
          • All usage is tracked in real-time and deducted from your credit balance
        </div>
      </Card>
    </div>
  )
}