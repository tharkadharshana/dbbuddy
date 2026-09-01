/**
 * embedSalesplayPlanFormat.js — shared plan-pricing helpers for the Salesplay
 * embed, used by both the account-backed plans screen (EmbedSalesplayPlans)
 * and the pre-account pricing preview on the consent screen
 * (EmbedSalesplayAutoInit). Keeps tier grouping / price formatting / feature
 * lists in one place so the two never drift apart.
 */

export const TIER_FEATURES = {
  0: [ // lowest plan
    { text: 'Ask Your Data (AI)',      ok: true  },
    { text: 'All Analytics & Reports', ok: true  },
    { text: 'Forecasting & Anomalies', ok: false },
    { text: 'Reports and charts downloadable', ok: false },
  ],
  1: [
    { text: 'Ask Your Data (AI)',      ok: true  },
    { text: 'All Analytics & Reports', ok: true  },
    { text: 'Forecasting & Anomalies', ok: true  },
    { text: 'Reports and charts downloadable', ok: false },
  ],
  2: [
    { text: 'Ask Your Data (AI)',      ok: true  },
    { text: 'All Analytics & Reports', ok: true  },
    { text: 'Forecasting & Anomalies', ok: true  },
    { text: 'Reports and charts downloadable', ok: true  },
  ],
}

// Salesplay's own product_name ("Access To Unlimited AI POS Data Module
// Monthly/Yearly") is their internal SKU label, not a name a merchant should
// see here — show our own plan name instead.
export const TIER_TO_PLAN_NAME = ['Standard', 'Growth', 'Pro']

// Salesplay returns 6 flat pricing_plans entries — 3 tiers × {MONTHLY, YEARLY}.
// Pair them into 3 tiers (by ascending price within each cycle) so the UI
// shows 3 cards with a Monthly/Yearly toggle instead of 6 separate cards.
// Fails safe: if the two cycle lists don't line up 1:1, don't guess a
// pairing — every product_code sent to /subscriptions/payment must be
// exactly the one the user saw the price for, no exceptions.
export function groupPlansByTier(plans) {
  const monthly = (plans || []).filter(p => p.billing_type === 'MONTHLY').sort((a, b) => Number(a.product_price) - Number(b.product_price))
  const yearly  = (plans || []).filter(p => p.billing_type === 'YEARLY').sort((a, b) => Number(a.product_price) - Number(b.product_price))
  if (monthly.length === 0 || yearly.length === 0 || monthly.length !== yearly.length) {
    return null // caller falls back to a flat, ungrouped list
  }
  return monthly.map((m, i) => ({ monthly: m, yearly: yearly[i] }))
}

// Salesplay already formats each plan's price in the merchant's own currency
// ("LKR 1,654.93") — show that string verbatim, no symbol logic of ours.
// Building it ourselves was wrong twice over: product_price is Salesplay's
// base amount (5/10/25), not what the merchant is charged, and
// product_currency_symbol reads "$" even on LKR accounts.
export function planPrice(plan) {
  return String(plan?.show_price_text || plan?.product_price_text || '').trim()
    || String(plan?.show_price ?? plan?.product_price ?? '')
}

export function yearlySavingsPct(tier) {
  if (!tier?.monthly || !tier?.yearly) return null
  const monthlyAnnualized = Number(tier.monthly.product_price) * 12
  const yearly = Number(tier.yearly.product_price)
  if (!monthlyAnnualized) return null
  return Math.round((1 - yearly / monthlyAnnualized) * 100)
}

export function displayPlanName(tierIndex, billingType) {
  const name = TIER_TO_PLAN_NAME[tierIndex] || 'Plan'
  return `${name} — ${billingType === 'YEARLY' ? 'Yearly' : 'Monthly'}`
}
