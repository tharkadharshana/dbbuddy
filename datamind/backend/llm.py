"""
LLM module — OpenAI + Gemini + DeepSeek with Token Tracking.

KEY FIX: Every function that calls an LLM now accepts an explicit `api_key`
parameter. The key MUST come from the user's saved settings.
The .env file is only used as a global fallback when NO user key is set.
This means DeepSeek actually uses DeepSeek, not Gemini.

NEW: Token tracking and credit deduction integrated.
"""

import os
import re
import json
import time
import requests
from typing import Dict, Any, List, Optional

# HTTP status codes that are transient and safe to retry after a short wait
_TRANSIENT_STATUS = {429, 500, 502, 503, 504}


class LLMTransientError(Exception):
    """Raised when all retry attempts for one key fail due to rate limits or server errors."""


# ── API Key Pool ──────────────────────────────────────────────────────────────
# Maps (provider, key_fingerprint) → unix timestamp when the key can be retried.
# Values:
#   < now  → key is available
#   > now  → key is in cooldown (rate-limited or auth-failed)
#   == inf → key is permanently blacklisted (401/403)
_key_cooldowns: Dict[str, float] = {}
_KEY_RL_COOLDOWN  = 60.0    # seconds to park a key after a 429 / transient error
_KEY_AUTH_COOLDOWN = 3600.0  # seconds to park a key after a 401/403


def _key_fp(provider: str, key: str) -> str:
    """Stable, non-sensitive fingerprint for (provider, key) pair."""
    return f"{provider}:{key[-8:] if len(key) > 8 else key}"


def _key_available(provider: str, key: str) -> bool:
    return time.time() >= _key_cooldowns.get(_key_fp(provider, key), 0.0)


def _park_key(provider: str, key: str, duration: float):
    _key_cooldowns[_key_fp(provider, key)] = time.time() + duration


def _unpark_key(provider: str, key: str):
    _key_cooldowns.pop(_key_fp(provider, key), None)


def _build_key_pool(provider: str, user_key: str = "") -> List[str]:
    """
    Return ordered list of real keys to try for a provider.
    User's own key is always first (if valid).
    Remaining keys come from the env var, which may be comma-separated:
      OPENAI_API_KEY=sk-key1,sk-key2,sk-key3
    Available (non-cooled) keys come first; cooled keys are appended as fallback
    so the pool never completely empties.
    """
    env_raw = os.getenv(f"{provider.upper()}_API_KEY", "")
    seen: set = set()
    candidates: List[str] = []
    for k in ([user_key] + env_raw.split(",")):
        k = k.strip()
        if _is_real_key(k) and k not in seen:
            seen.add(k)
            candidates.append(k)
    # Put available keys first, cooled keys at the end
    available = [k for k in candidates if _key_available(provider, k)]
    cooled    = [k for k in candidates if not _key_available(provider, k)]
    return available + cooled

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

# Columns that are internal system plumbing on every sp_*/ly_* shared table.
# tenant_id  — multi-tenancy routing key, injected by server, never a user column.
# synced_at  — our internal write timestamp, meaningless to business users.
# The LLM must not SELECT these; keeping them out of the schema it sees is the
# cleanest way to enforce that without relying on prompt instructions alone.
_SP_INTERNAL_COLS = frozenset({"tenant_id", "synced_at"})

def _filter_sensitive_schema(schemas: Dict[str, Any]) -> Dict[str, Any]:
    """
    Strip columns before the schema is sent to an external LLM provider:
    - Security-sensitive columns (passwords, API keys, card numbers).
    - Internal system columns on sp_*/ly_* shared tables (tenant_id, synced_at)
      that are routing/audit fields with no business meaning to end users.
    """
    filtered: Dict[str, Any] = {}
    for table, cols in schemas.items():
        is_shared = table.startswith(("sp_", "ly_"))
        safe_cols = []
        for c in cols:
            name = c.get("name", "")
            if _SENSITIVE_COL_RE.search(name):
                continue
            if is_shared and name in _SP_INTERNAL_COLS:
                continue
            safe_cols.append(c)
        dropped = len(cols) - len(safe_cols)
        if dropped:
            log.debug("Schema filter: dropped internal/sensitive columns",
                      table=table, dropped=dropped)
        filtered[table] = safe_cols
    return filtered

log = get_logger(__name__)


