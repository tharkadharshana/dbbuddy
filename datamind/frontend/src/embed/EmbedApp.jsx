/**
 * EmbedApp.jsx — Root component for the DataMind iframe embed widget.
 *
 * State machine:
 *   loading   → validates partner key via GET /embed/context
 *   error     → invalid/inactive partner key
 *   onboarding → new user (no dm_embed_token in localStorage)
 *   chat      → returning user (valid token found)
 */
import React, { useState, useEffect } from 'react'
import { createRoot } from 'react-dom/client'
import './embed.css'
import { embedValidateContext, salesplayGetProfile, salesplayCheckUser, salesplayOnboard, salesplaySubscriptionInfo, salesplayStartTrial, embedGetSubscription, embedSubmitFeedback, embedGetFeedbackStatus } from './embedApi'
import { evaluateSalesplayAccess } from './embedSalesplaySubscription'
import EmbedOnboarding from './EmbedOnboarding'
import EmbedChat from './EmbedChat'
import EmbedSalesplayAutoInit from './EmbedSalesplayAutoInit'
import EmbedSalesplayPlans from './EmbedSalesplayPlans'
import EmbedSearchBar from './EmbedSearchBar'
import EmbedFeedbackModal from './EmbedFeedbackModal'
import { appName } from './embedBranding'

// Combines Salesplay's raw subscription state (card/plans — it's the payment
// gateway) with DataMind's own billing (trial days, tokens — the actual
// access gate) into one access decision. Used at every widget open (returning
// + new merchants) and again right after a payment attempt.
async function checkSalesplayAccess(partnerKey, aat) {
  const [info, sub] = await Promise.all([
    salesplaySubscriptionInfo(partnerKey, aat),
    embedGetSubscription(),
  ])
  return evaluateSalesplayAccess(info, sub)
}

const sleep = (ms) => new Promise(r => setTimeout(r, ms))

