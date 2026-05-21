# DataMind AI

> AI-powered analytics platform for retail & POS businesses. Connect your data sources, ask questions in plain English, and get instant charts, forecasts, anomaly alerts, and professional reports — or embed the entire experience into your own platform via a white-label iframe widget.

---

## Features

### Analytics & AI

- **Natural language querying** — ask anything in plain English; the LLM generates MySQL and returns a chart + table
- **Think Mode** — second LLM pass analyses the SQL results and gives a written insight
- **24+ pre-built analytics templates** — revenue trends, product velocity, RFM segmentation, cohort retention, basket analysis, cashier performance, anomaly detection, and more
- **Time-series forecasting** — Prophet model with confidence bands and seasonality charts
- **Anomaly detection** — Isolation Forest with z-score severity levels (High / Medium / Low)
- **Professional report builder** — multi-section reports with KPI panels, AI narrative, and embedded charts

### Integrations

- **External POS & API connectors** — sync products, customers, receipts, stores, and employees from supported external systems
- **Bring Your Own DB** — connect any MySQL/MariaDB database directly and run NL queries against it
- **Background sync scheduler** — automatic incremental syncs with configurable intervals and error backoff
- **Unified multi-tenant schema** — all synced data lives in one shared table (`integration_records`) with per-user SQL views for zero-change analytics compatibility

### Billing & Subscriptions

- **Three-tier subscription plans** — Starter · Growth · Pro
- **Unified token system** — every operation (LLM calls, row reads, ML features) deducts from a single token balance
- **14-day free trial** on the Starter plan — auto-starts at registration, no credit card required
- **Add-on packs** — purchase extra tokens or row quota that rolls over between periods
- **Usage dashboard** — real-time token meter, per-operation history, plan comparison

### Embeddable iFrame Widget

- **White-label embed** — partners embed DataMind analytics into their own web portal via a `<script>` tag
- **One-shot onboarding** — user enters their API credentials once; DataMind account + connection + trial start silently in the background
- **Domain allowlist** — embed tokens are scoped to specific partner origins
- **Short-lived JWT sessions** — standard Bearer token reused across all API calls within the iframe

### Partner API (Pro plan)

- **Server-to-server API** — partners call DataMind on behalf of their users using an `X-API-Key`
- **5 REST endpoints** — integrations list, manual sync, records access (paginated), analytics template runner, usage stats
- **OpenAPI 3.0 spec** — machine-readable spec at `openapi.yaml`
- **SDK stubs** — Python (stdlib `urllib`, zero dependencies) and JavaScript (ESM, native `fetch`, Node ≥ 18)

### Security