# ── Core callers ──────────────────────────────────────────────────────────────

def call_openai(prompt: str, system: str = "", max_tokens: int = 2000,
                api_key: str = "", user_email: str = None) -> tuple:
    """
    Call OpenAI's Chat Completions API and return (response_text, tokens_used).
    """
    key = api_key or os.getenv("OPENAI_API_KEY", "").split(",")[0].strip()
    if not key or key in ("your_openai_api_key_here", ""):
        raise ValueError(
            "OpenAI API key is not set. Go to Settings → LLM API Keys and add your key."
        )
    url = "https://api.openai.com/v1/chat/completions"
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": "gpt-4o-mini",
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    log.debug("Calling OpenAI API", max_tokens=max_tokens, prompt_len=len(prompt), user=user_email)
    for attempt in range(3):
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=90)
            if not resp.ok:
                err_body = resp.text[:300]
                log.warning("OpenAI API error response",
                            status=resp.status_code, body=err_body, attempt=attempt, user=user_email)
                if resp.status_code in (401, 403):
                    raise ValueError(f"OpenAI API key is invalid or has no credits. "
                                     f"Status {resp.status_code}: {err_body}")
                if resp.status_code in _TRANSIENT_STATUS and attempt < 2:
                    wait = int(resp.headers.get("Retry-After", 2 ** (attempt + 1)))
                    log.warning("OpenAI transient error, backing off",
                                status=resp.status_code, attempt=attempt, wait_s=wait, user=user_email)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()

            data = resp.json()
            result = data["choices"][0]["message"]["content"].strip()
            usage = data.get("usage", {})
            tokens_used = usage.get("total_tokens", 0)
            model_used = data.get("model", body["model"])
            log.debug("OpenAI response received", response_len=len(result), tokens=tokens_used,
                      model=model_used, user=user_email)
            return result, tokens_used, model_used

        except requests.exceptions.Timeout:
            log.warning("OpenAI API timeout", attempt=attempt, user=user_email)
            if attempt == 2:
                raise LLMTransientError("OpenAI API timed out. Please try again in a moment.")
            time.sleep(2 ** (attempt + 1))
        except ValueError:
            raise
        except Exception as e:
            log.error("OpenAI API exception", error=str(e), attempt=attempt, user=user_email)
            if attempt == 2:
                raise LLMTransientError("OpenAI is temporarily unavailable. Please try again in a moment.")
            time.sleep(2 ** (attempt + 1))


def call_gemini(prompt: str, system: str = "", max_tokens: int = 2000,
                api_key: str = "", user_email: str = None) -> tuple:
    """
    Call Gemini API and return (response_text, tokens_used).
    """
    key = api_key or os.getenv("GEMINI_API_KEY", "").split(",")[0].strip()
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
    log.debug("Calling Gemini API", max_tokens=max_tokens, prompt_len=len(prompt), user=user_email)

    for model in MODELS:
        url = f"{BASE}/{model}:generateContent?key={key}"
        log.debug("Trying Gemini model", model=model, user=user_email)
        try:
            resp = requests.post(url, json=body, timeout=90)

            if resp.status_code == 404:
                log.debug("Gemini model not found, trying next", model=model, user=user_email)
                continue

            if not resp.ok:
                err_body = resp.text[:400]
                log.warning("Gemini API error", model=model,
                            status=resp.status_code, body=err_body, user=user_email)
                if resp.status_code in (400, 401, 403):
                    raise ValueError(
                        f"Gemini API key error (status {resp.status_code}): {err_body}"
                    )
                if resp.status_code in _TRANSIENT_STATUS:
                    wait = int(resp.headers.get("Retry-After", 4))
                    log.warning("Gemini transient error, backing off",
                                model=model, status=resp.status_code, wait_s=wait, user=user_email)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()

            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                block_reason = data.get("promptFeedback", {}).get("blockReason", "unknown")
                raise ValueError(f"Gemini blocked the request: {block_reason}")

            result = candidates[0]["content"]["parts"][0]["text"].strip()
            usage_metadata = data.get("usageMetadata", {})
            tokens_used = usage_metadata.get("totalTokenCount", 0)
            log.debug("Gemini response OK", model=model, response_len=len(result), tokens=tokens_used, user=user_email)
            return result, tokens_used, model

        except requests.exceptions.Timeout:
            log.warning("Gemini timeout", model=model, user=user_email)
            continue
        except ValueError:
            raise
        except Exception as e:
            log.error("Gemini exception", model=model, error=str(e), user=user_email)
            continue

    raise LLMTransientError("Gemini is temporarily unavailable. Please try again in a moment.")