// One retry before giving up — a backgrounded tab (e.g. user switches tabs
// mid-onboarding) can throttle/delay this call enough to fail transiently.
// Callers must NOT fail open to chat on the remaining error: that skips the
// plans screen entirely for a merchant who never actually subscribed.
async function checkSalesplayAccessRetrying(partnerKey, aat) {
  try {
    return await checkSalesplayAccess(partnerKey, aat)
  } catch {
    await sleep(800)
    return await checkSalesplayAccess(partnerKey, aat)
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
  localStorage.setItem(FEEDBACK_NEXT_PROMPT_KEY, String(Date.now() + delay))
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
    const saved = localStorage.getItem('dm_embed_theme') || 'light'
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

    const existingToken = localStorage.getItem('dm_embed_token')

    embedValidateContext(partnerKey)
      .then(async ctx => {
        setContext(ctx)
        // Register allowed origins for all subsequent postMessage calls
        setAllowedOrigins(ctx.allowed_origins || [])
        notifyParent('dm:ready', { partner_name: ctx.partner_name })

        const aat = params.get('aat') || ''
        setAatToken(aat)

        if (ctx.provider_id === 'salesplay') {
          // Salesplay flow: use the AAT to determine the merchant identity,
          // then decide whether to show the consent/onboard screen or go straight to chat.
          if (!aat) {
            setError(`Session token not found. Please access ${appName(ctx)} through the Salesplay backoffice.`)
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
              localStorage.setItem('dm_embed_token', result.token)
              localStorage.setItem('dm_sp_email', profile.email)
              if (result.user) localStorage.setItem('dm_embed_user', JSON.stringify(result.user))

              // Any user — paid, trial, or unpaid — gets re-checked at the start
              // of every session against Salesplay's own subscription state.
              try {
                const access = await checkSalesplayAccessRetrying(partnerKey, aat)
                if (access.hasAccess) {
                  setState('chat')
                  notifyParent('dm:chat_open')
                } else {
                  setSubAccess(access)
                  setState('salesplay_plans')
                  notifyParent('dm:onboarding_start')
                }
              } catch {
                // Still unreachable after a retry — don't fail open to chat,
                // that would skip the plans screen for a merchant who never
                // actually subscribed. Show plans with no data; its own
                // "Continue" button lets them retry the check manually.
                setSubAccess(null)
                setState('salesplay_plans')
                notifyParent('dm:onboarding_start')
              }
            } else {
              // New merchant — show consent screen before doing anything.
              setState('salesplay_init')
              notifyParent('dm:onboarding_start')
            }
          } catch (err) {
            if (err.response?.status === 401) {
              setError('Salesplay session expired. Please refresh the page.')
              setState('error')
            } else {
              // API unreachable — fall back to consent screen so the user can retry.
              setState('salesplay_init')
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

  async function handleOnboardingComplete(token, userData) {
    localStorage.setItem('dm_embed_token', token)
    if (userData) localStorage.setItem('dm_embed_user', JSON.stringify(userData))

    // Salesplay: onboarding no longer starts a trial by itself, so a brand-new
    // merchant has no subscription yet — checkSalesplayAccess reports
    // hasAccess: false and this naturally routes to the plans screen.
    if (context?.provider_id === 'salesplay' && aatToken) {
      try {
        const access = await checkSalesplayAccessRetrying(partnerKey, aatToken)
        setSubAccess(access)
        setState(access.hasAccess ? 'chat' : 'salesplay_plans')
        notifyParent(access.hasAccess ? 'dm:chat_open' : 'dm:onboarding_start')
        return
      } catch {
        // Still unreachable after a retry — don't fail open to chat, same
        // reasoning as the returning-merchant check above.
        setSubAccess(null)
        setState('salesplay_plans')
        notifyParent('dm:onboarding_start')
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
    const access = await checkSalesplayAccess(partnerKey, aatToken)
    if (access.hasAccess) {
      setState('chat')
      notifyParent('dm:chat_open')
      return
    }
    setSubAccess(access)
    throw new Error('Subscription could not be confirmed yet. Please try again in a moment.')
  }

  // Plans screen: polls this after sending the user to card_add_url, so a card
  // added there shows up here without a widget reopen. Just refreshes
  // subAccess — no state transition, no throw (the caller loops on the
  // returned value instead).
  async function handleRefreshAccess() {
    try {
      const access = await checkSalesplayAccess(partnerKey, aatToken)
      setSubAccess(access)
      return access
    } catch {
      return null
    }
  }

  function handleExpired() {
    localStorage.removeItem('dm_embed_token')
    localStorage.removeItem('dm_sp_email')
    localStorage.removeItem('dm_embed_user')
    setState('onboarding')
    notifyParent('dm:onboarding_start')
  }

  function handleLogout() {
    localStorage.removeItem('dm_embed_token')
    localStorage.removeItem('dm_sp_email')
    localStorage.removeItem('dm_embed_user')
    setState('onboarding')
    notifyParent('dm:logout')
  }

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

  // Apply accent colour from partner branding if provided
  const accentColor = context?.branding?.accent_color
  if (accentColor) {
    document.documentElement.style.setProperty('--blue', accentColor)
  }

  // Ask for a rating before actually closing/minimizing the widget — only if
  // the user sent at least one message this session, hasn't already submitted
  // feedback (ever, checked server-side), and isn't currently snoozed via
  // "remind me later".
  function requestClose(after) {
    const nextPrompt = Number(localStorage.getItem(FEEDBACK_NEXT_PROMPT_KEY) || 0)
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
  if (state === 'salesplay_init') {
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
  } else if (state === 'salesplay_plans') {
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
        cardLabel={subAccess?.cardLabel}
        availableCreditText={subAccess?.availableCreditText}
        showPriceText={subAccess?.showPriceText}
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
        <EmbedFeedbackModal onSubmit={handleFeedbackSubmit} onRemindLater={handleRemindLater} />
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
