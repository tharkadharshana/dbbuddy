import React, { useState, useEffect } from 'react'
import { fetchSubscription, fetchBillingPlans, subscribeToPlan, purchaseAddon } from '../utils/api'

// ── Feature lists ─────────────────────────────────────────────────────────────

const PLAN_FEATURES = {
  Starter: [
    { text: '300 AI Credits / month',    ok: true  },
    { text: '500K DB Rows / month',      ok: true  },
    { text: 'Natural language queries',  ok: true  },
    { text: 'Basic analytics',           ok: true  },
    { text: 'Forecasting & anomalies',   ok: false },
    { text: 'Priority support',          ok: false },
  ],
  Growth: [
    { text: '750 AI Credits / month',    ok: true  },
    { text: '2M DB Rows / month',        ok: true  },
    { text: 'Natural language queries',  ok: true  },
    { text: 'Full analytics suite',      ok: true  },
    { text: 'Forecasting & anomalies',   ok: true  },
    { text: 'Priority support',          ok: false },
  ],
  Pro: [
    { text: '2,000 AI Credits / month',  ok: true  },
    { text: '10M DB Rows / month',       ok: true  },
    { text: 'Natural language queries',  ok: true  },
    { text: 'Full analytics suite',      ok: true  },
    { text: 'Forecasting & anomalies',   ok: true  },
    { text: 'Priority support',          ok: true  },
  ],
}

const ACCENT = ['#4f8ef7', '#f97316', '#8b5cf6']

// ── Add-on rows config ────────────────────────────────────────────────────────
// price_per_pack: dollars, units_per_pack: human label, type matches backend

const ADDON_ROWS = [
  {
    type:       'ai_credits',
    icon:       '⚡',
    iconBg:     '#4f8ef7',
    name:       'AI Credits',
    priceLine:  '$1 per 50 credits',
    packSize:   50,       // units per $1 pack
    packDollars: 1,
  },
  {
    type:       'db_rows',
    icon:       '🗄',
    iconBg:     '#8b5cf6',
    name:       'DB Rows',
    priceLine:  '$1 per 100,000 rows',
    packSize:   100_000,
    packDollars: 1,
  },
]

// ── Helpers ───────────────────────────────────────────────────────────────────

function ProgressBar({ pct }) {
  const color = pct >= 100 ? '#ff453a' : pct >= 80 ? '#f97316' : '#4f8ef7'
  return (
    <div style={{ height: 5, background: 'var(--bg3)', borderRadius: 99, overflow: 'hidden', marginTop: 4 }}>
      <div style={{ height: '100%', width: `${Math.min(pct, 100)}%`, background: color, borderRadius: 99, transition: 'width .4s' }} />
    </div>
  )
}

function QtyBtn({ onClick, label }) {
  return (
    <button onClick={onClick} style={{
      width: 28, height: 28, borderRadius: 6, border: '1px solid var(--border)',
      background: 'var(--bg2)', color: 'var(--text)', fontSize: 16, fontWeight: 600,
      cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
    }}>{label}</button>
  )
}

function ConfirmModal({ plan, accent, onConfirm, onCancel, loading }) {
  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }}>
      <div style={{ background: 'var(--bg1)', border: '1px solid var(--border)', borderRadius: 16, padding: 32, maxWidth: 400, width: '90%', boxShadow: '0 20px 60px rgba(0,0,0,.3)' }}>
        <div style={{ fontSize: 18, fontWeight: 700, marginBottom: 8 }}>Confirm Subscription</div>
        <div style={{ fontSize: 13, color: 'var(--text2)', lineHeight: 1.7, marginBottom: 24 }}>
          Subscribe to <strong>DataMind {plan.name}</strong> at{' '}
          <strong style={{ color: accent }}>${(plan.price_cents / 100).toFixed(0)}/month</strong>.
          <br /><span style={{ color: 'var(--text3)' }}>Renews every {plan.validity_days || 30} days. Cancel anytime.</span>
        </div>
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button onClick={onCancel} disabled={loading} style={{ padding: '9px 20px', borderRadius: 8, border: '1px solid var(--border)', background: 'transparent', color: 'var(--text2)', cursor: 'pointer', fontSize: 13 }}>
            Cancel
          </button>
          <button onClick={onConfirm} disabled={loading} style={{ padding: '9px 22px', borderRadius: 8, border: 'none', background: accent, color: '#fff', cursor: loading ? 'wait' : 'pointer', fontSize: 13, fontWeight: 700 }}>
            {loading ? 'Processing…' : `Confirm — $${(plan.price_cents / 100).toFixed(0)}/mo`}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────

