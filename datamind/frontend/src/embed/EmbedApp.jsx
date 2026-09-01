/**
 * EmbedApp.jsx — Root component for the DataMind iframe embed widget.
 *
 * State machine:
 *   loading   → validates partner key via GET /embed/context
 *   error     → invalid/inactive partner key
 *   onboarding → new user (no dm_embed_token in localStorage)
 *   chat      → returning user (valid token found)
 */
import React, { useState, useEffect, useRef } from 'react'
import { createRoot } from 'react-dom/client'
import './embed.css'
import { embedValidateContext, salesplayGetProfile, salesplayCheckUser, salesplayOnboard, salesplaySubscriptionInfo, salesplayStartTrial, embedGetSubscription, embedSubmitFeedback, embedGetFeedbackStatus } from './embedApi'
import { evaluateSalesplayAccess } from './embedSalesplaySubscription'
import EmbedOnboarding from './EmbedOnboarding'
import EmbedChat from './EmbedChat'
import EmbedSalesplayAutoInit from './EmbedSalesplayAutoInit'
import EmbedSalesplayPlans from './EmbedSalesplayPlans'
import EmbedFreeBlocked from './EmbedFreeBlocked'
import EmbedSyncProgress from './EmbedSyncProgress'
import EmbedSearchBar from './EmbedSearchBar'
import EmbedFeedbackModal from './EmbedFeedbackModal'
import { appName, resolveBrand, applyBrandChrome } from './embedBranding'
import BrandLogo from './BrandLogo'
import * as storage from './embedStorage'

// Combines Salesplay's raw subscription state (card/plans — it's the payment
// gateway) with DataMind's own billing (trial days, tokens — the actual
// access gate) into one access decision. Used at every widget open (returning
// + new merchants) and again right after a payment attempt.
async function checkSalesplayAccess(partnerKey, aat, subscriptionFree = false) {
  // In free mode the provider's billing state decides nothing: access comes
  // entirely from our own subscription, and there is no card to check or price
  // to show. Calling it anyway would make a brand that charges nothing depend
  // on the provider having the AI product provisioned on its instance -- and a
  // whitelabel launching free is exactly the case where it is not. That failure
  // locked merchants out of a product that is free, so skip the call and let
  // evaluateSalesplayAccess work off internal state alone, which it already
  // tolerates.
  const [info, sub] = await Promise.all([
    subscriptionFree ? Promise.resolve(null) : salesplaySubscriptionInfo(partnerKey, aat),
    embedGetSubscription(),
  ])
  return evaluateSalesplayAccess(info, sub)
}

const sleep = (ms) => new Promise(r => setTimeout(r, ms))

// One retry before giving up — a backgrounded tab (e.g. user switches tabs
// mid-onboarding) can throttle/delay this call enough to fail transiently.
// Callers must NOT fail open to chat on the remaining error: that skips the
// plans screen entirely for a merchant who never actually subscribed.
async function checkSalesplayAccessRetrying(partnerKey, aat, subscriptionFree = false) {
  try {
    return await checkSalesplayAccess(partnerKey, aat, subscriptionFree)
  } catch {
    await sleep(800)
    return await checkSalesplayAccess(partnerKey, aat, subscriptionFree)
  }
}

// "Remind me later" cooldown, jittered like App Store / Play Store review
// prompts so users aren't all re-asked on the same schedule. Whether the user
// has ever submitted a rating is checked server-side (GET /embed/feedback/status)
// so it isn't forgotten on a different browser/device.
const FEEDBACK_NEXT_PROMPT_KEY = 'dm_feedback_next_prompt'
const FEEDBACK_COOLDOWN_MIN_MS = 3 * 24 * 60 * 60 * 1000 // 3 days
const FEEDBACK_COOLDOWN_MAX_MS = 7 * 24 * 60 * 60 * 1000 // 7 days

function snoozeFeedback() {
  const delay = FEEDBACK_COOLDOWN_MIN_MS + Math.random() * (FEEDBACK_COOLDOWN_MAX_MS - FEEDBACK_COOLDOWN_MIN_MS)
  storage.setItem(FEEDBACK_NEXT_PROMPT_KEY, String(Date.now() + delay))
}

