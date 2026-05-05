import React, { useState, useEffect } from 'react'
import Sidebar from './components/Sidebar'
import QueryPage from './pages/QueryPage'
import ForecastPage from './pages/ForecastPage'
import AnomalyPage from './pages/AnomalyPage'
import ReportsPage from './pages/ReportsPage'
import { fetchTables } from './utils/api'

export default function App() {
  const [page, setPage] = useState('query')
  const [llm, setLlm] = useState('gemini')
  const [tables, setTables] = useState([])
  const [schemas, setSchemas] = useState({})

  useEffect(() => {
    fetchTables()
      .then(d => {
        setTables(d.tables || [])
        setSchemas(d.schemas || {})
      })
      .catch(() => {
        setTables([])
        setSchemas({})
      })
  }, [])

  const pages = {
    query:    <QueryPage llm={llm} setLlm={setLlm} />,
    forecast: <ForecastPage tables={tables} schemas={schemas} />,
    anomaly:  <AnomalyPage tables={tables} schemas={schemas} />,
    reports:  <ReportsPage llm={llm} setLlm={setLlm} tables={tables} />,
  }

  const titles = {
    query: 'Natural Language Query',
    forecast: 'Time Series Forecasting',
    anomaly: 'Anomaly Detection',
    reports: 'AI Reports',
  }

  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden' }}>
      <Sidebar active={page} setActive={setPage} tables={tables} />

      <main style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {/* Top bar */}
        <header style={{
          height: 50,
          background: 'var(--bg-surface)',
          borderBottom: '1px solid var(--border)',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '0 20px', flexShrink: 0,
        }}>
          <h1 style={{ fontSize: 14, fontWeight: 500, color: 'var(--text-secondary)' }}>
            {titles[page]}
          </h1>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>Using</div>
            <div style={{ fontSize: 11, fontWeight: 500, color: 'var(--accent)', background: 'var(--accent-dim)', padding: '2px 10px', borderRadius: 20 }}>
              {llm === 'gemini' ? '✦ Gemini' : '◈ DeepSeek'}
            </div>
          </div>
        </header>

        {/* Page content */}
        <div style={{ flex: 1, overflowY: 'auto', padding: 20 }}>
          {pages[page]}
        </div>
      </main>
    </div>
  )
}
