/**
 * EmbedSalesplayPlans.jsx — AI POS subscription plan picker for the Salesplay embed.
 *
 * Shown in two situations (see EmbedApp.jsx's access gate):
 *   - First-time access: after the consent screen, before chat. Trial CTA
 *     shown on the lowest plan.
 *   - Blocked access: trial expired or quota exceeded. No trial CTA —
 *     `reason` explains why chat is no longer available.
 *
 * Picking "Start free trial" just proceeds into chat (the DataMind trial
 * already started during onboarding) — no extra screen, minimum clicks.
 *
 * Picking a paid plan:
 *   - No card on file  → sends the user to Salesplay's own card_add_url
 *     (new tab; can't navigate the iframe itself off the partner site).
 *   - Card on file     → shows a one-screen receipt (plan, price, card) with
 *     a single "Confirm & Subscribe" button — no separate review step.
 * On confirm, calls /subscriptions/payment — the backend activates the
 * matching DataMind plan synchronously in that same request the moment
 * Salesplay confirms the charge (Salesplay's own activation_status lags
 * unpredictably — confirmed still 0 after 2.5+ minutes on predev2 — so we
 * don't gate on it). A single re-check after payment confirms it landed; if
 * that one check somehow fails, the button becomes a safe "Check again"
 * (never re-runs the charge) rather than silently retrying forever.
 */
import React, { useState, useRef, useEffect } from 'react'
import { salesplaySubscriptionPayment } from './embedApi'
import { notifyParent } from './EmbedApp'
import { appName } from './embedBranding'
import BrandLogo from '../components/Logo'

const SP = {
  bg:        'linear-gradient(180deg, #F0F4F8 0%, #F7F9FB 100%)',
  card:      '#FFFFFF',
  heading:   '#191C1E',
  text:      '#545F73',
  text3:     '#8B93A7',
  blue:      '#0058BE',
  blueLight: '#D8E2FF',
  blueDark:  '#001A42',
  outline:   '#C2C6D6',
  green:     '#006947',
  red:       '#B3261E',
  redLight:  '#F9DEDC',
  shadow:    '0px 4px 20px 0px rgba(84,95,115,0.12)',
}

const TIER_FEATURES = {
  0: [ // lowest plan
    { text: 'Ask Your Data (AI)',      ok: true  },
    { text: 'All Analytics & Reports', ok: true  },
    { text: 'Forecasting & Anomalies', ok: false },
    { text: 'Priority Support',        ok: false },
  ],
  1: [
    { text: 'Ask Your Data (AI)',      ok: true  },
    { text: 'All Analytics & Reports', ok: true  },
    { text: 'Forecasting & Anomalies', ok: true  },
    { text: 'Priority Support',        ok: false },
  ],
  2: [
    { text: 'Ask Your Data (AI)',      ok: true  },
    { text: 'All Analytics & Reports', ok: true  },
    { text: 'Forecasting & Anomalies', ok: true  },
    { text: 'Priority Support',        ok: true  },
  ],
}

const SUPPORT_EMAIL = 'support@datamind.ai'

const REASON_COPY = {
  trial_expired:  (days) => `Your ${days}-day free trial has ended.`,
  quota_exceeded: () => "You've used up your plan's quota.",
}

// Salesplay returns 6 flat pricing_plans entries — 3 tiers × {MONTHLY, YEARLY}.
// Pair them into 3 tiers (by ascending price within each cycle) so the UI
// shows 3 cards with a Monthly/Yearly toggle instead of 6 separate cards.
// Fails safe: if the two cycle lists don't line up 1:1, don't guess a
// pairing — every product_code sent to /subscriptions/payment must be
// exactly the one the user saw the price for, no exceptions.
function groupPlansByTier(plans) {
  const monthly = (plans || []).filter(p => p.billing_type === 'MONTHLY').sort((a, b) => Number(a.product_price) - Number(b.product_price))
  const yearly  = (plans || []).filter(p => p.billing_type === 'YEARLY').sort((a, b) => Number(a.product_price) - Number(b.product_price))
  if (monthly.length === 0 || yearly.length === 0 || monthly.length !== yearly.length) {
    return null // caller falls back to a flat, ungrouped list
  }
  return monthly.map((m, i) => ({ monthly: m, yearly: yearly[i] }))
}

