# DataMind AI — SQL Analytics Platform

Connect your MySQL database and get natural language querying, forecasting, anomaly detection, and AI-generated reports — powered by Gemini or DeepSeek.

---

## 🚀 Built-in POS Data Model
This system comes with a pre-designed, professional **Point of Sale (POS)** schema. It is specifically optimized for multi-location businesses and time-series forecasting.

### Key Tables & Features:
- **`invoices`**: Full transaction header with 38+ fields (KOT numbers, Payment methods, Loyalty points, Multi-location keys).
- **`invoice_items`**: Itemized lines with prices, costs, discounts, and tax values.
- **`inventory_logs`**: Full audit trail of stock changes (Sales, Restocks, Damages) for accurate inventory forecasting.
- **`customers` & `products`**: Detailed profiles with loyalty tracking and category management.
- **`locations` & `employees`**: Support for multiple branches (e.g., Colombo, Kandy, Galle) with staff-level performance tracking.

---

## 🧠 The Intelligence Stack
We use a hybrid approach to provide the most accurate business insights:

### 1. Large Language Models (LLM) — Language Brain
- **Engines**: Google Gemini 1.5 Flash & DeepSeek.
- **Text-to-SQL**: Converts plain English (e.g., *"Show me the top 5 selling products in Kandy last week"*) into valid MySQL queries.
- **Automated Insights**: Generates professional analytics reports and business recommendations from your data.

### 2. Traditional Machine Learning (AI) — Math Brain
- **Forecasting (Meta Prophet)**: Predicts future revenue, transaction volume, and inventory levels with confidence bands.
- **Anomaly Detection (Isolation Forest)**: Automatically flags suspicious data points, fraud, or system errors in your financial records.

---

## 📂 Project Structure

```
datamind/
├── backend/
│   ├── main.py          # FastAPI app — all API routes
│   ├── db.py            # MySQL connection + schema utilities
│   ├── llm.py           # Gemini & DeepSeek API calls
│   ├── analytics.py     # Prophet forecasting + Isolation Forest
│   ├── schema.sql       # Professional POS Database Schema
│   ├── seed_data.py     # High-fidelity realistic data generator
│   ├── requirements.txt
│   └── .env.example     # ← copy this to .env and fill in your keys
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── QueryPage.jsx     # Natural language → SQL
│   │   │   ├── ForecastPage.jsx  # Prophet time-series
│   │   │   ├── AnomalyPage.jsx   # Isolation Forest
│   │   │   └── ReportsPage.jsx   # AI-generated reports
└── docker-compose.yml
```

---

## 🛠 Setup & Data Generation

### 1. Configure Environment
```bash
cd backend
cp .env.example .env
```
Fill in your `GEMINI_API_KEY` and MySQL credentials.

### 2. Seed Realistic POS Data
To replace garbage data with 2,000+ realistic transactions across 5 locations:
```bash
python seed_data.py
```
*Note: This will reset `datamind_db` and apply the professional POS schema.*

### 3. Run the Platform
**Using Docker (Recommended):**
```bash
docker-compose up --build
```
**Manual Setup:**
```bash
# Backend
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload

# Frontend
cd frontend && npm install && npm run dev
```

---

## 📊 Prediction Scenarios
The system is ready for the following realistic predictions out of the box:
1. **Revenue Forecasting**: Predict daily/weekly income for specific locations.
2. **Product Demand**: Identify which items will be out of stock soon using `inventory_logs`.
3. **Loyalty Growth**: Track and predict customer sign-up rates and point issuance.

---

## 🛡 Security Notes
- **Read-Only Focus**: The LLM prompt explicitly forbids `DROP`, `DELETE`, `INSERT`, and `UPDATE`.
- **Z-Score Severity**: Anomalies are ranked by severity (Low, Medium, High) to reduce false alarms.
- **Audit Trails**: Every inventory change is logged with a timestamp for forensic tracking.

---

## Requirements
- Python 3.10+
- Node.js 18+
- MySQL 8.0+
- Gemini API key (from [AI Studio](https://aistudio.google.com))
