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
import { salesplaySubscriptionPayment, salesplaySubscriptionPreview } from './embedApi'
import { notifyParent } from './EmbedApp'
import { appName } from './embedBranding'
import BrandLogo from '../components/Logo'
import salesplayLogo from '../assets/salesplay-logo.svg'
import { TIER_FEATURES, groupPlansByTier, planPrice, yearlySavingsPct, displayPlanName } from './embedSalesplayPlanFormat'

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

const SUPPORT_EMAIL = 'support@datamind.ai'

const REASON_COPY = {
  trial_expired:  (days) => `Your ${days}-day free trial has ended.`,
  plan_expired:   () => 'Your subscription has expired.',
  quota_exceeded: () => "You've used up your plan's quota.",
}

// show_price/available_credit (the raw numbers) are Salesplay's base tier
// code (5/10/25), NOT the merchant's charged amount — only *_text carries
// the real converted currency value ("LKR 3,305.97"). Math must run on the
// text fields; the raw numbers are for other things entirely.
function parseAmountText(text) {
  if (typeof text === 'number') return text
  return Number(String(text || '').replace(/[^0-9.]/g, '')) || 0
}

// Card-add happens on Salesplay's own page in another tab — poll for it, so
// the user isn't stuck needing a manual refresh to move on.
const CARD_POLL_INTERVAL_MS = Number(import.meta.env.VITE_SALESPLAY_CARD_POLL_INTERVAL_MS) || 3000
const sleep = (ms) => new Promise(r => setTimeout(r, ms))

// ponytail: tier index → DataMind subscription_plans.id/name. Matches the
// current DB seed (Standard=1, Growth=2, Pro=3, sort_order 1/2/3 — same order
// as ascending price, same order Salesplay's tiers are grouped in above).
// Revisit if subscription_plans is ever reseeded with different ids/order.
const TIER_TO_INTERNAL_PLAN_ID = [1, 2, 3]

function Spin({ color = '#fff' }) {
  return <div style={{ width:13, height:13, border:`2px solid ${color === '#fff' ? 'rgba(255,255,255,0.3)' : 'rgba(0,88,190,0.25)'}`, borderTopColor:color, borderRadius:'50%', animation:'spin 0.7s linear infinite' }} />
}

