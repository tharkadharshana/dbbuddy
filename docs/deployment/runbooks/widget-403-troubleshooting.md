# Widget returns 403 — read this first

**The single most common failure when onboarding a brand.** Hit twice in two days on dev
(Salesplay 2026-08-25, Sellmo 2026-08-26), same root cause both times.

---

## Symptom

The widget renders — correct brand name, correct logo — but every API call fails:

```
GET  /api/embed/partner/sync-status?partner_key=...   403
POST /api/embed/partner/profile                        403
GET  /api/embed/partner/subscription/info?...          403
```

In the widget UI this shows as: *"I couldn't finish working that out just now. Please ask
me again in a moment."* — an unhelpful message that looks like an LLM problem but is not.

`sync-status` polls on a loop, so the console fills with dozens of identical 403 lines.

---

## Cause

`_require_allowed_origin()` (`datamind/backend/embed.py:649`) rejected the request. The
browser's `Origin` header was not found in that brand's `embed_partners.allowed_origins`.

### The part that catches everyone

**The `Origin` on these requests is the IFRAME's own host — not the partner page the
iframe sits inside.**

Concretely, for the Sellmo setup:

| | Value |
|---|---|
| Partner's backoffice page (the parent) | `https://partnerpredev1.nvision.lk` |
| Widget iframe `src` host | `https://aidev.salesplay.com` |
| **`Origin` sent on the API calls** | **`https://aidev.salesplay.com`** |

The iframe is a separate browsing context with its own origin. Requests it makes carry
*that* origin. The parent page's domain never appears.

So `allowed_origins` must contain **the host serving the widget bundle**, in addition to
the partner's backoffice domains. Listing only the partner's domains is not enough, and is
exactly the mistake made both times.

---

## Fix

1. Find the widget's host — it is the hostname in the `iframe.src` of the embed snippet
   (e.g. `https://aidev.salesplay.com/src/embed/embed.html?pk=...`).

2. Confirm what the brand currently allows:

```sql
SELECT partner_key, partner_name, allowed_origins
FROM embed_partners
WHERE partner_key = '<THE_PK>';
```

3. Set the full list — partner backoffice origins **plus** the widget host:

```sql
UPDATE embed_partners
SET allowed_origins = 'https://partner-backoffice.example.com,https://WIDGET-HOST'
WHERE partner_key = '<THE_PK>';
```

Takes effect within `PARTNER_CACHE_TTL` (default 60 s). **No restart.** Reload the page
after a minute.

### Real example (Sellmo, applied 2026-08-26 12:11)

```sql
UPDATE embed_partners
SET allowed_origins = 'https://partnerpredev1.nvision.lk,https://predev1backoffice.nvision.lk,https://aidev.salesplay.com'
WHERE partner_key = 'sl_live_1htRGOQIPUePJ99wHpfC1dvn';
```

The third entry — the widget host — is the one that was missing.

---

## Rules for `allowed_origins`

- **Scheme + host (+ port). Never a path.** `https://example.com` — not
  `https://example.com/app`.
- **Comma-separated, no spaces.** A stray space makes that entry never match (the code
  strips, but don't rely on it).
- **Include every origin that will actually serve or embed the widget** — production,
  staging, regional hosts, and the widget's own host.
- **Empty means unrestricted.** An empty string disables the check entirely. Acceptable on
  a throwaway dev row; never in production.
- **Substitute placeholders.** On 2026-08-26 a literal `https://THEIR-ORIGIN` was found in
  Sellmo's list — copied from an example without replacing it. It never matches, so it is
  silently useless. Check for leftovers:

```sql
SELECT partner_key, allowed_origins FROM embed_partners
WHERE allowed_origins LIKE '%THEIR-ORIGIN%'
   OR allowed_origins LIKE '%YOUR-%'
   OR allowed_origins LIKE '%example.com%';
```

---

## Not to be confused with `branding.app_domains`

Two different allowlists on the same row. Mixing them up produces confusingly different
failures:

| Field | Contains | Checked against | Gates | Wrong value causes |
|---|---|---|---|---|
| `allowed_origins` | partner backoffice hosts **+ the widget host** | browser `Origin`/`Referer` | whether the iframe may call the API | **403 on every embed call** (this document) |
| `branding.app_domains` | our own frontend hostnames for this brand | HTTP `Host` header | which brand a direct login belongs to | **400 "This address is not configured for any brand"** on `/auth/login` and `/auth/register` |

A brand can need entries in both, and they are usually different values.

---

## Other 403s that are NOT this

Before editing `allowed_origins`, rule these out:

| Source | Message | Meaning |
|---|---|---|
| `embed.py:689` (`_salesplay_guard`) | "This endpoint is not available for this partner." | The `pk` belongs to a brand whose `provider_id` is not `salesplay` |
| `embed.py:1057` (`_reject_if_subscription_free`) | "Subscriptions are free right now…" | Brand is in free mode; payment endpoints deliberately refuse. Expected, not a bug. |

Also note: a **401** on these endpoints is an auth problem (`Depends(current_user)`), not an
origin problem. A **502** usually means the `aat` was issued by a different provider
instance than the brand's `api_config` points at — see
`2026-08-26-0946-sellmo-brand-onboarding-queries.md`.

---

## Checklist when onboarding any new brand

- [ ] `allowed_origins` includes every partner backoffice origin
- [ ] `allowed_origins` includes **the widget host** (the `iframe.src` hostname)
- [ ] No placeholder text left in the value
- [ ] `branding.app_domains` set if the brand has a standalone app (separate from the above)
- [ ] Load the widget from the partner's real page and confirm no 403s in the console
