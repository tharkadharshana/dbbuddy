import React, { useState, useEffect } from 'react'
import { fetchBillingPlans, fetchSubscription, subscribeToPlan, purchaseAddon } from '../utils/api'
import { Spinner } from '../components/UI'

// ── Plan feature definitions ──────────────────────────────────────────────────
const PLAN_FEATURES = {
  Starter: [
    { text: '300 AI Credits / month',   ok: true  },
    { text: '500K DB Rows / month',     ok: true  },
    { text: 'Natural language queries', ok: true  },
    { text: 'Basic analytics',          ok: true  },
    { text: 'Forecasting & anomalies',  ok: false },
    { text: 'Priority support',         ok: false },
  ],
  Growth: [
    { text: '750 AI Credits / month',   ok: true  },
    { text: '2M DB Rows / month',       ok: true  },
    { text: 'Natural language queries', ok: true  },
    { text: 'Advanced analytics',       ok: true  },
    { text: 'Forecasting & anomalies',  ok: true  },
    { text: 'Priority support',         ok: false },
  ],
  Pro: [
    { text: '2,000 AI Credits / month', ok: true  },
    { text: '10M DB Rows / month',      ok: true  },
    { text: 'Natural language queries', ok: true  },
    { text: 'Advanced analytics',       ok: true  },
    { text: 'Forecasting & anomalies',  ok: true  },
    { text: 'Priority support',         ok: true  },
  ],
}

