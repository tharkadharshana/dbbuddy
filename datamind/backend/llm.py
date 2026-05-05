import os
import requests
from typing import Dict, Any
from db import schema_to_text


# ── Gemini ───────────────────────────────────────────────────────────────────

def call_gemini(prompt: str, system: str = "") -> str:
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key or api_key == "your_gemini_api_key_here":
        raise ValueError("GEMINI_API_KEY not set in .env")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    body = {
        "contents": [{"parts": [{"text": f"{system}\n\n{prompt}" if system else prompt}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1024}
    }
    
    # Try up to 2 times on timeout
    for attempt in range(2):
        try:
            resp = requests.post(url, json=body, timeout=60)
            resp.raise_for_status()
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        except requests.exceptions.Timeout:
            if attempt == 0: continue
            raise Exception("Gemini API timed out after 60s. The service might be overloaded.")
        except Exception as e:
            raise e


# ── DeepSeek ─────────────────────────────────────────────────────────────────

def call_deepseek(prompt: str, system: str = "") -> str:
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key or api_key == "your_deepseek_api_key_here":
        raise ValueError("DEEPSEEK_API_KEY not set in .env")

    url = "https://api.deepseek.com/v1/chat/completions"
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {"model": "deepseek-chat", "messages": messages, "temperature": 0.1, "max_tokens": 1024}

    # Try up to 2 times on timeout
    for attempt in range(2):
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=60)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except requests.exceptions.Timeout:
            if attempt == 0: continue
            raise Exception("DeepSeek API timed out after 60s. The service might be overloaded.")
        except Exception as e:
            raise e


# ── Dispatch ─────────────────────────────────────────────────────────────────

def call_llm(prompt: str, system: str = "", llm: str = "gemini") -> str:
    if str(llm).lower() == "deepseek":
        return call_deepseek(prompt, system)
    return call_gemini(prompt, system)


# ── Text-to-SQL ──────────────────────────────────────────────────────────────

def query_to_sql(question: str, schemas: Dict[str, Any], llm: str = "gemini") -> str:
    schema_text = schema_to_text(schemas)
    system = (
        "You are an expert MySQL query writer. "
        "Given a database schema and a question in plain English, write a valid MySQL SELECT query. "
        "Return ONLY the raw SQL query — no markdown, no backticks, no explanation. "
        "Never use DROP, DELETE, INSERT, UPDATE, or any mutating statement."
    )
    prompt = f"Database schema:\n{schema_text}\n\nQuestion: {question}\n\nSQL:"
    raw = call_llm(prompt, system, llm)

    # Strip any accidental markdown fences
    sql = raw.replace("```sql", "").replace("```", "").strip()
    return sql


# ── Report Generation ────────────────────────────────────────────────────────

def generate_report_summary(prompt: str, sample_data: Dict[str, Any], llm: str = "gemini") -> str:
    data_text = ""
    for table, info in sample_data.items():
        cols = ", ".join(info["columns"])
        rows_preview = "\n".join(str(r) for r in info["rows"][:3])
        data_text += f"\nTable `{table}` columns: {cols}\nSample rows:\n{rows_preview}\n"

    system = (
        "You are a senior data analyst. Given sample data from a company's MySQL database, "
        "write a clear, insightful, professional analytics report. "
        "Highlight trends, risks, and actionable recommendations. Use plain text paragraphs."
    )
    full_prompt = f"{prompt}\n\nDatabase sample data:{data_text}"
    return call_llm(full_prompt, system, llm)