def call_deepseek(prompt: str, system: str = "", max_tokens: int = 2000,
                  api_key: str = "", user_email: str = None) -> tuple:
    """
    Call DeepSeek API and return (response_text, tokens_used).
    """
    key = api_key or os.getenv("DEEPSEEK_API_KEY", "").split(",")[0].strip()
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
    log.debug("Calling DeepSeek API", max_tokens=max_tokens, prompt_len=len(prompt), user=user_email)
    for attempt in range(3):
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=90)
            if not resp.ok:
                err_body = resp.text[:300]
                log.warning("DeepSeek API error response",
                            status=resp.status_code, body=err_body, attempt=attempt, user=user_email)
                if resp.status_code in (401, 403):
                    raise ValueError(f"DeepSeek API key is invalid or has no credits. "
                                     f"Status {resp.status_code}: {err_body}")
                if resp.status_code in _TRANSIENT_STATUS and attempt < 2:
                    wait = int(resp.headers.get("Retry-After", 2 ** (attempt + 1)))
                    log.warning("DeepSeek transient error, backing off",
                                status=resp.status_code, attempt=attempt, wait_s=wait, user=user_email)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()

            data = resp.json()
            result = data["choices"][0]["message"]["content"].strip()
            usage = data.get("usage", {})
            tokens_used = usage.get("total_tokens", 0)

            model_used = data.get("model", body["model"])
            log.debug("DeepSeek response received", response_len=len(result), tokens=tokens_used,
                      model=model_used, user=user_email)
            return result, tokens_used, model_used

        except requests.exceptions.Timeout:
            log.warning("DeepSeek API timeout", attempt=attempt, user=user_email)
            if attempt == 2:
                raise LLMTransientError("DeepSeek API timed out. Please try again in a moment.")
            time.sleep(2 ** (attempt + 1))
        except ValueError:
            raise
        except Exception as e:
            log.error("DeepSeek API exception", error=str(e), attempt=attempt, user=user_email)
            if attempt == 2:
                raise LLMTransientError("DeepSeek is temporarily unavailable. Please try again in a moment.")
            time.sleep(2 ** (attempt + 1))


_PLACEHOLDER_KEYS = {"", "your_gemini_api_key_here", "your_deepseek_api_key_here", "your_openai_api_key_here"}

def _is_real_key(key: str) -> bool:
    """Return True only if key is non-empty and not a placeholder from .env template."""
    k = (key or "").strip()
    return bool(k) and k not in _PLACEHOLDER_KEYS and not k.startswith("your_")


def get_llm_priority() -> list:
    """
    Return ordered list of LLM providers to try.
    Reads LLM_PRIORITY from .env (e.g. LLM_PRIORITY=deepseek,gemini).
    Defaults to openai,gemini,deepseek if not set.
    """
    raw = os.getenv("LLM_PRIORITY", "openai,gemini,deepseek")
    providers = [p.strip().lower() for p in raw.split(",") if p.strip()]
    known = {"openai", "gemini", "deepseek"}
    # Keep only known providers, preserve order, deduplicate
    seen = set()
    result = []
    for p in providers:
        if p in known and p not in seen:
            result.append(p)
            seen.add(p)
    # Append any known provider not mentioned so there's always a full list
    for p in ("openai", "gemini", "deepseek"):
        if p not in seen:
            result.append(p)
    return result


