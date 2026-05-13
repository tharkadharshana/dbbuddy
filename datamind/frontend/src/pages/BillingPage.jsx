import React, { useState, useEffect } from 'react'
import { fetchSubscription, fetchBillingPlan, subscribeToPro, purchaseAddon } from '../utils/api'

function ProgressBar({ pct, warn = 80 }) {
  const color = pct >= 100 ? 'var(--red)' : pct >= warn ? 'var(--amber)' : 'var(--blue)'
  return (
    <div style={{ height: 6, background: 'var(--bg3)', borderRadius: 99, overflow: 'hidden', marginTop: 6 }}>
      <div style={{ height: '100%', width: `${Math.min(pct, 100)}%`, background: color, borderRadius: 99, transition: 'width .4s' }} />
    </div>
  )
}

function StatusBadge({ status }) {
  const cfg = {
    trial:          { label: 'Trial',    bg: 'rgba(79,142,247,0.15)',   color: 'var(--blue)'  },
    active:         { label: 'Active',   bg: 'rgba(52,199,89,0.15)',    color: 'var(--green)' },
    expired:        { label: 'Expired',  bg: 'rgba(255,69,58,0.15)',    color: 'var(--red)'   },
    cancelled:      { label: 'Cancelled',bg: 'rgba(142,142,147,0.15)', color: 'var(--text3)' },
    no_subscription:{ label: 'No Plan',  bg: 'rgba(255,69,58,0.15)',    color: 'var(--red)'   },
  }[status] || { label: status, bg: 'var(--bg3)', color: 'var(--text2)' }

  return (
    <span style={{ fontSize: 11, fontWeight: 700, padding: '3px 9px', borderRadius: 99, background: cfg.bg, color: cfg.color, letterSpacing: '.04em' }}>
      {cfg.label}
    </span>
  )
}

