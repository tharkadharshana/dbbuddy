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
export const fetchDiscover        = () => api.get('/discover').then(r => r.data)
export const runNLQuery           = (question, llm) => api.post('/query', { question, llm }).then(r => r.data)
export const runAnalytics         = (template_id, llm, params={}) => api.post('/analytics/run', { template_id, llm, params }).then(r => r.data)
export const runForecast          = (table, date_column, value_column, periods=90) => api.post('/forecast', { table, date_column, value_column, periods }).then(r => r.data)
export const runAutoForecast      = (periods=90) => api.get(`/forecast/auto?periods=${periods}`).then(r => r.data)
export const runAnomalies         = (table, value_column, date_column=null) => api.post('/anomalies', { table, value_column, date_column }).then(r => r.data)
export const runAutoAnomalies     = () => api.get('/anomalies/auto').then(r => r.data)
export const generateReport       = (title, sections, llm, format='full') => api.post('/report', { title, sections, llm, format }).then(r => r.data)

// Cache
export const fetchCacheStatus  = () => api.get('/cache/status').then(r => r.data)
export const fetchCacheProgress = () => api.get('/cache/progress').then(r => r.data)
export const rebuildCache       = () => api.post('/cache/rebuild').then(r => r.data)