function yearlySavingsPct(tier) {
  if (!tier?.monthly || !tier?.yearly) return null
  const monthlyAnnualized = Number(tier.monthly.product_price) * 12
  const yearly = Number(tier.yearly.product_price)
  if (!monthlyAnnualized) return null
  return Math.round((1 - yearly / monthlyAnnualized) * 100)
}

// Card-add happens on Salesplay's own page in another tab — poll for it, so
// the user isn't stuck needing a manual refresh to move on.
const CARD_POLL_INTERVAL_MS = Number(import.meta.env.VITE_SALESPLAY_CARD_POLL_INTERVAL_MS) || 3000
const sleep = (ms) => new Promise(r => setTimeout(r, ms))

// ponytail: tier index → DataMind subscription_plans.id/name. Matches the
// current DB seed (Starter=1, Growth=2, Pro=3, sort_order 1/2/3 — same order
// as ascending price, same order Salesplay's tiers are grouped in above).
// Revisit if subscription_plans is ever reseeded with different ids/order.
const TIER_TO_INTERNAL_PLAN_ID = [1, 2, 3]
const TIER_TO_PLAN_NAME = ['Starter', 'Growth', 'Pro']

// Salesplay's own product_name ("Access To Unlimited AI POS Data Module
// Monthly/Yearly") is their internal SKU label, not a name a merchant should
// see here — show our own plan name instead.
function displayPlanName(tierIndex, billingType) {
  const name = TIER_TO_PLAN_NAME[tierIndex] || 'Plan'
  return `${name} — ${billingType === 'YEARLY' ? 'Yearly' : 'Monthly'}`
}

function Spin({ color = '#fff' }) {
  return <div style={{ width:13, height:13, border:`2px solid ${color === '#fff' ? 'rgba(255,255,255,0.3)' : 'rgba(0,88,190,0.25)'}`, borderTopColor:color, borderRadius:'50%', animation:'spin 0.7s linear infinite' }} />
}

