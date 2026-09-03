// Browser-side file export for chat answers. Nothing is uploaded, nothing is
// stored: the rows arrive in the response, the file is built here and written
// straight to the user's Downloads folder. There is no re-download later —
// asking the assistant again is what produces a fresh file.

// RFC4180: double any quote, and wrap anything holding a delimiter, quote or
// newline. Excel needs the BOM to read UTF-8, or non-ASCII product names arrive
// as mojibake.
const cell = v => {
  if (v == null) return ''
  const s = String(v)
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

export function toCSV(columns, rows) {
  const cols = columns?.length ? columns : Object.keys(rows?.[0] || {})
  const lines = [cols.map(cell).join(',')]
  for (const r of rows || []) lines.push(cols.map(c => cell(r[c])).join(','))
  return '﻿' + lines.join('\r\n')
}

export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  // Revoke on the next tick — Safari cancels the download if the URL dies first.
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

// Opens a self-contained page and asks the browser to print it, which is where
// the merchant picks "Save as PDF". No PDF library and no headless browser: the
// print engine every browser already ships produces a better document than
// anything worth adding a dependency for.
//
// Unlike ReportsPage's version this does NOT inline document.styleSheets (the
// page carries its own CSS) and does NOT close the window on a timer, which
// races the print dialog and can cancel the job.
export function printDocument(html) {
  const win = window.open('', '_blank')
  if (!win) return false          // popup blocked — caller surfaces it
  win.document.write(html)
  win.document.close()
  win.focus()
  // onload so images/fonts settle before the dialog; the fallback covers a
  // document that is already complete by the time the handler is attached.
  const go = () => { try { win.print() } catch { /* user can print manually */ } }
  if (win.document.readyState === 'complete') setTimeout(go, 60)
  else win.onload = go
  return true
}

// Recharts writes fill="var(--blue)" / stroke="var(--green)" straight onto the
// SVG nodes. A detached SVG has no CSS context, so those resolve to nothing and
// the PNG comes out blank — every var() has to be substituted with the value
// the chart is actually painted with before serialising. Reading them from the
// live element also means each embed brand's own accent colour comes along,
// since applyBrandChrome overwrites --blue per partner.
const VAR_RE = /var\(\s*(--[\w-]+)\s*\)/g

function resolveVars(svg) {
  const clone = svg.cloneNode(true)
  const style = getComputedStyle(svg)
  const seen = {}
  const lookup = name => {
    if (!(name in seen)) seen[name] = style.getPropertyValue(name).trim() || '#4f8cff'
    return seen[name]
  }
  const walk = node => {
    for (const attr of Array.from(node.attributes || [])) {
      if (attr.value.includes('var(')) {
        node.setAttribute(attr.name, attr.value.replace(VAR_RE, (_, n) => lookup(n)))
      }
    }
    for (const child of node.children) walk(child)
  }
  walk(clone)
  return clone
}

export function chartToPNG(svgEl, scale = 2) {
  return new Promise((resolve, reject) => {
    if (!svgEl) return reject(new Error('no chart'))
    const box = svgEl.getBoundingClientRect()
    const w = Math.ceil(box.width || svgEl.getAttribute('width') || 640)
    const h = Math.ceil(box.height || svgEl.getAttribute('height') || 240)

    const clone = resolveVars(svgEl)
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
    clone.setAttribute('width', w)
    clone.setAttribute('height', h)
    const svgText = new XMLSerializer().serializeToString(clone)
    const url = URL.createObjectURL(new Blob([svgText], { type: 'image/svg+xml;charset=utf-8' }))

    const img = new Image()
    img.onload = () => {
      const canvas = document.createElement('canvas')
      canvas.width = w * scale
      canvas.height = h * scale
      const c = canvas.getContext('2d')
      // The chart is transparent; paint the card background so the PNG is
      // readable in a document rather than invisible on white.
      c.fillStyle = getComputedStyle(svgEl).getPropertyValue('--bg2').trim() || '#ffffff'
      c.fillRect(0, 0, canvas.width, canvas.height)
      c.drawImage(img, 0, 0, canvas.width, canvas.height)
      URL.revokeObjectURL(url)
      canvas.toBlob(b => b ? resolve(b) : reject(new Error('encode failed')), 'image/png')
    }
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('render failed')) }
    img.src = url
  })
}

// SpreadsheetML 2003 — a single XML file Excel, Sheets and Numbers all open as
// a real spreadsheet, with numbers as numeric cells rather than text.
//
// ponytail: deliberately not SheetJS. npm's xlsx is stuck at 0.18.5 with an
// unfixed high-severity advisory (prototype pollution + ReDoS) because SheetJS
// left the registry, and a write-only export does not justify shipping that
// into the embed widget. If styling, multiple sheets or formulas are ever
// needed, revisit with the vendored SheetJS CDN build.
const xmlEscape = s => String(s).replace(/[<>&'"]/g, c => (
  { '<': '&lt;', '>': '&gt;', '&': '&amp;', "'": '&apos;', '"': '&quot;' }[c]
))

const xmlCell = v => {
  if (v == null || v === '') return '<Cell/>'
  // Only finite real numbers become Number cells. NaN/Infinity are not valid
  // SpreadsheetML numbers, and falling through to the String branch would put
  // the literal text "NaN" in the cell — blank is the honest representation.
  if (typeof v === 'number') {
    return Number.isFinite(v)
      ? `<Cell><Data ss:Type="Number">${v}</Data></Cell>`
      : '<Cell/>'
  }
  return `<Cell><Data ss:Type="String">${xmlEscape(v)}</Data></Cell>`
}

export function toXLSX(columns, rows) {
  const cols = columns?.length ? columns : Object.keys(rows?.[0] || {})
  const header = `<Row>${cols.map(c =>
    `<Cell><Data ss:Type="String">${xmlEscape(c)}</Data></Cell>`).join('')}</Row>`
  const body = (rows || []).map(r =>
    `<Row>${cols.map(c => xmlCell(r[c])).join('')}</Row>`).join('')
  const xml = `<?xml version="1.0"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"
 xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
<Worksheet ss:Name="Data"><Table>${header}${body}</Table></Worksheet>
</Workbook>`
  return new Blob([xml], { type: 'application/vnd.ms-excel;charset=utf-8' })
}

export function slugify(text, fallback = 'data') {
  const s = String(text || '').toLowerCase().replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '').slice(0, 40)
  return s || fallback
}
