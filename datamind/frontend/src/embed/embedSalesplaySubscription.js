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
 *   ('no_subscription' also covers a brand-new user before onboarding's
 *    start_trial() call has run, though in practice that always runs first)
 */
export function evaluateSalesplayAccess(salesplayInfo, internalSub) {
  const sub   = salesplayInfo?.data?.subscription?.[0]
  const plans = sub?.pricing_plans || []

  // billing_details_added is unreliable — confirmed on predev2 it stays false
  // even after a card is successfully attached. payment_methods.payment_method_id
  // is the field that actually reflects reality.
  const billingDetailsAdded = !!salesplayInfo?.data?.billing_details_added || !!salesplayInfo?.data?.payment_methods?.payment_method_id
  const cardAddUrl = salesplayInfo?.data?.card_add_url || null
  const card = salesplayInfo?.data?.payment_methods
  const cardLabel = card?.payment_method_id ? `${(card.brand || 'card').toUpperCase()} ····${card.last4 || ''}` : null

  const status = internalSub?.status // 'trial' | 'active' | 'expired' | 'cancelled' | 'no_subscription'
  const isLive = status === 'trial' || status === 'active'
  const tokensOk = internalSub?.can_use_ai !== false
  const hasAccess = isLive && tokensOk
  const trialAvailable = status === 'trial' // silently auto-started at onboarding — this just reflects "still in it"

  let blockReason = null
  if (!hasAccess) {
    blockReason = (isLive && !tokensOk) ? 'quota_exceeded' : 'trial_expired'
  }

  return {
    hasAccess,
    trialAvailable,
    blockReason,
    plans,
    trialDays: 14, // subscription_plans.trial_days — same for all 3 plans
    billingDetailsAdded,
    cardAddUrl,
    cardLabel,
  }
}
