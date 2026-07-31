import React, { useState, useEffect } from 'react'
import { qaGet, qaPost } from '../utils/api'

// ─────────────────────────────────────────────────────────────────────────────
// DEV-ONLY QA dashboard. Drives the backend's /qa/* routes to force an account
// into any billing / report-cache state on demand.
//
// This file is only ever imported behind `import.meta.env.DEV` in App.jsx, so
// Vite tree-shakes it out of a production build entirely — and even if it were
// bundled, every endpoint it calls 404s unless the backend was started with
// QA_ROUTES_ENABLED=true, a non-prod DB host, and the caller in
// QA_ROUTES_EMAILS. The UI is the convenience layer; the backend owns the guard.
// ─────────────────────────────────────────────────────────────────────────────

const PLANS = ['Starter', 'Growth', 'Pro']
const btn = {
  padding: '6px 12px', borderRadius: 6, border: '1px solid var(--border)',
  background: 'var(--bg2)', color: 'var(--text)', cursor: 'pointer', fontSize: 13,
}
const card = {
  background: 'var(--bg2)', border: '1px solid var(--border)',
  borderRadius: 10, padding: 16, marginBottom: 14,
}

export default function QAPage() {
  const [state, setState] = useState(null)
  const [busy, setBusy]   = useState(false)
  const [err, setErr]     = useState(null)
  const [target, setTarget] = useState('')

  const q = (path, body) =>
    body === undefined
      ? qaGet(path, target ? { email: target } : {})
      : qaPost(path, { ...body, ...(target ? { email: target } : {}) })

  async function run(fn) {
    setBusy(true); setErr(null)
    try {
      const res = await fn()
      if (res?.data && res.data.plan !== undefined) setState(res.data)
      else await refresh()
    } catch (e) {
      setErr(e?.response?.status === 404
        ? 'QA routes are not mounted. Start the backend with QA_ROUTES_ENABLED=true.'
        : e?.response?.data?.detail || e.message)
    } finally { setBusy(false) }
  }

  const refresh = async () => {
    try { setState((await q('/state')).data) }
    catch (e) {
      setErr(e?.response?.status === 404
        ? 'QA routes are not mounted. Start the backend with QA_ROUTES_ENABLED=true.'
        : e?.response?.data?.detail || e.message)
    }
  }

  useEffect(() => { refresh() }, [])   // eslint-disable-line react-hooks/exhaustive-deps

  const S = state || {}

  return (
    <div style={{ padding: 24, maxWidth: 900, margin: '0 auto' }}>
      <div style={{ marginBottom: 16 }}>
        <h2 style={{ margin: 0, fontSize: 20 }}>QA — development only</h2>
        <p style={{ color: 'var(--text3)', fontSize: 13, margin: '4px 0 0' }}>
          Mutates real billing and cache state for the target account. Never available in production.
        </p>
      </div>

      {err && (
        <div style={{ ...card, borderColor: 'var(--red)', color: 'var(--red)' }}>{err}</div>
      )}

      <div style={card}>
        <label style={{ fontSize: 12, color: 'var(--text3)' }}>
          Target account (blank = you)
        </label>
        <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
          <input value={target} onChange={e => setTarget(e.target.value)}
                 placeholder="someone@example.com"
                 style={{ flex: 1, padding: '6px 10px', borderRadius: 6,
                          border: '1px solid var(--border)', background: 'var(--bg)',
                          color: 'var(--text)' }} />
          <button style={btn} disabled={busy} onClick={() => run(refresh)}>Refresh</button>
        </div>
      </div>

      <div style={card}>
        <h3 style={{ margin: '0 0 10px', fontSize: 15 }}>Current state</h3>
        {!state ? <div style={{ color: 'var(--text3)' }}>Loading…</div> : (
          <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
            <tbody>
              {[
                ['Account', S.email],
                ['Plan', `${S.plan} (${S.status})`],
                ['Period', `${S.period_start} → ${S.period_end}`],
                ['Tokens', `${S.tokens_used} / ${S.tokens_total_available}`],
                ['History window', `${S.history_months} months (from ${S.window_start})`],
                ['Row limit', S.row_limit],
                ['Tenant', S.tenant_id || '— none —'],
                ['AI flow', S.ai_flow],
                ['Report cache enabled', String(S.report_cache_enabled)],
                ['Live POS token', S.report_cache?.live_token_valid ? 'valid' : 'expired/absent'],
              ].map(([k, v]) => (
                <tr key={k}>
                  <td style={{ padding: '4px 8px 4px 0', color: 'var(--text3)', whiteSpace: 'nowrap' }}>{k}</td>
                  <td style={{ padding: '4px 0' }}>{String(v)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div style={card}>
        <h3 style={{ margin: '0 0 10px', fontSize: 15 }}>Subscription</h3>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {PLANS.map(p => (
            <button key={p} style={btn} disabled={busy}
                    onClick={() => run(() => q('/plan', { plan: p, status: 'active' }))}>
              Set {p}
            </button>
          ))}
          <button style={{ ...btn, color: 'var(--red)' }} disabled={busy}
                  onClick={() => run(() => q('/expire', {}))}>
            Expire subscription
          </button>
        </div>
      </div>

      <div style={card}>
        <h3 style={{ margin: '0 0 10px', fontSize: 15 }}>Tokens</h3>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button style={{ ...btn, color: 'var(--red)' }} disabled={busy}
                  onClick={() => run(() => q('/tokens', { action: 'drain' }))}>
            Drain to zero
          </button>
          <button style={btn} disabled={busy}
                  onClick={() => run(() => q('/tokens', { action: 'reset' }))}>
            Reset usage
          </button>
        </div>
      </div>

      <div style={card}>
        <h3 style={{ margin: '0 0 10px', fontSize: 15 }}>Report cache</h3>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
          <button style={{ ...btn, color: 'var(--red)' }} disabled={busy}
                  onClick={() => run(() => q('/cache/clear', {}))}>
            Clear all cached months
          </button>
          <button style={btn} disabled={busy}
                  onClick={() => run(() => q('/cache/age?days=30', {}))}>
            Age cache 30 days (force deep re-finalize)
          </button>
        </div>
        {S.report_cache?.by_report?.length > 0 ? (
          <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ color: 'var(--text3)', textAlign: 'left' }}>
                <th style={{ padding: '4px 8px 4px 0' }}>Report</th>
                <th style={{ padding: '4px 8px' }}>Months</th>
                <th style={{ padding: '4px 8px' }}>Oldest</th>
                <th style={{ padding: '4px 8px' }}>Newest</th>
                <th style={{ padding: '4px 8px' }}>Last fetched</th>
              </tr>
            </thead>
            <tbody>
              {S.report_cache.by_report.map(r => (
                <tr key={r.report_id}>
                  <td style={{ padding: '4px 8px 4px 0' }}>{r.report_id}</td>
                  <td style={{ padding: '4px 8px' }}>{r.months}</td>
                  <td style={{ padding: '4px 8px' }}>{String(r.oldest).slice(0, 10)}</td>
                  <td style={{ padding: '4px 8px' }}>{String(r.newest).slice(0, 10)}</td>
                  <td style={{ padding: '4px 8px' }}>{String(r.last_fetched).slice(0, 19)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div style={{ color: 'var(--text3)', fontSize: 13 }}>Nothing cached.</div>
        )}
      </div>
    </div>
  )
}
