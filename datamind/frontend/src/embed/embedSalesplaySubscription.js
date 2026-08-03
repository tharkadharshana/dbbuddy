/**
 * embedSalesplaySubscription.js — decides chat access for the Salesplay embed.
 *
 * Salesplay is the payment gateway only. Access itself (trial days, token
 * quota, plan tiers) is DataMind's own — sourced from GET /billing/subscription
 * (billing.py's get_user_subscription), which already tracks a 14-day trial
 * per subscription_plans.trial_days and per-plan token limits.
 *
 * Salesplay's get_ai_pos_info response is only used for what it's actually
 * authoritative for: is there a card on file, and what are the real
 * product_code/price options to display (pricing_plans).
 *
 * account is active   when internal status is 'trial' or 'active' AND tokens remain
 * account is inactive when internal status is 'expired'/'cancelled'/'no_subscription'
 *   ('no_subscription' is the normal state for a brand-new user — nothing
 *    starts a trial until they explicitly click "Start free trial")
 */
export function evaluateSalesplayAccess(salesplayInfo, internalSub) {
  const sub   = salesplayInfo?.data?.subscription?.[0]
  const plans = sub?.pricing_plans || []

  // is_valid_card_added is Salesplay's authoritative "there is a usable card
  // on file" flag — the only one that gates whether /subscriptions/payment can
  // succeed. billing_details_added is no substitute in either direction:
  // confirmed on predev2 it stayed false with a card successfully attached,
  // and it flips true on a saved billing address with no card at all, which
  // showed "Subscribe" to merchants who could only ever hit a failed payment.
  const billingDetailsAdded = !!salesplayInfo?.data?.is_valid_card_added
  const cardAddUrl = salesplayInfo?.data?.card_add_url || null
  const card = salesplayInfo?.data?.payment_methods
  const cardLabel = card?.payment_method_id ? `${(card.brand || 'card').toUpperCase()} ····${card.last4 || ''}` : null

  const status = internalSub?.status // 'trial' | 'active' | 'expired' | 'cancelled' | 'no_subscription'
  const isLive = status === 'trial' || status === 'active'
  const tokensOk = internalSub?.can_use_ai !== false
  const hasAccess = isLive && tokensOk
  // Never subscribed yet — the only state where the "Start free trial" CTA
  // should show. Once trial/active/expired/cancelled, the trial offer is used up.
  const trialAvailable = status === 'no_subscription'

  let blockReason = null
  if (!hasAccess) {
    blockReason = trialAvailable ? null : (isLive && !tokensOk) ? 'quota_exceeded' : 'trial_expired'
  }

  // Paid plan, quota used up: Salesplay's own subscription is still active
  // (already charged this cycle) — re-running /subscriptions/payment isn't a
  // real option, there's nothing left to buy there. Only extra AI credits
  // (an addon, not a new plan) fix this, and that's not sold through this
  // screen — send the merchant to support instead of the plan picker.
  // Trial users hitting quota are unaffected: they haven't paid yet, so
  // subscribing to a paid plan is still the right, unblocked next step.
  const isPaidQuotaBlocked = status === 'active' && !tokensOk

  return {
    hasAccess,
    trialAvailable,
    blockReason,
    isPaidQuotaBlocked,
    plans,
    trialDays: 14, // subscription_plans.trial_days — same for all 3 plans
    billingDetailsAdded,
    cardAddUrl,
    cardLabel,
  }
}
