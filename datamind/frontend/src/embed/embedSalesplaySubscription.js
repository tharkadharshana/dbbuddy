/**
 * embedSalesplaySubscription.js — turns a raw GET /subscriptions/get_ai_pos_info
 * response into the access decision the embed UI needs: can this merchant use
 * chat right now, and if not, why (and are they still trial-eligible)?
 *
 * Field meanings confirmed against a real predev2 response for a brand-new,
 * never-subscribed merchant:
 *   activation_status: 0, subscribe_status: 0, last_subscribe_date: null,
 *   is_subscribe: true, is_trial: true, available_credit: 0
 * — i.e. is_subscribe/is_trial are static "this product is subscribable /
 * has a trial" flags, NOT "currently subscribed/trialing". activation_status
 * is the real "can they use it right now" signal.
 */
export function evaluateSalesplayAccess(info) {
  const sub = info?.data?.subscription?.[0]
  // Whether a card is on file — checked up front so the plans screen can send
  // the user to add one instead of calling /subscriptions/payment and hitting
  // a guaranteed server error with no card.
  // NOTE: billing_details_added is unreliable — confirmed on predev2 it stays
  // false even after a card is successfully attached (payment_methods gets
  // populated but the flag doesn't flip). payment_methods.payment_method_id
  // is the field that actually reflects reality; treat either as "true" so a
  // fixed billing_details_added later doesn't need a code change.
  const billingDetailsAdded = !!info?.data?.billing_details_added || !!info?.data?.payment_methods?.payment_method_id
  const cardAddUrl = info?.data?.card_add_url || null
  const card = info?.data?.payment_methods
  const cardLabel = card?.payment_method_id ? `${(card.brand || 'card').toUpperCase()} ····${card.last4 || ''}` : null

  if (!sub) {
    return { hasAccess: false, trialAvailable: true, blockReason: null, plans: [], trialDays: 14, billingDetailsAdded, cardAddUrl, cardLabel }
  }

  const plans = sub.pricing_plans || []
  const trialDays = Number(sub.trial_period) || 14
  const neverUsed = !sub.last_subscribe_date && sub.activation_status !== 1 && sub.subscribe_status !== 1

  if (neverUsed) {
    return { hasAccess: false, trialAvailable: true, blockReason: null, plans, trialDays, billingDetailsAdded, cardAddUrl, cardLabel }
  }

  // available_credit is pay-as-you-go top-up credit, not a gate on a paid
  // flat-fee ADDON subscription — confirmed on predev2: an active subscription
  // (activation_status:1, is_expired:0, expire_date a year out) had
  // available_credit:0 and still means full access. Only gate on expiry.
  const expired = sub.is_expired === 1
  const hasAccess = sub.activation_status === 1 && !expired

  return {
    hasAccess,
    trialAvailable: false,
    blockReason: hasAccess ? null : 'trial_expired',
    plans,
    trialDays,
    billingDetailsAdded,
    cardAddUrl,
    cardLabel,
  }
}
