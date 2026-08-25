/**
 * embedApi.js
 * Axios client for the DataMind embed widget.
 *
 * Uses VITE_API_URL as the base URL (set in .env.production).
 * Falls back to '' in development so the Vite proxy handles /api calls.
 *
 * Uses dm_embed_token (not dm_token) to avoid colliding with a user who
 * also has the full DataMind app open in another tab.
 */
import axios from 'axios'
import * as storage from './embedStorage'

// Same origin, always. Each brand is served from its own domain, so an
// absolute base would point every brand at whichever one was built.
const BASE_URL = import.meta.env.VITE_API_URL || ''

const api = axios.create({ baseURL: BASE_URL + '/api' })

api.interceptors.request.use(cfg => {
  const token = storage.getItem('dm_embed_token')
  if (token) cfg.headers.Authorization = `Bearer ${token}`
  return cfg
})

// On 401 clear embed token — do NOT redirect (we are inside an iframe)
api.interceptors.response.use(
  r => r,
  err => {
    if (err.response?.status === 401) {
      storage.removeItem('dm_embed_token')
    }
    return Promise.reject(err)
  }
)

export const embedValidateContext = (pk) =>
  api.get(`/embed/context?pk=${encodeURIComponent(pk)}`).then(r => r.data)

// Public token validation — no DataMind account or JWT needed.
// Used in Step 0 of the onboarding wizard before the user has an account.
export const embedValidateToken = (partnerKey, apiToken) =>
  api.post('/embed/validate-token', { partner_key: partnerKey, api_token: apiToken }).then(r => r.data)

export const embedInit = (data) =>
  api.post('/embed/init', data).then(r => r.data)

export const embedLogin = (email, password) =>
  api.post('/auth/login', { email, password }).then(r => r.data)

export const embedValidateProviderCreds = (provider_id, credentials) =>
  api.post('/providers/validate', { provider_id, credentials }).then(r => r.data)

// Keyed on the partner, not the provider: sending provider_id put the
// integration's name in a request body the merchant can read.
export const embedConnectProvider = (partnerKey, credentials, token) =>
  api.post(
    '/embed/partner/connect',
    { partner_key: partnerKey, credentials },
    { headers: { Authorization: `Bearer ${token}` } }
  ).then(r => r.data)

// Sync progress. Keyed on the partner key, not the provider id: the old
// /providers/{connection_id}/status put the integration's name in the URL of a
// request the widget polls on a loop, which every whitelabel merchant can read
// in their network tab.
export const embedGetProviderStatus = (partnerKey) =>
  api.get(`/embed/partner/sync-status?partner_key=${encodeURIComponent(partnerKey)}`)
     .then(r => r.data)

// "default" tells the backend to use the tenant's configured AI provider —
// the embed never names a specific vendor in API traffic.
export const embedRunQuery = (question, llm = 'default', thinkMode = false, conversationId = null) =>
  api.post('/query', { question, llm, think_mode: thinkMode, conversation_id: conversationId }).then(r => r.data)

// ── SSE streaming query ───────────────────────────────────────────────────────
// POST /query/stream emits: step → thinking → token (answer chunks) → data
// (the same payload embedRunQuery returns) → done. EventSource is GET-only,
// so we parse the SSE stream from fetch() ourselves. Returns the final data
// payload, or null when the caller should fall back to embedRunQuery
// (streaming disabled server-side, or the stream died before any output).
let _streamSupported = true

