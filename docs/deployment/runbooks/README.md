# Production runbooks

Step-by-step procedures for changes to the live SalesPlay AI deployment.
Each one carries its own pre-check, the change, a post-check and a rollback.

Read the whole runbook before running any of it. Every one assumes a backup
of the table it touches.

| Runbook | What it does | Risk |
|---|---|---|
| [enable-sellmo-brand.md](enable-sellmo-brand.md) | Registers Sellmo as a second brand on the existing deployment | Low — adds one row |
| [go-live-salesplay-billing.md](go-live-salesplay-billing.md) | Turns off free mode for Salesplay so trials convert to paid | **High — billing state** |
| [patch-v1.201.0-standalone-branding.md](patch-v1.201.0-standalone-branding.md) | Deploys the standalone-app branding fix and light login | Medium — code deploy |
| [widget-403-troubleshooting.md](widget-403-troubleshooting.md) | Diagnoses a widget refusing to load with 403 | Read-only |

## Conventions

**Brands are rows, not builds.** One deployment serves every brand. Logos,
names, colours, the BETA badge and free mode all come from
`embed_partners.branding`, resolved per request — by `?pk=` in the widget and
by the `Host` header in the standalone app. A change that names one brand in
code will surface in another brand's UI.

**Free mode is per brand.** `branding.subscription_free` overrides the
process-wide `SUBSCRIPTION_FREE` env var. That is what lets one brand launch
free while another charges on the same server. Never widen a `WHERE
partner_name = ...` clause on `embed_partners` — it charges every brand.

**`is_active = 0` does not migrate anyone.** Subscription rows join on
`plan_id`, so deactivating a plan leaves its merchants on its old limits.
Migrate them explicitly before relying on a plan being retired.

**Verified numbers go stale.** Counts quoted in these runbooks were read from
production on the date noted. Re-run the pre-check rather than trusting them.
