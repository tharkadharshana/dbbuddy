import React, { useState } from 'react'
import { Btn, Card, Spinner, SectionHeader } from '../components/UI'
import { generateReport } from '../utils/api'

const PRESETS = [
  { label: 'Executive summary', prompt: 'Write a concise executive summary of the current state of the business based on the available data, highlighting key trends, wins, and risks.' },
  { label: 'Revenue analysis', prompt: 'Analyze the revenue data across all relevant tables. Identify top-performing segments, growth trends, and any areas of concern.' },
  { label: 'Customer insights', prompt: 'Summarize insights about customer behavior, top customers, churn signals, and purchasing patterns from the data.' },
  { label: 'Inventory & supply', prompt: 'Review the inventory data and flag any low-stock risks, overstock situations, or unusual consumption patterns.' },
  { label: 'Anomaly report', prompt: 'Summarize any unusual patterns or statistical outliers found in the data that the team should investigate.' },
]

export default function ReportsPage({ llm, tables }) {
  const [prompt, setPrompt] = useState('')
  const [loading, setLoading] = useState(false)
  const [reports, setReports] = useState([])
  const [error, setError] = useState(null)

  async function handleGenerate() {
    if (!prompt.trim()) return
    setLoading(true); setError(null)
    try {
      const data = await generateReport(prompt, llm, tables)
      setReports(prev => [{
        prompt,
        text: data.report,
        llm,
        time: new Date().toLocaleTimeString(),
      }, ...prev])
      setPrompt('')
    } catch (e) {
      setError(e.response?.data?.detail || e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

      {/* Input */}
      <Card style={{ padding: 16 }}>
        <SectionHeader title="AI Report Generator" />

        {/* Presets */}
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
          {PRESETS.map(p => (
            <div key={p.label} onClick={() => setPrompt(p.prompt)} style={{
              padding: '4px 12px', borderRadius: 20, fontSize: 11,
              background: prompt === p.prompt ? '#f0f0f8' : 'var(--color-background-secondary)',
              color: prompt === p.prompt ? '#534AB7' : 'var(--color-text-secondary)',
              border: `0.5px solid ${prompt === p.prompt ? 'rgba(83,74,183,0.3)' : 'var(--color-border-tertiary)'}`,
              cursor: 'pointer',
              transition: 'all 0.1s'
            }} onMouseEnter={(e) => { if (prompt !== p.prompt) e.currentTarget.style.borderColor = 'var(--color-border-secondary)'; }} onMouseLeave={(e) => { if (prompt !== p.prompt) e.currentTarget.style.borderColor = 'var(--color-border-tertiary)'; }}>{p.label}</div>
          ))}
        </div>

        <textarea
          value={prompt}
          onChange={e => setPrompt(e.target.value)}
          placeholder="Describe the report you want… or pick a preset above"
          rows={3}
          style={{ width: '100%', padding: '10px 14px', borderRadius: 'var(--border-radius-md)', background: 'var(--color-background-secondary)', border: '0.5px solid var(--color-border-secondary)', color: 'var(--color-text-primary)', fontSize: 13, resize: 'none', marginBottom: 10, outline: 'none' }}
        />
        <Btn onClick={handleGenerate} disabled={loading || !prompt.trim()} style={{ padding: '10px 18px' }}>
          {loading ? <Spinner size={14} color="#fff" /> : 'Generate Report ↗'}
        </Btn>
      </Card>

      {error && (
        <Card style={{ padding: '14px 18px', background: '#FCEBEB', borderColor: 'rgba(163,45,45,0.1)' }}>
          <span style={{ fontSize: 13, color: '#A32D2D' }}>⚠ {error}</span>
        </Card>
      )}

      {/* Loading */}
      {loading && (
        <Card style={{ padding: 24, background: 'var(--color-background-secondary)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, color: 'var(--color-text-tertiary)', fontSize: 13 }}>
            <Spinner size={16} />
            The AI is analyzing your database and generating a report…
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 16 }}>
            {[80, 60, 90, 50].map((w, i) => (
              <div key={i} style={{ height: 12, width: `${w}%`, background: '#fff', borderRadius: 4, opacity: 0.5 }} />
            ))}
          </div>
        </Card>
      )}

      {/* Reports history */}
      {reports.length === 0 && !loading && (
        <div style={{ textAlign: 'center', padding: '48px 24px', color: 'var(--color-text-tertiary)' }}>
          <div style={{ fontSize: 32, marginBottom: 12 }}>📋</div>
          <div style={{ fontSize: 14, fontWeight: 500, color: 'var(--color-text-secondary)', marginBottom: 4 }}>No reports yet</div>
          <div style={{ fontSize: 12 }}>Pick a preset or write a custom prompt to generate your first AI report.</div>
        </div>
      )}

      {reports.map((r, i) => (
        <Card key={i} style={{ overflow: 'hidden' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 16px', borderBottom: '0.5px solid var(--color-border-tertiary)', background: 'var(--color-background-secondary)' }}>
            <div>
              <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--color-text-primary)' }}>{r.prompt.slice(0, 80)}{r.prompt.length > 80 ? '…' : ''}</div>
              <div style={{ fontSize: 11, color: 'var(--color-text-tertiary)', marginTop: 2 }}>Generated by {r.llm} · {r.time}</div>
            </div>
            <button
              onClick={() => navigator.clipboard.writeText(r.text)}
              style={{ padding: '4px 12px', background: '#fff', border: '0.5px solid var(--color-border-secondary)', borderRadius: 'var(--border-radius-md)', color: 'var(--color-text-secondary)', fontSize: 11, cursor: 'pointer' }}
            >
              Copy
            </button>
          </div>
          <div style={{ padding: '18px 20px' }}>
            <p style={{ fontSize: 13, color: 'var(--color-text-secondary)', lineHeight: 1.85, whiteSpace: 'pre-wrap' }}>{r.text}</p>
          </div>
        </Card>
      ))}
    </div>
  )
}