export default function EmbedSalesplayPlans({ context, partnerKey, aat, plans, trialAvailable, blockReason, isPaidQuotaBlocked, trialDays = 14, billingDetailsAdded, cardAddUrl, cardLabel, availableCreditText, showPriceText, initialExpanded = false, onTrialSelected, onSubscribed, onRefreshAccess, onClose }) {
  const [selectedPlan, setSelectedPlan] = useState(null) // { ...plan, _tierIndex } under review on the receipt screen
  const [checking, setChecking] = useState(false) // re-checking access after returning from card_add_url (manual "Continue")
  const [awaitingCard, setAwaitingCard] = useState(false) // polling for a card add — grays out the screen
  const [confirmBusy, setConfirmBusy] = useState(false) // paying + confirming activation
  const [paidPending, setPaidPending] = useState(false) // charge succeeded — blocks re-paying even if the confirm check below fails
  const [billingCycle, setBillingCycle] = useState('MONTHLY') // global toggle — one cycle for all 3 tiers
  const [plansExpanded, setPlansExpanded] = useState(initialExpanded) // trial-available plan list starts collapsed to just the trial CTA, unless the consent screen's "Explore plans" sent us here already open
  const [error, setError] = useState('')
  const [preview, setPreview] = useState(null) // raw order/preview response for the selected plan
  const [previewLoading, setPreviewLoading] = useState(false)
  const [paymentSuccess, setPaymentSuccess] = useState(false) // charge confirmed — shows the checkmark before handing off to chat
  const appNm = appName(context)
  const cardPollActive = useRef(false)

  useEffect(() => () => { cardPollActive.current = false }, []) // stop polling on unmount

  // Real per-order pricing (product_price × qty, credits, amount due) for
  // whichever plan is on the receipt screen. Salesplay's own numbers, shown
  // verbatim — see the Price/Charged-today rendering below for why we never
  // parse or reformat these.
  useEffect(() => {
    if (!selectedPlan) { setPreview(null); return }
    let cancelled = false
    setPreview(null)
    setPreviewLoading(true)
    setError('')
    salesplaySubscriptionPreview({
      partner_key: partnerKey,
      aat,
      subscription_type: Number(selectedPlan.subscription_type),
      product_code: selectedPlan.product_code,
      product_type: 'ADDON',
      coupon_code_verified: 0,
      coupon_code: '',
    })
      .then(res => {
        if (cancelled) return
        if (res?.status === 'success') setPreview(res.data)
        else setError(res?.message || 'Could not load pricing. Please try again.')
      })
      .catch(e => {
        if (cancelled) return
        setError(e.response?.data?.detail || e.message || 'Could not load pricing. Please try again.')
      })
      .finally(() => { if (!cancelled) setPreviewLoading(false) })
    return () => { cancelled = true }
  }, [selectedPlan])

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
    // No usable card (is_valid_card_added false) — never reach the payment
    // screen. Salesplay's own hosted page collects the card; with no
    // card_add_url to send them to there is nothing we can do but say so,
    // which beats letting them into a charge that can only fail.
    if (!billingDetailsAdded) {
      if (!cardAddUrl) {
        setError('Add a payment method in Salesplay before subscribing.')
        return
      }
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
    // The one place that charges a card. is_valid_card_added is the only thing
    // that says a charge can succeed, so re-check it here rather than trusting
    // that the screen we came from checked — a refresh poll can flip it while
    // this screen is open.
    if (!billingDetailsAdded) {
      setError('Add a payment method in Salesplay before subscribing.')
      setSelectedPlan(null) // back to the plans list, which offers the card-add route
      return
    }
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
      // happens next. Show the success checkmark first (the activation is
      // already done by now), then hand off to chat — the re-check below is
      // just confirming what's already true, not waiting on anything.
      setPaidPending(true)
      setConfirmBusy(false)
      setPaymentSuccess(true)
      await sleep(1400)
      await onSubscribed() // parent switches to 'chat' on success
    } catch (e) {
      setPaymentSuccess(false)
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
      setError(e.response?.data?.detail || e.message || 'Still not seeing an active subscription. Please try again.')
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
  const busy = confirmBusy || checking || awaitingCard || trialBusy || paymentSuccess

  // Price and Charged-today come straight from /subscriptions/order/preview
  // (data.invoiceTotal, data.invoice_amount_due_format) — Salesplay's own
  // already-formatted, already-computed currency strings, shown verbatim.
  // Never parsed or re-derived: that's real money, and the preview call is
  // the one place Salesplay tells us exactly what a card will be charged for
  // this specific plan/qty, ahead of the actual payment call.
  const previewProduct = preview?.productData?.[0]
  const selectedPriceText = preview?.invoiceTotal || (selectedPlan ? planPrice(selectedPlan) : showPriceText)
  const actualChargeText = preview?.invoice_amount_due_format || selectedPriceText
  // ponytail: credit line hidden from the receipt UI — deduction display
  // (strikethrough price / "Available credit" row) commented out below with
  // it. Charged-today still comes from Salesplay's own preview total, which
  // already nets out credit server-side, so nothing here affects the charge.
  // const creditAmount = parseAmountText(availableCreditText)
  // const hasCredit = creditAmount > 0
  // const creditAmountText = availableCreditText

  // Monthly/Yearly toggle + tier cards — shared between the trial-available
  // accordion body and the no-trial (blocked/expired) straight list, so the
  // two call sites never drift apart.
  function renderTierList() {
    return (
      <>
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

        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
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
                {isLowest && !trialAvailable && (
                  <span style={{
                    position: 'absolute', top: -10, left: 14,
                    background: SP.blue, color: '#fff', fontSize: 9, fontWeight: 700,
                    padding: '3px 9px', borderRadius: 9999, letterSpacing: '.04em',
                  }}>
                    MOST POPULAR
                  </span>
                )}

                <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginTop: isLowest ? 6 : 0 }}>
                  <span style={{ fontSize: 15, fontWeight: 700, color: SP.heading }}>
                    {displayPlanName(i, plan.billing_type)}
                  </span>
                  <span style={{ fontSize: 18, fontWeight: 800, color: SP.heading }}>
                    {planPrice(plan)}
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

                {/* Trial CTA already lives in the box header above this
                    accordion when trialAvailable — no second one here. */}
                <button
                  onClick={() => handleChoosePlan(plan, i)}
                  disabled={busy}
                  style={{
                    width: '100%', padding: '11px', borderRadius: 9999, fontSize: 13, fontWeight: 700,
                    background: showTrial ? 'transparent' : SP.blue,
                    color: showTrial ? SP.blue : '#fff',
                    border: showTrial ? `1px solid ${SP.blue}` : 'none',
                    cursor: busy ? 'not-allowed' : 'pointer',
                    marginTop: 12,
                  }}
                >
                  Subscribe Now
                </button>
              </div>
            )
          })}
        </div>
      </>
    )
  }

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

          {/* Card visual — stands in for the "which card gets charged" line a
              real checkout shows, branded to Salesplay (the actual payment
              gateway) instead of a generic Visa/Mastercard mark. */}
          <div style={{
            background: `linear-gradient(135deg, ${SP.blueDark} 0%, ${SP.blue} 100%)`,
            borderRadius: 16, padding: '18px 20px', marginBottom: 12,
            boxShadow: '0 8px 24px rgba(0,26,66,0.28)', color: '#fff', position: 'relative', overflow: 'hidden',
          }}>
            <div style={{
              position: 'absolute', top: -30, right: -30, width: 120, height: 120,
              borderRadius: '50%', background: 'rgba(255,255,255,0.06)',
            }} />
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '.08em', color: 'rgba(255,255,255,0.65)' }}>PAYMENT METHOD</span>
            </div>
            <div style={{ fontSize: 17, fontWeight: 700, letterSpacing: '.12em', margin: '20px 0 4px' }}>
              {cardLabel || '···· ···· ···· ····'}
            </div>
            <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.7)' }}>{cardLabel ? 'On file with Salesplay' : 'On file'}</div>
          </div>

          <div style={{ background: SP.card, borderRadius: 14, boxShadow: SP.shadow, padding: 16, marginBottom: 16 }}>
            {[
              ['Plan', displayPlanName(selectedPlan._tierIndex, selectedPlan.billing_type)],
              ['Billing', previewProduct?.product_price
                ? `${previewProduct.product_price} /${selectedPlan.billing_type === 'YEARLY' ? 'year' : 'month'} per shop`
                : `${selectedPlan.billing_type === 'YEARLY' ? 'Yearly' : 'Monthly'}${previewProduct?.activation_period ? ` (${previewProduct.activation_period})` : ''}`],
            ].map(([k, v], i) => (
              <div key={k} style={{ display: 'flex', justifyContent: 'space-between', padding: '9px 0', borderBottom: '1px solid rgba(15,23,42,0.06)' }}>
                <span style={{ fontSize: 12, color: SP.text3 }}>{k}</span>
                <span style={{ fontSize: 13, fontWeight: 600, color: SP.heading }}>{v}</span>
              </div>
            ))}

            {previewLoading ? (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, padding: '20px 0' }}>
                <Spin color={SP.blue} />
                <span style={{ fontSize: 12, color: SP.text3 }}>Loading pricing…</span>
              </div>
            ) : (
              <>
                <div style={{ display: 'flex', justifyContent: 'space-between', padding: '9px 0', borderBottom: '1px solid rgba(15,23,42,0.06)' }}>
                  <span style={{ fontSize: 12, color: SP.text3 }}>Price</span>
                  <span style={{ fontSize: 13, fontWeight: 600, color: SP.heading }}>
                    {previewProduct?.product_price && previewProduct?.product_qty
                      ? `${previewProduct.product_price} x ${previewProduct.product_qty} = ${selectedPriceText}`
                      : selectedPriceText}
                  </span>
                </div>

                {/* ponytail: "Available credit" row hidden — re-enable both
                    this and the hasCredit consts above together if it comes back. */}

                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px 0 0' }}>
                  <span style={{ fontSize: 13, fontWeight: 700, color: SP.heading }}>Charged today</span>
                  <span style={{ fontSize: 18, fontWeight: 800, color: SP.blue }}>{actualChargeText}</span>
                </div>
              </>
            )}
          </div>

          {paymentSuccess ? (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '10px 0 4px' }}>
              <div className="dm-success-wrap" style={{ marginBottom: 10 }}>
                <svg width="56" height="56" viewBox="0 0 56 56" fill="none">
                  <circle cx="28" cy="28" r="24" className="dm-success-circle" stroke={SP.green} strokeWidth="3" fill="rgba(0,105,71,0.06)" />
                  <path d="M18 28.5 24.5 35 38 21" className="dm-success-check" stroke={SP.green} strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round" fill="none" />
                </svg>
              </div>
              <div style={{ fontSize: 15, fontWeight: 700, color: SP.heading }}>Payment successful</div>
              <div style={{ fontSize: 12, color: SP.text3, marginTop: 2 }}>salesplay AI</div>
            </div>
          ) : paidPending ? (
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
                {/* busy, not checking: setPaidPending(true) renders this branch
                    while the post-payment confirm is still in flight
                    (confirmBusy), so keying off `checking` alone flashed the
                    idle "Check again" for a frame before the parent switched to
                    chat. It only reads as a retry once everything has settled. */}
                {busy ? <><Spin /> Checking…</> : 'Check again →'}
              </button>
            </>
          ) : (
            <>
              <button
                onClick={handleConfirmSubscribe}
                disabled={busy || previewLoading || !preview}
                style={{
                  width: '100%', padding: '12px', borderRadius: 9999, fontSize: 13, fontWeight: 700,
                  background: SP.blue, color: '#fff', border: 'none',
                  cursor: (busy || previewLoading || !preview) ? 'not-allowed' : 'pointer',
                  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
                  boxShadow: '0 4px 12px rgba(0,88,190,0.35)', marginBottom: 8,
                }}
              >
                {confirmBusy ? <><Spin /> Charging…</> : 'Confirm & Subscribe →'}
              </button>
            </>
          )}

          {error && (
            <div style={{ background: SP.redLight, color: SP.red, border: '1px solid rgba(179,38,30,0.2)', borderRadius: 10, padding: '10px 14px', fontSize: 12, marginTop: 8, textAlign: 'center' }}>
              {error}
            </div>
          )}
        </div>
      ) : trialAvailable ? (
        // ── Plan list — trial available: single centered box, CTA + an
        // accordion of plans that expands/collapses in place (no screen
        // switch). Sitting inside a vertically-centered flex column means
        // the box grows evenly up and down as it expands, for free. ─────────
        <div style={{
          opacity: grayedOut ? 0.4 : 1, pointerEvents: grayedOut ? 'none' : 'auto',
          height: '100%', display: 'flex', flexDirection: 'column',
          justifyContent: 'center', alignItems: 'center', textAlign: 'center',
        }}>
          <BrandLogo size={40} radius={11} shadow="0 4px 16px rgba(0,88,190,0.3)" style={{ marginBottom: 10 }} />
          <h2 style={{
            fontFamily: "'Manrope', 'Plus Jakarta Sans', sans-serif",
            fontSize: 22, lineHeight: '30px', letterSpacing: '-0.02em', fontWeight: 800,
            color: SP.heading, margin: '0 0 20px',
          }}>
            {plansExpanded ? 'Choose your plan' : "You're all set"}
          </h2>

          <div style={{
            width: '100%', maxWidth: 360, textAlign: 'left', background: SP.card,
            borderRadius: 16, boxShadow: SP.shadow, border: `1px solid ${SP.outline}`, overflow: 'hidden',
          }}>
            <div style={{ padding: 16 }}>
              <button
                onClick={handleStartTrial}
                disabled={busy}
                style={{
                  width: '100%', padding: '13px', borderRadius: 9999, fontSize: 14, fontWeight: 700,
                  background: SP.blue, color: '#fff', border: 'none', cursor: busy ? 'not-allowed' : 'pointer',
                  boxShadow: '0 4px 12px rgba(0,88,190,0.35)',
                }}
              >
                {trialBusy ? <><Spin /> Starting…</> : `Start Free Trial (${trialDays} Days)`}
              </button>
            </div>

            <button
              onClick={() => setPlansExpanded(v => !v)}
              style={{
                width: '100%', padding: '4px 16px 14px', background: 'transparent', border: 'none',
                color: SP.text3, fontSize: 12, fontWeight: 600, cursor: 'pointer',
                display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
              }}
            >
              {plansExpanded ? 'Hide plans' : 'Explore plans'}
              <span style={{
                display: 'inline-block', width: 7, height: 7,
                borderRight: `1.5px solid ${SP.text3}`, borderBottom: `1.5px solid ${SP.text3}`,
                transform: plansExpanded ? 'rotate(-135deg)' : 'rotate(45deg)',
                transition: 'transform .3s ease', marginTop: plansExpanded ? 2 : -2,
              }} />
            </button>

            <div style={{ display: 'grid', gridTemplateRows: plansExpanded ? '1fr' : '0fr', transition: 'grid-template-rows .35s ease' }}>
              <div style={{ overflow: 'hidden' }}>
                <div style={{ padding: '4px 16px 16px', borderTop: `1px solid rgba(15,23,42,0.08)` }}>
                  {renderTierList()}
                </div>
              </div>
            </div>
          </div>

          {error && (
            <div style={{
              width: '100%', maxWidth: 360, background: SP.redLight, color: SP.red,
              border: '1px solid rgba(179,38,30,0.2)', borderRadius: 10, padding: '10px 14px',
              fontSize: 12, marginTop: 12, textAlign: 'center',
            }}>
              {error}
            </div>
          )}
        </div>
      ) : (
        // ── Plan list — no trial (blocked/expired) — straight list ────────
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

          {renderTierList()}

          {error && (
            <div style={{
              background: SP.redLight, color: SP.red, border: '1px solid rgba(179,38,30,0.2)',
              borderRadius: 10, padding: '10px 14px', fontSize: 12, marginTop: 8, textAlign: 'center',
            }}>
              {error}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
