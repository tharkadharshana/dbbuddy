import React, { useState, useEffect } from 'react'
import { fetchBillingPlans, fetchSubscription, fetchBillingUsage, subscribeToPlan, purchaseAddon } from '../utils/api'
import { Spinner } from '../components/UI'

// ── Plan definitions ──────────────────────────────────────────────────────────
const PLAN_FEATURES = {
  Starter: [
    { text: '500 Tokens / month',        ok: true  },
    { text: 'Ask Your Data (AI)',         ok: true  },
    { text: 'All Analytics',             ok: true  },
    { text: 'All Reports',               ok: true  },
    { text: 'Forecasting & Anomalies',   ok: false },
    { text: 'Priority Support',          ok: false },
    { text: '1 Month data history',      ok: true  },
  ],
  Growth: [
    { text: '1,500 Tokens / month',      ok: true  },
    { text: 'Ask Your Data (AI)',         ok: true  },
    { text: 'All Analytics',             ok: true  },
    { text: 'All Reports',               ok: true  },
    { text: 'Forecasting & Anomalies',   ok: true  },
    { text: 'Priority Support',          ok: false },
    { text: '3 Months data history',     ok: true  },
  ],
  Pro: [
    { text: '10,000 Tokens / month',     ok: true  },
    { text: 'Ask Your Data (AI)',         ok: true  },
    { text: 'All Analytics',             ok: true  },
    { text: 'All Reports',               ok: true  },
    { text: 'Forecasting & Anomalies',   ok: true  },
    { text: 'Priority Support',          ok: true  },
    { text: 'External API integrations', ok: true  },
    { text: 'Web widget',                ok: true  },
    { text: '1 Year data history',       ok: true  },
  ],
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

// ── Shared components ─────────────────────────────────────────────────────────
function UsageBar({ label, used, limit, pct, addon = 0, decimals = 2 }) {
  const color = pct >= 100 ? 'var(--red)' : pct >= 80 ? 'var(--amber)' : 'var(--blue)'
  const fmt = (n) => Number(n).toLocaleString(undefined, { maximumFractionDigits: decimals })
  return (
    <div style={{ flex: 1 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 12, color: 'var(--text3)', marginBottom: 5 }}>
        <span>{label}</span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          {fmt(used)} / {fmt(limit)}
          {addon > 0 && <span style={{ color: 'var(--green)', fontWeight: 600 }}>+{fmt(addon)}</span>}
        </span>
      </div>
      <div style={{ height: 8, borderRadius: 4, background: 'var(--bg3)', overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${Math.min(pct, 100)}%`, background: color, borderRadius: 4, transition: 'width .3s' }} />
      </div>
      <div style={{ fontSize: 11, color, marginTop: 4 }}>{pct}% used</div>
    </div>
  )
}

function ConfirmModal({ plan, onConfirm, onCancel, loading }) {
  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }}>
      <div style={{ background: 'var(--bg1)', border: '1px solid var(--border)', borderRadius: 12, padding: 28, width: 360, maxWidth: '90vw' }}>
        <div style={{ fontSize: 17, fontWeight: 700, marginBottom: 10 }}>Confirm Plan Change</div>
        <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 22, lineHeight: 1.5 }}>
          You're switching to the <strong>{plan.name}</strong> plan (${(plan.price_cents / 100).toFixed(2)}/month).
          Your current subscription will be cancelled immediately.
        </div>
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button onClick={onCancel} disabled={loading} style={{ padding: '8px 18px', borderRadius: 8, border: '1px solid var(--border)', background: 'transparent', color: 'var(--text2)', cursor: 'pointer', fontSize: 13 }}>Cancel</button>
          <button onClick={onConfirm} disabled={loading} style={{ padding: '8px 18px', borderRadius: 8, border: 'none', background: 'var(--blue)', color: '#fff', cursor: 'pointer', fontSize: 13, fontWeight: 600, opacity: loading ? 0.6 : 1 }}>
            {loading ? 'Processing…' : 'Confirm'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function BillingPage({ onSubChange }) {
  const [tab, setTab]             = useState('plans')
  const [plans, setPlans]         = useState([])
  const [sub, setSub]             = useState(null)
  const [usage, setUsage]         = useState(null)
  const [loading, setLoading]     = useState(true)
  const [usageLoading, setUsageLoading] = useState(false)
  const [confirmPlan, setConfirmPlan]   = useState(null)
  const [subscribing, setSubscribing]   = useState(false)
  const [addonQty, setAddonQty]   = useState({ tokens: 0 })
  const [addonLoading, setAddonLoading] = useState(false)
  const [toast, setToast]         = useState(null)

  useEffect(() => { loadAll() }, [])

  async function loadAll() {
    setLoading(true)
    try {
      const [plansData, subData] = await Promise.all([fetchBillingPlans(), fetchSubscription()])
      setPlans(plansData.plans || [])
      setSub(subData)
    } catch { /* silent */ }
    setLoading(false)
  }

  async function loadUsage() {
    setUsageLoading(true)
    try { setUsage(await fetchBillingUsage()) } catch { /* silent */ }
    setUsageLoading(false)
  }

  function switchTab(t) {
    setTab(t)
    if (t === 'usage') loadUsage()
    if (t === 'plans') loadAll()
  }

  function showToast(msg, type = 'success') {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 3500)
  }

  async function handleSubscribe() {
    if (!confirmPlan) return
    setSubscribing(true)
    try {
      await subscribeToPlan(confirmPlan.id)
      showToast(`Switched to ${confirmPlan.name} plan.`)
      setConfirmPlan(null)
      await loadAll()
      onSubChange && onSubChange()
    } catch (e) {
      showToast(e?.response?.data?.detail || 'Subscription failed.', 'error')
    }
    setSubscribing(false)
  }

  async function handlePurchaseAddons() {
    if (!addonQty.tokens) return
    setAddonLoading(true)
    try {
      await purchaseAddon('ai_credits', addonQty.tokens)
      showToast('Add-ons purchased successfully.')
      setAddonQty({ tokens: 0 })
      await loadAll()
      onSubChange && onSubChange()
    } catch (e) {
      showToast(e?.response?.data?.detail || 'Purchase failed.', 'error')
    }
    setAddonLoading(false)
  }

  function ctaLabel(plan) {
    if (!sub || sub.status === 'no_subscription') return 'Start free trial'
    if (sub.plan_id === plan.id) return sub.status === 'trial' ? `Trial · ${sub.trial_days_remaining}d left` : 'Current Plan'
    const planIdx = plans.findIndex(p => p.id === plan.id)
    const subIdx  = plans.findIndex(p => p.id === sub.plan_id)
    return planIdx > subIdx ? 'Upgrade' : 'Switch Plan'
  }

  function ctaDisabled(plan) {
    return sub?.plan_id === plan.id && sub?.status === 'active'
  }

  const addonTotal = addonQty.tokens * 1

  if (loading) return (
    <div style={{ padding: 40, display: 'flex', alignItems: 'center', gap: 10, color: 'var(--text3)' }}>
      <Spinner /> Loading billing…
    </div>
  )

  return (
    <div style={{ maxWidth: 960, padding: '28px 24px 60px', position: 'relative' }}>

      {/* Toast */}
      {toast && (
        <div style={{ position: 'fixed', top: 20, right: 24, zIndex: 2000, padding: '10px 18px', borderRadius: 8, fontSize: 13, fontWeight: 500, background: toast.type === 'error' ? 'var(--red)' : 'var(--green)', color: '#fff', boxShadow: '0 4px 16px rgba(0,0,0,0.25)' }}>
          {toast.msg}
        </div>
      )}

      {confirmPlan && (
        <ConfirmModal plan={confirmPlan} onConfirm={handleSubscribe} onCancel={() => setConfirmPlan(null)} loading={subscribing} />
      )}

      {/* Header */}
      <div style={{ marginBottom: 24 }}>
        <div style={{ fontSize: 20, fontWeight: 700, marginBottom: 4 }}>Billing</div>
        <div style={{ fontSize: 13, color: 'var(--text3)' }}>Manage your plan, track usage, and purchase add-ons</div>
      </div>

      {/* Tabs */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 24, borderBottom: '1px solid var(--border)', paddingBottom: 0 }}>
        {[['plans', 'Plans & Add-ons'], ['usage', 'Usage']].map(([id, label]) => (
          <button key={id} onClick={() => switchTab(id)} style={{
            padding: '8px 18px', background: 'none', border: 'none', cursor: 'pointer',
            fontSize: 13, fontWeight: 600,
            color: tab === id ? 'var(--blue)' : 'var(--text3)',
            borderBottom: `2px solid ${tab === id ? 'var(--blue)' : 'transparent'}`,
            marginBottom: -1,
          }}>{label}</button>
        ))}
      </div>

      {/* ── PLANS TAB ── */}
      {tab === 'plans' && (
        <>
          {/* Current usage strip */}
          {sub && ['trial', 'active'].includes(sub.status) && (
            <div style={{ background: 'var(--bg1)', border: '1px solid var(--border)', borderRadius: 10, padding: '14px 18px', marginBottom: 28, display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 16 }}>
              <div>
                <span style={{ fontWeight: 600, fontSize: 14 }}>{sub.plan_name}</span>
                <span style={{ marginLeft: 8, fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 99, background: sub.status === 'trial' ? 'rgba(245,166,35,0.15)' : 'rgba(34,197,94,0.15)', color: sub.status === 'trial' ? 'var(--amber)' : 'var(--green)' }}>
                  {sub.status === 'trial' ? `Trial · ${sub.trial_days_remaining}d left` : 'Active'}
                </span>
              </div>
              <div style={{ fontSize: 12, color: 'var(--text3)', marginLeft: 'auto' }}>Renews {sub.period_end}</div>
              <div style={{ width: '100%' }}>
                <UsageBar label="Tokens" used={sub.tokens_used ?? 0} limit={sub.tokens_total_available ?? sub.tokens_limit ?? 0} pct={sub.tokens_pct ?? 0} addon={sub.tokens_addon_balance ?? 0} />
              </div>
            </div>
          )}

          {/* Plan cards */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 16, marginBottom: 40 }}>
            {plans.map(plan => {
              const features  = PLAN_FEATURES[plan.name] || []
              const isCurrent = sub?.plan_id === plan.id && ['trial', 'active'].includes(sub?.status)
              return (
                <div key={plan.id} style={{ background: 'var(--bg1)', border: `1px solid ${isCurrent ? 'var(--blue)' : 'var(--border)'}`, borderRadius: 12, padding: 22, display: 'flex', flexDirection: 'column', position: 'relative' }}>
                  {isCurrent && (
                    <div style={{ position: 'absolute', top: -1, right: 16, background: 'var(--blue)', color: '#fff', fontSize: 10, fontWeight: 700, padding: '3px 10px', borderRadius: '0 0 6px 6px', letterSpacing: '.05em' }}>CURRENT</div>
                  )}
                  <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>{plan.name}</div>
                  <div style={{ marginBottom: 16 }}>
                    <span style={{ fontSize: 28, fontWeight: 800 }}>${(plan.price_cents / 100).toFixed(0)}</span>
                    <span style={{ fontSize: 13, color: 'var(--text3)' }}>/mo</span>
                  </div>
                  <ul style={{ listStyle: 'none', padding: 0, margin: '0 0 20px', flex: 1 }}>
                    {features.map((f, i) => (
                      <li key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: f.ok ? 'var(--text)' : 'var(--text3)', marginBottom: 8 }}>
                        <span style={{ color: f.ok ? 'var(--green)' : 'var(--text3)', flexShrink: 0 }}>{f.ok ? '✓' : '–'}</span>
                        {f.text}
                      </li>
                    ))}
                  </ul>
                  <button
                    disabled={ctaDisabled(plan)}
                    onClick={() => !ctaDisabled(plan) && setConfirmPlan(plan)}
                    style={{ padding: '9px 0', borderRadius: 8, border: 'none', cursor: ctaDisabled(plan) ? 'default' : 'pointer', background: isCurrent ? 'var(--bg3)' : 'var(--blue)', color: isCurrent ? 'var(--text3)' : '#fff', fontWeight: 600, fontSize: 13, opacity: ctaDisabled(plan) ? 0.7 : 1 }}
                  >
                    {ctaLabel(plan)}
                  </button>
                </div>
              )
            })}
          </div>

          {/* Add-ons */}
          <div style={{ background: 'var(--bg1)', border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden' }}>
            <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', fontWeight: 600, fontSize: 15 }}>Add-ons</div>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border)' }}>
                  {['Feature', 'Price', 'Quantity', 'Subtotal'].map(h => (
                    <th key={h} style={{ padding: '10px 16px', textAlign: h === 'Subtotal' ? 'right' : 'left', fontWeight: 600, color: 'var(--text3)' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                <tr style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '12px 16px' }}>
                    <span style={{ marginRight: 6 }}>⚡</span>
                    <span style={{ fontWeight: 500 }}>Tokens</span>
                    <span style={{ marginLeft: 8, fontSize: 11, color: 'var(--text3)' }}>50 Tokens per pack · rolls over</span>
                  </td>
                  <td style={{ padding: '12px 16px', color: 'var(--text3)' }}>$1 per 50 Tokens</td>
                  <td style={{ padding: '12px 16px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <button onClick={() => setAddonQty(q => ({ ...q, tokens: Math.max(0, q.tokens - 1) }))} style={{ width: 26, height: 26, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg3)', cursor: 'pointer', fontSize: 15, color: 'var(--text2)' }}>−</button>
                      <span style={{ width: 24, textAlign: 'center', fontWeight: 600 }}>{addonQty.tokens}</span>
                      <button onClick={() => setAddonQty(q => ({ ...q, tokens: q.tokens + 1 }))} style={{ width: 26, height: 26, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg3)', cursor: 'pointer', fontSize: 15, color: 'var(--text2)' }}>+</button>
                    </div>
                  </td>
                  <td style={{ padding: '12px 16px', textAlign: 'right', fontWeight: 600 }}>${addonQty.tokens}.00</td>
                </tr>
                <tr>
                  <td colSpan={3} style={{ padding: '12px 16px', fontWeight: 600 }}>Total</td>
                  <td style={{ padding: '12px 16px', textAlign: 'right', fontWeight: 700, fontSize: 15 }}>${addonTotal}.00</td>
                </tr>
              </tbody>
            </table>
            <div style={{ padding: '12px 18px', borderTop: '1px solid var(--border)', display: 'flex', justifyContent: 'flex-end' }}>
              <button onClick={handlePurchaseAddons} disabled={addonTotal === 0 || addonLoading} style={{ padding: '9px 22px', borderRadius: 8, border: 'none', background: 'var(--blue)', color: '#fff', fontWeight: 600, fontSize: 13, cursor: addonTotal === 0 || addonLoading ? 'default' : 'pointer', opacity: addonTotal === 0 || addonLoading ? 0.5 : 1 }}>
                {addonLoading ? 'Processing…' : `Purchase Add-ons — $${addonTotal}.00`}
              </button>
            </div>
          </div>
        </>
      )}

      {/* ── USAGE TAB ── */}
      {tab === 'usage' && (
        <>
          {/* Token balance card */}
          {sub && sub.status !== 'no_subscription' && (
            <div style={{ background: 'var(--bg1)', border: '1px solid var(--border)', borderRadius: 10, padding: '20px 22px', marginBottom: 20 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 18, flexWrap: 'wrap' }}>
                <div style={{ fontSize: 15, fontWeight: 600 }}>{sub.plan_name}</div>
                <span style={{ fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 99, background: sub.status === 'trial' ? 'rgba(245,166,35,0.15)' : 'rgba(34,197,94,0.15)', color: sub.status === 'trial' ? 'var(--amber)' : 'var(--green)' }}>
                  {sub.status === 'trial' ? `Trial · ${sub.trial_days_remaining}d left` : 'Active'}
                </span>
                <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text3)' }}>Period: {sub.period_start} → {sub.period_end}</span>
              </div>

              <UsageBar label="Tokens" used={sub.tokens_used ?? 0} limit={sub.tokens_total_available ?? sub.tokens_limit ?? 0} pct={sub.tokens_pct ?? 0} addon={sub.tokens_addon_balance ?? 0} />

              <div style={{ marginTop: 14, padding: '10px 14px', background: 'var(--bg3)', borderRadius: 8, fontSize: 12, display: 'flex', gap: 24, flexWrap: 'wrap', color: 'var(--text3)' }}>
                <span>Plan limit: <b style={{ color: 'var(--text)' }}>{(sub.tokens_limit ?? 0).toLocaleString()}</b></span>
                {(sub.tokens_addon_balance ?? 0) > 0 && <span>Add-on: <b style={{ color: 'var(--green)' }}>+{Number(sub.tokens_addon_balance).toLocaleString(undefined, { maximumFractionDigits: 1 })}</b></span>}
                <span>Used: <b style={{ color: 'var(--blue)' }}>{Number(sub.tokens_used ?? 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}</b></span>
                <span>Remaining: <b style={{ color: 'var(--green)' }}>{Math.max(0, (sub.tokens_total_available ?? 0) - (sub.tokens_used ?? 0)).toLocaleString(undefined, { maximumFractionDigits: 2 })}</b></span>
              </div>
            </div>
          )}

          {(!sub || sub.status === 'no_subscription') && (
            <div style={{ padding: 20, marginBottom: 20, textAlign: 'center', color: 'var(--text3)', background: 'var(--bg1)', border: '1px solid var(--border)', borderRadius: 10 }}>
              No active subscription. Switch to Plans to get started.
            </div>
          )}

          {/* Activity history */}
          <div style={{ background: 'var(--bg1)', border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden' }}>
            <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', fontWeight: 600, fontSize: 15 }}>Recent Activity</div>
            {usageLoading ? (
              <div style={{ padding: 24, display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text3)', fontSize: 13 }}><Spinner /> Loading…</div>
            ) : !usage?.history?.length ? (
              <div style={{ padding: 24, fontSize: 13, color: 'var(--text3)' }}>No usage recorded yet.</div>
            ) : (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg3)' }}>
                      {['Date', 'Operation', 'Tokens'].map(h => (
                        <th key={h} style={{ textAlign: h === 'Tokens' ? 'right' : 'left', padding: '8px 14px', fontWeight: 600, color: 'var(--text2)' }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {usage.history.map((item, i) => (
                      <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                        <td style={{ padding: '10px 14px', color: 'var(--text3)', fontFamily: 'var(--mono)', fontSize: 11 }}>{item.created_at}</td>
                        <td style={{ padding: '10px 14px', fontWeight: 500 }}>{OP_LABELS[item.operation_type] || item.operation_type}</td>
                        <td style={{ padding: '10px 14px', textAlign: 'right', fontFamily: 'var(--mono)', color: 'var(--blue)', fontWeight: 600 }}>{Number(item.tokens || 0).toFixed(2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}