export default function BillingPage() {
  const [sub, setSub]       = useState(null)
  const [plans, setPlans]   = useState([])
  const [plansError, setPlansError] = useState(false)
  const [loading, setLoading] = useState(true)

  const [confirmPlan, setConfirmPlan]     = useState(null)
  const [confirmAccent, setConfirmAccent] = useState('#4f8ef7')
  const [subLoading, setSubLoading]       = useState(false)
  const [subError, setSubError]           = useState(null)

  // Add-on quantity state: { ai_credits: 0, db_rows: 0 }
  const [qty, setQty]       = useState({ ai_credits: 0, db_rows: 0 })
  const [addonBuying, setAddonBuying] = useState(false)
  const [addonMsg, setAddonMsg]       = useState(null)

  useEffect(() => { load() }, [])

  async function load() {
    setLoading(true)
    setPlansError(false)
    const [s, p] = await Promise.allSettled([
      fetchSubscription(),
      fetchBillingPlans(),
    ])
    setSub(s.status === 'fulfilled' ? s.value : null)
    if (p.status === 'fulfilled' && p.value?.plans?.length) {
      setPlans(p.value.plans)
    } else {
      setPlansError(true)
    }
    setLoading(false)
  }

  async function handleSubscribe() {
    if (!confirmPlan) return
    setSubLoading(true)
    setSubError(null)
    try {
      await subscribeToPlan(confirmPlan.id)
      setConfirmPlan(null)
      await load()
    } catch (e) {
      setSubError('Subscription failed. Please try again.')
    } finally {
      setSubLoading(false)
    }
  }

  async function handleBuyAddons() {
    const hasAny = Object.values(qty).some(v => v > 0)
    if (!hasAny) return
    setAddonBuying(true)
    setAddonMsg(null)
    try {
      await Promise.all(
        ADDON_ROWS
          .filter(r => qty[r.type] > 0)
          .map(r => purchaseAddon(r.type, qty[r.type]))
      )
      setAddonMsg('success')
      setQty({ ai_credits: 0, db_rows: 0 })
      await load()
    } catch {
      setAddonMsg('error')
    } finally {
      setAddonBuying(false)
    }
  }

  function changeQty(type, delta) {
    setQty(q => ({ ...q, [type]: Math.max(0, (q[type] || 0) + delta) }))
  }

  if (loading) return <div style={{ padding: 40, color: 'var(--text3)', fontSize: 14 }}>Loading billing…</div>

  const status        = sub?.status || 'no_subscription'
  const isActive      = status === 'active'
  const isTrial       = status === 'trial'
  const isLive        = isActive || isTrial
  const currentPlanId = sub?.plan_id

  const totalAddonCost = ADDON_ROWS.reduce((sum, r) => sum + (qty[r.type] || 0) * r.packDollars, 0)

  return (
    <div style={{ maxWidth: 980, padding: '32px 28px 60px' }}>

      {confirmPlan && (
        <ConfirmModal plan={confirmPlan} accent={confirmAccent}
          onConfirm={handleSubscribe} onCancel={() => setConfirmPlan(null)} loading={subLoading} />
      )}

      {/* Title */}
      <div style={{ textAlign: 'center', marginBottom: 36 }}>
        <div style={{ fontSize: 24, fontWeight: 800, marginBottom: 6 }}>Simple, transparent pricing</div>
        <div style={{ fontSize: 14, color: 'var(--text3)' }}>Start free for 14 days · No credit card required</div>
      </div>

      {subError && (
        <div style={{ padding: '12px 16px', background: 'rgba(255,69,58,0.1)', border: '1px solid rgba(255,69,58,0.3)', borderRadius: 10, color: '#ff453a', fontSize: 13, marginBottom: 24 }}>
          {subError}
        </div>
      )}

      {/* ── Current plan usage strip ───────────────────────────────────────── */}
      {isLive && sub && (
        <div style={{ background: 'var(--bg1)', border: '1px solid var(--border)', borderRadius: 12, padding: '14px 20px', marginBottom: 32, display: 'flex', alignItems: 'center', gap: 20, flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{ width: 8, height: 8, borderRadius: '50%', background: isActive ? '#34c759' : '#f97316' }} />
            <span style={{ fontWeight: 700, fontSize: 14 }}>DataMind {sub.plan_name}</span>
            <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 99,
              background: isTrial ? 'rgba(249,115,22,0.15)' : 'rgba(52,199,89,0.15)',
              color: isTrial ? '#f97316' : '#34c759' }}>
              {isTrial ? `Trial · ${sub.trial_days_remaining}d left` : 'Active'}
            </span>
            {isActive && sub.period_end && (
              <span style={{ fontSize: 11, color: 'var(--text3)' }}>
                renews {new Date(sub.period_end).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
              </span>
            )}
          </div>
          <div style={{ display: 'flex', gap: 24, marginLeft: 'auto' }}>
            {[
              { label: 'AI Credits', used: sub.ai_base_used, limit: sub.ai_base_limit, addon: sub.ai_addon_balance, pct: sub.usage_pct_ai },
              { label: 'DB Rows',    used: sub.db_base_used, limit: sub.db_base_limit, addon: sub.db_addon_balance, pct: sub.usage_pct_db },
            ].map(({ label, used, limit, addon, pct }) => (
              <div key={label} style={{ minWidth: 160 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--text3)', marginBottom: 2 }}>
                  <span>{label}</span>
                  <span>{(used||0).toLocaleString()} / {(limit||0).toLocaleString()}
                    {addon > 0 && <span style={{ color: '#34c759', marginLeft: 4 }}>+{addon.toLocaleString()}</span>}
                  </span>
                </div>
                <ProgressBar pct={pct || 0} />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Plan cards ────────────────────────────────────────────────────── */}
      {plansError ? (
        <div style={{ textAlign: 'center', padding: '40px 20px', background: 'var(--bg1)', borderRadius: 16, border: '1px solid var(--border)', marginBottom: 40 }}>
          <div style={{ fontSize: 14, color: 'var(--text3)', marginBottom: 12 }}>Could not load plans. Is the backend running?</div>
          <button onClick={load} style={{ padding: '8px 20px', borderRadius: 8, border: 'none', background: 'var(--blue)', color: '#fff', cursor: 'pointer', fontWeight: 600 }}>Retry</button>
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 20, marginBottom: 48, alignItems: 'start' }}>
          {plans.map((plan, i) => {
            const accent    = ACCENT[i] || '#4f8ef7'
            const isCurrent = plan.id === currentPlanId && isLive
            const isPopular = i === 1
            const features  = PLAN_FEATURES[plan.name] || []

            return (
              <div key={plan.id} style={{
                background: 'var(--bg1)',
                border: `${isCurrent ? '2.5px' : '1px'} solid ${isCurrent ? accent : 'var(--border)'}`,
                borderRadius: 16, padding: '28px 22px 22px',
                position: 'relative', display: 'flex', flexDirection: 'column',
                boxShadow: isPopular ? '0 8px 32px rgba(0,0,0,0.12)' : '0 2px 8px rgba(0,0,0,0.05)',
                transform: isPopular ? 'scale(1.025)' : 'none',
              }}>

                {/* Badge */}
                {(isCurrent || isPopular) && (
                  <div style={{
                    position: 'absolute', top: -13, left: '50%', transform: 'translateX(-50%)',
                    background: accent, color: '#fff', fontSize: 10, fontWeight: 800,
                    padding: '4px 14px', borderRadius: 99, whiteSpace: 'nowrap', letterSpacing: '.07em',
                  }}>
                    {isCurrent ? '✓ YOUR PLAN' : 'MOST POPULAR'}
                  </div>
                )}

                {/* Name */}
                <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.12em', color: 'var(--text3)', marginBottom: 10 }}>
                  {plan.name}
                </div>

                {/* Price */}
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 1, marginBottom: 2 }}>
                  <span style={{ fontSize: 18, fontWeight: 600, color: 'var(--text3)', marginTop: 8 }}>$</span>
                  <span style={{ fontSize: 54, fontWeight: 800, lineHeight: 1, letterSpacing: '-2px' }}>
                    {(plan.price_cents / 100).toFixed(0)}
                  </span>
                </div>
                <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 4 }}>
                  per month · {plan.trial_days}-day free trial
                </div>

                <div style={{ height: 1, background: 'var(--border)', margin: '16px 0' }} />

                {/* Features */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10, flex: 1, marginBottom: 22 }}>
                  {features.map(({ text, ok }) => (
                    <div key={text} style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
                      {ok ? (
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke={accent} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                          <polyline points="20 6 9 17 4 12" />
                        </svg>
                      ) : (
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--text3)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <line x1="5" y1="12" x2="19" y2="12" />
                        </svg>
                      )}
                      <span style={{ fontSize: 13, color: ok ? 'var(--text)' : 'var(--text3)', fontWeight: ok ? 500 : 400 }}>{text}</span>
                    </div>
                  ))}
                </div>

                {/* CTA */}
                <button
                  disabled={isCurrent}
                  onClick={() => { if (!isCurrent) { setConfirmPlan(plan); setConfirmAccent(accent) } }}
                  style={{
                    padding: '13px 0', borderRadius: 10, width: '100%', fontSize: 14, fontWeight: 700,
                    cursor: isCurrent ? 'default' : 'pointer',
                    border: isCurrent ? 'none' : `2px solid ${accent}`,
                    background: isCurrent ? `${accent}18` : isPopular ? accent : 'transparent',
                    color: isCurrent ? accent : isPopular ? '#fff' : accent,
                  }}
                >
                  {isCurrent
                    ? (isTrial ? `Trial · ${sub.trial_days_remaining}d left` : 'Current Plan')
                    : isLive
                      ? (plan.price_cents > (sub?.price_cents || 0) ? 'Upgrade' : 'Switch Plan')
                      : 'Start free trial'}
                </button>
              </div>
            )
          })}
        </div>
      )}

      {/* ── Add-ons table ─────────────────────────────────────────────────── */}
      <div style={{ borderTop: '1px solid var(--border)', paddingTop: 32 }}>
        <div style={{ fontSize: 18, fontWeight: 700, marginBottom: 6 }}>Add-ons</div>
        <div style={{ fontSize: 13, color: 'var(--text3)', marginBottom: 20, lineHeight: 1.5 }}>
          Top up instantly. Consumed only after your monthly base quota runs out. Unused balance <strong>rolls over</strong> each month.
        </div>

        {/* Table header */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 160px 130px 90px', gap: 12, padding: '8px 16px', marginBottom: 4 }}>
          {['Feature', 'Price', 'Quantity', 'Subtotal'].map(h => (
            <div key={h} style={{ fontSize: 12, fontWeight: 600, color: 'var(--text3)' }}>{h}</div>
          ))}
        </div>

        {/* Rows */}
        <div style={{ background: 'var(--bg1)', border: '1px solid var(--border)', borderRadius: 14, overflow: 'hidden' }}>
          {ADDON_ROWS.map((row, i) => {
            const q        = qty[row.type] || 0
            const subtotal = q * row.packDollars
            const isLast   = i === ADDON_ROWS.length - 1

            return (
              <div key={row.type} style={{
                display: 'grid', gridTemplateColumns: '1fr 160px 130px 90px',
                gap: 12, padding: '18px 16px', alignItems: 'center',
                borderBottom: isLast ? 'none' : '1px solid var(--border)',
              }}>
                {/* Feature */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <div style={{ width: 38, height: 38, borderRadius: 10, background: row.iconBg, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 18, flexShrink: 0 }}>
                    {row.icon}
                  </div>
                  <div>
                    <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 2 }}>{row.name}</div>
                    <div style={{ fontSize: 12, color: 'var(--text3)' }}>{row.priceLine}</div>
                  </div>
                </div>

                {/* Price per pack */}
                <div style={{ fontSize: 13, color: 'var(--text2)' }}>
                  ${row.packDollars} / {row.packSize >= 1_000_000
                    ? `${(row.packSize / 1_000_000).toFixed(0)}M`
                    : row.packSize >= 1_000
                      ? `${(row.packSize / 1_000).toFixed(0)}K`
                      : row.packSize} units
                </div>

                {/* Quantity stepper */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <QtyBtn onClick={() => changeQty(row.type, -1)} label="−" />
                  <span style={{ fontSize: 14, fontWeight: 600, minWidth: 28, textAlign: 'center' }}>{q}</span>
                  <QtyBtn onClick={() => changeQty(row.type, 1)}  label="+" />
                </div>

                {/* Subtotal */}
                <div style={{ fontSize: 14, fontWeight: 700, color: subtotal > 0 ? 'var(--blue)' : 'var(--text3)' }}>
                  ${subtotal.toFixed(0)}
                </div>
              </div>
            )
          })}

          {/* Total row */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 160px 130px 90px', gap: 12, padding: '14px 16px', background: 'var(--bg2)', alignItems: 'center', borderTop: '1px solid var(--border)' }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text2)' }}>
              {totalAddonCost > 0 ? `${ADDON_ROWS.filter(r => qty[r.type] > 0).map(r => `${qty[r.type]} × ${r.name}`).join(' + ')}` : 'Select quantities above'}
            </div>
            <div />
            <div />
            <div style={{ fontSize: 16, fontWeight: 800 }}>${totalAddonCost.toFixed(0)}</div>
          </div>
        </div>

        {/* Buy button + status */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginTop: 16 }}>
          <button
            onClick={handleBuyAddons}
            disabled={addonBuying || totalAddonCost === 0}
            style={{
              padding: '11px 32px', borderRadius: 10, border: 'none',
              background: totalAddonCost > 0 ? 'var(--blue)' : 'var(--bg3)',
              color: totalAddonCost > 0 ? '#fff' : 'var(--text3)',
              fontSize: 14, fontWeight: 700,
              cursor: totalAddonCost > 0 ? 'pointer' : 'default',
              opacity: addonBuying ? 0.7 : 1,
            }}
          >
            {addonBuying ? 'Processing…' : `Purchase Add-ons${totalAddonCost > 0 ? ` — $${totalAddonCost}` : ''}`}
          </button>
          {addonMsg === 'success' && (
            <span style={{ fontSize: 13, color: '#34c759', fontWeight: 600 }}>✓ Credits added to your account</span>
          )}
          {addonMsg === 'error' && (
            <span style={{ fontSize: 13, color: '#ff453a', fontWeight: 600 }}>Purchase failed — please try again</span>
          )}
        </div>
      </div>
    </div>
  )
}