export default function EmbedSalesplayPlans({ context, partnerKey, aat, plans, currency = '$', trialAvailable, blockReason, isPaidQuotaBlocked, trialDays = 14, billingDetailsAdded, cardAddUrl, cardLabel, onTrialSelected, onSubscribed, onRefreshAccess, onClose }) {
  const [selectedPlan, setSelectedPlan] = useState(null) // { ...plan, _tierIndex } under review on the receipt screen
  const [checking, setChecking] = useState(false) // re-checking access after returning from card_add_url (manual "Continue")
  const [awaitingCard, setAwaitingCard] = useState(false) // polling for a card add — grays out the screen
  const [confirmBusy, setConfirmBusy] = useState(false) // paying + confirming activation
  const [paidPending, setPaidPending] = useState(false) // charge succeeded — blocks re-paying even if the confirm check below fails
  const [billingCycle, setBillingCycle] = useState('MONTHLY') // global toggle — one cycle for all 3 tiers
  const [error, setError] = useState('')
  const appNm = appName(context)
  const cardPollActive = useRef(false)

  useEffect(() => () => { cardPollActive.current = false }, []) // stop polling on unmount

  const tiers = groupPlansByTier(plans)
  // Fallback for unexpected data shapes (e.g. only one cycle configured) —
  // flat list, no toggle, same as before this feature existed.
  const flatSorted = tiers ? null : [...(plans || [])].sort((a, b) => Number(a.product_price || 0) - Number(b.product_price || 0))
  const savingsPct = tiers ? yearlySavingsPct(tiers[0]) : null
  // The exact plan objects shown as cards — each carries the product_code
  // that will be sent to /subscriptions/payment untouched, for whichever
  // cycle is toggled. Never re-derive/re-price this client-side.
  const displayPlans = tiers ? tiers.map(t => (billingCycle === 'YEARLY' ? t.yearly : t.monthly)) : flatSorted

  // Polls subscription/info until a card shows up (or cancelled) so the user
  // isn't stuck needing to refresh the widget after adding one on Salesplay's page.
  async function pollForCard(plan) {
    cardPollActive.current = true
    setAwaitingCard(true)
    while (cardPollActive.current) {
      await sleep(CARD_POLL_INTERVAL_MS)
      if (!cardPollActive.current) return
      const access = await onRefreshAccess()
      if (access?.billingDetailsAdded) {
        cardPollActive.current = false
        setAwaitingCard(false)
        setSelectedPlan(plan) // → straight to the receipt screen, no extra click
        return
      }
    }
  }

  function handleCancelCardWait() {
    cardPollActive.current = false
    setAwaitingCard(false)
  }

  function handleChoosePlan(plan, tierIndex) {
    setError('')
    const withTier = { ...plan, _tierIndex: tierIndex }
    // No card on file — Salesplay's own hosted page collects it. We can't call
    // /subscriptions/payment without one (confirmed: it errors with no card),
    // so send them there instead of guessing at a payload that's bound to fail.
    if (!billingDetailsAdded && cardAddUrl) {
      notifyParent('dm:redirect', { url: cardAddUrl })
      window.open(cardAddUrl, '_blank', 'noopener,noreferrer')
      pollForCard(withTier)
      return
    }
    setSelectedPlan(withTier) // → receipt screen
  }

  async function handleConfirmSubscribe() {
    const plan = selectedPlan
    if (!plan) return
    setError('')
    setConfirmBusy(true)
    try {
      const payRes = await salesplaySubscriptionPayment({
        partner_key: partnerKey,
        aat,
        subscription_type: String(plan.subscription_type),
        subscription_product_code: plan.product_code,
        product_type: 'ADDON',
        // Confirmed with Salesplay directly: literal "MANUAL", not the plan's
        // billing_type (MONTHLY/YEARLY) — that's a display field, not this one.
        subscription_activation_type: 'MANUAL',
        // The backend activates this DataMind plan synchronously the instant
        // Salesplay confirms the charge — see TIER_TO_INTERNAL_PLAN_ID above.
        internal_plan_id: TIER_TO_INTERNAL_PLAN_ID[plan._tierIndex] || 1,
        internal_period_days: plan.billing_type === 'YEARLY' ? 365 : 30,
      })

      if (payRes?.status !== 'success') {
        const link = payRes?.data?.redirect_url || payRes?.redirect_url || payRes?.link
        if (link) {
          notifyParent('dm:redirect', { url: link })
          window.open(link, '_blank', 'noopener,noreferrer')
        }
        setError(payRes?.message || 'Payment could not be completed. Please try again.')
        setConfirmBusy(false)
        return
      }

      // Charge went through and the backend already activated our plan in
      // this same request — never re-pay from here again regardless of what
      // happens next. A single re-check should confirm it immediately; if it
      // somehow doesn't (real inconsistency, not lag), fall back to a manual
      // "Check again" rather than a blind retry loop.
      setPaidPending(true)
      await onSubscribed() // parent switches to 'chat' on success
    } catch (e) {
      setConfirmBusy(false)
      setError(e.response?.data?.detail || e.message || 'Payment succeeded but activation could not be confirmed yet.')
    }
  }

  // Re-checks subscription/info without a fresh payment call — used both for
  // the pre-payment "Continue" (card-add / paid elsewhere) and post-payment
  // "Check again" (paidPending — never re-runs the charge).
  async function handleContinue() {
    setError('')
    setChecking(true)
    try {
      await onSubscribed()
    } catch (e) {
      setError(e.message || 'Still not seeing an active subscription. Please try again.')
      setChecking(false)
    }
  }

  const [trialBusy, setTrialBusy] = useState(false)
  async function handleStartTrial() {
    setError('')
    setTrialBusy(true)
    try {
      await onTrialSelected()
    } catch (e) {
      setError(e.response?.data?.detail || e.message || 'Could not start your trial. Please try again.')
      setTrialBusy(false)
    }
  }

  const grayedOut = awaitingCard
  const busy = confirmBusy || checking || awaitingCard || trialBusy

  return (
    <div style={{
      height: '100%', overflowY: 'auto', padding: '24px 20px',
      background: SP.bg, position: 'relative',
    }}>
      {awaitingCard && (
        <div style={{
          position: 'absolute', inset: 0, zIndex: 10,
          background: 'rgba(240,244,248,0.85)', backdropFilter: 'blur(1px)',
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 14,
        }}>
          <div style={{ width: 28, height: 28, border: '3px solid rgba(0,88,190,0.2)', borderTopColor: SP.blue, borderRadius: '50%', animation: 'spin 0.7s linear infinite' }} />
          <div style={{ fontSize: 13, fontWeight: 600, color: SP.heading }}>Waiting for your payment method…</div>
          <div style={{ fontSize: 12, color: SP.text3, maxWidth: 240, textAlign: 'center' }}>Finish adding your card in the other tab — we'll pick it up automatically.</div>
          <button onClick={handleCancelCardWait} style={{ background: 'none', border: 'none', color: SP.blue, fontSize: 12, fontWeight: 600, cursor: 'pointer', marginTop: 4 }}>
            Cancel
          </button>
        </div>
      )}

      {onClose && !grayedOut && (
        <button onClick={onClose} style={{
          position: 'absolute', top: 12, right: 12,
          background: 'none', border: 'none', cursor: 'pointer',
          color: SP.text3, fontSize: 18, lineHeight: 1, padding: 4, borderRadius: 6,
        }}>✕</button>
      )}

      {isPaidQuotaBlocked && !selectedPlan ? (
        // ── Paid plan, quota used up ─────────────────────────────────────
        // Salesplay's subscription is already active this cycle — nothing to
        // buy here. Extra usage is an addon, not sold through this screen.
        <div style={{ textAlign: 'center', paddingTop: 24 }}>
          <BrandLogo size={40} radius={11} shadow="0 4px 16px rgba(0,88,190,0.3)" style={{ marginBottom: 10 }} />
          <h2 style={{
            fontFamily: "'Manrope', 'Plus Jakarta Sans', sans-serif",
            fontSize: 22, lineHeight: '30px', letterSpacing: '-0.02em', fontWeight: 800,
            color: SP.heading, margin: '0 0 10px',
          }}>
            You've used up your plan's usage
          </h2>
          <p style={{ fontSize: 13, lineHeight: '20px', color: SP.text, margin: '0 auto 20px', maxWidth: 280 }}>
            Your subscription is still active, so there's no plan to re-subscribe to.
            To get more usage added to your current plan, contact support.
          </p>
          <a
            href={`mailto:${SUPPORT_EMAIL}`}
            style={{
              display: 'inline-block', padding: '12px 22px', borderRadius: 9999,
              fontSize: 13, fontWeight: 700, background: SP.blue, color: '#fff',
              textDecoration: 'none', boxShadow: '0 4px 12px rgba(0,88,190,0.35)',
            }}
          >
            Contact support → {SUPPORT_EMAIL}
          </a>
        </div>
      ) : selectedPlan ? (
        // ── Receipt / confirm screen ──────────────────────────────────────
        <div style={{ opacity: grayedOut ? 0.4 : 1, pointerEvents: grayedOut ? 'none' : 'auto' }}>
          <div style={{ textAlign: 'center', marginBottom: 18 }}>
            <BrandLogo size={40} radius={11} shadow="0 4px 16px rgba(0,88,190,0.3)" style={{ marginBottom: 10 }} />
            <h2 style={{
              fontFamily: "'Manrope', 'Plus Jakarta Sans', sans-serif",
              fontSize: 22, lineHeight: '30px', letterSpacing: '-0.02em', fontWeight: 800,
              color: SP.heading, margin: '0 0 6px',
            }}>
              Confirm subscription
            </h2>
          </div>

          <div style={{ background: SP.card, borderRadius: 14, boxShadow: SP.shadow, padding: 16, marginBottom: 16 }}>
            {[
              ['Plan', displayPlanName(selectedPlan._tierIndex, selectedPlan.billing_type)],
              ['Billing', selectedPlan.billing_type === 'YEARLY' ? 'Yearly' : 'Monthly'],
              ['Card', cardLabel || 'On file'],
              ['Total', `${currency}${Number(selectedPlan.product_price).toFixed(0)} / ${selectedPlan.billing_type === 'YEARLY' ? 'yr' : 'mo'}`],
            ].map(([k, v], i, arr) => (
              <div key={k} style={{ display: 'flex', justifyContent: 'space-between', padding: '9px 0', borderBottom: i < arr.length - 1 ? '1px solid rgba(15,23,42,0.06)' : 'none' }}>
                <span style={{ fontSize: 12, color: SP.text3 }}>{k}</span>
                <span style={{ fontSize: 13, fontWeight: 600, color: SP.heading }}>{v}</span>
              </div>
            ))}
          </div>

          {paidPending ? (
            // Charge already went through — this only re-checks, never re-pays.
            <>
              <div style={{ fontSize: 12, color: SP.text3, textAlign: 'center', marginBottom: 10 }}>
                Payment received — confirming your subscription.
              </div>
              <button
                onClick={handleContinue}
                disabled={busy}
                style={{
                  width: '100%', padding: '12px', borderRadius: 9999, fontSize: 13, fontWeight: 700,
                  background: SP.blue, color: '#fff', border: 'none', cursor: busy ? 'not-allowed' : 'pointer',
                  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
                  boxShadow: '0 4px 12px rgba(0,88,190,0.35)', marginBottom: 8,
                }}
              >
                {checking ? <><Spin /> Checking…</> : 'Check again →'}
              </button>
            </>
          ) : (
            <>
              <button
                onClick={handleConfirmSubscribe}
                disabled={busy}
                style={{
                  width: '100%', padding: '12px', borderRadius: 9999, fontSize: 13, fontWeight: 700,
                  background: SP.blue, color: '#fff', border: 'none', cursor: busy ? 'not-allowed' : 'pointer',
                  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
                  boxShadow: '0 4px 12px rgba(0,88,190,0.35)', marginBottom: 8,
                }}
              >
                {confirmBusy ? <><Spin /> Charging…</> : 'Confirm & Subscribe →'}
              </button>

              {!busy && (
                <button
                  onClick={() => { setSelectedPlan(null); setError('') }}
                  style={{ width: '100%', padding: '8px', background: 'none', border: 'none', color: SP.text3, fontSize: 12, cursor: 'pointer' }}
                >
                  ← Back to plans
                </button>
              )}
            </>
          )}

          {error && (
            <div style={{ background: SP.redLight, color: SP.red, border: '1px solid rgba(179,38,30,0.2)', borderRadius: 10, padding: '10px 14px', fontSize: 12, marginTop: 8, textAlign: 'center' }}>
              {error}
            </div>
          )}
        </div>
      ) : (
        // ── Plan list ──────────────────────────────────────────────────────
        <div style={{ opacity: grayedOut ? 0.4 : 1, pointerEvents: grayedOut ? 'none' : 'auto' }}>
          <div style={{ textAlign: 'center', marginBottom: 18 }}>
            <BrandLogo size={40} radius={11} shadow="0 4px 16px rgba(0,88,190,0.3)" style={{ marginBottom: 10 }} />
            <h2 style={{
              fontFamily: "'Manrope', 'Plus Jakarta Sans', sans-serif",
              fontSize: 22, lineHeight: '30px', letterSpacing: '-0.02em', fontWeight: 800,
              color: SP.heading, margin: '0 0 6px',
            }}>
              Choose your plan
            </h2>
            <p style={{ fontSize: 13, lineHeight: '20px', color: SP.text, margin: 0 }}>
              Pick a plan to keep using {appNm}.
            </p>
          </div>

          {blockReason && (
            <div style={{
              background: SP.redLight, color: SP.red, border: '1px solid rgba(179,38,30,0.2)',
              borderRadius: 10, padding: '10px 14px', fontSize: 12, fontWeight: 600,
              marginBottom: 18, textAlign: 'center',
            }}>
              {(REASON_COPY[blockReason]?.(trialDays)) || 'Your access has ended.'} Choose a plan below to continue.
            </div>
          )}

          {tiers && (
            <div style={{
              display: 'flex', background: SP.card, borderRadius: 9999, padding: 4,
              boxShadow: SP.shadow, marginBottom: 16,
            }}>
              {['MONTHLY', 'YEARLY'].map(cycle => (
                <button
                  key={cycle}
                  onClick={() => setBillingCycle(cycle)}
                  style={{
                    flex: 1, padding: '9px 10px', borderRadius: 9999, border: 'none',
                    background: billingCycle === cycle ? SP.blue : 'transparent',
                    color: billingCycle === cycle ? '#fff' : SP.text,
                    fontSize: 12, fontWeight: 700, cursor: 'pointer',
                    display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
                  }}
                >
                  {cycle === 'MONTHLY' ? 'Monthly' : 'Yearly'}
                  {cycle === 'YEARLY' && savingsPct > 0 && (
                    <span style={{
                      fontSize: 9, fontWeight: 700, padding: '2px 6px', borderRadius: 9999,
                      background: billingCycle === cycle ? 'rgba(255,255,255,0.25)' : SP.blueLight,
                      color: billingCycle === cycle ? '#fff' : SP.blueDark,
                    }}>
                      SAVE {savingsPct}%
                    </span>
                  )}
                </button>
              ))}
            </div>
          )}

          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 16 }}>
            {displayPlans.map((plan, i) => {
              if (!plan) return null // guard: cycle missing for this tier — skip rather than render a broken card
              const features = TIER_FEATURES[Math.min(i, 2)] || []
              const isLowest = i === 0
              const showTrial = isLowest && trialAvailable

              return (
                <div key={plan.product_code} style={{
                  position: 'relative', background: SP.card, borderRadius: 14,
                  border: isLowest ? `2px solid ${SP.blue}` : `1px solid ${SP.outline}`,
                  boxShadow: SP.shadow, padding: '16px 16px 14px',
                }}>
                  {isLowest && (
                    <span style={{
                      position: 'absolute', top: -10, left: 14,
                      background: SP.blue, color: '#fff', fontSize: 9, fontWeight: 700,
                      padding: '3px 9px', borderRadius: 9999, letterSpacing: '.04em',
                    }}>
                      {trialAvailable ? `${trialDays}-DAY FREE TRIAL` : 'MOST POPULAR'}
                    </span>
                  )}

                  <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginTop: isLowest ? 6 : 0 }}>
                    <span style={{ fontSize: 15, fontWeight: 700, color: SP.heading }}>
                      {displayPlanName(i, plan.billing_type)}
                    </span>
                    <span style={{ fontSize: 18, fontWeight: 800, color: SP.heading }}>
                      {currency}{Number(plan.product_price).toFixed(0)}
                      <span style={{ fontSize: 11, fontWeight: 500, color: SP.text3 }}>
                        /{plan.billing_type === 'YEARLY' ? 'yr' : 'mo'}
                      </span>
                    </span>
                  </div>

                  <div style={{ display: 'flex', flexDirection: 'column', gap: 5, marginTop: 10, paddingTop: 10, borderTop: `1px solid rgba(15,23,42,0.06)` }}>
                    {features.map(({ text, ok }) => (
                      <div key={text} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span style={{ fontSize: 11, color: ok ? SP.green : SP.text3 }}>{ok ? '✓' : '–'}</span>
                        <span style={{ fontSize: 12, color: ok ? SP.text : SP.text3 }}>{text}</span>
                      </div>
                    ))}
                  </div>

                  {showTrial && (
                    <button
                      onClick={handleStartTrial}
                      disabled={busy}
                      style={{
                        width: '100%', padding: '11px', borderRadius: 9999, fontSize: 13, fontWeight: 700,
                        background: SP.blue, color: '#fff', border: 'none', cursor: busy ? 'not-allowed' : 'pointer', marginTop: 12,
                        boxShadow: '0 4px 12px rgba(0,88,190,0.35)',
                      }}
                    >
                      {trialBusy ? <><Spin /> Starting…</> : `Start ${trialDays}-day free trial →`}
                    </button>
                  )}

                  <button
                    onClick={() => handleChoosePlan(plan, i)}
                    disabled={busy}
                    style={{
                      width: '100%', padding: '11px', borderRadius: 9999, fontSize: 13, fontWeight: 700,
                      background: showTrial ? 'transparent' : SP.blue,
                      color: showTrial ? SP.blue : '#fff',
                      border: showTrial ? `1px solid ${SP.blue}` : 'none',
                      cursor: busy ? 'not-allowed' : 'pointer',
                      marginTop: showTrial ? 8 : 12,
                    }}
                  >
                    {billingDetailsAdded ? 'Subscribe →' : 'Add payment method →'}
                  </button>
                </div>
              )
            })}
          </div>

          {!billingDetailsAdded && cardAddUrl && (
            <button
              onClick={handleContinue}
              disabled={busy}
              style={{
                width: '100%', padding: '10px', borderRadius: 9999, fontSize: 12, fontWeight: 600,
                background: 'none', border: 'none', color: SP.blue, cursor: busy ? 'not-allowed' : 'pointer',
                display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, marginBottom: 8,
              }}
            >
              {checking ? <><Spin color={SP.blue} /> Checking…</> : "Already added a payment method or paid? Continue →"}
            </button>
          )}

          {error && (
            <div style={{
              background: SP.redLight, color: SP.red, border: '1px solid rgba(179,38,30,0.2)',
              borderRadius: 10, padding: '10px 14px', fontSize: 12, marginBottom: 8, textAlign: 'center',
            }}>
              {error}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