// ── Collapsed "search bar" layout (?layout=bar) ─────────────────────────────
// The widget can start as a small search-bar pill instead of the full chat
// box. Clicking it expands to the full chat. Since the iframe's on-page size
// is controlled by the partner page (not us), we ask it to resize via
// `dm:resize` postMessage — the partner snippet must apply width/height to
// the <iframe> element. See docs/SALESPLAY_EMBED.md.
const SIZE_COLLAPSED = { width: 320, height: 64 }

// Cap expanded width/height at the device screen size so the iframe doesn't
// overflow on narrow phones or short tablet viewports. window.screen is the
// physical device size, not the iframe viewport, so it's readable here.
// Height matters too: the panel is anchored `bottom:24px` fixed, so an
// uncapped 680px height pushes its top edge above short viewports (tablets
// in landscape), cropping the close button under the browser chrome.
function getExpandedSize() {
  const sw = typeof window !== 'undefined' && window.screen?.width > 0
    ? window.screen.width
    : 420
  const sh = typeof window !== 'undefined' && window.screen?.height > 0
    ? window.screen.height
    : 680
  return { width: Math.min(420, sw - 16), height: Math.min(680, sh - 48) }
}

// ── postMessage helper ────────────────────────────────────────────────────────
// Populated once /embed/context loads; used to validate incoming messages and
// scope outgoing ones. '*' is only used before context is available.
let _allowedOrigins = []

export function setAllowedOrigins(origins) {
  _allowedOrigins = Array.isArray(origins) ? origins : []
}

export function notifyParent(type, payload = {}) {
  try {
    const msg = { type, ...payload }
    if (_allowedOrigins.length > 0) {
      // Send only to known-good origins
      _allowedOrigins.forEach(origin => {
        try { window.parent.postMessage(msg, origin) } catch { /* cross-origin block */ }
      })
    } else {
      // Context not yet loaded (e.g. dm:ready itself) — use '*' for this one call only
      window.parent.postMessage(msg, '*')
    }
  } catch {
    // No parent frame — running standalone, ignore
  }
}