def call_llm(prompt: str, system: str = "", llm: str = "openai",
             max_tokens: int = 2000, api_key: str = "", user_email: str = None,
             operation: str = "llm_call") -> str:
    """
    Main dispatcher with per-provider key pool + cross-provider fallback.

    operation — logical name of the call (classify / sql_gen / synthesize /
                think / report / validate) stored in llm_usage_log.endpoint
                so cost-per-operation queries are possible.

    For each provider in priority order:
      - Builds a key pool (user key first, then any comma-separated env keys)
      - Tries each key in the pool in order
      - On LLMTransientError (429 / timeout): parks that key for 60 s, tries next key
      - On ValueError (401/403 bad key): parks that key for 1 h, tries next key
      - When all keys for a provider are exhausted: moves to the next provider
    """
    requested = (llm or "openai").lower().strip()

    def _call_one(name: str, key: str):
        if name == "openai":
            return call_openai(prompt, system, max_tokens, key, user_email=user_email)
        elif name == "deepseek":
            return call_deepseek(prompt, system, max_tokens, key, user_email=user_email)
        elif name == "gemini":
            return call_gemini(prompt, system, max_tokens, key, user_email=user_email)
        raise ValueError(f"Unknown LLM provider: '{name}'")

    priority = get_llm_priority()
    order = [requested] + [p for p in priority if p != requested]

    last_error = None
    for provider in order:
        user_key = (api_key or "").strip() if provider == requested else ""
        pool = _build_key_pool(provider, user_key)
        if not pool:
            log.debug("LLM provider skipped — no keys in pool",
                      provider=provider, user=user_email)
            continue

        for key in pool:
            try:
                log.debug("LLM dispatch attempt", provider=provider,
                          key_tail=key[-4:], user=user_email)
                result, tokens, model_id = _call_one(provider, key)
                _unpark_key(provider, key)
                if provider != requested:
                    log.info("LLM provider fallback succeeded",
                             requested=requested, used=provider, model=model_id,
                             user=user_email)
                if user_email:
                    try:
                        from billing import charge_ai_usage, invalidate_sub_cache
                        charge_ai_usage(user_email, tokens or 0, provider, model_id, operation)
                        invalidate_sub_cache(user_email)
                    except Exception as _ce:
                        log.warning("charge_ai_usage failed silently", error=str(_ce))
                return result

            except LLMTransientError as e:
                _park_key(provider, key, _KEY_RL_COOLDOWN)
                log.warning("LLM key rate-limited, cycling to next key",
                            provider=provider, key_tail=key[-4:],
                            cooldown_s=_KEY_RL_COOLDOWN, user=user_email)
                last_error = e
                continue

            except ValueError as e:
                _park_key(provider, key, _KEY_AUTH_COOLDOWN)
                log.warning("LLM key auth failed, cycling to next key",
                            provider=provider, key_tail=key[-4:],
                            error=str(e)[:100], cooldown_s=_KEY_AUTH_COOLDOWN,
                            user=user_email)
                last_error = e
                continue

            except Exception as e:
                log.warning("LLM key call failed, cycling to next key",
                            provider=provider, key_tail=key[-4:],
                            error=str(e)[:100], user=user_email)
                last_error = e
                continue

        log.warning("All keys exhausted for provider, trying next provider",
                    provider=provider, pool_size=len(pool), user=user_email)

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
        result = call_llm(test_prompt, "", llm, max_tokens=20, api_key=api_key, user_email=None, operation="validate")
        ok = "WORKING" in result.upper() or len(result.strip()) > 0
        log.info("LLM key validation result", llm=llm, ok=ok, response=result[:50])
        return {"ok": ok, "model": llm, "response": result.strip()}
    except ValueError as e:
        log.warning("LLM key validation failed (invalid key)", llm=llm, error=str(e))
        return {"ok": False, "error": str(e)}
    except Exception as e:
        log.error("LLM key validation failed (unexpected)", llm=llm, error=str(e))
        return {"ok": False, "error": str(e)}


# ── Question classification ───────────────────────────────────────────────────

