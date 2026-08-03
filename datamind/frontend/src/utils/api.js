import axios from 'axios'
import { getGlobalToast } from '../components/Toast'

const api = axios.create({ baseURL: '/api' })

// Attach JWT token to every request
api.interceptors.request.use(cfg => {
  const token = localStorage.getItem('dm_token')
  if (token) cfg.headers.Authorization = `Bearer ${token}`
  return cfg
})

// Centrally handle status codes that are not contextual to a specific component
api.interceptors.response.use(
  r => r,
  err => {
    const status = err.response?.status
    const toast = getGlobalToast()

    if (status === 401) {
      localStorage.removeItem('dm_token')
      localStorage.removeItem('dm_user')
      window.location.href = '/login'
    } else if (status === 429) {
      toast?.error('Too many requests — please slow down and try again in a moment.')
    }

    return Promise.reject(err)
  }
)

// Extracts the most useful error message from an axios error.
// For 5xx errors we never expose the raw axios message ("Request failed with
// status code 500") — always return the friendly fallback instead.
export function getErrorMessage(e, fallback = 'Something went wrong on our end — please try again in a moment.') {
  const status = e?.response?.status
  if (status >= 500) return fallback
  return e?.response?.data?.error
    || e?.response?.data?.detail
    || e?.message
    || fallback
}

// Auth
export const register  = (name, email, password) => api.post('/auth/register', { name, email, password }).then(r => r.data)
export const login     = (email, password) => api.post('/auth/login', { email, password }).then(r => r.data)

// Exchanges a one-time embed handoff token (?sso=...) for a normal session —
// lets users who authenticated inside the Salesplay Web Embed land here
// already signed in, without ever seeing their (generated) password.
export const ssoLogin  = (token) => api.post('/auth/sso-login', { token }).then(r => r.data)
export const fetchMe   = () => api.get('/auth/me').then(r => r.data)
export const deleteAccount = () => api.delete('/auth/account').then(r => r.data)

// Settings
export const fetchSettings        = () => api.get('/settings').then(r => r.data)
export const patchSettings        = (patch) => api.patch('/settings', patch).then(r => r.data)
export const addDBConfig          = (cfg) => api.post('/settings/db', cfg).then(r => r.data)
export const updateDBConfig       = (i, cfg) => api.put(`/settings/db/${i}`, cfg).then(r => r.data)
export const deleteDBConfig       = (i) => api.delete(`/settings/db/${i}`).then(r => r.data)
export const activateDBConfig     = (i) => api.post(`/settings/db/${i}/activate`).then(r => r.data)
export const testDBConnection     = (cfg) => api.post('/settings/db/test', cfg).then(r => r.data)

// Data
export const fetchTables          = () => api.get('/tables').then(r => r.data)
export const fetchTableColumns    = (table) => api.get(`/tables/${encodeURIComponent(table)}/columns`).then(r => r.data)
export const fetchDiscover        = () => api.get('/discover').then(r => r.data)
export const runNLQuery           = (question, llm, thinkMode = false, conversationId = null) => api.post('/query', { question, llm, think_mode: thinkMode, conversation_id: conversationId }).then(r => r.data)