// ── Root component ────────────────────────────────────────────────────────────
function EmbedApp() {
  const params     = new URLSearchParams(window.location.search)
  const partnerKey = params.get('pk') || ''

  const [state, setState]       = useState('loading')
  const [context, setContext]   = useState(null)
  const [errorMsg, setError]    = useState('')
  const [aatToken, setAatToken] = useState('')
  const [subAccess, setSubAccess] = useState(null) // { hasAccess, trialAvailable, blockReason, plans }
  // Backend SUBSCRIPTION_FREE, served on /embed/context. While on, the plans
  // screen is never routed to — see routeNoAccess below.
  const subscriptionFree = !!context?.subscription_free

  // routeNoAccess is called from inside the mount effect, whose closure was
  // built on the render where context is still null -- so reading
  // subscriptionFree there always saw false, and a free brand was sent to the
  // plans screen it must never reach. A ref is read at call time, so every
  // caller sees the brand that actually loaded.
  const contextRef = useRef(null)
  contextRef.current = context
  const [plansStartExpanded, setPlansStartExpanded] = useState(false) // consent screen's "Explore plans" was clicked — land on salesplay_plans already expanded
  const [plansAutoPick, setPlansAutoPick] = useState(null) // { tierIndex, cycle } — a tier was clicked on the consent screen; open its receipt directly

  const layoutBar = params.get('layout') === 'bar'
  const [expanded, setExpanded]         = useState(!layoutBar)
  const [initialInput, setInitialInput] = useState('')
  const [feedbackAfter, setFeedbackAfter] = useState(null) // fn to run once the feedback prompt is dismissed
  const hasChattedRef = React.useRef(false) // only ask for feedback if the user actually sent a message
  const hasFeedbackRef = React.useRef(null) // null=unknown, true=already submitted (server-checked), false=eligible

  // Check once per chat session whether this user has ever submitted feedback —
  // server-side, so it follows the user across browsers/devices, not just this one.
  useEffect(() => {
    if (state !== 'chat') return
    embedGetFeedbackStatus()
      .then(r => { hasFeedbackRef.current = !!r.has_feedback })
      .catch(() => { hasFeedbackRef.current = true }) // fail safe: don't nag if the check itself fails
  }, [state])

  // Apply saved theme on first load so onboarding is themed consistently
  useEffect(() => {
    const saved = storage.getItem('dm_embed_theme') || 'light'
    document.documentElement.setAttribute('data-theme', saved)
  }, [])

  // Tell the parent page how big the iframe should be, and let the page
  // behind a collapsed bar show through (transparent background).
  useEffect(() => {
    if (!layoutBar) return
    const size = expanded ? getExpandedSize() : SIZE_COLLAPSED
    notifyParent('dm:resize', { ...size, expanded })
    document.documentElement.classList.toggle('dm-collapsed', !expanded)
    document.body.classList.toggle('dm-collapsed', !expanded)
  }, [expanded, layoutBar])

  useEffect(() => {
    if (!partnerKey) {
      setError('No partner key provided. Add ?pk=YOUR_KEY to the iframe URL.')
      setState('error')
      return
    }

    const existingToken = storage.getItem('dm_embed_token')

    embedValidateContext(partnerKey)
      .then(async ctx => {
        setContext(ctx)
        // Register allowed origins for all subsequent postMessage calls
        setAllowedOrigins(ctx.allowed_origins || [])
        notifyParent('dm:ready', { partner_name: ctx.partner_name })

        const aat = params.get('aat') || ''
        setAatToken(aat)

        if (ctx.flow === 'partner') {
          // Salesplay flow: use the AAT to determine the merchant identity,
          // then decide whether to show the consent/onboard screen or go straight to chat.
          if (!aat) {
            setError(`Session token not found. Please open ${appName(ctx)} from the ${resolveBrand(ctx).companyName} backoffice.`)
            setState('error')
            return
          }

          try {
            // 1. Fetch the merchant's Salesplay profile (email, name) — no side effects.
            const profile = await salesplayGetProfile(partnerKey, aat)

            // 2. Check whether this merchant already has a DataMind account + credentials.
            const check = await salesplayCheckUser(partnerKey, profile.email)

            if (check.has_credentials) {
              // Returning merchant — silently refresh the JWT.
              // salesplayOnboard is safe here: for existing users it skips all setup steps
              // and just issues a new token (sync = "skipped").
              const result = await salesplayOnboard(partnerKey, aat)
              storage.setItem('dm_embed_token', result.token)
              storage.setItem('dm_sp_email', profile.email)
              if (result.user) storage.setItem('dm_embed_user', JSON.stringify(result.user))

              // Any user — paid, trial, or unpaid — gets re-checked at the start
              // of every session against Salesplay's own subscription state.
              try {
                const access = await checkSalesplayAccessRetrying(partnerKey, aat, !!ctx.subscription_free)
                if (access.hasAccess) {
                  setState('chat')
                  notifyParent('dm:chat_open')
                } else {
                  await routeNoAccess(access)
                }
              } catch {
                // Still unreachable after a retry — don't fail open to chat,
                // that would skip the plans screen for a merchant who never
                // actually subscribed. Show plans with no data; its own
                // "Continue" button lets them retry the check manually.
                await routeNoAccess(null)
              }
            } else {
              // New merchant — show consent screen before doing anything.
              setState('partner_init')
              notifyParent('dm:onboarding_start')
            }
          } catch (err) {
            if (err.response?.status === 401) {
              setError(`${resolveBrand(context).companyName} session expired. Please refresh the page.`)
              setState('error')
            } else {
              // API unreachable — fall back to consent screen so the user can retry.
              setState('partner_init')
              notifyParent('dm:onboarding_start')
            }
          }
        } else if (existingToken) {
          setState('chat')
          notifyParent('dm:chat_open')
        } else {
          setState('onboarding')
          notifyParent('dm:onboarding_start')
        }
      })
      .catch(err => {
        const msg = err.response?.data?.detail || 'Invalid embed configuration. Check the partner key.'
        setError(msg)
        setState('error')
      })

    // Listen for incoming commands from the parent window.
    // Only accept messages from origins registered for this partner key.
    function handleIncoming(event) {
      if (_allowedOrigins.length > 0 && !_allowedOrigins.includes(event.origin)) return
      if (event.data?.type === 'dm:logout') handleLogout()
    }
    window.addEventListener('message', handleIncoming)
    return () => window.removeEventListener('message', handleIncoming)
  }, [partnerKey])

  // Every "this merchant can't get in" path goes through here, so free mode
  // has exactly one place to diverge instead of four scattered setState calls.
  //
  // While subscriptions are free the plans screen is never a destination: a
  // merchant who has never subscribed is simply given the trial, and one who
  // can't be (already used it, quota gone, or we couldn't check) gets an
  // explanation instead of a card form.
  async function routeNoAccess(access) {
    if (!!contextRef.current?.subscription_free) {
      if (access?.trialAvailable) {
        try {
          await handleTrialSelected()
          return
        } catch { /* fall through to the explanation screen */ }
      }
      setSubAccess(access)
      setState('free_blocked')
      notifyParent('dm:onboarding_start')
      return
    }

    setSubAccess(access)
    setState('partner_plans')
    notifyParent('dm:onboarding_start')
  }

  async function handleOnboardingComplete(token, userData) {
    storage.setItem('dm_embed_token', token)
    if (userData) storage.setItem('dm_embed_user', JSON.stringify(userData))
    const intent = userData?.embed_intent // 'trial' | 'explore' — which consent-screen button was clicked
    setPlansStartExpanded(intent === 'explore')
    setPlansAutoPick(userData?.embed_plan || null)

    // Salesplay: onboarding no longer starts a trial by itself, so a brand-new
    // merchant has no subscription yet — checkSalesplayAccess reports
    // hasAccess: false and this naturally routes to the plans screen.
    if (context?.flow === 'partner' && aatToken) {
      try {
        const access = await checkSalesplayAccessRetrying(partnerKey, aatToken, subscriptionFree)

        // "Start Free Trial" was clicked — start it now and skip the plans
        // screen entirely, straight into chat. Falls through to the normal
        // plans-screen routing below if the trial can't be started for some
        // reason (already active, unreachable, etc).
        if (!access.hasAccess && intent === 'trial' && access.trialAvailable) {
          try {
            await handleTrialSelected()
            return
          } catch { /* fall through to plans screen so the user can retry there */ }
        }

        if (access.hasAccess) {
          setSubAccess(access)
          setState('chat')
          notifyParent('dm:chat_open')
        } else {
          await routeNoAccess(access)
        }
        return
      } catch {
        // Still unreachable after a retry — don't fail open to chat, same
        // reasoning as the returning-merchant check above.
        await routeNoAccess(null)
        return
      }
    }

    setState('chat')
    notifyParent('dm:chat_open')
  }

  // Plans screen: user explicitly picked the free trial — start it now, then
  // unlock chat. Throws on failure so the plans screen surfaces the error.
  async function handleTrialSelected() {
    await salesplayStartTrial()
    setState('chat')
    notifyParent('dm:chat_open')
  }

  // Plans screen: payment succeeded — re-confirm with Salesplay before
  // unlocking chat. Throws on failure so the plans screen shows the error
  // and re-enables its "Proceed" button (see EmbedSalesplayPlans's catch).
  async function handlePlanSubscribed() {
    const access = await checkSalesplayAccess(partnerKey, aatToken, subscriptionFree)
    if (access.hasAccess) {
      // Paid merchants see the workspace sync here rather than before the
      // plans screen — they came to pay, so nothing is allowed to sit between
      // them and the card. Sync has been running server-side since onboarding,
      // so by now it is usually already done and this passes straight through.
      setState('syncing')
      return
    }
    setSubAccess(access)
    throw new Error('Subscription could not be confirmed yet. Please try again in a moment.')
  }

  function handleSyncDone() {
    setState('chat')
    notifyParent('dm:chat_open')
  }

  // Plans screen: polls this after sending the user to card_add_url, so a card
  // added there shows up here without a widget reopen. Just refreshes
  // subAccess — no state transition, no throw (the caller loops on the
  // returned value instead).
  async function handleRefreshAccess() {
    try {
      const access = await checkSalesplayAccess(partnerKey, aatToken, subscriptionFree)
      setSubAccess(access)
      return access
    } catch {
      return null
    }
  }

  // Free mode's blocked screen shows a retry only when the access check itself
  // failed. Re-run it and route on the real answer — access may now be fine
  // (trial granted, quota reset), so this can land the merchant straight in chat.
  async function handleFreeBlockedRetry() {
    const access = await checkSalesplayAccess(partnerKey, aatToken, subscriptionFree)
    if (access.hasAccess) {
      setSubAccess(access)
      setState('chat')
      notifyParent('dm:chat_open')
      return
    }
    await routeNoAccess(access)
  }

  function handleExpired() {
    storage.removeItem('dm_embed_token')
    storage.removeItem('dm_sp_email')
    storage.removeItem('dm_embed_user')
    setState('onboarding')
    notifyParent('dm:onboarding_start')
  }

  function handleLogout() {
    storage.removeItem('dm_embed_token')
    storage.removeItem('dm_sp_email')
    storage.removeItem('dm_embed_user')
    setState('onboarding')
    notifyParent('dm:logout')
  }

  // Title, favicon and accent all come from the brand at runtime. They cannot
  // come from the HTML file or the build: one bundle serves every brand.
  //
  // This MUST stay above every early return below. A hook after a conditional
  // return runs on some renders and not others, and React tears the whole tree
  // down when the count changes -- which showed up as a blank iframe the moment
  // state left 'loading'. resolveBrand tolerates a null context, so it is safe
  // this early.
  const brand = resolveBrand(context)

  useEffect(() => {
    if (context) applyBrandChrome(brand)
  }, [context])

  // Collapsed search-bar — shown regardless of internal state (loading,
  // onboarding, chat, etc. all continue resolving in the background so the
  // full experience is ready the moment the user expands it).
  if (layoutBar && !expanded) {
    return (
      <EmbedSearchBar
        context={context}
        onExpand={(text = '') => { setInitialInput(text); setExpanded(true) }}
      />
    )
  }

  if (state === 'loading') {
    return (
      <div style={{ display:'flex', alignItems:'center', justifyContent:'center', height:'100%' }}>
        <div style={{ width:20, height:20, border:'2px solid var(--border)', borderTopColor:'var(--blue)', borderRadius:'50%', animation:'spin 0.7s linear infinite' }} />
      </div>
    )
  }

  if (state === 'error') {
    return (
      <div style={{ display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', height:'100%', padding:24, textAlign:'center', gap:12 }}>
        <div style={{ fontSize:32 }}>⚠️</div>
        <div style={{ fontSize:13, color:'var(--text2)', lineHeight:1.7, maxWidth:280 }}>{errorMsg}</div>
      </div>
    )
  }

  // Ask for a rating before actually closing/minimizing the widget — only if
  // the user sent at least one message this session, hasn't already submitted
  // feedback (ever, checked server-side), and isn't currently snoozed via
  // "remind me later".
  function requestClose(after) {
    const nextPrompt = Number(storage.getItem(FEEDBACK_NEXT_PROMPT_KEY) || 0)
    if (hasChattedRef.current && hasFeedbackRef.current === false && Date.now() >= nextPrompt) {
      setFeedbackAfter(() => after)
    } else {
      after()
    }
  }

  // Any dismissal that isn't a submission (including "Remind me later") snoozes
  // the prompt for a random 3-7 days before it can reappear.
  function handleRemindLater() {
    snoozeFeedback()
    const after = feedbackAfter
    setFeedbackAfter(null)
    if (after) after()
  }

  async function handleFeedbackSubmit(rating, comment) {
    try { await embedSubmitFeedback(rating, comment) } catch { /* best-effort */ }
    hasFeedbackRef.current = true
    const after = feedbackAfter
    setFeedbackAfter(null)
    if (after) after()
  }

  function handleClose() {
    requestClose(() => {
      notifyParent('dm:close')
      if (layoutBar) setExpanded(false)
    })
  }

  let content
  if (state === 'partner_init') {
    content = (
      <EmbedSalesplayAutoInit
        context={context}
        partnerKey={partnerKey}
        aatToken={aatToken}
        onComplete={handleOnboardingComplete}
        onError={(msg) => { setError(msg); setState('error') }}
        onClose={handleClose}
      />
    )
  } else if (state === 'syncing') {
    // Post-payment workspace sync — same screen onboarding shows trial users,
    // just reached later in the paid flow.
    content = (
      <div style={{
        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
        height: '100%', padding: '24px 20px', textAlign: 'center',
        background: 'linear-gradient(180deg, #F0F4F8 0%, #F7F9FB 100%)',
      }}>
        <BrandLogo brand={brand} size={32} radius={0} style={{ marginBottom: 10 }} />
        {!brand?.logoUrl && (
          <div style={{ fontSize: 15, fontWeight: 700, color: '#191C1E', marginBottom: 4 }}>
            {appName(context)}
          </div>
        )}
        <div style={{ width: '100%', maxWidth: 360 }}>
          <EmbedSyncProgress
            partnerKey={partnerKey}
            appNm={appName(context)}
            onDone={handleSyncDone}
          />
        </div>
      </div>
    )
  } else if (state === 'free_blocked') {
    content = (
      <EmbedFreeBlocked
        context={context}
        reason={subAccess?.blockReason}
        trialDays={subAccess?.trialDays || 14}
        onRetry={handleFreeBlockedRetry}
        onClose={handleClose}
      />
    )
  } else if (state === 'partner_plans') {
    content = (
      <EmbedSalesplayPlans
        context={context}
        partnerKey={partnerKey}
        aat={aatToken}
        plans={subAccess?.plans || []}
        trialAvailable={!!subAccess?.trialAvailable}
        blockReason={subAccess?.blockReason}
        isPaidQuotaBlocked={!!subAccess?.isPaidQuotaBlocked}
        trialDays={subAccess?.trialDays || 14}
        billingDetailsAdded={!!subAccess?.billingDetailsAdded}
        cardAddUrl={subAccess?.cardAddUrl}
        cardBrand={subAccess?.cardBrand}
        cardLast4={subAccess?.cardLast4}
        cardExpired={subAccess?.cardExpired}
        cardExpiry={subAccess?.cardExpiry}
        availableCreditText={subAccess?.availableCreditText}
        showPriceText={subAccess?.showPriceText}
        initialExpanded={plansStartExpanded}
        autoPick={plansAutoPick}
        onTrialSelected={handleTrialSelected}
        onSubscribed={handlePlanSubscribed}
        onRefreshAccess={handleRefreshAccess}
        onClose={handleClose}
      />
    )
  } else if (state === 'onboarding') {
    content = (
      <EmbedOnboarding
        context={context}
        partnerKey={partnerKey}
        onComplete={handleOnboardingComplete}
        onClose={handleClose}
      />
    )
  } else {
    content = (
      <EmbedChat
        context={context}
        onExpired={handleExpired}
        onLogout={handleLogout}
        onCollapse={layoutBar ? () => requestClose(() => setExpanded(false)) : undefined}
        onMessageSent={() => { hasChattedRef.current = true }}
        initialInput={initialInput}
      />
    )
  }

  return (
    <>
      {content}
      {feedbackAfter && (
        <EmbedFeedbackModal onSubmit={handleFeedbackSubmit} onRemindLater={handleRemindLater} appNm={appName(context)} />
      )}
    </>
  )
}

// ── Mount ─────────────────────────────────────────────────────────────────────
// Guard against Vite HMR re-executing this module and calling createRoot()
// on a container that already has a React root attached. In production the
// bundle runs once so this branch is never taken.
const container = document.getElementById('embed-root')
if (!container._reactRoot) {
  container._reactRoot = createRoot(container)
}
container._reactRoot.render(<EmbedApp />)
