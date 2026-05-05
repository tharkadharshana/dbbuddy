import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

export const fetchTables = () => api.get('/tables').then(r => r.data)

export const runNLQuery = (question, llm) =>
  api.post('/query', { question, llm }).then(r => r.data)

export const runForecast = (table, date_column, value_column, periods = 90) =>
  api.post('/forecast', { table, date_column, value_column, periods }).then(r => r.data)

export const runAnomalies = (table, value_column, date_column = null) =>
  api.post('/anomalies', { table, value_column, date_column }).then(r => r.data)

export const generateReport = (prompt, llm, tables = null) =>
  api.post('/report', { prompt, llm, tables }).then(r => r.data)
