const _CURRENCY_COLS = /revenue|money|amount|price|value|spend|ticket(?!s)|sale(?!_date)|avg_transaction|ltv|profit|order(?!s)|aov|monetary|cost|discount/i

export function getUserLocale() {
  try {
    const u = JSON.parse(localStorage.getItem('dm_user') || localStorage.getItem('dm_embed_user') || 'null')
    return u?.locale || null
  } catch { return null }
}

export function isCurrencyColumn(colName) {
  return _CURRENCY_COLS.test(colName || '')
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
