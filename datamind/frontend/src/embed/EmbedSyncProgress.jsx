/**
 * EmbedSyncProgress.jsx — workspace sync progress, with the polling that drives it.
 *
 * Shown at two points in the Salesplay embed, never both for the same merchant:
 *   - Trial:  onboarding runs it before handing off to chat (EmbedSalesplayAutoInit).
 *   - Paid:   after the charge clears (EmbedApp) — a merchant who came to
 *             subscribe goes card → payment → here, so nothing sits between
 *             them and the payment screen they came for.
 *
 * Renders the body only (progress bar + checklist); the caller supplies the
 * surrounding shell, since the two call sites frame it differently.
 *
 * Sync itself starts server-side at onboarding regardless — this only watches
 * it. Arriving after a payment usually means it already finished, so the
 * first poll ends it immediately.
 */
import React, { useState, useEffect } from 'react'
import { embedGetProviderStatus } from './embedApi'
import { notifyParent } from './EmbedApp'

const SP = {
  card:    '#FFFFFF',
  heading: '#191C1E',
  text:    '#545F73',
  text3:   '#8B93A7',
  blue:    '#0058BE',
  shadow:  '0px 4px 20px 0px rgba(84,95,115,0.12)',
}

const cardStyle = (sp) => ({
  background: sp ? SP.card : 'var(--bg2)',
  border: sp ? 'none' : '1px solid var(--border)',
  borderRadius: sp ? 14 : 8,
  padding: '14px',
  textAlign: 'left',
  marginBottom: 16,
  boxShadow: sp ? SP.shadow : 'none',
})

const rowStyle = (sp, isLast) => ({
  display: 'flex', gap: 10, alignItems: 'center',
  padding: '7px 0',
  borderBottom: isLast ? 'none' : `1px solid ${sp ? 'rgba(15,23,42,0.06)' : 'var(--border)'}`,
})

// Translates raw backend sync-progress messages (which name specific data
// types) into something a merchant reads without knowing our internals.
function friendlySyncMsg(raw, providerName) {
  const m = (raw || '').toLowerCase()
  if (m.includes('sync complete') || m.includes('✅')) return 'All set!'
  if (m.includes('shops') || m.includes('categories') || m.includes('payment types') || m.includes('products')) return 'Setting up your workspace…'
  if (m.includes('sync started')) return `Connecting to ${providerName}…`
  return 'Setting up your workspace…'
}

export default function EmbedSyncProgress({ partnerKey, appNm, sp = true, onDone }) {
  const [msg, setMsg]   = useState('Setting up your workspace…')
  const [pct, setPct]   = useState(0)
  const [rows, setRows] = useState(0)

  useEffect(() => {
    let attempts = 0
    let latestRows = 0
    let finished = false
    // Never leave the merchant on this screen: every exit path — done,
    // failed, or unreachable for 3 minutes — still calls onDone.
    const finish = (delay) => {
      if (finished) return
      finished = true
      clearInterval(interval)
      setTimeout(onDone, delay)
    }
    const interval = setInterval(async () => {
      attempts++
      try {
        const r = await embedGetProviderStatus(partnerKey)
        const prog = r.progress
        if (prog) {
          setMsg(friendlySyncMsg(prog.message, appNm))
          setPct(prog.percent || 0)
          setRows(prog.rows_synced || 0)
          latestRows = prog.rows_synced || latestRows
        }
        if (r.status === 'connected' || r.status === 'active') {
          notifyParent('dm:sync_complete', { rows: r.last_sync_rows || latestRows })
          finish(600)
        } else if (r.status === 'error') {
          finish(1500)
        }
      } catch {
        if (attempts > 90) finish(0)
      }
    }, 2000)
    return () => clearInterval(interval)
  }, [partnerKey]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div style={{ width: '100%', marginTop: 14 }}>
      <div style={{ fontSize: 13, color: sp ? SP.text : 'var(--text2)', marginBottom: 14, minHeight: 18 }}>
        {msg}
      </div>

      <div style={{ height: 4, background: sp ? '#E2E8F0' : 'var(--bg3)', borderRadius: 2, overflow: 'hidden', marginBottom: 10 }}>
        {pct > 0
          ? <div style={{ height: '100%', width: `${pct}%`, background: sp ? SP.blue : 'var(--blue)', borderRadius: 2, transition: 'width .6s ease' }} />
          : <div style={{ height: '100%', width: '30%', background: sp ? SP.blue : 'var(--blue)', borderRadius: 2, animation: 'obSlide 1.4s linear infinite' }} />
        }
      </div>

      <div style={{ display: 'flex', justifyContent: 'center', gap: 14, fontSize: 11, color: sp ? SP.text3 : 'var(--text3)', marginBottom: 16 }}>
        {rows > 0 && <span>Syncing data</span>}
        {pct > 0  && <span>{pct}%</span>}
      </div>

      <div style={cardStyle(sp)}>
        {[
          { icon: '🔗', text: `Connecting to ${appNm}` },
          { icon: '⚙️', text: 'Setting up your workspace' },
          { icon: '🧠', text: 'Preparing your analytics' },
          { icon: '⚡', text: 'This only happens once — future questions load instantly' },
        ].map(({ icon, text }, i, arr) => (
          <div key={i} style={rowStyle(sp, i === arr.length - 1)}>
            <span style={{ fontSize: 14, flexShrink: 0 }}>{icon}</span>
            <span style={{ fontSize: 11, color: sp ? SP.text : 'var(--text2)' }}>{text}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
