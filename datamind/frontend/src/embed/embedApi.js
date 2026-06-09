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

const BASE_URL = import.meta.env.VITE_API_URL || ''

const api = axios.create({ baseURL: BASE_URL + '/api' })

api.interceptors.request.use(cfg => {
  const token = localStorage.getItem('dm_embed_token')
  if (token) cfg.headers.Authorization = `Bearer ${token}`
  return cfg
})

// On 401 clear embed token — do NOT redirect (we are inside an iframe)
api.interceptors.response.use(
  r => r,
  err => {
    if (err.response?.status === 401) {
      localStorage.removeItem('dm_embed_token')
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

export const embedConnectProvider = (provider_id, credentials, token) =>
  api.post(
    '/providers/connect',
    { provider_id, credentials },
    { headers: { Authorization: `Bearer ${token}` } }
  ).then(r => r.data)

export const embedGetProviderStatus = (connection_id) =>
  api.get(`/providers/${connection_id}/status`).then(r => r.data)

export const embedRunQuery = (question, llm = 'gemini', thinkMode = false) =>
  api.post('/query', { question, llm, think_mode: thinkMode }).then(r => r.data)

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
  api.post('/embed/salesplay/onboard', { partner_key: partnerKey, aat }).then(r => r.data)

// Salesplay profile-only fetch — used to verify merchant identity without side effects.
export const salesplayGetProfile = (partnerKey, aat) =>
  api.post('/embed/salesplay/profile', { partner_key: partnerKey, aat }).then(r => r.data)

// Check whether a DataMind account with Salesplay credentials exists for a given email.
export const salesplayCheckUser = (partnerKey, email) =>
  api.post('/embed/salesplay/check-user', { partner_key: partnerKey, email }).then(r => r.data)
