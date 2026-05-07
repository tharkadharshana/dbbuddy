# DataMind AI v2 — SQL Analytics Platform

A full-stack AI analytics platform. Connect your MySQL database, add your LLM API keys, and get natural language querying, smart analytics discovery, time-series forecasting, anomaly detection, and professional AI-generated reports — all from a beautiful dark-mode UI.

---

## What's New in v2

| Feature | Description |
|---|---|
| **Login / Register** | Full JWT auth. Each user has their own account. |
| **API Keys in UI** | Add Gemini & DeepSeek keys from Settings — no .env needed. |
| **DB Manager in UI** | Add, edit, test, delete and switch between MySQL connections from Settings. |
| **Smart Analytics Hub** | LLM reads your schema and discovers all possible analytics automatically. |
| **24+ Analytics Templates** | Revenue, Products, Customers, Employees, Payments, Growth, Inventory. |
| **Professional Reports** | Multi-section reports with embedded charts, KPI panels and AI narrative. |
| **RFM Segmentation** | Champions, Loyal, At-Risk, Lost — full customer segmentation. |
| **Cohort Analysis** | Retention heatmap by acquisition month. |
| **Basket Analysis** | Product co-purchase patterns and confidence scores. |
| **Per-user data isolation** | Every user's settings, DB configs and API keys are stored separately. |

---

## Project Structure

```
datamind-v2/
├── backend/
│   ├── main.py          # FastAPI — all API routes (auth + analytics)
│   ├── auth.py          # JWT auth + TinyDB user store
│   ├── db.py            # MySQL connection (env or per-user config)
│   ├── llm.py           # Gemini + DeepSeek, Text-to-SQL, discovery
│   ├── analytics.py     # Prophet, Isolation Forest, RFM, Cohort, Basket…
│   ├── data/            # Auto-created — stores users.json (TinyDB)
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── App.jsx              # Root — auth guard, layout, routing
│   │   ├── pages/
│   │   │   ├── AuthPage.jsx     # Login + Register
│   │   │   ├── DiscoverPage.jsx # Smart Analytics Hub
│   │   │   ├── QueryPage.jsx    # Natural Language → SQL
│   │   │   ├── ForecastPage.jsx # Prophet time-series
│   │   │   ├── AnomalyPage.jsx  # Isolation Forest
│   │   │   ├── ReportsPage.jsx  # Professional report builder
│   │   │   └── SettingsPage.jsx # API keys + DB connections
│   │   ├── components/
│   │   │   ├── UI.jsx           # Shared design system
│   │   │   └── Sidebar.jsx      # Navigation
│   │   └── utils/api.js         # All API calls (with JWT)
│   ├── Dockerfile
│   ├── nginx.conf
│   └── package.json
└── docker-compose.yml
```

---

## Quick Start

### Option 1 — Docker (Recommended)

```bash
# 1. Copy and configure env
cd backend
cp .env.example .env
# Edit SECRET_KEY — change to a long random string
# DB_HOST etc. are optional — users can add DBs from the UI

# 2. Run
cd ..
docker-compose up --build

# Frontend → http://localhost:5173
# Backend API → http://localhost:8000
# API Docs → http://localhost:8000/docs
```

### Option 2 — Manual

**Backend:**
```bash
cd backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # Edit SECRET_KEY at minimum
uvicorn main:app --reload --port 8000
```

**Frontend:**
```bash
cd frontend
npm install
npm run dev
# Opens http://localhost:5173
```

---

## First-Time Setup Flow

1. **Open** http://localhost:5173
2. **Register** an account (stored locally in `backend/data/users.json`)
3. **Settings → LLM API Keys** — add your Gemini and/or DeepSeek key
4. **Settings → Database Connections** — click "Add Database Connection"
   - Fill in host, port, database, user, password
   - Click **⚡ Test Connection** to verify
   - Click **Save Connection** then **Use** to activate it
5. **Go to Analytics Hub** — click any card to run an analysis instantly

---

## Pages

### 🔐 Login / Register
- Email + password auth with JWT tokens (7-day expiry)
- Passwords hashed with bcrypt
- Animated dark-mode UI

### ⬡ Analytics Hub (Discover)
- LLM reads your full schema + foreign keys + sample data
- Returns a catalogue of 20-25 analytics tailored to your actual tables
- Click any card → results appear instantly (chart + table + KPI cards)
- Filter by category: Revenue / Products / Customers / Employees / Payments / Growth / Inventory

### ⌕ Natural Language Query
- Ask anything in plain English
- LLM generates MySQL with JOIN support across multiple tables
- Auto-detects chart type (bar / line) from result shape
- Query history sidebar

### 📈 Forecasting
- **Auto mode**: forecasts daily revenue from your invoices table
- **Manual mode**: pick any table, date column, value column
- Prophet model with yearly + weekly seasonality
- Confidence bands, seasonality chart, predicted growth %

### ⚠ Anomaly Detection
- **Auto mode**: scans daily revenue
- **Manual mode**: any table + numeric column
- Isolation Forest + z-score severity (High / Medium / Low)
- Anomaly score time-series chart

