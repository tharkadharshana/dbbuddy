const _CURRENCY_COLS = /revenue|money|amount|price|value|spend|ticket(?!s)|sale(?!_date)|avg_transaction|ltv|profit|order(?!s)|aov|monetary|cost|discount/i

export function getUserLocale() {
  try {
    // The embed suffixes its keys with the partner key (embedStorage.js), so a
    // bare 'dm_embed_user' lookup always missed and every amount in the widget
    // fell back to '$' -- a merchant on LKR got "$ 4,303.89" in a document
    // beside a chat that said "LKR 4,303.89". Read the suffixed key the widget
    // actually wrote before giving up.
    const pk = new URLSearchParams(window.location.search).get('pk')
    const raw = localStorage.getItem('dm_user')
      || (pk && localStorage.getItem('dm_embed_user_' + pk))
      || localStorage.getItem('dm_embed_user')
    return JSON.parse(raw || 'null')?.locale || null
  } catch { return null }
}

export function isCurrencyColumn(colName) {
  return _CURRENCY_COLS.test(colName || '')
}

// Money-vs-count decision, unified with the backend's _is_money_column.
// Prefer the backend-provided money_cols list; fall back to a corrected local
// heuristic (count tokens win; "total" alone is NOT money) for older payloads
// and loaded history snapshots that carry no flags.
// Kept in step with the backend's _MONEY_FRAGMENTS/_COUNT_TOKENS in main.py.
// Date-ish tokens are excluded because "sale" matches money and sale_date would
// otherwise render as currency.
const _COUNT_RE = /(^|_)(qty|quantity|count|cnt|units?|number|num|rows?|visits?|date|time|day|month|year|week|at|id)($|_)/i
const _MONEY_RE = /revenue|money|amount|price|value|spend|spent|sale|profit|cost|discount|tax|charge|paid|refund|tip|surcharge/i
export function isMoneyColumn(col, moneyCols) {
  if (Array.isArray(moneyCols)) return moneyCols.includes(col)
  if (!col) return false
  if (_COUNT_RE.test(col)) return false
  return _MONEY_RE.test(col)
}

// The "· <label>: <total>" suffix for the result summary line. Uses the
// backend-picked summary_col + summary_is_money so both surfaces render one
// consistent value instead of each re-deriving a first-numeric total.
export function summarySuffix(data) {
  const col = data?.summary_col
  if (!col || !data.data?.length) return ''
  const total = data.data.reduce((s, r) => s + (Number(r[col]) || 0), 0)
  const label = col.replace(/_/g, ' ')
  return ` · ${label}: ${data.summary_is_money ? formatCurrency(total) : formatNumber(total)}`
}

export function formatCurrency(value, locale) {
  const l   = locale || getUserLocale()
  const sym = (l?.currency || '$').trim()
  const neg = Number(value) < 0
  const abs = formatNumber(Math.abs(Number(value)), l)
  return neg ? `-${sym} ${abs}` : `${sym} ${abs}`
}

export function formatNumber(value, locale, decimals) {
  const l    = locale || getUserLocale()
  const dec  = decimals !== undefined ? decimals : (l?.number_format?.decimals ?? 2)
  const dSep = l?.number_format?.decimal_separator  || '.'
  const tSep = l?.number_format?.thousand_separator || ','
  const fixed = Number(value).toFixed(dec)
  const [intPart, fracPart] = fixed.split('.')
  const intFormatted = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, tSep)
  return dec > 0 ? `${intFormatted}${dSep}${fracPart}` : intFormatted
}