export async function embedStreamQuery(question, llm, thinkMode, conversationId, handlers = {}) {
  if (!_streamSupported) return null
  const token = storage.getItem('dm_embed_token')
  const resp = await fetch(`${BASE_URL}/api/query/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ question, llm, think_mode: thinkMode, conversation_id: conversationId }),
  })
  if (resp.status === 404) { _streamSupported = false; return null }  // flag off — don't retry per message
  if (resp.status === 401) {
    storage.removeItem('dm_embed_token')
    const e = new Error('Session expired'); e.status = 401; throw e
  }
  if (!resp.ok || !resp.body) return null

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = '', result = null, errorMsg = null
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const blocks = buffer.split('\n\n')
    buffer = blocks.pop()
    for (const block of blocks) {
      let event = 'message', data = ''
      for (const line of block.split('\n')) {
        if (line.startsWith('event: ')) event = line.slice(7).trim()
        else if (line.startsWith('data: ')) data += line.slice(6)
      }
      let payload = {}
      try { payload = JSON.parse(data || '{}') } catch { /* skip malformed */ }
      if (event === 'step') handlers.onStep?.(payload)
      else if (event === 'thinking') handlers.onThinking?.(payload)
      else if (event === 'token') handlers.onToken?.(payload.text || '')
      else if (event === 'data') result = payload
      else if (event === 'error') errorMsg = payload.message
    }
  }
  if (!result && errorMsg) return { success: false, type: 'error', message: errorMsg }
  return result
}

// Conversation history — same endpoints/data as the main app, so history
// created in the embed shows up in the main app's sidebar and vice versa.
export const embedCreateConversation = (id) =>
  api.post('/conversations', { id }).then(r => r.data)

export const embedListConversations = () =>
  api.get('/conversations').then(r => r.data)

export const embedGetConversationMessages = (convId) =>
  api.get(`/conversations/${convId}/messages`).then(r => r.data)

// Thumbs up/down on an assistant reply. vote: 1 (up), -1 (down), or null (clear).
export const embedVoteMessage = (convId, messageId, vote) =>
  api.patch(`/conversations/${convId}/messages/${messageId}/vote`, { vote }).then(r => r.data)

// One-time link so an already-authenticated embed user can open the
// standalone DataMind app without re-entering credentials.
export const embedGetSSOHandoff = () =>
  api.post('/auth/sso-handoff').then(r => r.data)

export const embedGetSubscription = () =>
  api.get('/billing/subscription').then(r => r.data)

export const embedGetPlans = () =>
  api.get('/billing/plans').then(r => r.data)

export const embedSubscribePlan = (plan_id) =>
  api.post('/billing/subscribe', { plan_id }).then(r => r.data)

// Salesplay one-shot onboarding — backend handles profile fetch, token creation,
// account setup, and provider connect in a single server-side call.
export const salesplayOnboard = (partnerKey, aat) =>
  api.post('/embed/partner/onboard', { partner_key: partnerKey, aat }).then(r => r.data)

// Salesplay profile-only fetch — used to verify merchant identity without side effects.
export const salesplayGetProfile = (partnerKey, aat) =>
  api.post('/embed/partner/profile', { partner_key: partnerKey, aat }).then(r => r.data)

// Check whether a DataMind account with Salesplay credentials exists for a given email.
export const salesplayCheckUser = (partnerKey, email) =>
  api.post('/embed/partner/check-user', { partner_key: partnerKey, email }).then(r => r.data)

// AI POS subscription state — trial/quota/plans, sourced from Salesplay's own
// billing system. Called on every widget open to decide chat vs. plans screen.
export const salesplaySubscriptionInfo = (partnerKey, aat) =>
  api.get('/embed/partner/subscription/info', { params: { partner_key: partnerKey, aat } }).then(r => r.data)

// Activate/renew the AI POS addon subscription for a paid plan.
export const salesplaySubscriptionPayment = (payload) =>
  api.post('/embed/partner/subscription/payment', payload).then(r => r.data)

// Real, pre-formatted pricing for the receipt screen (price × qty, credits,
// amount due) — never recompute these currency strings client-side.
export const salesplaySubscriptionPreview = (payload) =>
  api.post('/embed/partner/subscription/preview', payload).then(r => r.data)

// Explicitly starts the free trial — only called from the plans screen's
// "Start free trial" button, never implicitly at onboarding.
export const salesplayStartTrial = () =>
  api.post('/embed/partner/start-trial').then(r => r.data)

// Widget rating (1-5 stars) + optional free-text comment, asked when the user closes the widget.
export const embedSubmitFeedback = (rating, comment = '') =>
  api.post('/embed/feedback', { rating, comment }).then(r => r.data)

// Whether this user has ever submitted widget feedback (server-side, cross-device).
export const embedGetFeedbackStatus = () =>
  api.get('/embed/feedback/status').then(r => r.data)
