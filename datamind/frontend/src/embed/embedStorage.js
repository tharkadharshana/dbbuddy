/**
 * embedStorage.js — per-brand browser storage for the widget.
 *
 * localStorage is scoped to the origin, not to the partner key. Two brands
 * served from one origin therefore shared a single `dm_embed_token`, and
 * whichever widget loaded last owned it. Confirmed the hard way in local
 * testing: a chat opened under one brand ran every query as the other brand's
 * account.
 *
 * Per-brand domains avoid it, but nothing in the design requires them — the
 * brand comes from `?pk=`, so one domain may legitimately serve several. So
 * the key carries the partner key instead of relying on the deployment shape.
 *
 * The partner key is read straight from the URL rather than plumbed through,
 * because the axios interceptor in embedApi has no component context to read
 * it from and it is the same value the whole session.
 */

function partnerKey() {
  try {
    return new URLSearchParams(window.location.search).get('pk') || ''
  } catch {
    return ''
  }
}

const SUFFIX = partnerKey() ? '_' + partnerKey() : ''

/** The origin-unique name for a logical key. */
export function storageKey(name) {
  return name + SUFFIX
}

// Every accessor swallows its error: Safari private mode and "block site data"
// throw on access rather than returning null, and a widget that cannot
// remember a token should still run, just re-onboarding each time.
export function getItem(name) {
  try { return localStorage.getItem(storageKey(name)) } catch { return null }
}

export function setItem(name, value) {
  try { localStorage.setItem(storageKey(name), value) } catch { /* no-op */ }
}

export function removeItem(name) {
  try { localStorage.removeItem(storageKey(name)) } catch { /* no-op */ }
}
