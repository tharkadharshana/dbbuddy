import React, { useState, useEffect } from 'react'
import { fetchBillingUsage } from '../utils/api'
import { Card, Spinner, Badge } from '../components/UI'

const TDM = 10_000
const fmtTok = (raw) => {
  const n = (raw || 0) * TDM
  if (n >= 1_000_000) return `${parseFloat((n / 1_000_000).toFixed(2))}M`
  if (n >= 1_000)     return `${parseFloat((n / 1_000).toFixed(1))}K`
  return Math.round(n).toLocaleString()
}

const OP_LABELS = {
  llm:                 'AI Query',
  nl_query_rows:       'Query Results',
  prebuilt_template:   'Analytics Template',
  forecast:            'Forecasting',
  anomaly_detection:   'Anomaly Detection',
  rfm_analysis:        'RFM Analysis',
  cohort_analysis:     'Cohort Analysis',
  basket_analysis:     'Basket Analysis',
  growth_metrics:      'Growth Metrics',
  employee_performance:'Employee Performance',
  product_velocity:    'Product Velocity',
  payment_breakdown:   'Payment Analysis',
  location_comparison: 'Location Comparison',
}

function UsageBar({ label, used, limit, pct, decimals = 2, color, isTok = false }) {
  const barColor = color || (pct >= 100 ? 'var(--red)' : pct >= 80 ? 'var(--amber)' : 'var(--blue)')
  const fmt = (n) => isTok ? fmtTok(n) : Number(n).toLocaleString(undefined, { maximumFractionDigits: decimals })
  return (
    <div style={{ flex: 1, minWidth: 200 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: 'var(--text3)', marginBottom: 5 }}>
        <span>{label}</span>
        <span>{fmt(used)} / {fmt(limit)}</span>
      </div>
      <div style={{ height: 8, borderRadius: 4, background: 'var(--bg3)', overflow: 'hidden' }}>
        <div style={{
          height: '100%', width: `${Math.min(pct, 100)}%`,
          background: barColor, borderRadius: 4, transition: 'width .3s',
        }} />
      </div>
      <div style={{ fontSize: 11, color: barColor, marginTop: 4 }}>{pct}% used</div>
    </div>
  )
}

export default function UsagePage({ sub }) {
  const [usage, setUsage]               = useState(null)
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
          Token usage for the current billing period
        </div>
      </div>

      {/* Unified token bar */}
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

          <UsageBar
            label="Tokens"
            used={sub.tokens_used ?? 0}
            limit={sub.tokens_total_available ?? sub.tokens_limit ?? 0}
            pct={sub.tokens_pct ?? 0}
            isTok
          />

          {/* Breakdown detail */}
          <div style={{ marginTop: 16, padding: '12px 14px', background: 'var(--bg3)', borderRadius: 8, fontSize: 12 }}>
            <div style={{ fontWeight: 600, color: 'var(--text2)', marginBottom: 8 }}>Token breakdown</div>
            <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', color: 'var(--text3)' }}>
              <span>Plan limit: <b style={{ color: 'var(--text)' }}>{fmtTok(sub.tokens_limit ?? 0)}</b></span>
              {(sub.tokens_addon_balance ?? 0) > 0 && (
                <span>Add-on balance: <b style={{ color: 'var(--green)' }}>+{fmtTok(sub.tokens_addon_balance)}</b></span>
              )}
              <span>Tokens used: <b style={{ color: 'var(--blue)' }}>{fmtTok(sub.tokens_used ?? 0)}</b></span>
              <span>Remaining: <b style={{ color: 'var(--green)' }}>
                {fmtTok(Math.max(0, (sub.tokens_total_available ?? 0) - (sub.tokens_used ?? 0)))}
              </b></span>
            </div>
          </div>
        </Card>
      )}

      {(!sub || sub.status === 'no_subscription') && (
        <Card style={{ padding: 20, marginBottom: 20, textAlign: 'center', color: 'var(--text3)' }}>
          No active subscription. Visit Billing to choose a plan.
        </Card>
      )}

      {/* Token usage history */}
      <Card style={{ padding: 20 }}>
        <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 16 }}>Recent Activity</div>
        {usageLoading ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text3)', fontSize: 13 }}>
            <Spinner /> Loading…
          </div>
        ) : history.length === 0 ? (
          <div style={{ fontSize: 13, color: 'var(--text3)' }}>No usage recorded yet.</div>
        ) : (
          <div style={{ overflow: 'auto' }}>
            <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border)' }}>
                  {['Date', 'Operation', 'Tokens', 'AI Tokens', 'Rows'].map(h => (
                    <th key={h} style={{
                      textAlign: ['Tokens', 'AI Tokens', 'Rows'].includes(h) ? 'right' : 'left',
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
                      <Badge color="blue">{OP_LABELS[item.operation_type] || item.operation_type}</Badge>
                    </td>
                    <td style={{ padding: '10px 12px', textAlign: 'right', fontFamily: 'var(--mono)', color: 'var(--blue)', fontWeight: 600 }}>
                      {Number(item.tokens || 0).toFixed(4)}
                    </td>
                    <td style={{ padding: '10px 12px', textAlign: 'right', fontFamily: 'var(--mono)', color: 'var(--text3)' }}>
                      {(item.llm_tokens || 0).toLocaleString()}
                    </td>
                    <td style={{ padding: '10px 12px', textAlign: 'right', fontFamily: 'var(--mono)', color: 'var(--text3)' }}>
                      {(item.rows_charged || 0).toLocaleString()}
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
