/**
 * EmbedSalesplayAutoInit.jsx
 *
 * Salesplay-specific onboarding for the DataMind iframe embed.
 * No manual API token entry — uses the Salesplay session token (aat) passed
 * as a URL param by the Salesplay website.
 *
 * Flow:
 *   consent  → user reads what they can do with DataMind and clicks Accept
 *   loading  → single backend call handles everything (profile, token, account)
 *   sync     → first-time workspace setup with progress bar (trial intent only)
 *   error    → something went wrong, with retry
 *
 * "Subscribe Now" skips the sync screen entirely and hands straight off to the
 * plans screen: that merchant came to pay, so card-add and payment come first
 * and the sync screen runs after the charge clears (EmbedApp's 'syncing'
 * state). Sync still starts server-side here either way — only the waiting
 * moves.
 */
import React, { useState, useEffect, useRef } from 'react'
import { salesplayOnboard, embedGetProviderStatus, embedGetPlans, salesplaySubscriptionInfo } from './embedApi'
import { notifyParent } from './EmbedApp'
import { appName, productTitle as resolveProductTitle, resolveBrand } from './embedBranding'
import BrandLogo from './BrandLogo'
import BetaBadge from '../components/BetaBadge'
import EmbedSyncProgress from './EmbedSyncProgress'
import { TIER_FEATURES, groupPlansByTier, planPrice, yearlySavingsPct, displayPlanName } from './embedSalesplayPlanFormat'
import { fmtTok } from '../formatTokens'
import * as storage from './embedStorage'

// ── SalesPlay visual language (mirrors EmbedChat's isPartnerFlow branch) ───────
const SP = {
  bg:        'linear-gradient(180deg, #F0F4F8 0%, #F7F9FB 100%)',
  card:      '#FFFFFF',
  heading:   '#191C1E',
  text:      '#545F73',
  text3:     '#8B93A7',
  // Reads the CSS variable applyBrandChrome() sets from the brand's
  // primary_color, with the literal only as a fallback. A hardcoded hex here
  // silently ignored every brand but the one it was picked for -- invisible
  // today because both brands share this blue, and wrong the moment one does
  // not.
  blue:      'var(--blue, #0058BE)',
  blueLight: '#D8E2FF',
  blueDark:  '#001A42',
  outline:   '#C2C6D6',
  green:     '#006947',
  shadow:    '0px 4px 20px 0px rgba(84,95,115,0.12)',
}


const PLAN_FEATURES = {
  Starter: [
    { text: 'Ask Your Data (AI)',        ok: true  },
    { text: 'All Analytics',             ok: true  },
    { text: 'All Reports',               ok: true  },
    { text: 'Forecasting & Anomalies',   ok: false },
    { text: 'Priority Support',          ok: false },
    { text: '3 Months data history',     ok: true  },
  ],
  Growth: [
    { text: 'Ask Your Data (AI)',        ok: true  },
    { text: 'All Analytics',             ok: true  },
    { text: 'All Reports',               ok: true  },
    { text: 'Forecasting & Anomalies',   ok: true  },
    { text: 'Priority Support',          ok: false },
    { text: '12 Months data history',    ok: true  },
  ],
  Pro: [
    { text: 'Ask Your Data (AI)',        ok: true  },
    { text: 'All Analytics',             ok: true  },
    { text: 'All Reports',               ok: true  },
    { text: 'Forecasting & Anomalies',   ok: true  },
    { text: 'Priority Support',          ok: true  },
    { text: 'External API integrations', ok: true  },
    { text: 'Web widget',                ok: true  },
    { text: 'All historical data',       ok: true  },
  ],
}

// ── Shared styles ──────────────────────────────────────────────────────────
const primaryBtn = (disabled, sp) => ({
  width: '100%', padding: sp ? '14px' : '12px', borderRadius: sp ? 9999 : 8, fontSize: 13, fontWeight: 700,
  background: disabled
    ? (sp ? '#CBD5E1' : 'var(--bg3)')
    : (sp ? SP.blue : 'linear-gradient(135deg,#4f8ef7,#7c6af7)'),
  color: disabled ? (sp ? '#fff' : 'var(--text3)') : '#fff',
  border: disabled ? (sp ? 'none' : '1px solid var(--border2)') : 'none',
  cursor: disabled ? 'not-allowed' : 'pointer',
  marginTop: 6,
  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
  boxShadow: sp && !disabled ? '0 4px 12px rgba(0,88,190,0.35)' : 'none',
  transition: 'background .15s, box-shadow .15s',
})