- JWT authentication (HS256, 7-day expiry) with bcrypt password hashing
- Fernet encryption for all stored provider credentials and DB passwords
- LLM-generated SQL is guarded against mutation statements (DROP, DELETE, INSERT, UPDATE, …)
- Schema and sample data are filtered for sensitive columns before LLM transmission
- Rate limiting on all 47 endpoints across 6 configurable tiers
- HTTPS redirect + HSTS + security headers (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`)
- `X-Request-ID` tracing on every response

---

## Subscription Plans

| | Starter | Growth | Pro |
|---|---|---|---|
| **Price** | $5 / mo | $10 / mo | $25 / mo |
| **Tokens / period** | 500 | 1,500 | 10,000 |
| **DB rows** | 2M | 5M | 20M |
| **History** | 30 days | 90 days | 365 days |
| **Forecasting & Anomaly** | — | ✓ | ✓ |
| **Partner API** | — | — | ✓ |
| **Free trial** | 14 days | — | — |

---

## Project Structure

```text
datamind/
├── backend/
│   ├── main.py                  # FastAPI app — all 47 user-facing routes (/v1/*)
│   ├── auth.py                  # JWT auth, bcrypt, user CRUD
│   ├── billing.py               # Subscription plans, token metering, trial enforcement
│   ├── integrations.py          # Integration lifecycle, sync scheduler, SQL views
│   ├── embed.py                 # Embed partner router (/embed/*)
│   ├── v1.py                    # Partner API router (/v1/partner/*)
│   ├── limiter.py               # Rate limiting — IP-based + API-key-based instances
│   ├── llm.py                   # Gemini + DeepSeek, NL→SQL, sensitive schema filtering
│   ├── analytics.py             # Prophet, Isolation Forest, RFM, Cohort, Basket
│   ├── db.py                    # User DB introspection (schema, foreign keys, samples)
│   ├── pool.py                  # MySQL connection pool for DataMind's internal DB
│   ├── cache.py                 # Per-user analytics template cache
│   ├── schema_builder.py        # LLM-assisted SQL template generation
│   ├── logger.py                # Structured logging (pretty / JSON format)
│   ├── providers/
│   │   ├── base.py              # Abstract provider interface
│   │   ├── upsert.py            # Shared upsert_record() + lookup_map() helpers
│   │   ├── salesplay/
│   │   │   ├── provider.py      # SalesPlay credential validation
│   │   │   ├── sync.py          # SalesPlay full + delta sync
│   │   │   └── analytics.py     # SalesPlay-specific analytics SQL
│   │   └── loyverse/
│   │       ├── provider.py      # Loyverse credential validation
│   │       ├── sync.py          # Loyverse full + delta sync
│   │       └── analytics.py     # Loyverse-specific analytics SQL
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── App.jsx              # Root — auth guard, routing
│   │   ├── pages/
│   │   │   ├── AuthPage.jsx     # Login + Register
│   │   │   ├── ChatPage.jsx     # NL→SQL query interface + Think Mode
│   │   │   ├── DiscoverPage.jsx # Analytics Hub (template catalogue)
│   │   │   ├── ForecastPage.jsx # Time-series forecasting
│   │   │   ├── AnomalyPage.jsx  # Anomaly detection
│   │   │   ├── ReportsPage.jsx  # Report builder
│   │   │   ├── BillingPage.jsx  # Plans, usage meter, add-ons
│   │   │   ├── UsagePage.jsx    # Per-operation usage history
│   │   │   ├── ConnectionsPage.jsx  # Integration management
│   │   │   ├── SettingsPage.jsx     # Account, LLM keys, theme
│   │   │   ├── OnboardingWizard.jsx # First-time setup flow
│   │   │   └── DocsPage.jsx     # In-app documentation
│   │   ├── embed/
│   │   │   ├── EmbedApp.jsx     # iFrame root — JWT storage, postMessage
│   │   │   ├── EmbedOnboarding.jsx  # Step-by-step embed onboarding wizard
│   │   │   └── EmbedChat.jsx    # Compact NL query UI for the iframe
│   │   └── components/
│   │       ├── Sidebar.jsx
│   │       ├── UI.jsx           # Shared design system
│   │       └── UsageLimitBanner.jsx
│   ├── Dockerfile
│   ├── nginx.conf
│   └── package.json
├── sdk/
│   ├── python/
│   │   ├── datamind.py          # Python SDK (stdlib urllib, zero deps)
│   │   └── setup.py
│   └── js/
│       ├── datamind.js          # JavaScript SDK (ESM, native fetch, Node ≥18)
│       └── package.json
├── docs/
│   ├── CHANGELOG.md             # Full engineering changelog
│   ├── DATABASE_SCHEMA.md       # Complete schema reference
│   ├── token-system.md          # Token billing design
│   ├── unified-db-schema-migration.md
│   ├── security-hardening-plan.md
│   └── deployment/              # Red Hat / nginx deployment guides
├── openapi.yaml                 # OpenAPI 3.0.3 spec for the Partner API
└── docker-compose.yml
```

---

## Quick Start

### Option 1 — Docker (Recommended)

```bash
cd datamind

# 1. Configure environment
cp backend/.env.example backend/.env
# At minimum, set:
#   SECRET_KEY    — a strong random string (see below)
#   DATAMIND_DB_* — your MySQL credentials

# Generate a secret key:
python -c "import secrets; print(secrets.token_urlsafe(48))"

# 2. Start everything
docker-compose up --build
```

| Service | URL |
|---------|-----|
| Frontend | <http://localhost:5173> |
| Backend API | <http://localhost:8000> |
| Interactive API docs | <http://localhost:8000/docs> |

### Option 2 — Manual

**Backend:**

```bash
cd datamind/backend
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # Fill in SECRET_KEY and DATAMIND_DB_*
uvicorn main:app --reload --port 8000
```

**Frontend:**

```bash
cd datamind/frontend
npm install
npm run dev                   # http://localhost:5173
```

---

## Environment Variables

Copy `backend/.env.example` to `backend/.env` and fill in your values. Key variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_KEY` | **Yes** | JWT signing secret — generate with `secrets.token_urlsafe(48)` |
| `ENCRYPTION_KEY` | Recommended | Fernet key for encrypting stored credentials. Falls back to `SECRET_KEY` if unset. |
| `DATAMIND_DB_HOST` | **Yes** | MySQL host for DataMind's own database |
| `DATAMIND_DB_NAME` | **Yes** | DataMind's internal database name |
| `DATAMIND_DB_USER` | **Yes** | DataMind's database user |
| `DATAMIND_DB_PASSWORD` | **Yes** | DataMind's database password |
| `GEMINI_API_KEY` | Recommended | Server-level fallback LLM key (used for embed users) |
| `DEEPSEEK_API_KEY` | Optional | Alternative LLM provider |
| `EMBED_ALLOWED_ORIGINS` | Production | Comma-separated origins allowed to host the iframe widget |
| `FORCE_HTTPS` | Production | `true` to enable HTTPS redirect + HSTS |
| `LOG_LEVEL` | Optional | `DEBUG` / `INFO` / `WARNING` / `ERROR` (default: `INFO`) |
| `LOG_FORMAT` | Optional | `pretty` (coloured) or `json` (one line per event) |
| `DB_POOL_SIZE` | Optional | Internal DB connection pool size per worker (default: `20`) |

**Rate limit overrides** (all optional — defaults shown):

| Variable | Default | Applies to |
|----------|---------|-----------|
| `RATE_LIMIT_AUTH` | `5/minute` | `POST /v1/auth/register` |
| `RATE_LIMIT_AUTH_LOGIN` | `10/minute` | `POST /v1/auth/login` |
| `RATE_LIMIT_COMPUTE` | `10/minute` | `/v1/query`, analytics, forecast, anomaly, report |
| `RATE_LIMIT_READ` | `60/minute` | All GET endpoints |
| `RATE_LIMIT_WRITE` | `30/minute` | Settings, billing, sync mutations |
| `RATE_LIMIT_V1` | `30/minute` | All `/v1/partner/*` endpoints (per API key) |

---

## First-Time Setup

1. Open <http://localhost:5173>
2. **Register** an account — a 14-day Starter trial starts automatically
3. **Settings → LLM API Keys** — add your Gemini and/or DeepSeek key
4. **Connections → Add Integration** — enter your external API credentials to connect a supported data source
5. DataMind validates the credentials and starts an initial background sync
6. **Analytics Hub** — once the sync completes, all 24+ templates are available instantly

> Users can also connect their own MySQL database under **Settings → Database Connections** and run natural language queries directly against it.

---

## API Overview

All user endpoints are under `/v1/`. Partner API endpoints are under `/v1/partner/`. The embed bootstrap endpoints are under `/embed/` (unversioned — live in production iframes).

```text
GET    /health                           # Load balancer health check (no auth)

POST   /v1/auth/register                 # Create account + start free trial
POST   /v1/auth/login                    # Sign in, receive JWT
GET    /v1/auth/me                       # Current user info
DELETE /v1/auth/account                  # Delete account

GET    /v1/settings                      # User settings (API keys masked)
PATCH  /v1/settings                      # Update LLM keys, theme, default LLM
POST   /v1/settings/db                   # Add DB connection
PUT    /v1/settings/db/{i}               # Update DB connection
DELETE /v1/settings/db/{i}               # Remove DB connection
POST   /v1/settings/db/{i}/activate      # Switch active DB
POST   /v1/settings/db/test              # Test a connection (not saved)

GET    /v1/tables                        # Schema introspection for active DB
GET    /v1/discover                      # LLM analytics catalogue for active DB
POST   /v1/query                         # NL → SQL → results (+ Think Mode)
POST   /v1/analytics/run                 # Run a pre-built template by ID
GET    /v1/forecast/auto                 # Auto revenue forecast
POST   /v1/forecast                      # Manual forecast (any table + columns)
GET    /v1/anomalies/auto                # Auto anomaly scan
POST   /v1/anomalies                     # Manual anomaly scan
POST   /v1/report                        # Generate multi-section report

GET    /v1/integrations                  # List connected integrations
POST   /v1/integrations/{id}/sync        # Trigger manual sync
DELETE /v1/integrations/{id}             # Disconnect integration
GET    /v1/integrations/{id}/progress    # Live sync progress

GET    /v1/billing/plans                 # Available subscription plans
GET    /v1/billing/subscription          # Current subscription + usage
POST   /v1/billing/subscribe             # Upgrade or downgrade plan
POST   /v1/billing/addon                 # Purchase add-on pack
GET    /v1/billing/usage                 # Token usage summary for current period
GET    /v1/billing/history               # Per-operation usage log

GET    /v1/cache/status                  # Analytics cache status
GET    /v1/cache/progress                # Live cache build log
POST   /v1/cache/rebuild                 # Force a full cache rebuild
```

Interactive docs (Swagger UI): **<http://localhost:8000/docs>**

---

## Partner API

The Partner API lets you call DataMind server-to-server on behalf of your users. Requires a Pro plan and an `X-API-Key` header.

```text
GET  /v1/partner/integrations              # User's connected integrations
POST /v1/partner/sync/{provider}           # Trigger a sync
GET  /v1/partner/records/{provider}/{type} # Paginated synced records
GET  /v1/partner/analytics/{template_id}   # Run an analytics template
GET  /v1/partner/usage                     # User's token balance and plan
```

The full machine-readable spec is at [`openapi.yaml`](openapi.yaml).

**SDK quick-start:**

```python
# Python
from datamind import DataMindClient
client = DataMindClient(api_key="your_key")
result = client.analytics("customer_rfm", user_email="user@example.com")
```

```javascript
// JavaScript (ESM)
import { DataMindClient } from './datamind.js';
const client = new DataMindClient({ apiKey: 'your_key' });
const result = await client.analytics('customer_rfm', { userEmail: 'user@example.com' });
```

---

## Embedding the Widget

Add one `<script>` tag to your page. DataMind handles account creation, provider connection, and the full analytics UI inside the iframe.

```html
<script
  src="https://your-datamind-instance.com/embed/bundle.js"
  data-partner-key="YOUR_PARTNER_KEY"
  data-theme="light"
></script>
```

The widget detects first-time users automatically and shows a three-step onboarding wizard (enter API credentials → set up account → done). Returning users go straight to the analytics chat.

Partner keys and allowed origins are managed in the `embed_partners` table — contact the DataMind admin to register a new partner.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.10+, FastAPI, Uvicorn |
| Frontend | React 18, Vite, Recharts |
| Database | MySQL / MariaDB 10.4+ |
| ML / Forecasting | Prophet, scikit-learn (IsolationForest), pandas, NumPy |
| LLM providers | Google Gemini (primary), DeepSeek (fallback) |
| Auth | JWT (python-jose), bcrypt (passlib) |
| Encryption | Fernet (cryptography library) |
| Rate limiting | slowapi |
| Containerisation | Docker, docker-compose |

---

## Requirements

- Python 3.10+
- Node.js 18+
- MySQL 8.0+ or MariaDB 10.4+
- Gemini API key → <https://aistudio.google.com/app/apikey>
- DeepSeek API key (optional) → <https://platform.deepseek.com/api_keys>

---

## Security Notes

- **Change `SECRET_KEY` before deploying** — the default value is in the source code and makes all JWTs forgeable
- **Set `ENCRYPTION_KEY`** separately from `SECRET_KEY` in production — used to encrypt stored integration credentials and DB passwords
- All LLM-generated SQL is checked against a mutation guard before execution — `DROP`, `DELETE`, `INSERT`, `UPDATE`, `ALTER`, and other write statements are blocked
- Schema columns matching sensitive patterns (passwords, API keys, SSNs, card numbers) are stripped before any data is sent to an LLM
- For production BYODB access, use a read-only MySQL user:

  ```sql
  GRANT SELECT ON your_db.* TO 'datamind'@'%';
  FLUSH PRIVILEGES;
  ```

- Set `FORCE_HTTPS=true` behind a TLS-terminating reverse proxy to enable HTTPS redirect and HSTS

---

## Documentation

| Document | Description |
|----------|-------------|
| [`docs/CHANGELOG.md`](docs/CHANGELOG.md) | Full engineering changelog — every feature, logic detail, and design decision |
| [`docs/DATABASE_SCHEMA.md`](docs/DATABASE_SCHEMA.md) | Complete schema reference — every table and column explained |
| [`docs/token-system.md`](docs/token-system.md) | Token billing formula and known gaps |
| [`docs/unified-db-schema-migration.md`](docs/unified-db-schema-migration.md) | M2 schema migration notes |
| [`docs/security-hardening-plan.md`](docs/security-hardening-plan.md) | SEC-01 through SEC-14 implementation notes |
| [`openapi.yaml`](openapi.yaml) | OpenAPI 3.0.3 spec for the Partner API |

---

## License

Copyright © 2026 Tharka Dharshana Karunanayake. All rights reserved.

This software and its source code are proprietary and confidential. Unauthorised copying, distribution, modification, or deployment of this software, via any medium, is strictly prohibited.

For licensing enquiries: tharkadharshana@gmail.com