function ConfirmModal({ plan, onConfirm, onCancel, loading }) {
  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }}>
      <div style={{ background: 'var(--bg1)', border: '1px solid var(--border)', borderRadius: 'var(--r-lg)', padding: 28, maxWidth: 380, width: '90%' }}>
        <div style={{ fontSize: 17, fontWeight: 700, marginBottom: 10 }}>Confirm Subscription</div>
        <div style={{ fontSize: 13, color: 'var(--text2)', lineHeight: 1.6, marginBottom: 20 }}>
          You're subscribing to <strong>DataMind Pro</strong> at <strong>${(plan.price_cents / 100).toFixed(2)}/month</strong>.
          Your billing period starts today.
        </div>
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button onClick={onCancel} disabled={loading} style={{ padding: '8px 18px', borderRadius: 'var(--r-md)', border: '1px solid var(--border)', background: 'transparent', color: 'var(--text2)', cursor: 'pointer', fontSize: 13 }}>
            Cancel
          </button>
          <button onClick={onConfirm} disabled={loading} style={{ padding: '8px 18px', borderRadius: 'var(--r-md)', border: 'none', background: 'var(--blue)', color: '#fff', cursor: loading ? 'wait' : 'pointer', fontSize: 13, fontWeight: 600 }}>
            {loading ? 'Processing…' : 'Subscribe — $25/mo'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default function BillingPage() {
  const [sub, setSub]         = useState(null)
  const [plan, setPlan]       = useState(null)
  const [loading, setLoading] = useState(true)
  const [showModal, setShowModal] = useState(false)
  const [subLoading, setSubLoading] = useState(false)
  const [addonQty, setAddonQty] = useState({ ai_credits: 1, db_rows: 1 })
  const [addonMsg, setAddonMsg] = useState({})
  const [error, setError]     = useState(null)

  useEffect(() => { load() }, [])

  async function load() {
    setLoading(true)
    try {
      const [s, p] = await Promise.all([
        fetchSubscription().catch(() => null),
        fetchBillingPlan().catch(() => null),
      ])
      setSub(s)
      setPlan(p)
    } catch {
      setError('Failed to load billing data.')
    } finally {
      setLoading(false)
    }
  }

  async function handleSubscribe() {
    if (!plan) return
    setSubLoading(true)
    try {
      await subscribeToPro(plan.id)
      setShowModal(false)
      await load()
    } catch {
      setError('Subscription failed. Please try again.')
    } finally {
      setSubLoading(false)
    }
  }

  async function handleAddon(type) {
    const qty = addonQty[type] || 1
    try {
      await purchaseAddon(type, qty)
      setAddonMsg(m => ({ ...m, [type]: 'Purchased!' }))
      setTimeout(() => setAddonMsg(m => ({ ...m, [type]: null })), 2500)
      await load()
    } catch {
      setAddonMsg(m => ({ ...m, [type]: 'Purchase failed.' }))
    }
  }

  if (loading) return (
    <div style={{ padding: 32, color: 'var(--text3)', fontSize: 14 }}>Loading billing…</div>
  )

  const status = sub?.status || 'no_subscription'
  const isActive = status === 'active'
  const isTrial  = status === 'trial'
  const isExpired = status === 'expired' || status === 'cancelled' || status === 'no_subscription'
  const canSubscribe = !isActive

  const aiUsed  = sub?.ai_credits_used  || 0
  const aiLimit = sub?.ai_credits_limit || 0
  const dbUsed  = sub?.db_rows_used     || 0
  const dbLimit = sub?.db_rows_limit    || 0
  const pctAi   = sub?.usage_pct_ai    || 0
  const pctDb   = sub?.usage_pct_db    || 0

  const addonAi = sub?.addon_ai_balance || 0
  const addonDb = sub?.addon_db_balance || 0

  const pricePerPack = { ai_credits: 2, db_rows: 1 }

  return (
    <div style={{ maxWidth: 820, padding: '28px 24px 60px' }}>
      {showModal && plan && (
        <ConfirmModal plan={plan} onConfirm={handleSubscribe} onCancel={() => setShowModal(false)} loading={subLoading} />
      )}

      <div style={{ marginBottom: 28 }}>
        <div style={{ fontSize: 20, fontWeight: 700, marginBottom: 4 }}>Plans & Billing</div>
        <div style={{ fontSize: 13, color: 'var(--text3)' }}>Manage your DataMind Pro subscription</div>
      </div>

      {error && (
        <div style={{ padding: '12px 16px', background: 'rgba(255,69,58,0.1)', border: '1px solid rgba(255,69,58,0.3)', borderRadius: 'var(--r-md)', color: 'var(--red)', fontSize: 13, marginBottom: 20 }}>
          {error}
        </div>
      )}

      {/* ── Status Card ─────────────────────────────────────────────────── */}
      <div style={{ background: 'var(--bg1)', border: '1px solid var(--border)', borderRadius: 'var(--r-lg)', padding: 24, marginBottom: 20 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 18 }}>
          <div style={{ fontSize: 16, fontWeight: 700, flex: 1 }}>DataMind Pro</div>
          <StatusBadge status={status} />
        </div>

        {isTrial && (
          <div style={{ fontSize: 13, color: 'var(--amber)', fontWeight: 500, marginBottom: 14, padding: '8px 12px', background: 'rgba(245,166,35,0.1)', borderRadius: 'var(--r-md)', border: '1px solid rgba(245,166,35,0.2)' }}>
            {sub.trial_days_remaining > 0
              ? `${sub.trial_days_remaining} day${sub.trial_days_remaining !== 1 ? 's' : ''} remaining in your free trial`
              : 'Your trial ends today — subscribe to keep access'}
          </div>
        )}

        {isExpired && (
          <div style={{ fontSize: 13, color: 'var(--red)', fontWeight: 500, marginBottom: 14, padding: '8px 12px', background: 'rgba(255,69,58,0.1)', borderRadius: 'var(--r-md)', border: '1px solid rgba(255,69,58,0.2)' }}>
            Your trial has ended. Subscribe to DataMind Pro to restore full access.
          </div>
        )}

        {isActive && sub.period_end && (
          <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 14 }}>
            Next renewal: {new Date(sub.period_end).toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })}
          </div>
        )}

        {/* Usage meters */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: 'var(--text2)' }}>
              <span>AI Credits</span>
              <span style={{ fontFamily: 'var(--mono)' }}>
                {aiUsed.toLocaleString()} / {aiLimit.toLocaleString()}
                {addonAi > 0 && <span style={{ color: 'var(--green)', marginLeft: 4 }}>+{addonAi} add-on</span>}
              </span>
            </div>
            <ProgressBar pct={pctAi} />
          </div>
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: 'var(--text2)' }}>
              <span>DB Rows</span>
              <span style={{ fontFamily: 'var(--mono)' }}>
                {dbUsed.toLocaleString()} / {dbLimit.toLocaleString()}
                {addonDb > 0 && <span style={{ color: 'var(--green)', marginLeft: 4 }}>+{addonDb.toLocaleString()} add-on</span>}
              </span>
            </div>
            <ProgressBar pct={pctDb} />
          </div>
        </div>
      </div>

      {/* ── Pro Plan Card ────────────────────────────────────────────────── */}
      <div style={{ background: 'linear-gradient(135deg, rgba(79,142,247,0.08), rgba(167,139,250,0.08))', border: '1.5px solid rgba(79,142,247,0.3)', borderRadius: 'var(--r-lg)', padding: 28, marginBottom: 20, textAlign: 'center' }}>
        <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.1em', color: 'var(--blue)', marginBottom: 8 }}>DataMind Pro</div>
        <div style={{ fontSize: 42, fontWeight: 800, marginBottom: 2 }}>$25</div>
        <div style={{ fontSize: 13, color: 'var(--text3)', marginBottom: 24 }}>per month</div>

        <div style={{ display: 'inline-flex', flexDirection: 'column', gap: 10, textAlign: 'left', marginBottom: 28 }}>
          {[
            ['1,500 AI Credits / month', 'var(--blue)'],
            ['2,000,000 DB Rows / month', 'var(--purple)'],
            ['Unlimited queries & reports', 'var(--green)'],
            ['14-day free trial included', 'var(--amber)'],
          ].map(([label, color]) => (
            <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 13 }}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="20 6 9 17 4 12" />
              </svg>
              {label}
            </div>
          ))}
        </div>

        {isActive ? (
          <div style={{ display: 'inline-block', padding: '10px 28px', borderRadius: 'var(--r-md)', background: 'var(--bg3)', color: 'var(--text3)', fontSize: 13, fontWeight: 600 }}>
            Current Plan
          </div>
        ) : (
          <button
            onClick={() => setShowModal(true)}
            style={{ padding: '12px 36px', borderRadius: 'var(--r-md)', border: 'none', background: 'linear-gradient(135deg,#4f8ef7,#a78bfa)', color: '#fff', fontSize: 14, fontWeight: 700, cursor: 'pointer', letterSpacing: '.02em' }}
          >
            {isTrial ? 'Subscribe Now' : 'Start Subscription'}
          </button>
        )}
      </div>

      {/* ── Add-ons ──────────────────────────────────────────────────────── */}
      <div>
          <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 6, marginTop: 4 }}>Add-ons</div>
          <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 14 }}>
            Purchase extra credits or rows on top of your plan quota. Packs never expire.
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
            {[
              { type: 'ai_credits', label: 'AI Credits Pack', detail: '100 credits per pack', unit: 'credits', price: pricePerPack.ai_credits },
              { type: 'db_rows',    label: 'DB Rows Pack',    detail: '100,000 rows per pack', unit: 'rows',    price: pricePerPack.db_rows },
            ].map(({ type, label, detail, price }) => (
              <div key={type} style={{ background: 'var(--bg1)', border: '1px solid var(--border)', borderRadius: 'var(--r-lg)', padding: 20 }}>
                <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 4 }}>{label}</div>
                <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 14 }}>{detail} — ${price}/pack</div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <input
                    type="number"
                    min={1}
                    max={50}
                    value={addonQty[type]}
                    onChange={e => setAddonQty(q => ({ ...q, [type]: Math.max(1, parseInt(e.target.value) || 1) }))}
                    style={{ width: 60, padding: '6px 10px', borderRadius: 'var(--r-sm)', border: '1px solid var(--border)', background: 'var(--bg2)', color: 'var(--text)', fontSize: 13, textAlign: 'center' }}
                  />
                  <span style={{ fontSize: 12, color: 'var(--text3)' }}>${(addonQty[type] * price).toFixed(2)}</span>
                  <button
                    onClick={() => handleAddon(type)}
                    style={{ marginLeft: 'auto', padding: '7px 16px', borderRadius: 'var(--r-sm)', border: 'none', background: 'var(--blue)', color: '#fff', fontSize: 12, fontWeight: 600, cursor: 'pointer' }}
                  >
                    Buy
                  </button>
                </div>
                {addonMsg[type] && (
                  <div style={{ fontSize: 12, marginTop: 8, color: addonMsg[type] === 'Purchased!' ? 'var(--green)' : 'var(--red)' }}>
                    {addonMsg[type]}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
    </div>
  )
}
