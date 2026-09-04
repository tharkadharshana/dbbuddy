/**
 * Self-check for the printable document renderer.
 *   node src/utils/documentHtml.test.mjs
 *
 * Loads through Vite so the extensionless './locale' import resolves the same
 * way it does in the app.
 *
 * The load-bearing property under test: every figure on the page comes from the
 * rows, and totals are summed here rather than taken from anything the model
 * wrote. If that ever regresses, a merchant can hand a customer a document with
 * an invented total on it.
 */
import { createServer } from 'vite'

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom', logLevel: 'error' })
global.localStorage = { getItem: () => null }
const { buildDocumentHTML } = await server.ssrLoadModule('/src/utils/documentHtml.js')

let failures = 0
const need = (cond, msg) => { if (!cond) { console.error('FAIL: ' + msg); failures++ } }

const spec = {
  title: 'Sales Document',
  subtitle: 'Receipt SP-10023',
  header_fields: { Receipt: 'receipt_number', Customer: 'customer_name' },
  line_columns: ['product_name', 'quantity', 'price', 'total_money'],
  total_columns: ['total_money'],
  notes: 'Thank you.',
}
const data = [
  { receipt_number: 'SP-10023', customer_name: 'A & Co <script>alert(1)</script>',
    product_name: 'Tea "large"', quantity: 2, price: 350.5, total_money: 701 },
  { receipt_number: 'SP-10023', customer_name: 'A & Co <script>alert(1)</script>',
    product_name: 'Bun', quantity: 3, price: 100, total_money: 300 },
]
const html = buildDocumentHTML({
  document: spec, data, moneyCols: ['price', 'total_money'],
  accent: '#0058BE',
})

// Totals are computed from the rows, never supplied.
need(html.includes('1,001.00'), 'total not summed from rows (expected 1,001.00)')
need(/<tfoot>/.test(html), 'tfoot missing when totals requested')

// Formatting matches ResultTable: money keeps decimals, counts do not.
need(html.includes('350.50'), 'money column lost its decimals')
need(!/>\s*2\.00\s*</.test(html), 'count column wrongly money-formatted')
need(/>\s*2\s*</.test(html), 'count column not rendered as a whole number')

// Values are merchant data and reach the page as text, never as markup.
need(!html.includes('<script>'), 'XSS: raw script tag survived escaping')
need(html.includes('&amp;'), 'ampersand not escaped')
need(html.includes('&quot;'), 'quote not escaped')

// Print structure.
need(html.includes('display: table-header-group'), 'header will not repeat across pages')
need(html.includes('@page'), 'no page margins set')

// No product or brand name anywhere on the page. The document is the
// merchant's, handed to their own customers -- our name has no place on it,
// and neither does the partner's.
for (const word of ['salesplay', 'sellmo', 'datamind', 'nvision']) {
  need(!html.toLowerCase().includes(word), `brand name "${word}" leaked into the document`)
}
need(html.includes('#0058BE'), 'accent colour not applied')

// Provenance and the AI disclaimer, and never a claim of being a tax document.
need(html.includes('Generated from your sales records'), 'provenance footer missing')
need(html.includes('Content is AI generated and unverified.'), 'AI disclaimer missing')
need(/\d{1,2}:\d{2}/.test(html), 'footer carries no time, only a date')
need(!/tax\s*invoice/i.test(html), 'document presents itself as a tax invoice')

// Header block is filled from the first row.
need(html.includes('SP-10023'), 'header field not filled from row data')

// A document without totals renders cleanly.
const noTotals = buildDocumentHTML({ document: { ...spec, total_columns: [] }, data })
need(!/<tfoot>/.test(noTotals), 'tfoot rendered with no total columns')

// A single-row document (one receipt line) still renders.
const oneRow = buildDocumentHTML({ document: spec, data: [data[0]], moneyCols: ['total_money'] })
need(oneRow.includes('701.00'), 'single-row total wrong')

// A header field that varies across rows must be dropped rather than printed
// as row one's value -- "Total spent: LKR 7,000" above a table totalling
// 17,095 is worse than no field at all.
const varying = buildDocumentHTML({
  document: {
    title: 'Summary',
    header_fields: { 'Total spent': 'total_money', Customer: 'customer' },
    line_columns: ['product_name', 'total_money'],
    total_columns: ['total_money'],
  },
  data: [
    { customer: 'A', product_name: 'Pizza', total_money: 7000 },
    { customer: 'A', product_name: 'Bun', total_money: 10095.2 },
  ],
  moneyCols: ['total_money'],
})
need(!varying.includes('Total spent'), 'a varying column was printed as a header field')
need(varying.includes('Customer'), 'a constant header field was wrongly dropped')
need(varying.includes('17,095.20'), 'table total wrong')

// A percentage column prints as percentage POINTS with no "+" sign. The bug:
// a margin of 18.96 printed as "+0.1896%" because the value arrived unscaled
// and every pct column got a growth sign.
const pct = buildDocumentHTML({
  document: {
    title: 'Sales Summary',
    line_columns: ['gross_margin_pct', 'change_pct'],
    total_columns: [],
  },
  data: [{ gross_margin_pct: 18.96, change_pct: 4.2 }],
})
need(pct.includes('18.96%'), 'margin percent not rendered as points')
need(!pct.includes('+18.96%'), 'a level was printed with a growth sign')
need(pct.includes('+4.20%'), 'a delta column lost its sign')

// A merchant on LKR must not get "$" in a document. The embed stores its user
// under a partner-key-suffixed localStorage key, so the plain 'dm_embed_user'
// lookup always missed and every amount fell back to the default symbol.
globalThis.localStorage = {
  _d: { 'dm_embed_user_sp_dev_test': JSON.stringify({ locale: { currency: 'LKR' } }) },
  getItem(k) { return this._d[k] ?? null },
}
globalThis.window = { location: { search: '?pk=sp_dev_test' } }

const lkr = buildDocumentHTML({
  document: { title: 'Sales', line_columns: ['net_sales', 'refunds', 'sold_qty'],
              total_columns: ['net_sales'] },
  data: [{ net_sales: 3010.72, refunds: 1263.05, sold_qty: 5 }],
  moneyCols: ['net_sales', 'refunds'],
})
need(lkr.includes('LKR 3,010.72'), 'currency symbol not taken from the embed locale')
need(!lkr.includes('$'), 'fell back to the default currency symbol')
need(lkr.includes('LKR 1,263.05'), 'a refunds column lost its decimals')
need(/>5</.test(lkr), 'a count was padded with decimals')

await server.close()
console.log(failures ? `${failures} check(s) failed` : 'documentHtml self-check passed')
process.exit(failures ? 1 : 0)
