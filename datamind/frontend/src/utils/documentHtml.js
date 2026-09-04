import { formatCurrency, formatNumber, isMoneyColumn } from './locale'

// Builds a standalone, print-ready page from a document layout the model chose
// and the rows the query actually returned.
//
// The split matters: the model supplies structure (title, which columns are the
// header, which are line items), and every value on the page is read from the
// rows here. Nothing printed passes through the model's own text, so a document
// can never carry a figure it invented.
//
// The page carries its own CSS rather than inheriting the app's. A print window
// has no access to the parent's stylesheet objects, and the app's dark theme is
// wrong on paper anyway.

const esc = s => String(s ?? '').replace(/[<>&"']/g, c => (
  { '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&#39;' }[c]
))

// Column names are database identifiers; a printed page needs headings a
// merchant reads. Anything not in the map falls back to title case.
const HEADINGS = {
  sku: 'SKU', qty: 'Qty', quantity: 'Qty',
  total_money: 'Amount', gross_total_money: 'Gross', total_discount: 'Discount',
  total_tax: 'Tax', price: 'Unit Price', product_name: 'Item',
  receipt_number: 'Receipt', customer_name: 'Customer', shop_name: 'Shop',
  created_at: 'Date', payment_type_name: 'Payment', category_name: 'Category',
}

const label = col => HEADINGS[col] || String(col || '').replace(/_/g, ' ')
  .replace(/\b\w/g, c => c.toUpperCase())

// Mirrors ResultTable's fmt() exactly — money with decimals, percentages with a
// sign, everything else as a whole number. A printed figure has to match the one
// in the chat digit for digit, or the merchant is looking at two different
// answers to the same question.
// Percentages are scaled to points before they get here (DownloadButton's
// toPercentPoints, using the columns the backend named), so this only formats.
// A leading "+" is for a CHANGE, not a level: "+18.96%" reads as growth when
// it is a margin.
const _PCT_RE = /pct|rate|percent|margin/i
const _DELTA_RE = /change|delta|growth|vs_|_diff/i

function cellText(value, col, moneyCols) {
  if (value == null || value === '') return ''
  if (typeof value === 'number') {
    if (isMoneyColumn(col, moneyCols)) return formatCurrency(value)
    if (_PCT_RE.test(col || '')) {
      const sign = value > 0 && _DELTA_RE.test(col) ? '+' : ''
      return `${sign}${formatNumber(value, null, 2)}%`
    }
    return formatNumber(value, null, 0)
  }
  return String(value)
}

const CSS = `
  * { box-sizing: border-box; }
  body { margin: 0; padding: 0; background: #fff; color: #14181f;
         font: 13px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  .page { max-width: 780px; margin: 0 auto; padding: 32px; }
  .top { display: flex; justify-content: space-between; align-items: flex-start;
         gap: 24px; padding-bottom: 16px; border-bottom: 2px solid var(--accent); }
  .title { font-size: 22px; font-weight: 700; letter-spacing: -0.01em; margin: 0; }
  .subtitle { font-size: 13px; color: #5b6472; margin-top: 3px; }
  .fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 8px 32px; margin: 20px 0 24px; }
  .field { display: flex; gap: 8px; font-size: 12.5px; }
  .field dt { color: #5b6472; min-width: 92px; }
  .field dd { margin: 0; font-weight: 600; }
  table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
  thead th { text-align: left; padding: 8px 10px; background: #f1f4f8;
             border-bottom: 1.5px solid #d4dae3; font-weight: 600; white-space: nowrap; }
  tbody td { padding: 7px 10px; border-bottom: 1px solid #e8ecf2; vertical-align: top; }
  tfoot td { padding: 10px; border-top: 1.5px solid #d4dae3; font-weight: 700; }
  .num { text-align: right; white-space: nowrap; }
  .notes { margin-top: 22px; font-size: 12.5px; color: #3d444a; }
  .foot { margin-top: 26px; padding-top: 12px; border-top: 1px solid #e8ecf2;
          font-size: 11px; color: #78818f; }
  @page { margin: 16mm; }
  @media print {
    .page { max-width: none; padding: 0; }
    /* Repeat the column headers when a long item list runs onto another page. */
    thead { display: table-header-group; }
    tfoot { display: table-row-group; }
    tr { break-inside: avoid; }
  }
`

export function buildDocumentHTML({ document: spec, data, moneyCols, accent }) {
  const rows = data || []
  const lineCols = spec?.line_columns || []
  const totalCols = spec?.total_columns || []

  // Header values come from the first row, which only makes sense for a field
  // that is the same on every row -- a receipt number, a date, a customer.
  // A field that varies (an amount, a per-item count) would print row one's
  // value under a label like "Total spent", which reads as a total and is not
  // one. Those are dropped: a missing field is recoverable, a wrong figure on a
  // document the merchant hands to someone else is not.
  const first = rows[0] || {}
  const isConstant = col => rows.every(r => r[col] === first[col])
  const fields = Object.entries(spec?.header_fields || {})
    .filter(([, col]) => isConstant(col))
    .map(([lbl, col]) => `<div class="field"><dt>${esc(lbl)}</dt>
         <dd>${esc(cellText(first[col], col, moneyCols))}</dd></div>`)
    .join('')

  const head = lineCols.map(c =>
    `<th class="${typeof first[c] === 'number' ? 'num' : ''}">${esc(label(c))}</th>`).join('')

  const body = rows.map(r => `<tr>${lineCols.map(c =>
    `<td class="${typeof r[c] === 'number' ? 'num' : ''}">${esc(cellText(r[c], c, moneyCols))}</td>`
  ).join('')}</tr>`).join('')

  // Totals are summed here from the rows, never taken from the model.
  let foot = ''
  if (totalCols.length) {
    const cells = lineCols.map((c, i) => {
      if (totalCols.includes(c)) {
        const sum = rows.reduce((s, r) => s + (Number(r[c]) || 0), 0)
        return `<td class="num">${esc(cellText(sum, c, moneyCols))}</td>`
      }
      return `<td>${i === 0 ? 'Total' : ''}</td>`
    }).join('')
    foot = `<tfoot><tr>${cells}</tr></tfoot>`
  }

  // Date and time, not just the date: two documents pulled the same day from
  // data that moved in between are otherwise indistinguishable.
  const generated = new Date().toLocaleString(undefined, {
    year: 'numeric', month: 'long', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })

  return `<!DOCTYPE html><html><head><meta charset="utf-8">
<title>${esc(spec?.title || 'Document')}</title>
<style>:root { --accent: ${esc(accent || '#0058BE')}; }${CSS}</style>
</head><body><div class="page">
  <div class="top">
    <div>
      <h1 class="title">${esc(spec?.title || 'Sales Document')}</h1>
      ${spec?.subtitle ? `<div class="subtitle">${esc(spec.subtitle)}</div>` : ''}
    </div>
  </div>
  ${fields ? `<dl class="fields">${fields}</dl>` : ''}
  <table>
    <thead><tr>${head}</tr></thead>
    <tbody>${body}</tbody>
    ${foot}
  </table>
  ${spec?.notes ? `<div class="notes">${esc(spec.notes)}</div>` : ''}
  <div class="foot">
    Generated from your sales records on ${esc(generated)}.<br>
    Content is AI generated and unverified.
  </div>
</div></body></html>`
}