// ── SSE streaming query ───────────────────────────────────────────────────────
// POST /query/stream emits: step → thinking → token (answer chunks) → data
// (same payload runNLQuery returns) → done. EventSource is GET-only, so we parse
// the SSE stream from fetch() ourselves. Returns the final data payload, or null
// when the caller should fall back to runNLQuery (streaming disabled server-side
// via 404, or the stream died before producing output). Mirrors the embed's
// embedStreamQuery so both surfaces behave identically.
let _streamSupported = true
export async function streamNLQuery(question, llm, thinkMode, conversationId, handlers = {}) {
  if (!_streamSupported) return null
  const token = localStorage.getItem('dm_token')
  const resp = await fetch('/api/query/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify({ question, llm, think_mode: thinkMode, conversation_id: conversationId }),
  })
  if (resp.status === 404) { _streamSupported = false; return null }  // flag off — don't retry per message
  if (resp.status === 401) {
    localStorage.removeItem('dm_token'); localStorage.removeItem('dm_user')
    window.location.href = '/login'
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
export const runAnalytics         = (template_id, llm, params={}, provider=null) => api.post('/analytics/run', { template_id, llm, params, provider }).then(r => r.data)
export const runForecast          = (table, date_column, value_column, periods=90) => api.post('/forecast', { table, date_column, value_column, periods }).then(r => r.data)
export const runAutoForecast      = (periods=90) => api.get(`/forecast/auto?periods=${periods}`).then(r => r.data)
export const runAnomalies         = (table, value_column, date_column=null) => api.post('/anomalies', { table, value_column, date_column }).then(r => r.data)
export const runAutoAnomalies     = () => api.get('/anomalies/auto').then(r => r.data)
export const generateReport       = (title, sections, llm, format='full') => api.post('/report', { title, sections, llm, format }).then(r => r.data)

// Cache
export const fetchCacheStatus  = () => api.get('/cache/status').then(r => r.data)
export const fetchCacheProgress = () => api.get('/cache/progress').then(r => r.data)
export const rebuildCache       = () => api.post('/cache/rebuild').then(r => r.data)

// Onboarding
export const onboardingValidateKey = (llm, api_key) => api.post('/onboarding/validate-key', { llm, api_key }).then(r => r.data)
export const onboardingTestDB      = (cfg)           => api.post('/onboarding/test-db', cfg).then(r => r.data)
export const onboardingConnectDB   = (cfg)           => api.post('/onboarding/connect-db', cfg).then(r => r.data)
export const fetchLLMModels        = ()              => api.get('/llm/models').then(r => r.data)

// External Providers
export const fetchProviders          = ()                    => api.get('/providers').then(r => r.data)
export const fetchConnectedProviders = ()                    => api.get('/providers/connected').then(r => r.data)
export const fetchProviderStats      = ()                    => api.get('/providers/stats').then(r => r.data)
export const validateProviderCreds   = (provider_id, credentials) => api.post('/providers/validate', { provider_id, credentials }).then(r => r.data)
export const connectProvider         = (provider_id, credentials) => api.post('/providers/connect', { provider_id, credentials }).then(r => r.data)
export const disconnectProvider      = (connection_id)       => api.delete(`/providers/${connection_id}`).then(r => r.data)
export const syncProvider            = (connection_id)       => api.post(`/providers/${connection_id}/sync`).then(r => r.data)
export const fetchProviderStatus     = (connection_id)       => api.get(`/providers/${connection_id}/status`).then(r => r.data)
export const fetchProviderHistory    = (connection_id)       => api.get(`/providers/${connection_id}/history`).then(r => r.data)
export const fetchIntegrationTemplates = (provider_id)       => api.get(`/integrations/${provider_id}/analytics/templates`).then(r => r.data)
export const runIntegrationAnalytics   = (provider_id, template_id) => api.post(`/integrations/${provider_id}/analytics/run`, { template_id }).then(r => r.data)

// ── Developer API Key (Pro only) ──────────────────────────────────────────────
export const getDeveloperKey      = ()  => api.get('/developer/key').then(r => r.data)
export const generateDeveloperKey = ()  => api.post('/developer/key').then(r => r.data)
export const revokeDeveloperKey   = ()  => api.delete('/developer/key').then(r => r.data)

// ── Conversations ─────────────────────────────────────────────────────────────
export const createConversation       = (id)       => api.post('/conversations', { id }).then(r => r.data)
export const listConversations        = ()          => api.get('/conversations').then(r => r.data)
export const getConversationMessages  = (convId)   => api.get(`/conversations/${convId}/messages`).then(r => r.data)
export const deleteConversation       = (convId)   => api.delete(`/conversations/${convId}`).then(r => r.data)
// vote: 1 (thumbs up), -1 (thumbs down), or null (clear)
export const voteMessage              = (convId, messageId, vote) => api.patch(`/conversations/${convId}/messages/${messageId}/vote`, { vote }).then(r => r.data)

// ── Billing ───────────────────────────────────────────────────────────────────
export const fetchBillingPlans  = () => api.get('/billing/plans').then(r => r.data)
export const fetchSubscription  = () => api.get('/billing/subscription').then(r => r.data)
export const subscribeToPlan    = (plan_id) => api.post('/billing/subscribe', { plan_id }).then(r => r.data)
export const startTrial         = () => api.post('/billing/trial').then(r => r.data)
export const purchaseAddon      = (addon_type, quantity) => api.post('/billing/addon', { addon_type, quantity }).then(r => r.data)
export const fetchBillingUsage  = () => api.get('/billing/usage').then(r => r.data)
export const fetchBillingConfig = () => api.get('/billing/config').then(r => r.data)
export const setBillingConfig   = (patch) => api.post('/billing/config', patch).then(r => r.data)