### 📋 Report Builder
- Select from 15 data sections across Revenue, Products, Customers, People, Finance
- 4 quick presets (Executive Summary, Revenue Deep Dive, Customer Report, Operations)
- 3 formats: Detailed / Executive / Quick Bullets
- Output: KPI panel + AI narrative + embedded mini-charts per section
- Copy to clipboard

### ⚙ Settings
- **Account**: profile info + sign out
- **LLM API Keys**: Gemini + DeepSeek, with direct links to get keys; choose default LLM
- **Database Connections**: add/edit/delete MySQL connections; test before saving; switch active connection

---

## Analytics Templates (24 total)

**Revenue**
- Monthly Revenue Trend · Category Breakdown · Location Performance · Hourly Pattern · Last 7 Days · Discount Analysis · Tax Analysis

**Products**
- Top 20 Products · Slow Movers · Margin by Category · Product Velocity · Basket Co-purchase Analysis

**Customers**
- Top Customers by LTV · RFM Segmentation · Cohort Retention Heatmap · Monthly Retention Rate · Loyalty Tier Performance

**Employees**
- Cashier Performance Ranking (sales, avg ticket, discount rate, sales/day)

**Payments**
- Payment Method Breakdown · Credit & Collections by Month

**Growth**
- Month-over-Month Growth Metrics · Location KPI Comparison · Revenue by Order Type & Channel

**Inventory**
- Inventory Movement Log

---

## API Reference

All endpoints require `Authorization: Bearer <token>` except `/auth/*`.

| Method | Path | Description |
|---|---|---|
| POST | `/auth/register` | Create account |
| POST | `/auth/login` | Sign in, get token |
| GET | `/auth/me` | Current user |
| GET | `/settings` | Get user settings (DB configs, API keys masked) |
| PATCH | `/settings` | Update API keys / default LLM |
| POST | `/settings/db` | Add DB connection |
| PUT | `/settings/db/{i}` | Update DB connection |
| DELETE | `/settings/db/{i}` | Delete DB connection |
| POST | `/settings/db/{i}/activate` | Switch active DB |
| POST | `/settings/db/test` | Test a connection (not saved) |
| GET | `/tables` | List tables + schemas + foreign keys |
| GET | `/discover` | LLM-generated analytics catalogue |
| POST | `/query` | NL → SQL → results |
| POST | `/analytics/run` | Run a template by ID |
| GET | `/forecast/auto` | Auto revenue forecast |
| POST | `/forecast` | Manual forecast |
| GET | `/anomalies/auto` | Auto revenue anomaly scan |
| POST | `/anomalies` | Manual anomaly scan |
| POST | `/report` | Generate professional report |

Full interactive docs: http://localhost:8000/docs

---

## Security Notes

- **Change `SECRET_KEY`** in `.env` before deploying — the default is public
- User data (accounts, settings) stored in `backend/data/users.json` — back this up
- API keys are stored per-user in the JSON file — consider encrypting at rest in production
- SQL generation prompt forbids mutating statements (DROP, DELETE, INSERT, UPDATE)
- For production MySQL access, use a read-only user:
  ```sql
  GRANT SELECT ON your_db.* TO 'datamind'@'%';
  FLUSH PRIVILEGES;
  ```
- The `docker-compose.yml` mounts a named volume `backend_data` so user data survives container restarts

---

## Requirements

- Python 3.10+
- Node.js 18+
- MySQL 5.7+ or 8.0
- Gemini API key → https://aistudio.google.com/app/apikey
- DeepSeek API key → https://platform.deepseek.com/api_keys


---

## v3 — Fully Dynamic + Cached Architecture

### The problem with v2
All analytics SQL was hardcoded for a specific POS database schema (`invoices`, `products`, `customers`…). Any other database would break.

### How v3 fixes it

**One-time LLM build (when you add a DB):**
1. DataMind reads your full schema — every table, column, type, foreign key
2. LLM generates custom MySQL SQL for all 21 analytics templates based on YOUR column names
3. LLM detects the best date/value columns for auto-forecast and auto-anomaly
4. LLM writes a personalised analytics catalogue describing what's possible
5. All of this is saved to `backend/data/cache/{user}_{db}.json`

**Every visit after that:**
- Analytics Hub loads instantly — reads catalogue from cache file
- Each template runs the pre-generated SQL directly on your DB
- Zero LLM tokens used for any analytics

**Three-tier fallback:**
```
Cache SQL → Python analytics (RFM, Cohort, Basket…) → Hardcoded POS SQL
```

### New files
- `backend/cache.py` — read/write/invalidate per-user, per-DB JSON cache files
- `backend/schema_builder.py` — LLM prompt builder + SQL validator + one-time build engine

### New API endpoints
| Method | Path | Description |
|---|---|---|
| GET | `/cache/status` | Is cache built? How many templates? When? |
| GET | `/cache/progress` | Live build log (poll while building) |
| POST | `/cache/rebuild` | Force a full rebuild for the active DB |

