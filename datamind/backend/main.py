from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Any
import os
from dotenv import load_dotenv

from db import get_connection, get_table_schemas
from llm import query_to_sql, generate_report_summary
from analytics import run_forecast, run_anomaly_detection

load_dotenv()

app = FastAPI(title="DataMind AI", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request/Response Models ──────────────────────────────────────────────────

class NLQueryRequest(BaseModel):
    question: str
    llm: str = "gemini"  # "gemini" or "deepseek"

class ForecastRequest(BaseModel):
    table: str
    date_column: str
    value_column: str
    periods: int = 90

class AnomalyRequest(BaseModel):
    table: str
    value_column: str
    date_column: Optional[str] = None

class ReportRequest(BaseModel):
    prompt: str
    llm: str = "gemini"
    tables: Optional[List[str]] = None


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/tables")
def list_tables():
    """Return all table names and their schemas from the MySQL database."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES")
        tables = [row[0] for row in cursor.fetchall()]
        schemas = get_table_schemas(conn, tables)
        conn.close()
        return {"tables": tables, "schemas": schemas}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/query")
def natural_language_query(req: NLQueryRequest):
    """Convert a natural language question to SQL and execute it."""
    try:
        conn = get_connection()
        schemas = get_table_schemas(conn, None)

        # Generate SQL from the question
        sql = query_to_sql(req.question, schemas, req.llm)

        # Execute the SQL
        cursor = conn.cursor()
        cursor.execute(sql)
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        conn.close()

        # Convert to list of dicts
        data = [dict(zip(columns, row)) for row in rows]

        # Serialize any non-JSON-safe types
        import decimal, datetime
        def safe(v):
            if isinstance(v, decimal.Decimal):
                return float(v)
            if isinstance(v, (datetime.date, datetime.datetime)):
                return str(v)
            return v

        data = [{k: safe(v) for k, v in row.items()} for row in data]

        return {
            "sql": sql,
            "columns": columns,
            "data": data,
            "row_count": len(data)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/forecast")
def forecast(req: ForecastRequest):
    """Run Prophet time-series forecasting on a table column."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT `{req.date_column}`, `{req.value_column}` FROM `{req.table}` ORDER BY `{req.date_column}`"
        )
        rows = cursor.fetchall()
        conn.close()

        result = run_forecast(rows, req.periods)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/anomalies")
def anomalies(req: AnomalyRequest):
    """Run Isolation Forest anomaly detection on a table column."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        if req.date_column:
            cursor.execute(
                f"SELECT `{req.date_column}`, `{req.value_column}` FROM `{req.table}` ORDER BY `{req.date_column}`"
            )
        else:
            cursor.execute(f"SELECT `{req.value_column}` FROM `{req.table}`")
        rows = cursor.fetchall()
        conn.close()

        result = run_anomaly_detection(rows, has_date=bool(req.date_column))
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/report")
def generate_report(req: ReportRequest):
    """Ask the LLM to generate an analytics report from your data."""
    try:
        conn = get_connection()
        tables = req.tables or []
        if not tables:
            cursor = conn.cursor()
            cursor.execute("SHOW TABLES")
            tables = [row[0] for row in cursor.fetchall()]

        # Gather sample data from each table (up to 5 rows)
        sample_data = {}
        cursor = conn.cursor()
        for table in tables[:5]:  # Limit to 5 tables
            cursor.execute(f"SELECT * FROM `{table}` LIMIT 5")
            cols = [d[0] for d in cursor.description]
            rows = cursor.fetchall()
            sample_data[table] = {"columns": cols, "rows": [list(r) for r in rows]}
        conn.close()

        summary = generate_report_summary(req.prompt, sample_data, req.llm)
        return {"report": summary}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
