import React, { useState } from 'react'

// Copy an answer to the clipboard, as plain text a merchant can paste into
// Slack, email or a doc. Lives in components/ so the app and every embed brand
// share one copy, the way ResultChart and DownloadButton do.
//
// The partner iframe snippet ships allow="clipboard-write", so the async
// clipboard API is available in the widget. The execCommand fallback covers
// older browsers and any non-secure context, where navigator.clipboard is
// undefined rather than merely failing.

function legacyCopy(text) {
  const ta = document.createElement('textarea')
  ta.value = text
  // Off-screen rather than hidden: a display:none element cannot be selected.
  ta.style.cssText = 'position:fixed;top:-9999px;left:-9999px;opacity:0'
  document.body.appendChild(ta)
  ta.select()
  let ok = false
  try { ok = document.execCommand('copy') } catch { ok = false }
  ta.remove()
  return ok
}

export async function copyText(text) {
  if (!text) return false
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // Permission denied or a non-secure context — fall through and try the
    // synchronous path rather than reporting failure to the merchant.
  }
  return legacyCopy(text)
}

// Renders the result grid as a markdown table so the numbers survive the paste.
// The answer usually refers to them ("pizza accounts for LKR 15,925"), which
// reads as an unsupported claim once the table is gone.
function tableToMarkdown(columns, rows) {
  if (!columns?.length || !rows?.length) return ''
  const cell = v => (v == null ? '' : String(v).replace(/\|/g, '\\|'))
  const head = `| ${columns.join(' | ')} |`
  const rule = `| ${columns.map(() => '---').join(' | ')} |`
  const body = rows.map(r => `| ${columns.map(c => cell(r[c])).join(' | ')} |`)
  return [head, rule, ...body].join('\n')
}

// The visible answer, in the order it appears on screen. `analysis` is the
// model's own answer in the agent flow and Think Mode commentary in the legacy
// one; `content` is the legacy summary line, which is hidden when show_data is
// false and so is skipped here too.
export function messageToText(msg) {
  const parts = []
  if (msg?.analysis) parts.push(String(msg.analysis).trim())
  if (msg?.content && msg?.data?.show_data !== false) parts.push(String(msg.content).trim())
  const d = msg?.data
  if (d?.show_data !== false && d?.data?.length) {
    const table = tableToMarkdown(d.columns, d.data)
    if (table) parts.push(table)
  }
  return parts.filter(Boolean).join('\n\n')
}

export default function CopyButton({ msg, title = 'Copy answer' }) {
  const [copied, setCopied] = useState(false)
  const [failed, setFailed] = useState(false)

  const text = messageToText(msg)
  if (!text) return null

  const onClick = async () => {
    const ok = await copyText(text)
    setCopied(ok); setFailed(!ok)
    setTimeout(() => { setCopied(false); setFailed(false) }, 1600)
  }

  return (
    <>
    <style>{`
      .dm-copy-btn { transition: transform .12s ease, color .12s ease, opacity .12s ease; }
      .dm-copy-btn:hover { transform: scale(1.15); opacity: 1; }
    `}</style>
    <button
      className="dm-copy-btn"
      type="button" onClick={onClick}
      title={failed ? "Couldn't copy" : copied ? 'Copied' : title}
      aria-label={title}
      style={{
        background: 'none', border: 'none', cursor: 'pointer', padding: 4,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        color: copied ? 'var(--green, #41c99e)' : 'var(--text3)',
        opacity: copied ? 1 : 0.6,
      }}
    >
      {copied ? (
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M20 6 9 17l-5-5" />
        </svg>
      ) : (
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <rect x="9" y="9" width="13" height="13" rx="2" />
          <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
        </svg>
      )}
    </button>
    </>
  )
}