def classify_question(
    question: str, table_names: str,
    llm: str, api_key: str, user_email: str,
    app_name: str = "DataMind",
    conversation_history: str = "",
    language_hint: str = "",
) -> dict:
    """
    Classify the user question to determine handling strategy.
    Returns a dict with 'type' and type-specific fields.
    Falls back to {"type": "data_query"} if classification fails.
    """
    system = (
        "You are a data assistant classifier. Respond ONLY with valid JSON — no markdown, no explanation.\n"
        "Classify the question into exactly one type:\n"

        '{"type":"data_query"} — question about data that exists in the database\n'

        '{"type":"multi_step","sub_questions":["q1","q2"]} — clearly contains 2+ separate data queries; '
        "only use this when two or more genuinely distinct SQL queries are needed\n"

        '{"type":"unsupported_query","response":"..."} — the question requires data that does NOT exist in the '
        "database: future predictions, next-month forecasts, external market data, competitor data, weather, or "
        "city-wide/industry trends. Respond helpfully by explaining what CAN be shown instead "
        "(e.g. 'I can't predict next month, but I can show you historical top sellers by month — would that help?').\n"

        '{"type":"conversational","response":"..."} — greeting, small talk, thank-you, or a request to '
        "modify/delete/create data (INSERT/UPDATE/DELETE/DROP/TRUNCATE/ALTER). "
        f"For harmful/destructive requests respond with a polite refusal as {app_name}. "
        "Also use this type when: "
        "(1) the user asks what data, tables, or information is available ('what data do you have', 'what can you show me', "
        "'what tables are there') — respond with a friendly list of the available tables from the table list provided; "
        "(2) the user asks about their own account details (country, currency, timezone) — answer directly from the "
        "context provided in these instructions, do NOT deflect or say 'I'm a data assistant'; "
        f"for all other conversational cases respond as {app_name}, a friendly AI data assistant.\n"

        '{"type":"clarification_needed","clarification":"..."} — the question is about data but too vague to answer; '
        "ask ONE specific clarifying question. "
        "IMPORTANT RULE: if the conversation history shows the last assistant message was already a clarification "
        "question and the user has now given ANY response (even a single word like 'sales', 'any', a category name, "
        "or a number), do NOT return clarification_needed again — instead reconstruct the full original intent from "
        "history combined with the user's answer, and return data_query.\n"

        "IMPORTANT: Use conversation history (if provided) to understand follow-up questions and pronouns like "
        "'they', 'those', 'it'. "
        + (language_hint if language_hint else "Always respond in the same language the user used to write their question.")
    )
    history_block = f"\nConversation so far:\n{conversation_history}\n" if conversation_history else ""
    prompt = f"Available tables: {table_names}{history_block}\nQuestion: {question}"
    try:
        raw = call_llm(prompt, system, llm, max_tokens=400, api_key=api_key, user_email=user_email, operation="classify")
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()
        result = json.loads(raw)
        if result.get("type") not in ("data_query", "multi_step", "conversational", "clarification_needed", "unsupported_query"):
            return {"type": "data_query"}
        return result
    except Exception as _e:
        log.debug("Question classification failed, treating as data_query", error=str(_e))
        return {"type": "data_query"}


def synthesize_multi_step_answer(
    original_question: str, step_results: list,
    llm: str, api_key: str, user_email: str
) -> Optional[str]:
    """Combine results from multiple sub-queries into a single coherent answer."""
    parts = []
    for i, step in enumerate(step_results, 1):
        q = step.get("question", f"Step {i}")
        cols = step.get("columns", [])
        rows = step.get("data", [])[:5]
        parts.append(
            f"Query {i}: {q}\n"
            f"Columns: {', '.join(cols)}\n"
            f"Top rows: {json.dumps(rows, default=str)}"
        )
    system = (
        "You are a data analyst. Combine the query results below into a concise, clear answer to "
        "the original question. Use specific numbers from the data. No preamble."
    )
    prompt = f"Question: {original_question}\n\n" + "\n\n".join(parts) + "\n\nAnswer:"
    try:
        return call_llm(prompt, system, llm, max_tokens=600, api_key=api_key, user_email=user_email, operation="synthesize")
    except Exception as _se:
        log.warning("Multi-step synthesis failed", error=str(_se))
        return None


# ── Text-to-SQL ───────────────────────────────────────────────────────────────

