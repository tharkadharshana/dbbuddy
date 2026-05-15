import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

// Attach JWT token to every request
api.interceptors.request.use(cfg => {
  const token = localStorage.getItem('dm_token')
  if (token) cfg.headers.Authorization = `Bearer ${token}`
  return cfg
})

// Auto logout on 401
api.interceptors.response.use(
  r => r,
  err => {
    if (err.response?.status === 401) {
      localStorage.removeItem('dm_token')
      localStorage.removeItem('dm_user')
      window.location.href = '/login'
    }
    return Promise.reject(err)
  }
)

// Auth
export const register  = (name, email, password) => api.post('/auth/register', { name, email, password }).then(r => r.data)
export const login     = (email, password) => api.post('/auth/login', { email, password }).then(r => r.data)
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
export const runNLQuery           = (question, llm) => api.post('/query', { question, llm }).then(r => r.data)
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

// ── Billing ───────────────────────────────────────────────────────────────────
export const fetchBillingPlans  = () => api.get('/billing/plans').then(r => r.data)
export const fetchSubscription  = () => api.get('/billing/subscription').then(r => r.data)
export const subscribeToPlan    = (plan_id) => api.post('/billing/subscribe', { plan_id }).then(r => r.data)
export const purchaseAddon      = (addon_type, quantity) => api.post('/billing/addon', { addon_type, quantity }).then(r => r.data)
export const fetchBillingUsage  = () => api.get('/billing/usage').then(r => r.data)
