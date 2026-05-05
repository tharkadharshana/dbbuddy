import React, { useState, useEffect } from 'react'
import Sidebar from './components/Sidebar'
import QueryPage from './pages/QueryPage'
import ForecastPage from './pages/ForecastPage'
import AnomalyPage from './pages/AnomalyPage'
import ReportsPage from './pages/ReportsPage'
import { fetchTables } from './utils/api'

export default function App() {
  const [page, setPage] = useState('query')
  const [llm, setLlm] = useState('Gemini')
  const [tables, setTables] = useState([])
  const [schemas, setSchemas] = useState({})
  const [theme, setTheme] = useState(localStorage.getItem('theme') || 'light')

  useEffect(() => {
    localStorage.setItem('theme', theme)
    document.documentElement.setAttribute('data-theme', theme)
  }, [theme])

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
    query:    <QueryPage llm={llm} />,
    forecast: <ForecastPage tables={tables} schemas={schemas} />,
    anomaly:  <AnomalyPage tables={tables} schemas={schemas} />,
    reports:  <ReportsPage llm={llm} tables={tables} />,
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: 'var(--color-background-tertiary)' }}>
      {/* Topbar */}
      <header style={{
        height: 52,
        background: 'var(--color-background-primary)',
        borderBottom: '0.5px solid var(--color-border-tertiary)',
        padding: '0 24px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 15, fontWeight: 600, color: 'var(--color-text-primary)' }}>
          <div style={{ width: 28, height: 28, background: '#1a1a2e', borderRadius: 7, display: 'flex', alignItems: 'center', justifyCenter: 'center', padding: 6 }}>
            <svg viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
              <rect x="2" y="2" width="5" height="5" rx="1" fill="#7F77DD"/>
              <rect x="9" y="2" width="5" height="5" rx="1" fill="#5DCAA5"/>
              <rect x="2" y="9" width="5" height="5" rx="1" fill="#5DCAA5"/>
              <rect x="9" y="9" width="5" height="5" rx="1" fill="#7F77DD"/>
            </svg>
          </div>
          DataMind AI
        </div>
        
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, background: '#eaf3de', color: '#3B6D11', borderRadius: 20, padding: '4px 12px', fontSize: 12, fontWeight: 500 }}>
            <div style={{ width: 6, height: 6, background: '#639922', borderRadius: '50%' }}></div>
            MySQL · connected
          </div>

          <button 
            onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}
            style={{
              width: 32,
              height: 32,
              borderRadius: 'var(--border-radius-md)',
              background: 'var(--color-background-secondary)',
              border: '0.5px solid var(--color-border-tertiary)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'var(--color-text-secondary)',
              cursor: 'pointer'
            }}
          >
            {theme === 'light' ? (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path>
              </svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="5"></circle>
                <line x1="12" y1="1" x2="12" y2="3"></line>
                <line x1="12" y1="21" x2="12" y2="23"></line>
                <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line>
                <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line>
                <line x1="1" y1="12" x2="3" y2="12"></line>
                <line x1="21" y1="12" x2="23" y2="12"></line>
                <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line>
                <line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line>
              </svg>
            )}
          </button>
          
          <div style={{ display: 'flex', background: 'var(--color-background-secondary)', border: '0.5px solid var(--color-border-tertiary)', borderRadius: 'var(--border-radius-md)', padding: 3, gap: 2 }}>
            {['Gemini', 'DeepSeek'].map(name => (
              <button
                key={name}
                onClick={() => setLlm(name)}
                style={{
                  padding: '4px 12px',
                  borderRadius: 6,
                  fontSize: 12,
                  fontWeight: 500,
                  background: llm === name ? 'var(--color-background-primary)' : 'transparent',
                  color: llm === name ? 'var(--color-text-primary)' : 'var(--color-text-secondary)',
                  border: llm === name ? '0.5px solid var(--color-border-secondary)' : 'none',
                }}
              >
                {name}
              </button>
            ))}
          </div>
        </div>
      </header>

      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        <Sidebar active={page} setActive={setPage} tables={tables} />
        
        <main style={{ flex: 1, overflowY: 'auto', padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
          {pages[page]}
        </main>
      </div>
    </div>
  )
}
