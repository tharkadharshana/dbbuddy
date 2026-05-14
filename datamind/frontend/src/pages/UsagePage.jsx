import React, { useState, useEffect } from 'react'
import { fetchSubscription, fetchBillingUsage } from '../utils/api'
import { Card, Spinner, Badge } from '../components/UI'

export default function UsagePage() {
  const [sub, setSub]         = useState(null)
  const [history, setHistory] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      fetchSubscription().catch(() => null),
      fetchBillingUsage().catch(() => ({ history: [] })),
    ]).then(([s, u]) => {
      setSub(s)
      setHistory(u?.history || [])
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  if (loading) return (
    <div style={{ padding: 32, display: 'flex', alignItems: 'center', gap: 10, color: 'var(--text3)' }}>
      <Spinner /> Loading usage…
    </div>
  )

  const status  = sub?.status || 'no_subscription'
  const aiUsed  = sub?.ai_credits_used  || 0
  const aiLimit = sub?.ai_credits_limit || 0
  const aiLeft  = Math.max(0, aiLimit - aiUsed)
  const totalTokens = history.reduce((s, h) => s + (h.tokens || 0), 0)

  // Group by day for chart
  const byDay = {}
  history.forEach(h => {
    const day = h.created_at?.split('T')[0]
    if (!day) return
    if (!byDay[day]) byDay[day] = { tokens: 0 }
    byDay[day].tokens += h.tokens || 0
  })
  const dailyData = Object.entries(byDay)
    .map(([date, d]) => ({ date, ...d }))
    .sort((a, b) => a.date.localeCompare(b.date))
    .slice(-30)

  const statusLabel = {
    trial:           { text: 'Trial',    color: 'var(--blue)'  },
    active:          { text: 'Pro',      color: 'var(--green)' },
    expired:         { text: 'Expired',  color: 'var(--red)'   },
    cancelled:       { text: 'Cancelled',color: 'var(--text3)' },
    no_subscription: { text: 'No Plan',  color: 'var(--red)'   },
  }[status] || { text: status, color: 'var(--text2)' }

  return (
    <div style={{ maxWidth: 1000, padding: '24px 24px 60px' }}>
      <div style={{ marginBottom: 24 }}>
        <div style={{ fontSize: 20, fontWeight: 700, marginBottom: 4 }}>Usage</div>
        <div style={{ fontSize: 13, color: 'var(--text3)' }}>AI credits and usage activity for your current billing period</div>
      </div>

      {/* Subscription status pill */}
      <div style={{ marginBottom: 22 }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, padding: '5px 14px', borderRadius: 99, background: 'var(--bg2)', border: '1px solid var(--border)', fontSize: 12, fontWeight: 600 }}>
          <span style={{ width: 7, height: 7, borderRadius: '50%', background: statusLabel.color, display: 'inline-block' }} />
          DataMind {statusLabel.text}
          {sub?.ai_credits_limit > 0 && (
            <span style={{ color: 'var(--text3)', fontWeight: 400 }}>
              · {aiLeft.toLocaleString()} credits left
            </span>
          )}
        </span>
      </div>

      {/* Stat cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 14, marginBottom: 28 }}>
        <Card style={{ padding: 18, background: 'linear-gradient(135deg, rgba(79,142,247,0.1), rgba(167,139,250,0.1))' }}>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.5px' }}>AI Credits Used</div>
          <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--blue)' }}>{aiUsed.toLocaleString()}</div>
          {aiLimit > 0 && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>of {aiLimit.toLocaleString()} limit</div>}
        </Card>

        <Card style={{ padding: 18 }}>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.5px' }}>Credits Remaining</div>
          <div style={{ fontSize: 28, fontWeight: 700, color: aiLeft === 0 ? 'var(--red)' : 'var(--text)' }}>{aiLeft.toLocaleString()}</div>
          {sub?.addon_ai_balance > 0 && (
            <div style={{ fontSize: 11, color: 'var(--green)', marginTop: 4 }}>+{sub.addon_ai_balance} from add-ons</div>
          )}
        </Card>

        <Card style={{ padding: 18 }}>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.5px' }}>Total Tokens</div>
          <div style={{ fontSize: 28, fontWeight: 700 }}>{totalTokens.toLocaleString()}</div>
        </Card>

        <Card style={{ padding: 18 }}>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.5px' }}>AI Calls</div>
          <div style={{ fontSize: 28, fontWeight: 700 }}>{history.length}</div>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>This period</div>
        </Card>
      </div>

      {/* Daily usage chart */}
      {dailyData.length > 0 && (
        <Card style={{ padding: 20, marginBottom: 28 }}>
          <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 16 }}>Daily Token Usage (Last 30 Days)</div>
          <div style={{ height: 180, display: 'flex', alignItems: 'flex-end', gap: 4 }}>
            {dailyData.map((day, i) => {
              const maxTokens = Math.max(...dailyData.map(d => d.tokens), 1)
              const height = (day.tokens / maxTokens) * 100
              return (
                <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
                  <div
                    style={{ width: '100%', height: `${height}%`, background: 'linear-gradient(to top, var(--blue), var(--purple))', borderRadius: '4px 4px 0 0', minHeight: day.tokens > 0 ? 4 : 0 }}
                    title={`${day.date}: ${day.tokens.toLocaleString()} tokens`}
                  />
                  <div style={{ fontSize: 9, color: 'var(--text3)', transform: 'rotate(-45deg)', transformOrigin: 'top left' }}>
                    {day.date.slice(5)}
                  </div>
                </div>
              )
            })}
          </div>
        </Card>
      )}

      {/* Usage history table */}
      <Card style={{ padding: 20 }}>
        <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 16 }}>AI Call History</div>
        {history.length === 0 ? (
          <div style={{ color: 'var(--text3)', fontSize: 13, padding: '20px 0' }}>No AI calls recorded yet.</div>
        ) : (
          <div style={{ overflow: 'auto' }}>
            <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border)' }}>
                  <th style={{ textAlign: 'left', padding: '8px 12px', fontWeight: 600, color: 'var(--text2)' }}>Date & Time</th>
                  <th style={{ textAlign: 'left', padding: '8px 12px', fontWeight: 600, color: 'var(--text2)' }}>Type</th>
                  <th style={{ textAlign: 'right', padding: '8px 12px', fontWeight: 600, color: 'var(--text2)' }}>Tokens</th>
                  <th style={{ textAlign: 'right', padding: '8px 12px', fontWeight: 600, color: 'var(--text2)' }}>Credits</th>
                  <th style={{ textAlign: 'left', padding: '8px 12px', fontWeight: 600, color: 'var(--text2)' }}>Endpoint</th>
                </tr>
              </thead>
              <tbody>
                {history.slice(0, 50).map((item, i) => (
                  <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={{ padding: '10px 12px', color: 'var(--text3)', fontFamily: 'var(--mono)', fontSize: 11 }}>
                      {new Date(item.created_at).toLocaleString()}
                    </td>
                    <td style={{ padding: '10px 12px' }}>
                      <Badge color="blue">AI</Badge>
                    </td>
                    <td style={{ padding: '10px 12px', textAlign: 'right', fontFamily: 'var(--mono)' }}>
                      {(item.tokens || 0).toLocaleString()}
                    </td>
                    <td style={{ padding: '10px 12px', textAlign: 'right', fontFamily: 'var(--mono)', color: 'var(--blue)' }}>
                      {(item.credits_charged || 0).toFixed(2)}
                    </td>
                    <td style={{ padding: '10px 12px', color: 'var(--text3)', fontSize: 11 }}>
                      {item.endpoint || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