// ── Usage progress bar ────────────────────────────────────────────────────────
function UsageBar({ label, used, limit, pct }) {
  const color = pct >= 100 ? 'var(--red)' : pct >= 80 ? 'var(--amber)' : 'var(--blue)'
  return (
    <div style={{ flex: 1 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--text3)', marginBottom: 4 }}>
        <span>{label}</span>
        <span>{used.toLocaleString()} / {limit.toLocaleString()}</span>
      </div>
      <div style={{ height: 6, borderRadius: 3, background: 'var(--bg3)', overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${Math.min(pct, 100)}%`, background: color, borderRadius: 3, transition: 'width .3s' }} />
      </div>
    </div>
  )
}

// ── Confirm modal ─────────────────────────────────────────────────────────────
function ConfirmModal({ plan, onConfirm, onCancel, loading }) {
  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
    }}>
      <div style={{
        background: 'var(--bg1)', border: '1px solid var(--border)',
        borderRadius: 12, padding: 28, width: 360, maxWidth: '90vw',
      }}>
        <div style={{ fontSize: 17, fontWeight: 700, marginBottom: 10 }}>Confirm Plan Change</div>
        <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 22, lineHeight: 1.5 }}>
          You're switching to the <strong>{plan.name}</strong> plan (${(plan.price_cents / 100).toFixed(2)}/month).
          Your current subscription will be cancelled immediately.
        </div>
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button onClick={onCancel} disabled={loading} style={{
            padding: '8px 18px', borderRadius: 8, border: '1px solid var(--border)',
            background: 'transparent', color: 'var(--text2)', cursor: 'pointer', fontSize: 13,
          }}>Cancel</button>
          <button onClick={onConfirm} disabled={loading} style={{
            padding: '8px 18px', borderRadius: 8, border: 'none',
            background: 'var(--blue)', color: '#fff', cursor: 'pointer', fontSize: 13, fontWeight: 600,
            opacity: loading ? 0.6 : 1,
          }}>
            {loading ? 'Processing…' : 'Confirm'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function BillingPage() {
  const [plans, setPlans]         = useState([])
  const [sub, setSub]             = useState(null)
  const [loading, setLoading]     = useState(true)
  const [confirmPlan, setConfirmPlan] = useState(null)
  const [subscribing, setSubscribing] = useState(false)
  const [addonQty, setAddonQty]   = useState({ ai_credits: 0, db_rows: 0 })
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
    } catch (e) {
      showToast(e?.response?.data?.detail || 'Subscription failed.', 'error')
    }
    setSubscribing(false)
  }

  async function handlePurchaseAddons() {
    const items = Object.entries(addonQty).filter(([, qty]) => qty > 0)
    if (!items.length) return
    setAddonLoading(true)
    try {
      await Promise.all(items.map(([type, qty]) => purchaseAddon(type, qty)))
      showToast('Add-ons purchased successfully.')
      setAddonQty({ ai_credits: 0, db_rows: 0 })
      await loadAll()
    } catch (e) {
      showToast(e?.response?.data?.detail || 'Purchase failed.', 'error')
    }
    setAddonLoading(false)
  }

  function ctaLabel(plan) {
    if (!sub || sub.status === 'no_subscription') return 'Start free trial'
    if (sub.plan_id === plan.id) {
      return sub.status === 'trial'
        ? `Trial · ${sub.trial_days_remaining}d left`
        : 'Current Plan'
    }
    const planIdx  = plans.findIndex(p => p.id === plan.id)
    const subIdx   = plans.findIndex(p => p.id === sub.plan_id)
    return planIdx > subIdx ? 'Upgrade' : 'Switch Plan'
  }

  function ctaDisabled(plan) {
    return sub?.plan_id === plan.id && sub?.status === 'active'
  }

  const addonTotal = (addonQty.ai_credits + addonQty.db_rows) * 1  // $1 per pack

  if (loading) return (
    <div style={{ padding: 40, display: 'flex', alignItems: 'center', gap: 10, color: 'var(--text3)' }}>
      <Spinner /> Loading billing…
    </div>
  )

  return (
    <div style={{ maxWidth: 900, padding: '28px 24px 60px', position: 'relative' }}>
      {/* Toast */}
      {toast && (
        <div style={{
          position: 'fixed', top: 20, right: 24, zIndex: 2000,
          padding: '10px 18px', borderRadius: 8, fontSize: 13, fontWeight: 500,
          background: toast.type === 'error' ? 'var(--red)' : 'var(--green)',
          color: '#fff', boxShadow: '0 4px 16px rgba(0,0,0,0.25)',
        }}>{toast.msg}</div>
      )}

      {confirmPlan && (
        <ConfirmModal
          plan={confirmPlan}
          onConfirm={handleSubscribe}
          onCancel={() => setConfirmPlan(null)}
          loading={subscribing}
        />
      )}

      {/* Header */}
      <div style={{ marginBottom: 32, textAlign: 'center' }}>
        <div style={{ fontSize: 26, fontWeight: 700, marginBottom: 6 }}>Simple, transparent pricing</div>
        <div style={{ fontSize: 14, color: 'var(--text3)' }}>Start free for 14 days · No credit card required</div>
      </div>

      {/* Current usage strip */}
      {sub && ['trial', 'active'].includes(sub.status) && (
        <div style={{
          background: 'var(--bg1)', border: '1px solid var(--border)',
          borderRadius: 10, padding: '14px 18px', marginBottom: 28,
          display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 16,
        }}>
          <div>
            <span style={{ fontWeight: 600, fontSize: 14 }}>{sub.plan_name}</span>
            <span style={{
              marginLeft: 8, fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 99,
              background: sub.status === 'trial' ? 'rgba(245,166,35,0.15)' : 'rgba(34,197,94,0.15)',
              color: sub.status === 'trial' ? 'var(--amber)' : 'var(--green)',
            }}>
              {sub.status === 'trial' ? `Trial · ${sub.trial_days_remaining}d left` : 'Active'}
            </span>
          </div>
          <div style={{ fontSize: 12, color: 'var(--text3)', marginLeft: 'auto' }}>
            Renews {sub.period_end}
          </div>
          <div style={{ width: '100%', display: 'flex', gap: 24, flexWrap: 'wrap' }}>
            <UsageBar
              label="AI Credits"
              used={sub.ai_base_used}
              limit={sub.ai_total_available}
              pct={sub.usage_pct_ai}
            />
            <UsageBar
              label="DB Rows"
              used={sub.db_base_used}
              limit={sub.db_total_available}
              pct={sub.usage_pct_db}
            />
          </div>
        </div>
      )}

      {/* Plan cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 16, marginBottom: 40 }}>
        {plans.map(plan => {
          const features = PLAN_FEATURES[plan.name] || []
          const isCurrent = sub?.plan_id === plan.id && ['trial', 'active'].includes(sub?.status)
          return (
            <div key={plan.id} style={{
              background: 'var(--bg1)', border: `1px solid ${isCurrent ? 'var(--blue)' : 'var(--border)'}`,
              borderRadius: 12, padding: 22, display: 'flex', flexDirection: 'column',
              position: 'relative',
            }}>
              {isCurrent && (
                <div style={{
                  position: 'absolute', top: -1, right: 16,
                  background: 'var(--blue)', color: '#fff',
                  fontSize: 10, fontWeight: 700, padding: '3px 10px',
                  borderRadius: '0 0 6px 6px', letterSpacing: '.05em',
                }}>CURRENT</div>
              )}
              <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>{plan.name}</div>
              <div style={{ marginBottom: 16 }}>
                <span style={{ fontSize: 28, fontWeight: 800 }}>${(plan.price_cents / 100).toFixed(0)}</span>
                <span style={{ fontSize: 13, color: 'var(--text3)' }}>/mo</span>
              </div>
              <ul style={{ listStyle: 'none', padding: 0, margin: '0 0 20px', flex: 1 }}>
                {features.map((f, i) => (
                  <li key={i} style={{
                    display: 'flex', alignItems: 'center', gap: 8,
                    fontSize: 13, color: f.ok ? 'var(--text)' : 'var(--text3)',
                    marginBottom: 8,
                  }}>
                    <span style={{ color: f.ok ? 'var(--green)' : 'var(--text3)', flexShrink: 0 }}>
                      {f.ok ? '✓' : '–'}
                    </span>
                    {f.text}
                  </li>
                ))}
              </ul>
              <button
                disabled={ctaDisabled(plan)}
                onClick={() => !ctaDisabled(plan) && setConfirmPlan(plan)}
                style={{
                  padding: '9px 0', borderRadius: 8, border: 'none', cursor: ctaDisabled(plan) ? 'default' : 'pointer',
                  background: isCurrent ? 'var(--bg3)' : 'var(--blue)',
                  color: isCurrent ? 'var(--text3)' : '#fff',
                  fontWeight: 600, fontSize: 13,
                  opacity: ctaDisabled(plan) ? 0.7 : 1,
                }}
              >
                {ctaLabel(plan)}
              </button>
            </div>
          )
        })}
      </div>

      {/* Add-ons section */}
      <div style={{ background: 'var(--bg1)', border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden' }}>
        <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', fontWeight: 600, fontSize: 15 }}>
          Add-ons
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)' }}>
              {['Feature', 'Price', 'Quantity', 'Subtotal'].map(h => (
                <th key={h} style={{ padding: '10px 16px', textAlign: h === 'Subtotal' ? 'right' : 'left', fontWeight: 600, color: 'var(--text3)' }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {[
              { key: 'ai_credits', icon: '⚡', label: 'AI Credits', price: '$1 per 50 credits' },
              { key: 'db_rows',    icon: '🗄', label: 'DB Rows',    price: '$1 per 100K rows' },
            ].map(row => (
              <tr key={row.key} style={{ borderBottom: '1px solid var(--border)' }}>
                <td style={{ padding: '12px 16px' }}><span style={{ marginRight: 6 }}>{row.icon}</span>{row.label}</td>
                <td style={{ padding: '12px 16px', color: 'var(--text3)' }}>{row.price}</td>
                <td style={{ padding: '12px 16px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <button
                      onClick={() => setAddonQty(q => ({ ...q, [row.key]: Math.max(0, q[row.key] - 1) }))}
                      style={{ width: 26, height: 26, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg3)', cursor: 'pointer', fontSize: 15, color: 'var(--text2)' }}
                    >−</button>
                    <span style={{ width: 24, textAlign: 'center', fontWeight: 600 }}>{addonQty[row.key]}</span>
                    <button
                      onClick={() => setAddonQty(q => ({ ...q, [row.key]: q[row.key] + 1 }))}
                      style={{ width: 26, height: 26, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg3)', cursor: 'pointer', fontSize: 15, color: 'var(--text2)' }}
                    >+</button>
                  </div>
                </td>
                <td style={{ padding: '12px 16px', textAlign: 'right', fontWeight: 600 }}>
                  ${addonQty[row.key]}.00
                </td>
              </tr>
            ))}
            <tr>
              <td colSpan={3} style={{ padding: '12px 16px', fontWeight: 600 }}>Total</td>
              <td style={{ padding: '12px 16px', textAlign: 'right', fontWeight: 700, fontSize: 15 }}>
                ${addonTotal}.00
              </td>
            </tr>
          </tbody>
        </table>
        <div style={{ padding: '12px 18px', borderTop: '1px solid var(--border)', display: 'flex', justifyContent: 'flex-end' }}>
          <button
            onClick={handlePurchaseAddons}
            disabled={addonTotal === 0 || addonLoading}
            style={{
              padding: '9px 22px', borderRadius: 8, border: 'none',
              background: 'var(--blue)', color: '#fff', fontWeight: 600, fontSize: 13,
              cursor: addonTotal === 0 || addonLoading ? 'default' : 'pointer',
              opacity: addonTotal === 0 || addonLoading ? 0.5 : 1,
            }}
          >
            {addonLoading ? 'Processing…' : `Purchase Add-ons — $${addonTotal}.00`}
          </button>
        </div>
      </div>
    </div>
  )
}
