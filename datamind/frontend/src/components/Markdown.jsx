import React from 'react'

// Minimal, safe markdown renderer for the assistant's analyst answers.
// Escape-first: all HTML is escaped BEFORE any transform, so the model's text
// can never inject raw HTML — then a fixed whitelist of transforms runs.
// Covers exactly what the answer prompt emits: **bold**, *italic*, `code`,
// bullet / numbered lists, and paragraphs. No links or images (not emitted,
// and the riskiest surface), so none are produced. Shared by the main app
// (ChatPage) and the embed widget (EmbedChat) so both render identically.

const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

function inline(text) {
  let t = esc(text)
  t = t.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')  // bold before italic
  t = t.replace(/\*(.+?)\*/g, '<em>$1</em>')
  t = t.replace(/`(.+?)`/g, '<code>$1</code>')
  return t
}

export default function Markdown({ text, style }) {
  if (!text) return null
  const blocks = []
  let list = null                               // { type:'ul'|'ol', items:[] }
  const flush = () => { if (list) { blocks.push(list); list = null } }

  for (const line of String(text).split('\n')) {
    const bullet   = line.match(/^\s*[-*•]\s+(.*)$/)
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/)
    if (bullet) {
      if (!list || list.type !== 'ul') { flush(); list = { type: 'ul', items: [] } }
      list.items.push(bullet[1])
    } else if (numbered) {
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
        const items = b.items.map((it, j) =>
          <li key={j} dangerouslySetInnerHTML={{ __html: inline(it) }} />)
        return b.type === 'ul'
          ? <ul key={i} style={{ margin: '4px 0 8px', paddingLeft: 20 }}>{items}</ul>
          : <ol key={i} style={{ margin: '4px 0 8px', paddingLeft: 20 }}>{items}</ol>
      })}
    </div>
  )
}
