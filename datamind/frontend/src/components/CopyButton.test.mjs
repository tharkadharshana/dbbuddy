/**
 * Self-check for what the copy button puts on the clipboard.
 *   node src/components/CopyButton.test.mjs
 *
 * The property that matters: a copied answer keeps the figures it refers to.
 * The prose usually says something like "pizza accounts for LKR 15,925", which
 * reads as an unsupported claim once the table is dropped.
 */
import { createServer } from 'vite'

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom', logLevel: 'error' })
const { messageToText, messageToHTML } = await server.ssrLoadModule('/src/components/CopyButton.jsx')

let failures = 0
const need = (cond, msg) => { if (!cond) { console.error('FAIL: ' + msg); failures++ } }

// Agent answer: analysis is the answer, show_data false, no table shown.
const agent = messageToText({
  analysis: 'Pizza is their strongest preference.',
  data: { agent_answer: true, show_data: false, columns: ['a'], data: [{ a: 1 }] },
})
need(agent === 'Pizza is their strongest preference.', 'agent answer should be prose only, got: ' + JSON.stringify(agent))
need(!agent.includes('|'), 'hidden table must not be copied when show_data is false')

// Legacy answer with a visible result table.
const legacy = messageToText({
  content: 'Found 2 results',
  data: {
    show_data: true,
    columns: ['product_name', 'total'],
    data: [{ product_name: 'Pizza', total: 15925 }, { product_name: 'Tea', total: 493.31 }],
  },
})
need(legacy.includes('Found 2 results'), 'summary line missing')
need(legacy.includes('| product_name | total |'), 'table header missing')
need(legacy.includes('| --- | --- |'), 'markdown rule missing')
need(legacy.includes('| Pizza | 15925 |'), 'row missing')
need(legacy.includes('| Tea | 493.31 |'), 'second row missing')

// Both prose blocks present (legacy Think Mode + summary).
const both = messageToText({
  analysis: 'Commentary.', content: 'Found 1 result',
  data: { show_data: true, columns: ['a'], data: [{ a: 1 }] },
})
need(both.startsWith('Commentary.'), 'analysis should come first')
need(both.indexOf('Found 1 result') > 0, 'content should follow analysis')

// A pipe in a value must not break the table structure.
const piped = messageToText({
  content: 'x', data: { show_data: true, columns: ['name'], data: [{ name: 'A|B' }] },
})
need(piped.includes('A\\|B'), 'pipe in a value not escaped')

// Nothing to copy -> empty, so the button hides rather than copying blank.
need(messageToText({}) === '', 'empty message should yield empty text')
need(messageToText({ data: { show_data: true, columns: [], data: [] } }) === '',
     'empty result should yield empty text')
need(messageToText(null) === '', 'null message should not throw')

// Null cells render blank rather than "null".
const nulls = messageToText({
  content: 'x', data: { show_data: true, columns: ['a', 'b'], data: [{ a: null, b: 2 }] },
})
need(!nulls.includes('null'), 'null cell rendered as the word null')

// ---- text/html flavour: what Word, Outlook, Teams and Docs actually paste ----

// A markdown table the MODEL wrote inside its prose must become a real table.
// This is the case the screenshot showed arriving as a wall of pipes.
const proseTable = messageToHTML({
  analysis: [
    'Based on all available sales, these products are selling fastest:',
    '',
    '| Rank | Product | Units sold |',
    '|---:|---|---:|',
    '| 1 | Chicken and Cheese Pasta | 22 |',
    '| 2 | Sprite bottle 100ml | 20 |',
    '',
    '**Key takeaway:** Pasta is the volume leader.',
  ].join('\n'),
  data: { agent_answer: true, show_data: false },
})
need(proseTable.includes('<table'), 'markdown table in prose did not become a real table')
need(proseTable.includes('<th'), 'table header cells missing')
need((proseTable.match(/<tr>/g) || []).length === 3, 'expected 1 header + 2 body rows')
need(proseTable.includes('Chicken and Cheese Pasta'), 'row content missing')
need(!proseTable.includes('|---'), 'markdown separator leaked into the HTML')
need(proseTable.includes('<strong>Key takeaway:</strong>'), 'bold not converted')
need(proseTable.includes('<p>'), 'prose not wrapped in paragraphs')

// Inline styles, not a stylesheet -- pasted HTML arrives with <style> stripped.
need(proseTable.includes('style="'), 'table has no inline styles to survive the paste')

// A result grid becomes a table too.
const gridHTML = messageToHTML({
  content: 'Found 2 results',
  data: { show_data: true, columns: ['product', 'total'],
          data: [{ product: 'Pizza', total: 15925 }, { product: 'Tea', total: 493.31 }] },
})
need(gridHTML.includes('<table'), 'result grid did not become a table')
need(gridHTML.includes('Pizza'), 'grid row missing')

// Merchant data must never become markup, in either flavour.
const evil = { content: 'x', data: { show_data: true, columns: ['name'],
               data: [{ name: '<img src=x onerror=alert(1)>' }] } }
need(!messageToHTML(evil).includes('<img'), 'XSS: raw tag survived into clipboard HTML')
need(messageToHTML(evil).includes('&lt;img'), 'value not escaped')

const evilProse = messageToHTML({ analysis: '<script>alert(1)</script>', data: { show_data: false } })
need(!evilProse.includes('<script>'), 'XSS: script tag survived from prose')

// Lists and headings carry over.
const rich = messageToHTML({
  analysis: '### Insights\n\n- First point\n- Second point',
  data: { show_data: false },
})
need(rich.includes('<h5>') || rich.includes('<h4>') || rich.includes('<h3>'), 'heading missing')
need(rich.includes('<ul>') && (rich.match(/<li>/g) || []).length === 2, 'list items missing')

// Hidden tables stay hidden in HTML too.
const hidden = messageToHTML({ analysis: 'Prose.', data: { show_data: false, columns: ['a'], data: [{ a: 1 }] } })
need(!hidden.includes('<table'), 'hidden result grid leaked into the HTML flavour')

// Nothing to copy -> empty, so no blank clipboard write.
need(messageToHTML({}) === '', 'empty message should yield empty HTML')
need(messageToHTML(null) === '', 'null message should not throw')

// The two flavours must describe the same answer.
const msg = { analysis: 'A.', content: 'B', data: { show_data: true, columns: ['c'], data: [{ c: 'D' }] } }
need(messageToText(msg).includes('D') && messageToHTML(msg).includes('D'),
     'a value present in one flavour is missing from the other')

await server.close()
console.log(failures ? `${failures} check(s) failed` : 'CopyButton self-check passed')
process.exit(failures ? 1 : 0)
