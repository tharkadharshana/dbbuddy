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
  { receipt_number: 'SP-10023', customer_name: 'A & Co',
    product_name: 'Bun', quantity: 3, price: 100, total_money: 300 },
]
const html = buildDocumentHTML({
  document: spec, data, moneyCols: ['price', 'total_money'],
  brandName: 'SalesPlay AI', accent: '#0058BE',
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

// Brand belongs to the partner, never to us.
need(html.includes('SalesPlay AI'), 'brand name missing')
need(!html.toLowerCase().includes('datamind'), 'our product name leaked into the document')
need(html.includes('#0058BE'), 'brand accent not applied')

// Provenance, and never a claim of being a tax document.
need(html.includes('Generated from your sales records'), 'provenance footer missing')
need(!/tax\s*invoice/i.test(html), 'document presents itself as a tax invoice')

// Header block is filled from the first row.
need(html.includes('SP-10023'), 'header field not filled from row data')

// A document without totals renders cleanly.
const noTotals = buildDocumentHTML({ document: { ...spec, total_columns: [] }, data, brandName: 'Sellmo' })
need(!/<tfoot>/.test(noTotals), 'tfoot rendered with no total columns')
need(noTotals.includes('Sellmo'), 'second brand not applied')

// A single-row document (one receipt line) still renders.
const oneRow = buildDocumentHTML({ document: spec, data: [data[0]], moneyCols: ['total_money'], brandName: 'X' })
need(oneRow.includes('701.00'), 'single-row total wrong')

await server.close()
console.log(failures ? `${failures} check(s) failed` : 'documentHtml self-check passed')
process.exit(failures ? 1 : 0)
