/**
 * embedBranding.js — single source of truth for the embed widget's display name.
 *
 * The product/company name is owned by the backend (`APP_NAME` in backend/.env)
 * and delivered at runtime via GET /embed/context (`app_name`). Nothing in the
 * embed UI should hardcode a product name — call appName(context) instead.
 *
 * Fallback chain (each used only if the previous is missing):
 *   1. context.app_name           — backend APP_NAME (authoritative)
 *   2. VITE_APP_NAME              — build-time default if context not yet loaded
 *   3. 'SalesPlay AI'            — last-resort literal
 */
export function appName(context) {
  return (
    context?.app_name ||
    import.meta.env.VITE_APP_NAME ||
    'SalesPlay AI'
  )
}

/**
 * productTitle — the widget header title. A partner may override it with a
 * branded phrase (branding.product_name, e.g. "SalesPlay AI");
 * otherwise it falls back to the app name.
 */
export function productTitle(context) {
  return context?.branding?.product_name || appName(context)
}
