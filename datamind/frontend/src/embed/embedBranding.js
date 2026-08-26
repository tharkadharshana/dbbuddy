/**
 * embedBranding.js — the widget's only source of brand values.
 *
 * Every brand-visible string, colour, link and image comes from the partner row
 * via GET /embed/context. Nothing here reads import.meta.env: a build-time
 * default would bake one brand into a bundle that serves all of them.
 *
 * The defaults below are deliberately brand-neutral. A default that named one
 * brand would surface in another brand's widget the moment a field is missing,
 * which is exactly the leak this module exists to prevent.
 */

const NEUTRAL_COLORS = {
  bg:        'linear-gradient(180deg,#F0F4F8 0%,#F7F9FB 100%)',
  card:      '#FFFFFF',
  heading:   '#191C1E',
  text:      '#545F73',
  text3:     '#8B93A7',
  blueLight: '#D8E2FF',
  blueDark:  '#001A42',
  outline:   '#C2C6D6',
  green:     '#006947',
  red:       '#B3261E',
  redLight:  '#F9DEDC',
}

const DEFAULT_SUGGESTIONS = [
  'What was my total revenue last month?',
  'Which products are selling the fastest?',
  'Who are my top 10 customers?',
  'Compare sales across all my locations',
]

/**
 * resolveBrand — the whole brand, every field defaulted.
 *
 * Call once and pass the result down; never reach into context.branding
 * directly, or a missing field becomes an undefined on screen.
 */
export function resolveBrand(context) {
  const b = context?.branding || {}
  const name = b.product_name || context?.app_name || context?.partner_name || 'AI Assistant'
  return {
    productName:   name,
    // The host system's own name, for copy like "open it in <company>".
    companyName:   b.company_name || context?.partner_name || 'your provider',
    logoUrl:       b.logo_url || null,
    logoMarkUrl:   b.logo_mark_url || b.logo_url || null,
    faviconUrl:    b.favicon_url || null,
    appUrl:        b.app_url || null,
    termsUrl:      b.terms_url || null,
    privacyUrl:    b.privacy_url || null,
    supportEmail:  b.support_email || null,
    accent:        b.primary_color || '#0058BE',
    colors:        { ...NEUTRAL_COLORS, ...(b.colors || {}) },
    showBetaBadge: b.show_beta_badge === true,
    welcome:       b.welcome_message || null,
    suggestions:   (b.suggestions && b.suggestions.length) ? b.suggestions : DEFAULT_SUGGESTIONS,
  }
}

/**
 * applyBrandChrome — paint the brand onto the document itself.
 *
 * Title and favicon cannot come from the HTML file: one build serves every
 * brand, so they have to be set at runtime once the context has loaded.
 */
export function applyBrandChrome(brand) {
  if (!brand) return
  document.title = brand.productName
  const root = document.documentElement
  // Both names exist because different components read different ones; setting
  // one and not the other is why the blocked-screen button used to render the
  // wrong colour.
  root.style.setProperty('--accent', brand.accent)
  root.style.setProperty('--blue', brand.accent)
  if (brand.faviconUrl) {
    let link = document.querySelector("link[rel='icon']")
    if (!link) {
      link = document.createElement('link')
      link.rel = 'icon'
      document.head.appendChild(link)
    }
    link.href = brand.faviconUrl
  }
}

// Kept for the older call sites that only ever wanted the name.
export function appName(context) {
  return resolveBrand(context).productName
}

export function productTitle(context) {
  return resolveBrand(context).productName
}
