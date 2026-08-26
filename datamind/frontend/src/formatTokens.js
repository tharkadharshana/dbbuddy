// Token Display Multiplier — the single source of truth for how token values
// are rendered anywhere in the app or the embed widget.
//
// The backend stores a small composite credit unit (see billing.py:
// T = llm_tokens/1000 + rows_returned/1000 + FEATURE_COST[op]), so the
// Standard cap is 25,000. Every surface multiplies that out for display.
//
// This is a display scale only: `tokens_pct` and the usage bars come from the
// backend unscaled, and `used`/`limit` are both passed through fmtTok, so the
// ratio a user sees is always correct regardless of TDM's value.
//
// It lives here because it used to be copy-pasted — identically — into five
// files, which meant changing the scale silently left four screens disagreeing.
export const TDM = 10_000

export const fmtTok = (raw) => {
  const n = (raw || 0) * TDM
  if (n >= 1_000_000) return `${parseFloat((n / 1_000_000).toFixed(2))}M`
  if (n >= 1_000)     return `${parseFloat((n / 1_000).toFixed(1))}K`
  return Math.round(n).toLocaleString()
}
