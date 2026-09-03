import React, { useState } from 'react'
import { parseBlocks, inline, esc } from './Markdown'

// Copy an answer to the clipboard. Lives in components/ so the app and every
// embed brand share one copy, the way ResultChart and DownloadButton do.
//
// Two flavours go on the clipboard at once: text/html and text/plain. Word,
// Outlook, Teams and Google Docs take the HTML and paste a real formatted
// table; anything plain-text (a terminal, a code editor, Slack's composer)
// takes the markdown. Writing only text/plain is what made a copied table
// arrive as a wall of pipes.
//
// The partner iframe snippet ships allow="clipboard-write", so the async
// clipboard API is available in the widget. The execCommand fallback covers
// older browsers and any non-secure context, where navigator.clipboard is
// undefined rather than merely failing -- that path can only carry plain text,
// which is the correct degradation.

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

export async function copyRich(html, text) {
  if (!text) return false
  // ClipboardItem is the only way to put two representations on the clipboard.
  // Missing in older Safari/Firefox, where writeText still gets the markdown.
  try {
    if (html && navigator.clipboard?.write && typeof ClipboardItem !== 'undefined') {
      await navigator.clipboard.write([new ClipboardItem({
        'text/html':  new Blob([html], { type: 'text/html' }),
        'text/plain': new Blob([text], { type: 'text/plain' }),
      })])
      return true
    }
  } catch {
    // Permission denied, an unsupported type, or a non-secure context -- fall
    // through rather than reporting failure to the merchant.
  }
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch { /* fall through to the synchronous path */ }
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

// The same text, as HTML a rich-text target will render. Markdown tables the
// model wrote inside its prose become real <table>s here -- that is the case
// the plain-text-only copy handled worst, since a pipe table pasted into
// Outlook is just a wall of pipes.
//
// Reuses Markdown.jsx's own parser and inline formatter, so a pasted answer
// cannot drift from the rendered one. Both escape before transforming, so
// merchant data never becomes markup.
//
// Styles are inline attributes, not a stylesheet: pasted HTML arrives stripped
// of anything a <style> block would have carried.
const TD = 'padding:5px 9px;border:1px solid #d4dae3;'
const TH = TD + 'background:#f1f4f8;font-weight:600;text-align:left;'

function blocksToHTML(blocks) {
  const out = []
  for (const b of blocks) {
    if (b.type === 'p') out.push(`<p>${inline(b.text)}</p>`)
    else if (b.type === 'h') out.push(`<h${Math.min(b.level + 2, 6)}>${inline(b.text)}</h${Math.min(b.level + 2, 6)}>`)
    else if (b.type === 'ul') out.push(`<ul>${b.items.map(i => `<li>${inline(i)}</li>`).join('')}</ul>`)
    else if (b.type === 'ol') out.push(`<ol>${b.items.map(i => `<li>${inline(i)}</li>`).join('')}</ol>`)
    else if (b.type === 'table') {
      const head = b.header.map(c => `<th style="${TH}">${inline(c)}</th>`).join('')
      const body = b.rows.map(r => `<tr>${r.map(c => `<td style="${TD}">${inline(c)}</td>`).join('')}</tr>`).join('')
      out.push(`<table style="border-collapse:collapse;"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`)
    }
    // 'chart' blocks are a rendered picture, not text -- nothing to paste.
  }
  return out.join('\n')
}

function gridToHTML(columns, rows) {
  if (!columns?.length || !rows?.length) return ''
  const head = columns.map(c => `<th style="${TH}">${esc(String(c))}</th>`).join('')
  const body = rows.map(r => `<tr>${columns.map(c =>
    `<td style="${TD}">${esc(r[c] == null ? '' : String(r[c]))}</td>`).join('')}</tr>`).join('')
  return `<table style="border-collapse:collapse;"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`
}

export function messageToHTML(msg) {
  const parts = []
  if (msg?.analysis) parts.push(blocksToHTML(parseBlocks(String(msg.analysis))))
  if (msg?.content && msg?.data?.show_data !== false)
    parts.push(blocksToHTML(parseBlocks(String(msg.content))))
  const d = msg?.data
  if (d?.show_data !== false && d?.data?.length) {
    const grid = gridToHTML(d.columns, d.data)
    if (grid) parts.push(grid)
  }
  return parts.filter(Boolean).join('\n')
}

export default function CopyButton({ msg, title = 'Copy answer' }) {
  const [copied, setCopied] = useState(false)
  const [failed, setFailed] = useState(false)

  const text = messageToText(msg)
  if (!text) return null

  const onClick = async () => {
    const ok = await copyRich(messageToHTML(msg), text)
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
