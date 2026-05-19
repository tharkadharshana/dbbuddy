import React, { useState, useEffect } from 'react'
import { fetchBillingUsage } from '../utils/api'
import { Card, Spinner, Badge } from '../components/UI'

function UsageBar({ label, used, limit, pct, decimals = 0 }) {
  const color = pct >= 100 ? 'var(--red)' : pct >= 80 ? 'var(--amber)' : 'var(--blue)'
  const fmt = (n) => Number(n).toLocaleString(undefined, { maximumFractionDigits: decimals })
  return (
    <div style={{ flex: 1, minWidth: 200 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: 'var(--text3)', marginBottom: 5 }}>
        <span>{label}</span>
        <span>{fmt(used)} / {fmt(limit)}</span>
      </div>
      <div style={{ height: 8, borderRadius: 4, background: 'var(--bg3)', overflow: 'hidden' }}>
        <div style={{
          height: '100%', width: `${Math.min(pct, 100)}%`,
          background: color, borderRadius: 4, transition: 'width .3s',
        }} />
      </div>
      <div style={{ fontSize: 11, color, marginTop: 4 }}>{pct}% used</div>
    </div>
  )
}

export default function UsagePage({ sub }) {
  const [usage, setUsage]         = useState(null)
  const [usageLoading, setUsageLoading] = useState(true)

  useEffect(() => {
    fetchBillingUsage()
      .then(u => setUsage(u))
      .catch(() => {})
      .finally(() => setUsageLoading(false))
  }, [])

  const history = usage?.history || []

  if (!sub) return (
    <div style={{ padding: 32, display: 'flex', alignItems: 'center', gap: 10, color: 'var(--text3)' }}>
      <Spinner /> Loading usage data…
    </div>
  )

  return (
    <div style={{ maxWidth: 1000, padding: '24px 24px 60px' }}>
      <div style={{ marginBottom: 28 }}>
        <div style={{ fontSize: 20, fontWeight: 700, marginBottom: 4 }}>Usage</div>
        <div style={{ fontSize: 13, color: 'var(--text3)' }}>
          Current billing period usage and recent AI activity
        </div>
      </div>

      {/* Subscription summary */}
      {sub && sub.status !== 'no_subscription' && (
        <Card style={{ padding: 20, marginBottom: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 18, flexWrap: 'wrap' }}>
            <div style={{ fontSize: 15, fontWeight: 600 }}>{sub.plan_name}</div>
            <span style={{
              fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 99,
              background: sub.status === 'trial' ? 'rgba(245,166,35,0.15)' : 'rgba(34,197,94,0.15)',
              color: sub.status === 'trial' ? 'var(--amber)' : 'var(--green)',
            }}>
              {sub.status === 'trial' ? `Trial · ${sub.trial_days_remaining}d left` : 'Active'}
            </span>
            <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text3)' }}>
              Period: {sub.period_start} → {sub.period_end}
            </span>
          </div>

          <div style={{ display: 'flex', gap: 32, flexWrap: 'wrap' }}>
            <UsageBar
              label="AI Credits"
              used={sub.ai_base_used}
              limit={sub.ai_total_available}
              pct={sub.usage_pct_ai}
              decimals={1}
            />
            <UsageBar
              label="DB Rows"
              used={sub.db_base_used}
              limit={sub.db_total_available}
              pct={sub.usage_pct_db}
            />
          </div>

          {(sub.ai_addon_balance > 0 || sub.db_addon_balance > 0) && (
            <div style={{ marginTop: 16, padding: '10px 14px', background: 'var(--bg3)', borderRadius: 8, fontSize: 12, color: 'var(--text2)' }}>
              Add-on balance: {sub.ai_addon_balance > 0 && <span style={{ marginRight: 12 }}>⚡ {sub.ai_addon_balance.toLocaleString()} AI credits</span>}
              {sub.db_addon_balance > 0 && <span>🗄 {sub.db_addon_balance.toLocaleString()} DB rows</span>}
            </div>
          )}
        </Card>
      )}

      {(!sub || sub.status === 'no_subscription') && (
        <Card style={{ padding: 20, marginBottom: 20, textAlign: 'center', color: 'var(--text3)' }}>
          No active subscription. Visit Billing to choose a plan.
        </Card>
      )}

      {/* AI usage history */}
      <Card style={{ padding: 20 }}>
        <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 16 }}>Recent AI Usage</div>
        {usageLoading ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text3)', fontSize: 13 }}>
            <Spinner /> Loading…
          </div>
        ) : history.length === 0 ? (
          <div style={{ fontSize: 13, color: 'var(--text3)' }}>No AI usage recorded yet.</div>
        ) : (
          <div style={{ overflow: 'auto' }}>
            <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border)' }}>
                  {['Date', 'Model', 'Tokens', 'Credits Charged'].map(h => (
                    <th key={h} style={{
                      textAlign: h === 'Tokens' || h === 'Credits Charged' ? 'right' : 'left',
                      padding: '8px 12px', fontWeight: 600, color: 'var(--text2)',
                    }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {history.map((item, i) => (
                  <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={{ padding: '10px 12px', color: 'var(--text3)', fontFamily: 'var(--mono)', fontSize: 11 }}>
                      {item.created_at}
                    </td>
                    <td style={{ padding: '10px 12px' }}>
                      <Badge color="blue">{item.model || '—'}</Badge>
                    </td>
                    <td style={{ padding: '10px 12px', textAlign: 'right', fontFamily: 'var(--mono)' }}>
                      {(item.tokens || 0).toLocaleString()}
                    </td>
                    <td style={{ padding: '10px 12px', textAlign: 'right', fontFamily: 'var(--mono)', color: 'var(--blue)' }}>
                      {(item.credits_charged || 0).toFixed(4)}
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
