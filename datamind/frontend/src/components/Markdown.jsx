import React from 'react'
import ResultChart from './ResultChart'

// Minimal, safe markdown renderer for the assistant's analyst answers.
// Escape-first: all HTML is escaped BEFORE any transform, so the model's text
// can never inject raw HTML — then a fixed whitelist of transforms runs.
// Covers exactly what the answer prompt emits: **bold**, *italic*, `code`,
// bullet / numbered lists, GFM pipe tables, and paragraphs. No links or images
// (not emitted, and the riskiest surface), so none are produced. Shared by the
// main app (ChatPage) and the embed widget (EmbedChat) so both render identically.

const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

function inline(text) {
  let t = esc(text)
  t = t.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')  // bold before italic
  t = t.replace(/\*(.+?)\*/g, '<em>$1</em>')
  t = t.replace(/`(.+?)`/g, '<code>$1</code>')
  return t
}

// A ```chart fence lets the model put a picture inside its own answer, the same
// way it puts a markdown table there — it decides when a visual helps instead of
// us rendering one on every response that happened to return rows. Payload:
//   { "kind": "line"|"bar"|"pie", "title": "...", "rows": [{...}, ...] }
// Malformed JSON renders as nothing rather than breaking the answer around it.
const _isChartFence = line => /^\s*```chart\s*$/i.test(line)
const _isFenceEnd   = line => /^\s*```\s*$/.test(line)

function _parseChart(body) {
  try {
    const spec = JSON.parse(body)
    const rows = spec?.rows
    if (!Array.isArray(rows) || rows.length < 2) return null
    return { kind: spec.kind, title: spec.title, rows, columns: Object.keys(rows[0]) }
  } catch { return null }
}

const _isTableRow = line => /^\s*\|.*\|\s*$/.test(line)
const _isTableSep  = line => /^\s*\|?(\s*:?-+:?\s*\|)+\s*:?-+:?\s*\|?\s*$/.test(line)
const _splitRow = line => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim())

export default function Markdown({ text, style }) {
  if (!text) return null
  const blocks = []
  let list = null                               // { type:'ul'|'ol', items:[] }
  const flush = () => { if (list) { blocks.push(list); list = null } }

  const lines = String(text).split('\n')
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    // ```chart fence — the model's own inline visual
    if (_isChartFence(line)) {
      flush()
      let j = i + 1
      const body = []
      while (j < lines.length && !_isFenceEnd(lines[j])) { body.push(lines[j]); j++ }
      const chart = _parseChart(body.join('\n'))
      if (chart) blocks.push({ type: 'chart', ...chart })
      i = j
      continue
    }
    // GFM table: a "| a | b |" row immediately followed by a "|---|---|" row
    if (_isTableRow(line) && i + 1 < lines.length && _isTableSep(lines[i + 1])) {
      flush()
      const header = _splitRow(line)
      let j = i + 2
      const rows = []
      while (j < lines.length && _isTableRow(lines[j])) { rows.push(_splitRow(lines[j])); j++ }
      blocks.push({ type: 'table', header, rows })
      i = j - 1
      continue
    }
    // #, ##, ### headings — the answer prompt encourages markdown, and models
    // routinely emit these (e.g. "### Insights:"). Previously unhandled, so
    // the raw "### " landed in a <p> as literal text.
    const heading = line.match(/^\s*(#{1,6})\s+(.*)$/)
    const bullet   = line.match(/^\s*[-*•]\s+(.*)$/)
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/)
    if (heading) {
      flush(); blocks.push({ type: 'h', level: heading[1].length, text: heading[2] })
    } else if (bullet) {
      if (!list || list.type !== 'ul') { flush(); list = { type: 'ul', items: [] } }
      list.items.push(bullet[1])
    } else if (numbered) {
      // Renumber ourselves rather than relying on <ol>'s auto-numbering: a
      // model that writes "1." for every item (common — it isn't tracking a
      // running count) would otherwise render every line as "1." since we
      // pass the model's own list-item text straight through and <ol> only
      // auto-numbers when EVERY <li> comes from one contiguous list in DOM
      // order, which breaks the moment a non-list line (e.g. a blank line
      // the model left between "steps") splits it into two adjacent <ol>s
      // that both restart at 1.
      if (!list || list.type !== 'ol') { flush(); list = { type: 'ol', items: [] } }
      list.items.push(numbered[1])
    } else if (line.trim() === '') {
      flush()
    } else {
      flush(); blocks.push({ type: 'p', text: line })
    }
  }
  flush()

  return (
    <div style={style}>
      {blocks.map((b, i) => {
        if (b.type === 'p')
          return <p key={i} style={{ margin: '0 0 8px' }}
                    dangerouslySetInnerHTML={{ __html: inline(b.text) }} />
        if (b.type === 'h') {
          const Tag = `h${Math.min(b.level + 2, 6)}`   // model's # -> our h3, so it never outsizes the answer bubble
          const size = { 3: 16, 4: 15, 5: 14, 6: 14 }[Math.min(b.level + 2, 6)]
          return <Tag key={i} style={{ margin: '10px 0 6px', fontSize: size, fontWeight: 600, color: 'var(--text)' }}
                      dangerouslySetInnerHTML={{ __html: inline(b.text) }} />
        }
        if (b.type === 'chart')
          return (
            <div key={i} style={{ margin: '4px 0 10px' }}>
              {b.title && <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 2 }}>{b.title}</div>}
              <ResultChart columns={b.columns} data={b.rows} kind={b.kind} />
            </div>
          )
        if (b.type === 'table')
          return (
            <div key={i} style={{ overflowX: 'auto', margin: '4px 0 10px' }}>
              <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: '0.95em' }}>
                <thead>
                  <tr>
                    {b.header.map((c, k) => (
                      <th key={k} style={{ textAlign: 'left', padding: '6px 10px', borderBottom: '2px solid var(--border2, #444)', whiteSpace: 'nowrap' }}
                          dangerouslySetInnerHTML={{ __html: inline(c) }} />
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {b.rows.map((r, ri) => (
                    <tr key={ri}>
                      {r.map((c, ci) => (
                        <td key={ci} style={{ padding: '6px 10px', borderBottom: '1px solid var(--border, #333)' }}
                            dangerouslySetInnerHTML={{ __html: inline(c) }} />
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        if (b.type === 'ul') {
          const items = b.items.map((it, j) =>
            <li key={j} dangerouslySetInnerHTML={{ __html: inline(it) }} />)
          return <ul key={i} style={{ margin: '4px 0 8px', paddingLeft: 20 }}>{items}</ul>
        }
        // Ordered list: number ourselves rather than relying on <ol>'s native
        // auto-numbering. Models frequently write "1." for every item (they
        // aren't tracking a running count, especially mid-stream) — CSS
        // counter-based auto-numbering follows the SOURCE markers in some
        // renderers and would print "1. 1. 1." verbatim, which is exactly
        // what was reported. Rendering an explicit "{j+1}." span makes the
        // displayed number independent of whatever digit the model wrote.
        return (
          <ol key={i} style={{ margin: '4px 0 8px', paddingLeft: 0, listStyle: 'none' }}>
            {b.items.map((it, j) => (
              <li key={j} style={{ display: 'flex', gap: 6, margin: '2px 0' }}>
                <span style={{ flexShrink: 0, color: 'var(--text3)' }}>{j + 1}.</span>
                <span dangerouslySetInnerHTML={{ __html: inline(it) }} />
              </li>
            ))}
          </ol>
        )
      })}
    </div>
  )
}
