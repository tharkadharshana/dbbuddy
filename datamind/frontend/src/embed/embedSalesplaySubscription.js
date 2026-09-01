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
  // Confirmed shape (Salesplay predev2): a single object, not an array —
  //   { payment_method_id, payment_method_type, provider, brand: "visa",
  //     last4: "4242", is_expired: 0, title: "VISA 4242", is_card: true }
  const card = salesplayInfo?.data?.payment_methods
  const cardBrand = card?.payment_method_id ? (card.brand || '') : null
  const cardLast4 = card?.payment_method_id ? (card.last4 || '') : null
  const cardExpired = !!card?.is_expired
  // MM/YY, only when Salesplay actually sends the pair — they send is_expired
  // today and nothing else, so this is null until that payload grows.
  const cardExpiry = card?.exp_month && card?.exp_year
    ? `${String(card.exp_month).padStart(2, '0')}/${String(card.exp_year).slice(-2)}`
    : null

  // Account credit balance — same subscription object as pricing_plans, sits
  // alongside it rather than inside it (one balance, applies whichever tier
  // is picked). *_text carries the real converted currency amount; the raw
  // available_credit/show_price numbers are Salesplay's internal tier code
  // (5/10/25), wrong scale for arithmetic — text fields only, parsed at
  // render time.
  const availableCreditText = sub?.available_credit_text
  const showPriceText = sub?.show_price_text

  const status = internalSub?.status // 'trial' | 'active' | 'expired' | 'cancelled' | 'no_subscription'
  const isLive = status === 'trial' || status === 'active'
  const tokensOk = internalSub?.can_use_ai !== false
  const hasAccess = isLive && tokensOk
  // Never subscribed yet — the only state where the "Start free trial" CTA
  // should show. Once trial/active/expired/cancelled, the trial offer is used up.
  const trialAvailable = status === 'no_subscription'

  let blockReason = null
  if (!hasAccess) {
    // status is 'expired'/'cancelled' only for a merchant who previously had
    // a subscription (see billing.py's get_user_subscription fallback query)
    // — was_paid_plan distinguishes a lapsed paid plan from a used-up trial,
    // which need different copy (never conflate with quota_exceeded, that's
    // an active plan that's merely used up this cycle, not lapsed).
    const lapsed = status === 'expired' || status === 'cancelled'
    blockReason = trialAvailable
      ? null
      : (isLive && !tokensOk)
        ? 'quota_exceeded'
        : (lapsed && internalSub?.was_paid_plan)
          ? 'plan_expired'
          : 'trial_expired'
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
    cardBrand,
    cardLast4,
    cardExpired,
    cardExpiry,
    availableCreditText,
    showPriceText,
  }
}