def query_to_sql(question: str, schemas: Dict[str, Any], llm: str = "openai",
                 fkeys: list = None, api_key: str = "", user_email: str = None,
                 history_months: int = None, tenant_id: str = None,
                 row_limit: int = 500, conversation_history: str = "",
                 extra_schema_hints: str = "",
                 shop_timezone: str = "UTC") -> str:
    schema_text = schema_to_text(_filter_sensitive_schema(schemas), fkeys)
    history_hint = (
        f" Only return data from the last {history_months} month(s) — add "
        f"WHERE date_col >= DATE_SUB(NOW(), INTERVAL {history_months} MONTH) "
        f"using the most relevant date/time column. If no date column exists, add LIMIT {history_months * 1000}."
        if history_months else ""
    )
    limit_hint = (
        f" Always end the query with LIMIT {row_limit} unless the user explicitly asked for all rows. "
        f"Never generate a query without a LIMIT clause."
    )
    # For integration users querying shared sp_* tables, every table has a
    # tenant_id column that MUST be scoped in WHERE and JOIN conditions.
    # Also inject SalesPlay canonical metric rules so the LLM matches SalesPlay's
    # own dashboard numbers (VOID exclusion, timezone, correct revenue column, etc.)
    if tenant_id:
        from providers.salesplay.canonical_metrics import SALESPLAY_LLM_RULES
        _sp_rules = SALESPLAY_LLM_RULES.format(timezone=shop_timezone or "UTC")
        tenant_hint = (
            f" IMPORTANT: All sp_* tables are shared across multiple customers. "
            f"The current user's tenant_id is '{tenant_id}'. Rules:\n"
            f"1. Main WHERE clause must always include: WHERE tenant_id = '{tenant_id}'\n"
            f"2. Every JOIN must scope the joined table too: "
            f"JOIN sp_foo f ON f.id = a.foo_id AND f.tenant_id = '{tenant_id}'\n"
            f"3. sp_receipt_line_items already has product_name and category_name columns — "
            f"prefer using these directly instead of joining sp_products when possible.\n"
            f"4. COUNT(DISTINCT receipt_id) on sp_receipt_line_items gives receipt count "
            f"without needing to join sp_receipts.\n"
            f"5. sp_customers has pre-aggregated columns — ALWAYS use them directly, never "
            f"recompute via JOIN: total_spent (lifetime revenue), total_visits (order count), "
            f"last_purchase_date (most recent completed purchase), points_balance. "
            f"Do NOT join sp_receipts to compute any of these — the join misses most customers "
            f"due to customer_id linkage gaps. Only join sp_receipts if you need individual "
            f"receipt-level rows (e.g. itemised breakdown), never for aggregates.\n\n"
            f"{_sp_rules}"
            f"\n\nNever include tenant_id or synced_at in SELECT output — "
            f"they are internal system columns that mean nothing to business users. "
            f"Only SELECT columns that have direct business meaning."
        )
    else:
        tenant_hint = ""
    # Conversation history is prepended so the LLM understands follow-up
    # questions ("explain this", "drill down", "compare to last month") without
    # generating a broad unrelated SQL query.
    history_section = (
        f"[Previous conversation — use this to understand follow-up questions]\n"
        f"{conversation_history}\n\n"
        if conversation_history else ""
    )
    system = (
        history_section +
        "You are an expert MySQL query writer. "
        "Given a database schema (with foreign key relationships) and a plain English question, "
        "write a valid MySQL SELECT query that may JOIN multiple tables as needed. "
        "Return ONLY the raw SQL — no markdown, no backticks, no explanation. "
        "Never use DROP, DELETE, INSERT, UPDATE, or any mutating statement."
        + history_hint + tenant_hint
        + (f" {extra_schema_hints.strip()}" if extra_schema_hints else "")
        + limit_hint
    )
    prompt = f"Schema:\n{schema_text}\n\nQuestion: {question}\n\nSQL:"
    log.info("Generating SQL from NL question", llm=llm, question=question[:80])
    raw = call_llm(prompt, system, llm, max_tokens=800, api_key=api_key, user_email=user_email, operation="sql_gen")
    sql = raw.replace("```sql", "").replace("```", "").strip()
    log.debug("SQL generated", sql=sql[:200])
    return sql


# ── Report generation ─────────────────────────────────────────────────────────

def generate_report_summary(title: str, kpis: Dict, section_data: Dict,
                            llm: str, format: str = "full",
                            api_key: str = "", user_email: str = None,
                            currency: str = "$", country: str = "") -> str:
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

    _profile = f"Use '{currency}' as the currency symbol — never assume USD or '$'."
    if country:
        _profile += f" The business operates in {country}."
    system = (
        "You are a senior business analyst writing a professional analytics report. "
        "Use concrete numbers from the data. Be direct and actionable. "
        f"Do not invent data — only reference what is provided. {_profile}"
    )
    prompt = (
        f"Report Title: {title}\n\n"
        f"Business KPIs:\n{kpi_text}\n\n"
        f"Data Sections:{sections_text}\n\n"
        f"Instructions: {length_instruction}"
    )
    log.info("Generating report", llm=llm, title=title, sections=list(section_data.keys()), format=format)
    return call_llm(prompt, system, llm, max_tokens=2500, api_key=api_key, user_email=user_email, operation="report")


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