const cardStyle = (sp) => ({
  background: sp ? SP.card : 'var(--bg2)',
  border: sp ? 'none' : '1px solid var(--border)',
  borderRadius: sp ? 14 : 8,
  padding: '14px',
  textAlign: 'left',
  marginBottom: 16,
  boxShadow: sp ? SP.shadow : 'none',
})

const rowStyle = (sp, isLast) => ({
  display: 'flex', gap: 10, alignItems: 'center',
  padding: '7px 0',
  borderBottom: isLast ? 'none' : `1px solid ${sp ? 'rgba(15,23,42,0.06)' : 'var(--border)'}`,
})

function Spin({ sp }) {
  return (
    <div style={{
      width: 13, height: 13,
      border: `2px solid ${sp ? 'rgba(255,255,255,0.3)' : 'rgba(255,255,255,0.3)'}`,
      borderTopColor: '#fff',
      borderRadius: '50%',
      animation: 'spin 0.7s linear infinite',
    }} />
  )
}

function Logo({ brand }) {
  return <BrandLogo brand={brand} size={32} radius={0} style={{ marginBottom: 10 }} />
}

export default function EmbedSalesplayAutoInit({ context, partnerKey, aatToken, onComplete, onError, onClose }) {
  const sp = context?.flow === 'partner'

  // Launch period (backend SUBSCRIPTION_FREE, served on /embed/context): no
  // prices anywhere. One "Try <app>" button, no plan tiers, no explore
  // accordion — and nothing to fetch pricing for.
  const subscriptionFree = !!context?.subscription_free

  const [phase, setPhase]       = useState('consent')  // 'consent' | 'profile' | 'account' | 'sync' | 'error'
  const [pendingComplete, setPendingComplete] = useState(null) // { token, profile } handed to onComplete once sync finishes
  const [errorMsg, setErrorMsg] = useState('')
  const [loading, setLoading]   = useState(false)
  const [plans, setPlans]       = useState([])
  const [selectedPlan, setSelectedPlan] = useState(null)

  // Real Salesplay pricing preview — shown when "Explore plans" is expanded,
  // fetched pre-account (aat alone is enough for /subscription/info; no
  // DataMind account needed just to look at prices).
  const [spExpanded, setSpExpanded]       = useState(false)
  const [spCycle, setSpCycle]             = useState('MONTHLY')
  const [spTiers, setSpTiers]             = useState(null) // null = not fetched yet
  const [spPreviewLoading, setSpPreviewLoading] = useState(false)
  const [spPreviewError, setSpPreviewError]     = useState('')
  const scrollRef = useRef(null) // outer scroll container
  const boxRef     = useRef(null) // CTA + explore-plans box

  // Auto-scroll so the expanding/collapsing box is always in view — no
  // manual scrolling needed. Timed to the accordion's own transition
  // (.35s ease, see the grid-template-rows div below) rather than firing
  // immediately, so it doesn't scroll to a height the box hasn't reached yet.
  useEffect(() => {
    const el = scrollRef.current
    const box = boxRef.current
    if (!el || !box) return
    const t = setTimeout(() => {
      if (spExpanded) {
        el.scrollTo({ top: box.offsetTop + box.offsetHeight - el.clientHeight, behavior: 'smooth' })
      } else {
        el.scrollTo({ top: 0, behavior: 'smooth' })
      }
    }, 350)
    return () => clearTimeout(t)
  }, [spExpanded])

  // Load subscription plans for the consent screen pricing section.
  // Non-fatal — if this fails, the consent screen still renders without pricing.
  useEffect(() => {
    if (subscriptionFree) return   // no pricing section to fill
    embedGetPlans()
      .then(r => setPlans(Array.isArray(r?.plans) ? r.plans : []))
      .catch(() => setPlans([]))
  }, [subscriptionFree])

  // Default-select the first plan once plans load (drives the highlighted card).
  useEffect(() => {
    if (plans.length > 0 && selectedPlan === null) {
      setSelectedPlan(plans[0]?.id ?? plans[0]?.name)
    }
  }, [plans, selectedPlan])

  // Read aat directly from URL params — more reliable than the prop which travels
  // through React state and can be empty on the first render in some React versions.
  const aat = aatToken || new URLSearchParams(window.location.search).get('aat') || ''

  const brand         = resolveBrand(context)
  const productTitle  = resolveProductTitle(context)
  const appNm         = appName(context)
  const providerName  = brand.companyName

  // "Explore plans" toggle — pure preview, no account created. Fetches once
  // on first expand, then just toggles open/closed.
  async function handleToggleExplore() {
    if (spExpanded) { setSpExpanded(false); return }
    setSpExpanded(true)
    if (spTiers !== null || spPreviewLoading) return
    setSpPreviewLoading(true)
    setSpPreviewError('')
    try {
      const info = await salesplaySubscriptionInfo(partnerKey, aat)
      const rawPlans = info?.data?.subscription?.[0]?.pricing_plans || []
      const tiers = groupPlansByTier(rawPlans)
      if (!tiers) {
        setSpPreviewError('Could not load pricing. Please try again.')
      } else {
        setSpTiers(tiers)
      }
    } catch (e) {
      setSpPreviewError(e.response?.data?.detail || e.message || 'Could not load pricing. Please try again.')
    } finally {
      setSpPreviewLoading(false)
    }
  }

  // Both buttons run the exact same onboarding call — there's no account yet
  // to start a trial or price real plans against. `intent` just tells
  // EmbedApp what to do once the account exists: 'trial' starts the trial
  // immediately and goes straight to chat; 'explore' lands on the plans
  // screen already expanded so the user can see pricing / subscribe.
  const intentRef = useRef('trial')
  // Which tier card was clicked, so the plans screen can go straight to that
  // plan's receipt instead of asking the user to pick all over again.
  const pickedPlanRef = useRef(null)

  async function handleAccept(intent = 'trial', pickedPlan = null) {
    intentRef.current = intent
    pickedPlanRef.current = pickedPlan
    setLoading(true)
    await runFlow()
    setLoading(false)
  }

  async function runFlow() {
    setErrorMsg('')

    if (!aat) {
      fail(`Session token not found. Please open ${appNm} from the ${brand.companyName} backoffice.`)
      return
    }

    // Single backend call — profile fetch, token creation, account setup all happen server-side.
    // 'explore' keeps the consent screen up while this runs: that merchant clicked a
    // priced tier, and a full-screen spinner between the click and the card is the
    // step they read as "nothing happened". Their button already spins "Setting up…".
    if (intentRef.current !== 'explore') setPhase('loading')
    let result
    try {
      result = await salesplayOnboard(partnerKey, aat)
    } catch (e) {
      const detail = e.response?.data?.detail || e.response?.data?.error || e.message || 'Setup failed. Please try again.'
      fail(detail)
      return
    }

    storage.setItem('dm_embed_token', result.token)
    if (result.user?.email) storage.setItem('dm_sp_email', result.user.email)

    // Carried through to onComplete so EmbedApp can force-show the plans
    // screen once for brand-new users regardless of computed access (their
    // trial is already silently active — this is informational/upsell).
    result.user = {
      ...result.user,
      is_new_user: result.is_new_user,
      embed_intent: intentRef.current,
      // { tierIndex, cycle } — the tier clicked here, so the plans screen opens
      // that plan's receipt (or its card-add redirect) with no second pick.
      embed_plan: pickedPlanRef.current,
    }

    // Only the trial path waits on sync here. "Subscribe Now" goes straight
    // to the plans screen — sync keeps running server-side and is shown after
    // payment instead, so nothing delays the merchant reaching the card.
    if (result.sync === 'started' && intentRef.current !== 'explore') {
      setPhase('sync')
      setPendingComplete({ token: result.token, profile: result.user })
      notifyParent('dm:onboarding_sync_started')
    } else {
      if (intentRef.current !== 'explore') notifyParent('dm:chat_open')
      onComplete(result.token, result.user)
    }
  }

  function fail(msg) {
    setErrorMsg(msg)
    setPhase('error')
    setLoading(false)
  }

  async function handleRetry() {
    setLoading(true)
    setPhase('consent')
    await runFlow()
    setLoading(false)
  }

  // ── Render ────────────────────────────────────────────────────────────────────
  const canClose = phase === 'consent' || phase === 'error'

  return (
    <div ref={scrollRef} className={sp ? 'dm-scroll-hidden' : undefined} style={{
      height: '100%', overflowY: 'auto',
      display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: phase === 'consent' ? 'flex-start' : 'center',
      padding: '24px 20px', textAlign: 'center',
      background: sp ? SP.bg : undefined,
      position: 'relative',
    }}>
      {canClose && onClose && (
        <button onClick={onClose} style={{
          position: 'absolute', top: 12, right: 12,
          background: 'none', border: 'none', cursor: 'pointer',
          color: sp ? SP.text3 : 'var(--text3)',
          fontSize: 18, lineHeight: 1, padding: 4, borderRadius: 6,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>✕</button>
      )}
      {sp && phase === 'consent' ? null : (
        <>
          <Logo brand={brand} />
          {/* Same reason as the consent screen below: the logo is a wordmark
              carrying the product name, so printing the title under it says it
              twice. Brands with no logo still need the name. */}
          {!brand?.logoUrl && (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 15, fontWeight: 700, color: sp ? SP.heading : 'var(--text)', marginBottom: 4 }}>
              {productTitle}
            </div>
          )}
        </>
      )}

      {/* ── CONSENT ────────────────────────────────────────────────────────── */}
      {phase === 'consent' && (sp ? (
        <div style={{ width: '100%' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10, margin: '14px 0 10px' }}>
            <BrandLogo brand={brand} size={44} radius={0} style={{ flexShrink: 0 }} />
            {/* The logo is a wordmark carrying the product name — printing the
                title beside it says the same thing twice. Brands with no logo
                still need the name. */}
            {!brand?.logoUrl && (
              <h2 style={{
                fontFamily: "'Manrope', 'Plus Jakarta Sans', sans-serif",
                fontSize: 26, lineHeight: '34px', letterSpacing: '-0.02em', fontWeight: 800,
                color: SP.heading, margin: 0,
              }}>
                {productTitle}
              </h2>
            )}
            {brand?.showBetaBadge && <BetaBadge size={11} style={{ alignSelf: 'center' }} />}
          </div>
          <p style={{ fontSize: 14, lineHeight: '22px', color: SP.text, marginBottom: 24 }}>
            Ask questions in your own language, discover past performance and emerging trends.
          </p>

          <div style={{ textAlign: 'left', marginBottom: 20 }}>
            <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '.08em', textTransform: 'uppercase', color: SP.text, marginBottom: 10, padding: '0 2px' }}>
              What you can do with {appNm}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {[
                { icon: '💬', text: 'Ask questions in your own language — no spreadsheets, no formulas' },
                { icon: '📊', text: 'Get instant answers about sales, top products & customers' },
                { icon: '🔮', text: 'Spot trends and unusual patterns before they become problems' },
                { icon: '⚡', text: `Insights in seconds, right inside ${providerName}` },
              ].map(({ icon, text }, i) => (
                <div key={i} style={{
                  display: 'flex', alignItems: 'center', gap: 12,
                  background: SP.card, padding: '12px 14px', borderRadius: 14,
                  boxShadow: SP.shadow, border: '1px solid rgba(255,255,255,0.6)',
                }}>
                  <span style={{ fontSize: 20, flexShrink: 0 }}>{icon}</span>
                  <span style={{ flex: 1, fontSize: 12, fontWeight: 500, color: SP.heading }}>{text}</span>
                </div>
              ))}
            </div>
          </div>

          {/* BILLING HIDDEN — subscription plan selection commented out
          {plans.length > 0 && (
            <div style={{
              textAlign: 'left', background: SP.card, padding: 16, borderRadius: 16,
              boxShadow: SP.shadow, border: '1px solid rgba(255,255,255,0.8)', marginBottom: 20,
            }}>
              <div style={{ fontSize: 14, fontWeight: 700, color: SP.heading, marginBottom: 2 }}>
                Subscription plans
              </div>
              <div style={{ fontSize: 11, color: SP.blue, marginBottom: 14 }}>
                Start with a free {plans[0]?.trial_days || 14}-day trial — no card required
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {plans.map((p, i) => {
                  const pid = p.id ?? p.name
                  const isSelected = selectedPlan === pid
                  const features = PLAN_FEATURES[p.name] || []
                  return (
                    <label key={pid} onClick={() => setSelectedPlan(pid)} style={{
                      position: 'relative', display: 'block',
                      padding: '12px 14px', borderRadius: 12, cursor: 'pointer',
                      border: isSelected ? `2px solid ${SP.blue}` : `1px solid ${SP.outline}`,
                      background: isSelected ? 'rgba(0,88,190,0.05)' : 'transparent',
                      transition: 'border-color .15s, background .15s',
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                        <div style={{ display: 'flex', flexDirection: 'column' }}>
                          <span style={{ fontSize: 12, fontWeight: 700, color: SP.heading }}>{p.name}</span>
                          <span style={{ fontSize: 11, color: SP.text, marginTop: 2 }}>{fmtTok(p.tokens_limit)} Tokens / month</span>
                        </div>
                        <span style={{ fontSize: 14, fontWeight: 700, color: SP.heading }}>
                          ${Number(p.price_usd).toFixed(2)}/mo
                        </span>
                        {i === 0 && (
                          <span style={{
                            position: 'absolute', top: -9, right: 14,
                            background: SP.blue, color: '#fff', fontSize: 9, fontWeight: 700,
                            padding: '2px 8px', borderRadius: 9999, letterSpacing: '.04em',
                          }}>
                            POPULAR
                          </span>
                        )}
                      </div>
                      {isSelected && features.length > 0 && (
                        <div style={{ marginTop: 10, paddingTop: 10, borderTop: `1px solid ${SP.outline}`, display: 'flex', flexDirection: 'column', gap: 5 }}>
                          {features.map(({ text, ok }) => (
                            <div key={text} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                              <span style={{ fontSize: 11, color: ok ? SP.green : SP.text3, flexShrink: 0 }}>{ok ? '✓' : '–'}</span>
                              <span style={{ fontSize: 11, color: ok ? SP.text : SP.text3 }}>{text}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </label>
                  )
                })}
              </div>
            </div>
          )}
          */}

          <div ref={boxRef} style={{
            background: SP.card, borderRadius: 16, boxShadow: SP.shadow,
            border: `1px solid ${SP.outline}`, overflow: 'hidden', marginBottom: 14,
          }}>
            <div style={{ padding: 16 }}>
              <button onClick={() => handleAccept('trial')} disabled={loading} style={{ ...primaryBtn(loading, sp), marginTop: 0 }}>
                {loading
                  ? <><Spin sp={sp} /> Setting up…</>
                  : subscriptionFree ? `Try ${appNm}` : 'Start Free Trial (14 Days)'}
              </button>
            </div>
            {/* Pricing is hidden entirely while subscriptions are free: no
                explore toggle, no tier cards, nothing that names a price. */}
            {!subscriptionFree && (
              <>
            <button
              onClick={handleToggleExplore}
              disabled={loading}
              style={{
                width: '100%', padding: '4px 16px 14px', background: 'transparent', border: 'none',
                borderTop: `1px solid rgba(15,23,42,0.08)`, color: SP.text3, fontSize: 12, fontWeight: 600,
                cursor: loading ? 'not-allowed' : 'pointer',
                display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
              }}
            >
              {spExpanded ? 'Hide plans' : 'Explore plans'}
              <span style={{
                display: 'inline-block', width: 7, height: 7,
                borderRight: `1.5px solid ${SP.text3}`, borderBottom: `1.5px solid ${SP.text3}`,
                transform: spExpanded ? 'rotate(-135deg)' : 'rotate(45deg)',
                transition: 'transform .3s ease', marginTop: spExpanded ? 2 : -2,
              }} />
            </button>

            <div style={{ display: 'grid', gridTemplateRows: spExpanded ? '1fr' : '0fr', transition: 'grid-template-rows .35s ease' }}>
              <div style={{ overflow: 'hidden' }}>
                <div style={{ padding: '4px 16px 16px', borderTop: `1px solid rgba(15,23,42,0.08)`, textAlign: 'left' }}>
                  {spPreviewLoading ? (
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, padding: '20px 0' }}>
                      <Spin sp={sp} />
                      <span style={{ fontSize: 12, color: SP.text3 }}>Loading pricing…</span>
                    </div>
                  ) : spPreviewError ? (
                    <div style={{ fontSize: 12, color: '#B3261E', textAlign: 'center', padding: '10px 0' }}>{spPreviewError}</div>
                  ) : spTiers ? (
                    <>
                      <div style={{ display: 'flex', background: SP.bg, borderRadius: 9999, padding: 4, marginBottom: 14 }}>
                        {['MONTHLY', 'YEARLY'].map(cycle => (
                          <button
                            key={cycle}
                            onClick={() => setSpCycle(cycle)}
                            style={{
                              flex: 1, padding: '8px 10px', borderRadius: 9999, border: 'none',
                              background: spCycle === cycle ? SP.blue : 'transparent',
                              color: spCycle === cycle ? '#fff' : SP.text,
                              fontSize: 11, fontWeight: 700, cursor: 'pointer',
                              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
                            }}
                          >
                            {cycle === 'MONTHLY' ? 'Monthly' : 'Yearly'}
                            {cycle === 'YEARLY' && yearlySavingsPct(spTiers[0]) > 0 && (
                              <span style={{
                                fontSize: 9, fontWeight: 700, padding: '2px 6px', borderRadius: 9999,
                                background: spCycle === cycle ? 'rgba(255,255,255,0.25)' : SP.blueLight,
                                color: spCycle === cycle ? '#fff' : SP.blueDark,
                              }}>
                                SAVE {yearlySavingsPct(spTiers[0])}%
                              </span>
                            )}
                          </button>
                        ))}
                      </div>

                      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                        {spTiers.map((tier, i) => {
                          const plan = spCycle === 'YEARLY' ? tier.yearly : tier.monthly
                          if (!plan) return null
                          const features = TIER_FEATURES[Math.min(i, 2)] || []
                          return (
                            <div key={plan.product_code} style={{
                              position: 'relative', background: SP.card, borderRadius: 14,
                              border: i === 0 ? `2px solid ${SP.blue}` : `1px solid ${SP.outline}`,
                              padding: '16px 16px 14px',
                            }}>
                              {i === 0 && (
                                <span style={{
                                  position: 'absolute', top: -10, left: 14,
                                  background: SP.blue, color: '#fff', fontSize: 9, fontWeight: 700,
                                  padding: '3px 9px', borderRadius: 9999, letterSpacing: '.04em',
                                }}>
                                  MOST POPULAR
                                </span>
                              )}
                              <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginTop: i === 0 ? 6 : 0 }}>
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
                              <button
                                onClick={() => handleAccept('explore', { tierIndex: i, cycle: spCycle })}
                                disabled={loading}
                                style={{
                                  width: '100%', padding: '11px', borderRadius: 9999, fontSize: 13, fontWeight: 700,
                                  background: 'transparent', color: SP.blue, border: `1px solid ${SP.blue}`,
                                  cursor: loading ? 'not-allowed' : 'pointer', marginTop: 12,
                                }}
                              >
                                {loading ? <><Spin sp={sp} /> Setting up…</> : 'Subscribe Now'}
                              </button>
                            </div>
                          )
                        })}
                      </div>
                    </>
                  ) : null}
                </div>
              </div>
            </div>
              </>
            )}
          </div>

          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, marginTop: 14 }}>
            <span style={{ fontSize: 11, color: SP.text }}>Powered by {appNm}</span>
          </div>

          <div style={{ fontSize: 11, color: SP.text, marginTop: 10, lineHeight: 1.6 }}>
            By continuing, you agree to {appNm}'s{' '}
            <a href={brand.termsUrl || "#"} target="_blank" rel="noopener noreferrer"
              style={{ color: SP.blue, textDecoration: 'underline' }}>Terms and Conditions</a>
            {' '}and{' '}
            <a href={brand.privacyUrl || "#"} target="_blank" rel="noopener noreferrer"
              style={{ color: SP.blue, textDecoration: 'underline' }}>Privacy Policy</a>.
          </div>
        </div>
      ) : (
        <div style={{ width: '100%', marginTop: 12 }}>
          <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 16, lineHeight: 1.7 }}>
            To get started, connect your {providerName} account — it only takes a few seconds.
          </div>

          <div style={cardStyle(sp)}>
            <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text)', marginBottom: 8 }}>
              What you can do with {appNm}
            </div>
            {[
              { icon: '💬', text: 'Ask questions in plain English — no spreadsheets, no formulas' },
              { icon: '📊', text: 'Get instant answers about sales, top products & customers' },
              { icon: '🔮', text: 'Spot trends and unusual patterns before they become problems' },
              { icon: '⚡', text: `Insights in seconds, right inside ${providerName}` },
            ].map(({ icon, text }, i, arr) => (
              <div key={i} style={rowStyle(sp, i === arr.length - 1)}>
                <span style={{ fontSize: 14, flexShrink: 0 }}>{icon}</span>
                <span style={{ fontSize: 12, color: 'var(--text2)' }}>{text}</span>
              </div>
            ))}
          </div>

          {plans.length > 0 && (
            <div style={cardStyle(sp)}>
              <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text)', marginBottom: 4 }}>
                Subscription plans
              </div>
              <div style={{ fontSize: 11, color: 'var(--blue)', marginBottom: 8 }}>
                Start with a free {plans[0]?.trial_days || 14}-day trial — no card required
              </div>
              {plans.map((p, i) => (
                <div key={p.id || p.name} style={{ ...rowStyle(sp, i === plans.length - 1), justifyContent: 'space-between' }}>
                  <span style={{ fontSize: 12, color: 'var(--text2)' }}>{p.name}</span>
                  <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)' }}>
                    ${Number(p.price_usd).toFixed(2)}/mo
                    <span style={{ fontWeight: 400, color: 'var(--text3)', marginLeft: 6 }}>
                      · {fmtTok(p.tokens_limit)} Tokens/mo
                    </span>
                  </span>
                </div>
              ))}
            </div>
          )}

          {/* Arrow-wrapped, not passed bare: React hands onClick the event, which
              landed in `intent` and made every downstream `intent === 'trial'`
              check fail — the trial never auto-started. */}
          <button onClick={() => handleAccept('trial')} disabled={loading} style={primaryBtn(loading, sp)}>
            {loading ? <><Spin sp={sp} /> Setting up…</> : `Try ${appNm} with Starter`}
          </button>

          <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 10, lineHeight: 1.6 }}>
            By continuing, you agree to {appNm}'s{' '}
            <a href={brand.termsUrl || "#"} target="_blank" rel="noopener noreferrer"
              style={{ color: 'var(--blue)', textDecoration: 'none' }}>Terms and Conditions</a>
            {' '}and{' '}
            <a href={brand.privacyUrl || "#"} target="_blank" rel="noopener noreferrer"
              style={{ color: 'var(--blue)', textDecoration: 'none' }}>Privacy Policy</a>.
          </div>
        </div>
      ))}

      {/* ── LOADING (single backend call covers everything) ─────────────────── */}
      {phase === 'loading' && (
        <>
          <div style={{ fontSize: 13, color: sp ? SP.text : 'var(--text2)', marginBottom: 18, marginTop: 10 }}>
            Setting up your account…
          </div>
          <div style={{ display: 'flex', justifyContent: 'center' }}>
            <div style={{
              width: 20, height: 20,
              border: `2px solid ${sp ? '#E2E8F0' : 'var(--border)'}`,
              borderTopColor: sp ? SP.blue : 'var(--blue)',
              borderRadius: '50%',
              animation: 'spin 0.7s linear infinite',
            }} />
          </div>
        </>
      )}

      {/* ── SYNC ───────────────────────────────────────────────────────────── */}
      {phase === 'sync' && pendingComplete && (
        <EmbedSyncProgress
          partnerKey={partnerKey}
          appNm={appNm}
          sp={sp}
          onDone={() => onComplete(pendingComplete.token, pendingComplete.profile)}
        />
      )}

      {/* ── ERROR ──────────────────────────────────────────────────────────── */}
      {phase === 'error' && (
        <div style={{ width: '100%', marginTop: 10 }}>
          <div style={{ fontSize: 28, marginBottom: 12 }}>⚠️</div>
          <div style={{ fontSize: 13, color: sp ? SP.text : 'var(--text2)', lineHeight: 1.7, maxWidth: 280, margin: '0 auto 20px' }}>
            {errorMsg}
          </div>
          <button onClick={handleRetry} disabled={loading} style={primaryBtn(loading, sp)}>
            {loading ? <><Spin sp={sp} /> Retrying…</> : 'Try Again'}
          </button>
        </div>
      )}
    </div>
  )
}
