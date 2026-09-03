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
const { messageToText } = await server.ssrLoadModule('/src/components/CopyButton.jsx')

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

await server.close()
console.log(failures ? `${failures} check(s) failed` : 'CopyButton self-check passed')
process.exit(failures ? 1 : 0)
