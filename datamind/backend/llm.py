"""
LLM module — Gemini + DeepSeek.

KEY FIX: Every function that calls an LLM now accepts an explicit `api_key`
parameter.  The key MUST come from the user's saved settings.
The .env file is only used as a global fallback when NO user key is set.
This means DeepSeek actually uses DeepSeek, not Gemini.
"""

import os
import json
import requests
from typing import Dict, Any, List, Optional

from logger import get_logger
from db import schema_to_text

log = get_logger(__name__)


# ── Core callers ──────────────────────────────────────────────────────────────

def call_gemini(prompt: str, system: str = "", max_tokens: int = 2000,
                api_key: str = "") -> str:
    key = api_key or os.getenv("GEMINI_API_KEY", "")
    if not key or key in ("your_gemini_api_key_here", ""):
        raise ValueError(
            "Gemini API key is not set. Go to Settings → LLM API Keys and add your key."
        )

    # Model fallback chain — tries newest first, falls back on 404
    MODELS = [
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "gemini-1.5-flash-latest",
        "gemini-1.5-flash",
        "gemini-1.5-pro-latest",
        "gemini-pro",
    ]
    BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    body = {
        "contents": [{"parts": [{"text": f"{system}\n\n{prompt}" if system else prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": max_tokens},
    }
    log.debug("Calling Gemini API", max_tokens=max_tokens, prompt_len=len(prompt))

    last_error = None
    for model in MODELS:
        url = f"{BASE}/{model}:generateContent?key={key}"
        log.debug("Trying Gemini model", model=model)
        try:
            resp = requests.post(url, json=body, timeout=90)

            if resp.status_code == 404:
                log.debug("Gemini model not found, trying next", model=model)
                continue  # try next model in chain

            if not resp.ok:
                err_body = resp.text[:400]
                log.warning("Gemini API error", model=model,
                            status=resp.status_code, body=err_body)
                if resp.status_code in (400, 401, 403):
                    raise ValueError(
                        f"Gemini API key error (status {resp.status_code}): {err_body}"
                    )
                resp.raise_for_status()

            data = resp.json()
            # Handle blocked / empty responses
            candidates = data.get("candidates", [])
            if not candidates:
                block_reason = data.get("promptFeedback", {}).get("blockReason", "unknown")
                raise ValueError(f"Gemini blocked the request: {block_reason}")

            result = candidates[0]["content"]["parts"][0]["text"].strip()
            log.debug("Gemini response OK", model=model, response_len=len(result))
            return result

        except requests.exceptions.Timeout:
            log.warning("Gemini timeout", model=model)
            last_error = Exception(f"Gemini model {model} timed out.")
            continue
        except ValueError:
            raise  # key / block errors bubble up immediately
        except Exception as e:
            log.error("Gemini exception", model=model, error=str(e))
            last_error = e
            continue

    raise last_error or Exception("All Gemini models failed. Check your API key and quota.")


def call_deepseek(prompt: str, system: str = "", max_tokens: int = 2000,
                  api_key: str = "") -> str:
    key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
    if not key or key in ("your_deepseek_api_key_here", ""):
        raise ValueError(
            "DeepSeek API key is not set. Go to Settings → LLM API Keys and add your key."
        )
    url = "https://api.deepseek.com/v1/chat/completions"
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": "deepseek-chat",
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    log.debug("Calling DeepSeek API", max_tokens=max_tokens, prompt_len=len(prompt))
    for attempt in range(2):
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=90)
            if not resp.ok:
                err_body = resp.text[:300]
                log.warning("DeepSeek API error response",
                            status=resp.status_code, body=err_body, attempt=attempt)
                if resp.status_code in (401, 403):
                    raise ValueError(f"DeepSeek API key is invalid or has no credits. "
                                     f"Status {resp.status_code}: {err_body}")
                resp.raise_for_status()
            result = resp.json()["choices"][0]["message"]["content"].strip()
            log.debug("DeepSeek response received", response_len=len(result))
            return result
        except requests.exceptions.Timeout:
            log.warning("DeepSeek API timeout", attempt=attempt)
            if attempt == 1:
                raise Exception("DeepSeek API timed out after 90s on both attempts.")
        except ValueError:
            raise
        except Exception as e:
            log.error("DeepSeek API exception", error=str(e), attempt=attempt)
            if attempt == 1:
                raise


def call_llm(prompt: str, system: str = "", llm: str = "gemini",
             max_tokens: int = 2000, api_key: str = "") -> str:
    """
    Main dispatcher. Always use the explicit api_key.
    llm must be 'gemini' or 'deepseek' — this is respected exactly.
    """
    llm = (llm or "gemini").lower().strip()
    log.debug("LLM dispatch", llm=llm, has_key=bool(api_key))
    if llm == "deepseek":
        return call_deepseek(prompt, system, max_tokens, api_key)
    elif llm == "gemini":
        return call_gemini(prompt, system, max_tokens, api_key)
    else:
        raise ValueError(f"Unknown LLM provider: '{llm}'. Use 'gemini' or 'deepseek'.")


# ── API key validation ────────────────────────────────────────────────────────

def validate_llm_key(llm: str, api_key: str) -> Dict[str, Any]:
    """
    Send a tiny test prompt to verify the key works and has credits.
    Returns {"ok": True/False, "error": str, "model": str}
    """
    log.info("Validating LLM API key", llm=llm)
    test_prompt = "Reply with exactly the word: WORKING"
    try:
        result = call_llm(test_prompt, "", llm, max_tokens=20, api_key=api_key)
        ok = "WORKING" in result.upper() or len(result.strip()) > 0
        log.info("LLM key validation result", llm=llm, ok=ok, response=result[:50])
        return {"ok": ok, "model": llm, "response": result.strip()}
    except ValueError as e:
        log.warning("LLM key validation failed (invalid key)", llm=llm, error=str(e))
        return {"ok": False, "error": str(e)}
    except Exception as e:
        log.error("LLM key validation failed (unexpected)", llm=llm, error=str(e))
        return {"ok": False, "error": str(e)}


# ── Text-to-SQL ───────────────────────────────────────────────────────────────

def query_to_sql(question: str, schemas: Dict[str, Any], llm: str = "gemini",
                 fkeys: list = None, api_key: str = "") -> str:
    schema_text = schema_to_text(schemas, fkeys)
    system = (
        "You are an expert MySQL query writer. "
        "Given a database schema (with foreign key relationships) and a plain English question, "
        "write a valid MySQL SELECT query that may JOIN multiple tables as needed. "
        "Return ONLY the raw SQL — no markdown, no backticks, no explanation. "
        "Never use DROP, DELETE, INSERT, UPDATE, or any mutating statement."
    )
    prompt = f"Schema:\n{schema_text}\n\nQuestion: {question}\n\nSQL:"
    log.info("Generating SQL from NL question", llm=llm, question=question[:80])
    raw = call_llm(prompt, system, llm, max_tokens=800, api_key=api_key)
    sql = raw.replace("```sql", "").replace("```", "").strip()
    log.debug("SQL generated", sql=sql[:200])
    return sql


# ── Report generation ─────────────────────────────────────────────────────────

def generate_report_summary(title: str, kpis: Dict, section_data: Dict,
                            llm: str, format: str = "full",
                            api_key: str = "") -> str:
    kpi_text = "\n".join(f"  {k}: {v}" for k, v in kpis.items())
    sections_text = ""
    for sid, data in section_data.items():
        sections_text += f"\n\n## {data.get('title', sid)}\n"
        cols = data.get("columns", [])
        rows = data.get("data", [])[:5]
        if rows:
            sections_text += f"Columns: {', '.join(cols)}\n"
            for row in rows:
                sections_text += f"  {row}\n"

    if format == "executive":
        length_instruction = "Write 3-4 concise paragraphs (executive summary). Focus on 3 most important findings and one strategic recommendation."
    elif format == "quick":
        length_instruction = "Write 5-7 bullet points covering key findings. Be very concise."
    else:
        length_instruction = "Write a detailed professional report with 5-7 paragraphs: overview, findings per section, risks, opportunities, strategic recommendations."

    system = (
        "You are a senior business analyst writing a professional analytics report. "
        "Use concrete numbers from the data. Be direct and actionable. "
        "Do not invent data — only reference what is provided."
    )
    prompt = (
        f"Report Title: {title}\n\n"
        f"Business KPIs:\n{kpi_text}\n\n"
        f"Data Sections:{sections_text}\n\n"
        f"Instructions: {length_instruction}"
    )
    log.info("Generating report", llm=llm, title=title, sections=list(section_data.keys()), format=format)
    return call_llm(prompt, system, llm, max_tokens=2500, api_key=api_key)


def list_gemini_models(api_key: str) -> list:
    """
    Query Google's API to return all currently available Gemini models.
    Useful for debugging 404 issues.
    """
    key = api_key or os.getenv("GEMINI_API_KEY", "")
    if not key:
        return []
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
        resp = requests.get(url, timeout=15)
        if not resp.ok:
            return []
        models = resp.json().get("models", [])
        return [m["name"].replace("models/", "") for m in models
                if "generateContent" in m.get("supportedGenerationMethods", [])]
    except Exception:
        return []
