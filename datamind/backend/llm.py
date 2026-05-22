"""
LLM module — Gemini + DeepSeek with Token Tracking.

KEY FIX: Every function that calls an LLM now accepts an explicit `api_key`
parameter. The key MUST come from the user's saved settings.
The .env file is only used as a global fallback when NO user key is set.
This means DeepSeek actually uses DeepSeek, not Gemini.

NEW: Token tracking and credit deduction integrated.
"""

import os
import re
import json
import requests
from typing import Dict, Any, List, Optional

from logger import get_logger
from db import schema_to_text

# SEC-12: columns whose names suggest security-sensitive data that should
# never appear in analytics queries or be transmitted to external LLM providers.
_SENSITIVE_COL_RE = re.compile(
    r'\b(password|passwd|pwd|secret|api_key|apikey|access_token|refresh_token|'
    r'auth_token|bearer|private_key|encryption_key|enc_key|salt|hash|'
    r'ssn|sin|cvv|cvc|card_number|credit_card|bank_account|routing_number)\b',
    re.IGNORECASE,
)

def _filter_sensitive_schema(schemas: Dict[str, Any]) -> Dict[str, Any]:
    """
    Strip columns whose names match sensitive security patterns before the
    schema is sent to an external LLM provider. Analytics queries have no
    legitimate need for password hashes, API keys, or card numbers.
    """
    filtered: Dict[str, Any] = {}
    for table, cols in schemas.items():
        safe_cols = [c for c in cols if not _SENSITIVE_COL_RE.search(c.get("name", ""))]
        dropped = len(cols) - len(safe_cols)
        if dropped:
            log.debug("Schema filter: dropped sensitive columns",
                      table=table, dropped=dropped)
        filtered[table] = safe_cols
    return filtered

log = get_logger(__name__)


# ── Core callers ──────────────────────────────────────────────────────────────

def call_gemini(prompt: str, system: str = "", max_tokens: int = 2000,
                api_key: str = "") -> tuple:
    """
    Call Gemini API and return (response_text, tokens_used).
    """
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
            
            # Extract token usage
            usage_metadata = data.get("usageMetadata", {})
            tokens_used = usage_metadata.get("totalTokenCount", 0)
            
            log.debug("Gemini response OK", model=model, response_len=len(result), tokens=tokens_used)
            return result, tokens_used

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
                  api_key: str = "") -> tuple:
    """
    Call DeepSeek API and return (response_text, tokens_used).
    """
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
            
            data = resp.json()
            result = data["choices"][0]["message"]["content"].strip()
            
            # Extract token usage
            usage = data.get("usage", {})
            tokens_used = usage.get("total_tokens", 0)
            
            log.debug("DeepSeek response received", response_len=len(result), tokens=tokens_used)
            return result, tokens_used
            
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


_PLACEHOLDER_KEYS = {"", "your_gemini_api_key_here", "your_deepseek_api_key_here"}

def _is_real_key(key: str) -> bool:
    """Return True only if key is non-empty and not a placeholder from .env template."""
    k = (key or "").strip()
    return bool(k) and k not in _PLACEHOLDER_KEYS and not k.startswith("your_")


def get_llm_priority() -> list:
    """
    Return ordered list of LLM providers to try.
    Reads LLM_PRIORITY from .env (e.g. LLM_PRIORITY=deepseek,gemini).
    Defaults to gemini,deepseek if not set.
    """
    raw = os.getenv("LLM_PRIORITY", "gemini,deepseek")
    providers = [p.strip().lower() for p in raw.split(",") if p.strip()]
    known = {"gemini", "deepseek"}
    # Keep only known providers, preserve order, deduplicate
    seen = set()
    result = []
    for p in providers:
        if p in known and p not in seen:
            result.append(p)
            seen.add(p)
    # Append any known provider not mentioned so there's always a full list
    for p in ("gemini", "deepseek"):
        if p not in seen:
            result.append(p)
    return result


