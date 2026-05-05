import React, { useState } from 'react'
import { Btn, Card, LLMToggle, Spinner, SectionHeader } from '../components/UI'
import { generateReport } from '../utils/api'

const PRESETS = [
  { label: 'Executive summary', prompt: 'Write a concise executive summary of the current state of the business based on the available data, highlighting key trends, wins, and risks.' },
  { label: 'Revenue analysis', prompt: 'Analyze the revenue data across all relevant tables. Identify top-performing segments, growth trends, and any areas of concern.' },
  { label: 'Customer insights', prompt: 'Summarize insights about customer behavior, top customers, churn signals, and purchasing patterns from the data.' },
  { label: 'Inventory & supply', prompt: 'Review the inventory data and flag any low-stock risks, overstock situations, or unusual consumption patterns.' },
  { label: 'Anomaly report', prompt: 'Summarize any unusual patterns or statistical outliers found in the data that the team should investigate.' },
]

export default function ReportsPage({ llm, setLlm, tables }) {
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
      <Card style={{ padding: 20 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <SectionHeader title="AI Report Generator" />
          <LLMToggle value={llm} onChange={setLlm} />
        </div>

        {/* Presets */}
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
          {PRESETS.map(p => (
            <button key={p.label} onClick={() => setPrompt(p.prompt)} style={{
              padding: '4px 12px', borderRadius: 20, fontSize: 11,
              background: prompt === p.prompt ? 'var(--accent-dim)' : 'var(--bg-elevated)',
              color: prompt === p.prompt ? 'var(--accent)' : 'var(--text-muted)',
              border: `1px solid ${prompt === p.prompt ? 'rgba(91,138,240,0.3)' : 'var(--border)'}`,
              cursor: 'pointer',
            }}>{p.label}</button>
          ))}
        </div>

        <textarea
          value={prompt}
          onChange={e => setPrompt(e.target.value)}
          placeholder="Describe the report you want… or pick a preset above"
          rows={3}
          style={{ width: '100%', padding: '10px 14px', resize: 'vertical', marginBottom: 10 }}
        />
        <Btn onClick={handleGenerate} disabled={loading || !prompt.trim()}>
          {loading ? <><Spinner size={14} color="#fff" /> Generating…</> : 'Generate Report ↗'}
        </Btn>
      </Card>

      {error && (
        <Card style={{ padding: '14px 18px', borderColor: 'rgba(240,96,96,0.3)', background: 'var(--red-dim)' }}>
          <span style={{ fontSize: 13, color: 'var(--red)' }}>⚠ {error}</span>
        </Card>
      )}

      {/* Loading */}
      {loading && (
        <Card style={{ padding: 24 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, color: 'var(--text-muted)', fontSize: 13 }}>
            <Spinner size={16} />
            The AI is analyzing your database and generating a report…
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 16 }}>
            {[80, 60, 90, 50].map((w, i) => (
              <div key={i} className="skeleton" style={{ height: 14, width: `${w}%`, borderRadius: 4 }} />
            ))}
          </div>
        </Card>
      )}

      {/* Reports history */}
      {reports.length === 0 && !loading && (
        <div style={{ textAlign: 'center', padding: '48px 24px', color: 'var(--text-muted)' }}>
          <div style={{ fontSize: 32, marginBottom: 12 }}>📋</div>
          <div style={{ fontSize: 14, fontWeight: 500, color: 'var(--text-secondary)', marginBottom: 4 }}>No reports yet</div>
          <div style={{ fontSize: 12 }}>Pick a preset or write a custom prompt to generate your first AI report.</div>
        </div>
      )}

      {reports.map((r, i) => (
        <Card key={i} className="fade-up" style={{ overflow: 'hidden' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 16px', borderBottom: '1px solid var(--border)' }}>
            <div>
              <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-primary)' }}>{r.prompt.slice(0, 80)}{r.prompt.length > 80 ? '…' : ''}</div>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>Generated by {r.llm} · {r.time}</div>
            </div>
            <button
              onClick={() => navigator.clipboard.writeText(r.text)}
              style={{ padding: '4px 12px', background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', color: 'var(--text-muted)', fontSize: 11, cursor: 'pointer' }}
            >
              Copy
            </button>
          </div>
          <div style={{ padding: '18px 20px' }}>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.85, whiteSpace: 'pre-wrap' }}>{r.text}</p>
          </div>
        </Card>
      ))}
    </div>
  )
}
