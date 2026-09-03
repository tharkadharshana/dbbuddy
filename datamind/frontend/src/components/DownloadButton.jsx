import React, { useState } from 'react'
import { toCSV, toXLSX, chartToPNG, downloadBlob, printDocument, slugify } from '../utils/export'
import { buildDocumentHTML } from '../utils/documentHtml'

// Shown only when the merchant actually asked for a file — the agent's
// export_data tool puts an `export` payload on the response, and no payload
// means no button anywhere. Lives in components/ (not embed/) so the app and
// every embed brand share one copy, the way ResultChart does.
//
// A gesture is required: browsers cancel a download that fires without one, so
// the file is built on click rather than on arrival. Nothing is stored, so this
// button is gone when the conversation is reloaded.
const LABELS = { excel: 'Download Excel', csv: 'Download CSV', chart: 'Download chart',
                 document: 'Download PDF' }
// .xls, not .xlsx: the workbook is SpreadsheetML XML, and Excel shows a
// format-mismatch warning if an XML workbook arrives named .xlsx.
const EXT = { excel: 'xls', csv: 'csv', chart: 'png' }

export default function DownloadButton({ payload, chartRef, question, theme, brandName }) {
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  if (!payload?.data?.length) return null

  // The chart form needs a rendered chart. ResultChart returns null for a
  // single row or a shape it can't plot, so fall back to the spreadsheet
  // rather than offering a button that can't produce anything.
  const svg = chartRef?.current?.querySelector('svg')
  let format = payload.format || 'excel'
  if (format === 'chart' && !svg) format = 'excel'
  // A document with no validated layout can't be rendered; the spreadsheet
  // still carries the same figures, so fall back rather than print a blank page.
  if (format === 'document' && !payload.document?.line_columns?.length) format = 'excel'
  // The file carries the host's brand, never ours: one build serves every
  // partner, so a hardcoded name would put our product in a whitelabel's
  // downloads folder. Falls back to the document title, which
  // applyBrandChrome has already set to the brand's own product name.
  const brandLabel = brandName || document.title || 'Report'
  const brand = slugify(brandLabel, 'report')
  const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, '')
  const name = `${brand}-${slugify(question, 'data')}-${stamp}.${EXT[format]}`

  // The printed page gets the brand's own accent, the same value
  // applyBrandChrome painted onto the widget for this partner.
  const accentColor = () =>
    getComputedStyle(document.documentElement).getPropertyValue('--blue').trim() || '#0058BE'

  const run = async () => {
    setBusy(true); setFailed(false)
    try {
      const { columns, data } = payload
      if (format === 'csv') {
        downloadBlob(new Blob([toCSV(columns, data)], { type: 'text/csv;charset=utf-8' }), name)
      } else if (format === 'chart') {
        downloadBlob(await chartToPNG(svg), name)
      } else if (format === 'document') {
        const html = buildDocumentHTML({
          document: payload.document, data, moneyCols: payload.money_cols,
          brandName: brandLabel, accent: accentColor(),
        })
        if (!printDocument(html)) setFailed('blocked')
      } else {
        downloadBlob(toXLSX(columns, data), name)
      }
    } catch {
      setFailed(true)
    } finally {
      setBusy(false)
    }
  }

  const isLight = theme === 'light'
  return (
    <div style={{ marginTop: 10 }}>
      <button onClick={run} disabled={busy} style={{
        display: 'inline-flex', alignItems: 'center', gap: 6,
        fontSize: 12, fontWeight: 500, padding: '7px 14px', borderRadius: 8,
        cursor: busy ? 'default' : 'pointer', color: '#fff',
        background: 'var(--blue)', border: '1px solid var(--blue)',
        opacity: busy ? 0.6 : 1,
      }}>
        <span aria-hidden="true">⬇</span>
        {busy ? 'Preparing…' : LABELS[format]}
        {format !== 'chart' && payload.data.length > 1 && (
          <span style={{ opacity: 0.75, fontWeight: 400 }}>
            · {payload.data.length.toLocaleString()} rows
          </span>
        )}
      </button>
      {failed && (
        <span style={{ marginLeft: 8, fontSize: 11, color: isLight ? '#b4453f' : 'var(--red)' }}>
          {failed === 'blocked'
            ? 'Your browser blocked the print window — allow pop-ups here, then try again.'
            : "Couldn't prepare that file — please ask again."}
        </span>
      )}
    </div>
  )
}