def call_llm(prompt: str, system: str = "", llm: str = "gemini",
             max_tokens: int = 2000, api_key: str = "", user_email: str = None) -> str:
    """
    Main dispatcher with priority-based fallback.

    Priority order is determined by:
      1. The requested `llm` (if it has a real key)
      2. Then the remaining providers in LLM_PRIORITY env var order

    If a provider has a key but the call fails at runtime (quota, timeout, etc.),
    it falls through to the next provider in the priority list.
    """
    requested = (llm or "gemini").lower().strip()

    def _env_key(name: str) -> str:
        return os.getenv(f"{name.upper()}_API_KEY", "").strip()

    def _effective_key(name: str, explicit_key: str = "") -> str:
        k = (explicit_key or "").strip()
        return k if _is_real_key(k) else _env_key(name)

    def _call_one(name: str, key: str):
        if name == "deepseek":
            return call_deepseek(prompt, system, max_tokens, key)
        elif name == "gemini":
            return call_gemini(prompt, system, max_tokens, key)
        raise ValueError(f"Unknown LLM provider: '{name}'")

    # Build the ordered list: requested provider first, then priority order for the rest
    priority = get_llm_priority()
    order = [requested] + [p for p in priority if p != requested]

    last_error = None
    for provider in order:
        key = _effective_key(provider, api_key if provider == requested else "")
        if not _is_real_key(key):
            log.debug("LLM provider skipped — no key", provider=provider)
            continue
        try:
            log.debug("LLM dispatch attempt", provider=provider, user=user_email)
            result, tokens = _call_one(provider, key)
            if provider != requested:
                log.info("LLM fallback succeeded", requested=requested, used=provider, user=user_email)
            if user_email:
                try:
                    from billing import charge_ai_usage
                    charge_ai_usage(user_email, tokens or 0, provider, "llm_call")
                except Exception as _ce:
                    log.warning("charge_ai_usage failed silently", error=str(_ce))
            return result
        except ValueError:
            raise  # key/auth errors are fatal — don't try the next provider
        except Exception as e:
            log.warning("LLM provider failed, trying next", provider=provider,
                        error=str(e), user=user_email)
            last_error = e
            continue

    raise last_error or ValueError(
        f"No LLM provider available. Set at least one API key in Settings or .env "
        f"(LLM_PRIORITY={','.join(priority)})."
    )


# ── API key validation ────────────────────────────────────────────────────────

def validate_llm_key(llm: str, api_key: str) -> Dict[str, Any]:
    """
    Send a tiny test prompt to verify the key works and has credits.
    Returns {"ok": True/False, "error": str, "model": str}
    """
    log.info("Validating LLM API key", llm=llm)
    test_prompt = "Reply with exactly the word: WORKING"
    try:
        result = call_llm(test_prompt, "", llm, max_tokens=20, api_key=api_key, user_email=None)
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
                 fkeys: list = None, api_key: str = "", user_email: str = None,
                 history_months: int = None, tenant_id: str = None) -> str:
    schema_text = schema_to_text(_filter_sensitive_schema(schemas), fkeys)
    history_hint = (
        f" Only return data from the last {history_months} month(s) — add "
        f"WHERE date_col >= DATE_SUB(NOW(), INTERVAL {history_months} MONTH) "
        f"using the most relevant date/time column. If no date column exists, add LIMIT {history_months * 1000}."
        if history_months else ""
    )
    # For integration users querying shared sp_* tables, every table has a
    # tenant_id column that MUST be scoped in WHERE and JOIN conditions.
    tenant_hint = (
        f" IMPORTANT: All sp_* tables are shared across multiple customers. "
        f"The current user's tenant_id is '{tenant_id}'. Rules:\n"
        f"1. Main WHERE clause must always include: WHERE tenant_id = '{tenant_id}'\n"
        f"2. Every JOIN must scope the joined table too: "
        f"JOIN sp_foo f ON f.id = a.foo_id AND f.tenant_id = '{tenant_id}'\n"
        f"3. sp_receipt_line_items already has product_name and category_name columns — "
        f"prefer using these directly instead of joining sp_products when possible.\n"
        f"4. COUNT(DISTINCT receipt_id) on sp_receipt_line_items gives receipt count "
        f"without needing to join sp_receipts."
        if tenant_id else ""
    )
    system = (
        "You are an expert MySQL query writer. "
        "Given a database schema (with foreign key relationships) and a plain English question, "
        "write a valid MySQL SELECT query that may JOIN multiple tables as needed. "
        "Return ONLY the raw SQL — no markdown, no backticks, no explanation. "
        "Never use DROP, DELETE, INSERT, UPDATE, or any mutating statement."
        + history_hint + tenant_hint
    )
    prompt = f"Schema:\n{schema_text}\n\nQuestion: {question}\n\nSQL:"
    log.info("Generating SQL from NL question", llm=llm, question=question[:80])
    raw = call_llm(prompt, system, llm, max_tokens=800, api_key=api_key, user_email=user_email)
    sql = raw.replace("```sql", "").replace("```", "").strip()
    log.debug("SQL generated", sql=sql[:200])
    return sql


# ── Report generation ─────────────────────────────────────────────────────────

def generate_report_summary(title: str, kpis: Dict, section_data: Dict,
                            llm: str, format: str = "full",
                            api_key: str = "", user_email: str = None) -> str:
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
    return call_llm(prompt, system, llm, max_tokens=2500, api_key=api_key, user_email=user_email)


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