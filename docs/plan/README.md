# DataMind AI — Implementation Plan (Claude Code work packages)

This folder is the step-by-step build spec for the report-cache + streaming + insights enhancement. Each `PLAN_0X` file is a self-contained work package to attach to a Claude Code session.

## How to use
1. **Always attach `PLAN_00` first** — it has repo facts, conventions, feature flags, and the fallback rule that every session must follow.
2. Attach the phase file for the session, plus the background doc it names (`06`–`09`, which live one folder up).
3. Build in order: **01 → 02 → 03 → 04 → 05 → 06 → 07 → 08**. (Pull PLAN_08 Step 1 safety patches forward to session 1 if you like — they're tiny and high-value.)
4. Each phase ends with Acceptance + Manual verification; update `PROGRESS.md`.

## The phases
| # | File | What it delivers |
|---|---|---|
| 00 | `PLAN_00_Overview_And_Conventions.md` | Map, repo facts, conventions, flags, DoD |
| 01 | `PLAN_01_Foundations_DataModel_And_ReportClient.md` | Migrations (3 fact + profile tables), `ReportAPIClient`, registry w/ additivity, normalization |
| 02 | `PLAN_02_Profile_And_Subscription_Sync.md` | Tenant profile/shops/cashiers/tier windows |
| 03 | `PLAN_03_Report_Ingestion_And_Cache_Store.md` | Fetch→normalize→upsert facts, coverage, freshness status |
| 04 | `PLAN_04_Jobs_Onboarding_Backfill_Rollover_Retention.md` | Task queue, onboarding, lazy backfill, rollover, re-finalize, retention |
| 05 | `PLAN_05_Answer_Layer_MCP_Tools_And_Aggregation.md` | Router/persona, cache-first report tools, additivity aggregation, wired into query |
| 06 | `PLAN_06_Forecasting_Predictions_And_Business_Insights.md` | Forecast + grounded business-suggestion tools |
| 07 | `PLAN_07_SSE_Streaming.md` | `/v1/query/stream` SSE endpoint + event contract + embed UI |
| 08 | `PLAN_08_Evals_Tracing_Safety_And_CI.md` | Safety patches, AST guard, tracing, parity/eval suite, CI |

## Background (the "why", one folder up)
`06_Industry_Standard_AI_Architecture_And_MCP_Audit.md` · `07_MCP_Enhancement_And_Report_Tools_Guide.md` · `08_Report_API_Tools_Architecture.md` · `09_Report_Cache_Plan_Review.md`

## Non-negotiables (repeated from PLAN_00)
- Everything ships behind feature flags (default OFF) and **falls back to the existing pipeline on any error**.
- Only SalesPlay-embed tenants are affected; Loyverse/BYODB/External-API paths stay untouched.
- Calculated numbers always come from the report API/cache — never recomputed from raw tables (so answers match the POS dashboard).
- Store raw numbers; tag metric additivity; never sum non-additive metrics; open periods go live.
