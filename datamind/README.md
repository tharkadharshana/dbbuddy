# DataMind AI — SQL Analytics Platform

Connect your MySQL database and get natural language querying, forecasting, anomaly detection, and AI-generated reports — powered by Gemini or DeepSeek.

---

## What's inside

```
datamind/
├── backend/
│   ├── main.py          # FastAPI app — all API routes
│   ├── db.py            # MySQL connection + schema utilities
│   ├── llm.py           # Gemini & DeepSeek API calls
│   ├── analytics.py     # Prophet forecasting + Isolation Forest
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example     # ← copy this to .env and fill in your keys
├── frontend/
│   ├── src/
│   │   ├── App.jsx
│   │   ├── pages/
│   │   │   ├── QueryPage.jsx     # Natural language → SQL
│   │   │   ├── ForecastPage.jsx  # Prophet time-series
│   │   │   ├── AnomalyPage.jsx   # Isolation Forest
│   │   │   └── ReportsPage.jsx   # AI-generated reports
│   │   ├── components/
│   │   │   ├── UI.jsx            # Shared components
│   │   │   └── Sidebar.jsx
│   │   └── utils/api.js          # All backend API calls
│   ├── Dockerfile
│   ├── nginx.conf
│   └── package.json
└── docker-compose.yml
```

---

## Quick start (recommended: Docker)

### 1. Set up your environment variables

```bash
cd backend
cp .env.example .env
```

Edit `.env` and fill in:
```
DB_HOST=your-mysql-host
DB_PORT=3306
DB_NAME=your_database
DB_USER=your_user
DB_PASSWORD=your_password

GEMINI_API_KEY=your_gemini_key
DEEPSEEK_API_KEY=your_deepseek_key
```

### 2. Run with Docker Compose

```bash
docker-compose up --build
```

- Frontend: http://localhost:5173
- Backend API: http://localhost:8000
- API docs: http://localhost:8000/docs

---

## Manual setup (no Docker)

### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # fill in your keys
uvicorn main:app --reload --port 8000
```

> Note: Installing Prophet can take a few minutes. It requires gcc/g++ on Linux.
> On Mac: `brew install gcc` if you hit compiler errors.
> On Windows: install Visual C++ Build Tools.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173

---

## API reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/tables` | List all tables + schemas |
| POST | `/query` | Natural language → SQL → results |
| POST | `/forecast` | Prophet time-series forecast |
| POST | `/anomalies` | Isolation Forest anomaly detection |
| POST | `/report` | AI-generated analytics report |

Full interactive docs at http://localhost:8000/docs

---

## How each feature works

### Natural Language Query
1. Your question + full MySQL schema are sent to Gemini or DeepSeek
2. The LLM generates a SQL SELECT query
3. The app runs it on your actual DB
4. Results come back as a table + auto-chart

### Forecasting
1. You pick a table, date column, and numeric column
2. The backend fetches the data from MySQL
3. Meta's Prophet runs time-series decomposition
4. Returns historical + N-day forecast with confidence bands

### Anomaly Detection
1. You pick a table and numeric column
2. Isolation Forest (scikit-learn) scans the values
3. Returns anomaly scores, z-scores, and severity (high/medium/low)
4. Visualized as a time-series score chart

### Reports
1. You write a prompt or pick a preset
2. Sample rows from your tables are sent to Gemini/DeepSeek
3. The LLM generates a prose analytics report
4. Reports are saved in-session; use the Copy button to export

---

## Switching LLMs

Use the toggle in the top bar or the per-page toggle. Both Gemini and DeepSeek work for:
- SQL generation
- Report writing

Forecasting and anomaly detection are handled by Python ML libraries (no LLM needed for those).

---

## Security notes

- The SQL generation prompt explicitly forbids DROP, DELETE, INSERT, UPDATE
- Never commit your `.env` file — it's in `.gitignore` by default
- For production, restrict DB user to SELECT-only permissions:
  ```sql
  GRANT SELECT ON your_database.* TO 'datamind'@'%';
  ```

---

## Requirements

- Python 3.10+
- Node.js 18+
- MySQL 5.7+ or 8.0
- Gemini API key (from https://aistudio.google.com)
- DeepSeek API key (from https://platform.deepseek.com)
