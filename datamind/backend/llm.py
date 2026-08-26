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

# ── Model selection (env-overridable) ─────────────────────────────────────────
# OPENAI_MODEL=gpt-4o-mini
# GEMINI_MODELS=gemini-2.0-flash,gemini-1.5-flash   (fallback chain, first wins)
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
GEMINI_MODELS = [m.strip() for m in os.getenv(
    "GEMINI_MODELS",
    "gemini-2.0-flash,gemini-2.0-flash-lite,gemini-1.5-flash-latest,"
    "gemini-1.5-flash,gemini-1.5-pro-latest,gemini-pro",
).split(",") if m.strip()]


# ── HOTFIX 2026-08-18: OpenAI request params differ between model generations ──
# Switching OPENAI_MODEL from gpt-4o-mini to gpt-5.6-luna broke every OpenAI call.
# Verified against the live API — no single request body serves both generations:
#
#   body field                     gpt-4o-mini            gpt-5.x (e.g. gpt-5.6-luna)
#   ---------------------------------------------------------------------------
#   max_tokens                     OK                     400 (use max_completion_tokens)
#   max_completion_tokens          OK                     OK          <- send this always
#   temperature: 0.2               OK                     400 (only the default 1)
#   reasoning_effort: "none"       400 (unrecognized)     REQUIRED to use tools
#
# So `max_completion_tokens` is unconditional, and the other two branch on _is_gpt5().
# Anything reached through OPENAI_MODEL must keep working on BOTH generations —
# do not "simplify" these branches away without re-testing the model you are not using.
# DeepSeek shares _stream_chat_completions and still wants max_tokens + temperature.
def _is_gpt5(model: str) -> bool:
    """True for model generations that reject temperature != 1 and need
    reasoning_effort="none" before they will use tools on /v1/chat/completions."""
    # ponytail: prefix match, not a model list — new gpt-5.x names work without a code change.
    # If OpenAI ships a generation that breaks the pattern, this is the one place to fix.
    return model.startswith(("gpt-5", "o1", "o3", "o4"))


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
# NOTE: 'id' is intentionally kept in the schema — hiding it causes the LLM to
# hallucinate a non-existent 'customer_id'/'product_id' column and crash the query.
# ID columns are stripped from results in _run_sql instead (main.py).
_SP_INTERNAL_COLS = frozenset({"tenant_id", "synced_at"})

# Result-row hygiene shared by every path that returns rows to the user (SQL
# results, MCP SQL tools, MCP report tools): surrogate keys and raw POS
# internal fields are meaningless to a business user and must never surface,
# whether they came from our own DB schema or straight off the report API's
# JSON (which carries fields like 'key'/'app_key'/terminal ids that have no
# equivalent in our sp_* schema, so _ID_COL_RE below is the only guard on them).
_ID_COL_RE = re.compile(r'^id$|_id$', re.IGNORECASE)
_REPORT_API_INTERNAL_COLS = frozenset({
    "key", "app_key", "terminal_key", "device_id", "invoice_key",
    "master_username", "user_name", "tenant_id", "synced_at",
})


def strip_internal_fields(rows: list) -> list:
    """Remove surrogate-id, sensitive, and internal-plumbing columns from a
    list of result dicts — used for both DB query results and raw report-API
    rows before they reach the user or the model's final answer."""
    if not rows:
        return rows
    hidden = _REPORT_API_INTERNAL_COLS
    out = []
    for row in rows:
        out.append({
            k: v for k, v in row.items()
            if k not in hidden and not _ID_COL_RE.search(k) and not _SENSITIVE_COL_RE.search(k)
        })
    return out

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
        "model": OPENAI_MODEL,
        "messages": messages,
        "max_completion_tokens": max_tokens,   # accepted by both gpt-4o* and gpt-5.x
    }
    if not _is_gpt5(OPENAI_MODEL):
        body["temperature"] = 0.2
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
    MODELS = GEMINI_MODELS
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


# ── Streaming provider calls (real token deltas) ──────────────────────────────
# Same (text, tokens, model) return contract as the non-streaming calls above,
# so call_llm's fallback + charging loop is reused unchanged. on_delta(text) is
# fired per content chunk. Failure BEFORE any output raises (transient/auth) so
# the key pool can retry; failure AFTER output returns the partial answer so we
# never re-stream and double-charge / duplicate visible text.

def _estimate_tokens(*parts: str) -> int:
    # ponytail: ~4 chars/token — only used when the provider omits stream usage.
    return sum(len(p or "") for p in parts) // 4


def _stream_chat_completions(provider, url, model, prompt, system, max_tokens,
                             key, on_delta, user_email):
    """Stream an OpenAI-compatible Chat Completions response (OpenAI + DeepSeek)."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body = {"model": model, "messages": messages, "stream": True,
            "stream_options": {"include_usage": True}}
    # DeepSeek shares this helper and still takes the older param name.
    body["max_completion_tokens" if provider == "openai" else "max_tokens"] = max_tokens
    if not _is_gpt5(model):
        body["temperature"] = 0.2
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    resp = requests.post(url, json=body, headers=headers, timeout=90, stream=True)
    if not resp.ok:
        err = resp.text[:300]
        if resp.status_code in (401, 403):
            raise ValueError(f"{provider} API key is invalid or has no credits. "
                             f"Status {resp.status_code}: {err}")
        if resp.status_code in _TRANSIENT_STATUS:
            raise LLMTransientError(f"{provider} is busy. Please try again in a moment.")
        resp.raise_for_status()

    full, tokens, model_used = [], 0, model
    try:
        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            data = line[6:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except Exception:
                continue
            choices = chunk.get("choices") or []
            if choices:
                delta = (choices[0].get("delta") or {}).get("content")
                if delta:
                    full.append(delta)
                    on_delta(delta)
            if chunk.get("usage"):
                tokens = chunk["usage"].get("total_tokens", tokens)
            if chunk.get("model"):
                model_used = chunk["model"]
    except Exception as e:
        if not full:
            raise LLMTransientError(f"{provider} stream failed before any output.")
        log.warning("Stream broke mid-answer, returning partial",
                    provider=provider, error=str(e), user=user_email)

    text = "".join(full).strip()
    if not text:
        raise LLMTransientError(f"{provider} stream produced no output.")
    return text, tokens or _estimate_tokens(prompt, system, text), model_used


def _stream_gemini(prompt, system, max_tokens, key, on_delta, user_email):
    """Stream a Gemini streamGenerateContent (SSE) response."""
    MODELS = GEMINI_MODELS
    BASE = "https://generativelanguage.googleapis.com/v1beta/models"
    body = {"contents": [{"parts": [{"text": f"{system}\n\n{prompt}" if system else prompt}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": max_tokens}}
    for model in MODELS:
        url = f"{BASE}/{model}:streamGenerateContent?alt=sse&key={key}"
        try:
            resp = requests.post(url, json=body, timeout=90, stream=True)
            if resp.status_code == 404:
                continue
            if not resp.ok:
                err = resp.text[:400]
                if resp.status_code in (400, 401, 403):
                    raise ValueError(f"Gemini API key error (status {resp.status_code}): {err}")
                if resp.status_code in _TRANSIENT_STATUS:
                    continue
                resp.raise_for_status()

            full, tokens = [], 0
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue
                try:
                    chunk = json.loads(line[6:].strip())
                except Exception:
                    continue
                cands = chunk.get("candidates") or []
                if cands:
                    for p in (cands[0].get("content") or {}).get("parts", []):
                        if p.get("text"):
                            full.append(p["text"])
                            on_delta(p["text"])
                if chunk.get("usageMetadata"):
                    tokens = chunk["usageMetadata"].get("totalTokenCount", tokens)
            text = "".join(full).strip()
            if not text:
                continue  # empty from this model — try the next
            return text, tokens or _estimate_tokens(prompt, system, text), model
        except requests.exceptions.Timeout:
            continue
        except ValueError:
            raise
        except Exception as e:
            log.error("Gemini stream exception", model=model, error=str(e), user=user_email)
            continue
    raise LLMTransientError("Gemini is temporarily unavailable. Please try again in a moment.")


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
             operation: str = "llm_call", on_delta=None) -> str:
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
        if on_delta is not None:   # real token streaming — same return contract
            if name == "openai":
                return _stream_chat_completions("openai", "https://api.openai.com/v1/chat/completions",
                                                OPENAI_MODEL, prompt, system, max_tokens, key, on_delta, user_email)
            elif name == "deepseek":
                return _stream_chat_completions("deepseek", "https://api.deepseek.com/v1/chat/completions",
                                                "deepseek-chat", prompt, system, max_tokens, key, on_delta, user_email)
            elif name == "gemini":
                return _stream_gemini(prompt, system, max_tokens, key, on_delta, user_email)
            raise ValueError(f"Unknown LLM provider: '{name}'")
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

# Categories that get an immediate, cheap prompt-level deflection — NO schema
# load, SQL generation, or tool loop. Edit this list to add/remove scopes:
# add a line to widen the guardrail, delete one to let those questions through
# to the normal (data-grounded) answer path. Keep it to genuinely off-topic
# asks — general business/economics reasoning is NOT here on purpose; those get
# a real analytical answer grounded in the merchant's data.
OUT_OF_SCOPE_SCOPES = [
    "writing, debugging, or explaining code / programming / scripts (any language), "
    "including SQL syntax help, regex, and general software-engineering how-tos",
]
OUT_OF_SCOPE_DEFLECTION = (
    "I'm your business data assistant, so I can't help with that — "
    "but ask me anything about your sales, products, customers, or trends and I'm all yours."
)

# Curated reply for "what can you do / what can I do / what is this" — a branded,
# concrete overview beats a re-generated LLM one-liner. .format(app=…, provider=…).
CAPABILITIES_MESSAGE = (
    "I'm {app}, your business analyst. I read your own {provider} data and answer in plain language. "
    "I can:\n"
    "- **Track performance** — sales, revenue, taxes, and profit for any period\n"
    "- **Rank things** — best/worst products, top customers, busiest days or hours\n"
    "- **Spot trends** — how sales are moving and what's driving the change\n"
    "- **Forecast** — a simple estimate of where a metric is heading\n"
    "- **Advise** — grounded suggestions to grow sales, based on your actual numbers\n\n"
    "Try: *\"top 5 products last month\"*, *\"how are sales trending?\"*, "
    "*\"what should I do to increase revenue?\"*"
)


def classify_question(
    question: str, table_names: str,
    llm: str, api_key: str, user_email: str,
    # Deliberately generic, never a brand name: a brand-named default
    # (this used to say "DataMind") reaches a merchant of a DIFFERENT
    # brand the moment a caller forgets to pass theirs. Degrading to an
    # unbranded phrase is survivable; naming the wrong company is not.
    app_name: str = "your AI assistant",
    conversation_history: str = "",
    language_hint: str = "",
    smart_answers: bool = False,
    business_knowledge: bool = False,
    answer_everything: bool = False,
) -> dict:
    """
    Classify the user question to determine handling strategy.
    Returns a dict with 'type' and type-specific fields.
    Falls back to {"type": "data_query"} if classification fails.

    business_knowledge (D2): when True, two extra categories are offered —
    "business_knowledge" (answerable from general retail/business knowledge, no
    merchant data needed) and "hybrid" (knowledge the merchant's own numbers
    would enrich) — and out_of_scope is narrowed to coding/unrelated topics only.
    When False the prompt and the accepted-type set are unchanged.
    """
    if smart_answers:
        scope_lines = "".join(f"  - {s}\n" for s in OUT_OF_SCOPE_SCOPES)
        out_of_scope_block = (
            '{"type":"out_of_scope","response":"..."} — the question is NOT about the '
            "merchant's own business data and falls into one of these off-topic scopes:\n"
            f"{scope_lines}"
            "Return a short, friendly one-line deflection as the response. "
            "IMPORTANT: business, sales, economics, and strategy questions are NOT out_of_scope even when "
            "they invoke general knowledge — e.g. 'how could the world economy affect my sales', "
            "'what should I do to grow', 'is now a good time to raise prices' are all data_query (the "
            "assistant answers them analytically using the merchant's own numbers plus general reasoning).\n"
            + (
                "out_of_scope means ONLY coding/programming/SQL-syntax help and topics with no business "
                "bearing at all. Retail, sales, pricing, stock, staffing, customers, accounting concepts, "
                "and the product itself are ALL in scope — definitions and how-to questions about them are "
                "business_knowledge, NEVER out_of_scope.\n"
                if business_knowledge else ""
            )
        )
        unsupported_block = (
            '{"type":"unsupported_query","response":"..."} — ONLY when the answer needs external data the '
            "database simply does not contain: competitor data, weather, or city-wide/industry market data. "
            "Do NOT use this for advice ('what should I do', 'how do I increase sales'), for simple "
            "forecasting/trend questions ('what will next month look like', 'am I trending up'), or for any "
            "question with a time filter — those are all data_query. "
            "Respond by explaining what CAN be shown instead.\n"
        )
        clarification_block = (
            '{"type":"clarification_needed","clarification":"..."} — use ONLY when the metric itself is '
            "missing or ambiguous (e.g. 'show me the good ones', 'how are things') so no sensible SQL can be "
            "written. Do NOT ask for a time period — a data question with no explicit period is still a "
            "data_query; a sensible default window is applied automatically. "
        )
    else:
        out_of_scope_block = ""
        unsupported_block = (
            '{"type":"unsupported_query","response":"..."} — ONLY use this when the question requires data that '
            "genuinely cannot come from the database: future predictions (next month/next year), "
            "external market data, competitor data, weather, or city-wide/industry trends. "
            "NEVER use this for questions with time filters like 'this month', 'this week', 'today', "
            "'last 30 days', 'so far this year' — those are valid data_query questions that filter "
            "existing records by date. "
            "Respond helpfully by explaining what CAN be shown instead "
            "(e.g. 'I can't predict next month, but I can show you historical top sellers by month — would that help?').\n"
        )
        clarification_block = (
            '{"type":"clarification_needed","clarification":"..."} — the question is about data but too vague to answer; '
            "ask ONE specific clarifying question. "
        )

    capabilities_block = (
        'If the user asks what you can do / how you can help / what this tool is / "what can I do" — '
        'return {"type":"conversational","subtype":"capabilities"} (leave "response" out; a curated '
        "answer is filled in).\n"
    ) if smart_answers else ""

    # D2: routes for retail/business knowledge questions the assistant should
    # answer, not refuse. hybrid = the same but the merchant's own numbers would
    # make the answer materially better.
    knowledge_block = (
        '{"type":"business_knowledge"} — a definition, formula, concept, or how-to question '
        "answerable from general retail/business knowledge WITHOUT needing the merchant's data "
        "(e.g. 'what is the difference between net and gross sales', 'define average order value', "
        "'what is a POS system', 'what is debt in sales', 'how do I calculate the number of sales per day').\n"
        '{"type":"hybrid"} — a knowledge/definition question where the merchant\'s OWN figures would '
        "make the answer materially better (e.g. 'what’s my average order value and is it good', "
        "'explain my net vs gross sales'). The assistant will explain the concept AND ground it in their numbers.\n"
        "Few-shot: 'explain the difference between net sales and gross sales' -> business_knowledge; "
        "'define average order value' -> business_knowledge; 'what is a POS system' -> business_knowledge; "
        "'what is debt in sales' -> business_knowledge; 'how to calculate the number of sales per day' -> "
        "business_knowledge; 'write me a python script to sum a column' -> out_of_scope.\n"
    ) if business_knowledge else ""

    # T1 (PLAN_10): scope -> safety inversion. Default to answering; refuse only
    # for genuine harm ('unsafe') and politely decline programming help ('coding').
    # Supersedes and widens the business_knowledge route above. When on, the
    # topic-based out_of_scope / unsupported options are removed entirely below.
    answer_everything_block = (
        '{"type":"knowledge"} — a general-knowledge, definition, concept, or "how does X work" question '
        "answerable from your own knowledge WITHOUT the merchant's data (e.g. 'difference between net and "
        "gross sales', 'what is a POS system', 'what is a discount', 'who won the cricket'). Answer it.\n"
        '{"type":"advisory"} — strategy, "what should I do", "is X a good idea", "how do I grow" — answer '
        "with real reasoning, grounded in the merchant's own figures where relevant (e.g. 'how do I grow my "
        "sales', 'is now a good time to raise prices', 'should I incorporate my business').\n"
        '{"type":"unsafe","response":"..."} — ONLY genuine harm: malware/exploits/hacking, weapons or '
        "explosives, other illicit how-to, or anything sexualising minors. Give a brief plain refusal.\n"
        '{"type":"coding","response":"..."} — programming, SQL, regex or software-engineering help. Politely '
        "decline — that genuinely isn't this product. A business question that merely mentions numbers is "
        "NOT coding.\n"
        "SCOPE PRINCIPLE: default to ANSWERING. NEVER refuse a question just because it isn't about the "
        "merchant's data. Definitions, general knowledge, current events, opinions-with-caveats, strategy "
        "and advice are ALL answerable (knowledge or advisory). Legal/financial/medical/tax questions are "
        "answered too (a short caveat is added downstream), never refused. The ONLY refusal is 'unsafe'; "
        "the ONLY decline is 'coding'.\n"
        "Few-shot: 'difference between net and gross sales' -> knowledge; 'what is a POS system' -> "
        "knowledge; 'what is a discount' -> knowledge; 'who won the cricket' -> knowledge; 'how do I grow "
        "my sales' -> advisory; 'is now a good time to raise prices' -> advisory; 'write me a SQL query' -> "
        "coding; 'how do I make a weapon' -> unsafe.\n"
    ) if answer_everything else ""
    if answer_everything:
        # Inversion: no topic refusals. Harm -> unsafe, programming -> coding.
        out_of_scope_block = ""
        unsupported_block = ""
        knowledge_block = ""   # superseded by the wider knowledge/advisory split

    if smart_answers:
        data_query_block = (
            '{"type":"data_query","intent":"lookup|advice|forecast|trend"} — question about data that exists '
            "in the database. Set intent: "
            '"advice" when the user asks what to DO / how to grow / a strategy (marketing, pricing, promotions) — '
            "the data is background context, not the answer; "
            '"forecast" for a prediction / what happens next; '
            '"trend" for how a metric is moving over time; '
            '"lookup" otherwise (a specific number, list, or ranking).\n'
        )
    else:
        data_query_block = '{"type":"data_query"} — question about data that exists in the database\n'

    system = (
        "You are a data assistant classifier. Respond ONLY with valid JSON — no markdown, no explanation.\n"
        "Classify the question into exactly one type:\n"

        + data_query_block

        + '{"type":"multi_step","sub_questions":["q1","q2"]} — clearly contains 2+ separate data queries; '
        "only use this when two or more genuinely distinct SQL queries are needed\n"

        + out_of_scope_block
        + unsupported_block
        + knowledge_block
        + answer_everything_block

        + '{"type":"conversational","response":"..."} — greeting, small talk, thank-you, or a request to '
        "modify/delete/create data (INSERT/UPDATE/DELETE/DROP/TRUNCATE/ALTER). "
        f"For harmful/destructive requests respond with a polite refusal as {app_name}. "
        "Also use this type when: "
        "(1) the user asks what data, tables, or information is available ('what data do you have', 'what can you show me', "
        '\'what tables are there\') — return {"type":"conversational","subtype":"capabilities"} (a curated '
        "business-language overview is filled in). NEVER list internal table names, column names, or SQL to the user; "
        "(2) the user asks about their own account details (country, currency, timezone) OR the current date/time — "
        "answer directly from the context provided in these instructions, do NOT deflect or say 'I'm a data assistant'; "
        "(3) the user asks what they asked earlier or to summarise the conversation — answer from the conversation "
        "history if it is provided; if there is no history, say plainly that each chat starts fresh, and NEVER claim "
        "you cannot recall when history is available; "
        f"for all other conversational cases respond as {app_name}, a friendly AI data assistant.\n"

        + capabilities_block
        + clarification_block
        + "IMPORTANT RULE: if the conversation history shows the last assistant message was already a clarification "
        "question and the user has now given ANY response (even a single word like 'sales', 'any', a category name, "
        "or a number), do NOT return clarification_needed again — instead reconstruct the full original intent from "
        "history combined with the user's answer, and return data_query.\n"

        "CRITICAL FOLLOW-UP RULE: if the conversation history shows the assistant's last message offered an "
        "alternative or asked if the user would like to see something (e.g. 'I can show you X — would that help?', "
        "'shall I show historical data instead?') and the user responds with acceptance (e.g. 'yes', 'do it', "
        "'sure', 'go ahead', 'show me', 'yeah', 'yep', 'ok'), treat this as a data_query — reconstruct the full "
        "data request from the conversation history and return data_query. Never return unsupported_query for "
        "a user acceptance of an alternative the assistant itself offered.\n"

        "DEFLECTION RULE: whenever you write a 'response' that says you can't do something (an off-topic "
        "deflection, an unsupported-data reply, or a conversational refusal), it MUST have two parts: (a) what "
        "you can't do, in one short clause, and (b) one concrete thing you CAN do right now, phrased in terms of "
        "the merchant's own sales, products, customers, or trends. Never close the door without offering a way "
        "forward.\n"

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
        _allowed = ["data_query", "multi_step", "conversational", "clarification_needed",
                    "unsupported_query", "out_of_scope"]
        if business_knowledge:
            _allowed += ["business_knowledge", "hybrid"]
        if answer_everything:
            _allowed += ["knowledge", "advisory", "unsafe", "coding"]
        if result.get("type") not in _allowed:
            return {"type": "data_query"}
        return result
    except Exception as _e:
        log.debug("Question classification failed, treating as data_query", error=str(_e))
        return {"type": "data_query"}


# ── Follow-up rewriting (D1) ──────────────────────────────────────────────────
# A dedicated, cheap step that runs BEFORE classification: rewrite a short
# follow-up ("for this week", "then fried rice?", "the latest one") into a
# standalone question using the last few turns, so everything downstream sees a
# complete question. This is standard conversational-search practice — one call
# cannot both resolve a reference and pick a category, which is why bare phrases
# were being bounced back as "please specify". See PLAN_09 S1.

_REWRITE_SYSTEM = (
    "You rewrite a user's latest chat message into a STANDALONE question, using the "
    "conversation so far. You do not answer it. Reply with strict JSON only, no markdown.\n"
    'Output: {"standalone": "<rewritten question>", "resolved": true|false, '
    '"carried": ["metric"|"period"|"shop"|"product"|"filter"], "changed": true|false}\n'
    "Rules:\n"
    "- Carry forward the metric, period, shop, product and filters from the previous turn "
    "UNLESS the new message overrides them.\n"
    "- Resolve pronouns and ellipsis ('that', 'those', 'the latest one', 'then fried rice?', "
    "'for this week', 'payment type') to the concrete thing they refer to in the history.\n"
    "- If the message is already a complete standalone question, return it UNCHANGED with "
    "changed=false.\n"
    "- Never invent a metric, period, product or filter that appears nowhere in the history "
    "or the new message.\n"
    "- Set resolved=false ONLY when the history genuinely contains no referent to resolve "
    "against — that is the only case where the assistant may ask for clarification.\n"
    "- 'carried' lists which of metric/period/shop/product/filter you pulled from earlier turns."
)


def rewrite_followup(question: str, conversation_history: str,
                     llm: str, api_key: str, user_email: str) -> dict:
    """Rewrite a follow-up utterance into a standalone question using recent turns.

    Returns {"standalone": str, "resolved": bool, "carried": list[str], "changed": bool}.
    Never raises — on ANY failure (or empty history) it returns the original question
    unchanged with resolved=True/changed=False, i.e. exactly today's behaviour.
    """
    fallback = {"standalone": question, "resolved": True, "carried": [], "changed": False}
    if not conversation_history or not conversation_history.strip():
        return fallback
    prompt = (
        f"Conversation so far:\n{conversation_history}\n\n"
        f"New message: {question}\n\nJSON:"
    )
    try:
        raw = call_llm(prompt, _REWRITE_SYSTEM, llm, max_tokens=200,
                       api_key=api_key, user_email=user_email, operation="rewrite").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()
        result = json.loads(raw)
        standalone = (result.get("standalone") or "").strip() or question
        return {
            "standalone": standalone,
            "resolved": bool(result.get("resolved", True)),
            "carried": result.get("carried") if isinstance(result.get("carried"), list) else [],
            "changed": bool(result.get("changed", standalone.strip() != question.strip())),
        }
    except Exception as _e:
        log.debug("Follow-up rewrite failed, using original question", error=str(_e))
        return fallback


# Heuristic markers that an assistant turn was a clarification request (not an
# answer). Leans toward True on purpose: the only consumer is the "never two
# clarifications in a row" guard, where a false positive means we answer instead
# of re-asking — the safe direction.
_CLARIFY_MARKERS = (
    "please specify", "could you", "can you tell me", "which ", "what specific",
    "provide more detail", "more details about", "clarif", "did you mean",
    "what would you like", "let me know which",
)


def last_assistant_was_clarification(conversation_history: str) -> bool:
    """True if the most recent assistant turn in the formatted history looks like
    a clarification question. Pure string inspection — no DB round-trip."""
    if not conversation_history:
        return False
    last = ""
    for line in conversation_history.splitlines():
        if line.startswith("Assistant:"):
            last = line[len("Assistant:"):].strip().lower()
    if not last or "?" not in last:
        return False
    return any(m in last for m in _CLARIFY_MARKERS)


# ── Answer sanitiser (D3) ─────────────────────────────────────────────────────
# Exit guard: no internal identifier (prefixed table names, SQL, MCP tool names,
# underscored report slugs) may reach a merchant. Pure function — runs on every
# outgoing answer at no cost. See PLAN_09 S2. Note: single plain words that
# happen to be report ids ('receipts', 'taxes', 'refunds', 'charges', 'shifts')
# are legitimate business language and are deliberately NOT treated as internal.

_MCP_TOOL_NAMES = (
    "get_schema", "run_select_query", "get_report_metrics", "get_report_detail",
    "list_reports", "get_sample_rows", "get_date_range",
)

_INTERNAL_TOKEN_RES = [
    re.compile(r"\bsp_[a-z][a-z_]*\b"),
    re.compile(r"\bly_[a-z][a-z_]*\b"),
    re.compile(r"\b(?:" + "|".join(_MCP_TOOL_NAMES) + r")\b"),
]
# A SELECT … FROM anywhere in the answer (case-insensitive, bounded span).
_SQL_RE = re.compile(r"\bSELECT\b[\s\S]{0,600}?\bFROM\b", re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


def _internal_report_slugs() -> tuple:
    """Report ids that read as internal identifiers — underscored slugs only."""
    try:
        from report_cache.registry import REPORTS
        return tuple(sorted((r for r in REPORTS if "_" in r), key=len, reverse=True))
    except Exception:
        return ()


def _internal_hits(text: str, slugs: tuple) -> list:
    hits: list = []
    for rx in _INTERNAL_TOKEN_RES:
        hits += rx.findall(text)
    if _SQL_RE.search(text):
        hits.append("SQL")
    low = text.lower()
    for s in slugs:
        if re.search(r"\b" + re.escape(s) + r"\b", low):
            hits.append(s)
    return hits


# Business labels for the internal names most likely to slip out. Substituting
# beats deleting: dropping a whole sentence was survivable when the answer was a
# regenerated table narrative, but in the agent flow the model's own text IS the
# product, and cutting a sentence out of a good analytical answer mangles it.
_BUSINESS_LABELS = {
    "sp_receipt_line_items": "your sales lines",
    "ly_receipt_line_items": "your sales lines",
    "sp_payment_types": "your payment methods",
    "sp_receipts": "your receipts",
    "ly_receipts": "your receipts",
    "sp_products": "your products",
    "ly_products": "your products",
    "sp_categories": "your categories",
    "ly_categories": "your categories",
    "sp_customers": "your customers",
    "ly_customers": "your customers",
    "sp_shops": "your shops",
    "sales_by_products": "your product sales",
    "sales_by_category": "your category sales",
    "sales_summary": "your sales summary",
    "credit_notes": "your credit notes",
}
# Longest first so sp_receipt_line_items is not eaten by sp_receipts.
_LABEL_RE = re.compile(
    r"\b(" + "|".join(sorted((re.escape(k) for k in _BUSINESS_LABELS),
                             key=len, reverse=True)) + r")\b",
    re.IGNORECASE)


def sanitise_answer(text, fallback: str = ""):
    """Strip internal identifiers from a user-facing answer.

    Returns (cleaned_text, found). Substitutes every known internal name with
    its business label first, then drops any sentence still carrying one; if
    that empties the answer entirely, returns `fallback` (intended to be the
    capabilities message). Pure — no LLM call, no DB.

    A hit means the PROMPT is leaking and the prompt is what should be fixed —
    callers log every hit for exactly that reason.
    """
    if not text or not isinstance(text, str):
        return text, []
    slugs = _internal_report_slugs()
    found = _internal_hits(text, slugs)
    if not found:
        return text, []
    text = _LABEL_RE.sub(lambda m: _BUSINESS_LABELS[m.group(0).lower()], text)
    if not _internal_hits(text, slugs):
        return text, found            # substitution alone made it clean
    kept = [s for s in _SENTENCE_SPLIT_RE.split(text)
            if s.strip() and not _internal_hits(s, slugs)]
    cleaned = " ".join(p.strip() for p in kept).strip()
    if not cleaned:
        return fallback, found
    return cleaned, found


# ── Safety gate (T4 / PLAN_10) ────────────────────────────────────────────────
# A code-level safety control, NOT prompt-dependent (prompts drift). The
# classifier's 'unsafe'/'coding' categories are the primary router; this is the
# deterministic backstop that refuses genuine harm even if the classifier misses,
# and flags legal/financial/medical questions for a one-line advice caveat.

SAFE_REFUSAL = (
    "I can't help with that one. But I'm glad to help with your business — your sales, "
    "products, customers, pricing, or anything you'd like to understand or improve."
)
CODING_DECLINE = (
    "I don't write code or SQL — that's outside what I do here. But ask me anything about "
    "your business — sales, products, customers, trends — and I'm all yours."
)
_ADVICE_CAVEAT = "This is general information, not professional advice."

# Genuine-harm patterns. Deliberately require an intent verb NEAR a harmful object
# so ordinary business phrasing ("knife supplier", "gun shop sales") does not trip.
# ponytail: keyword heuristic; if it proves leaky, swap for a dedicated model check.
_HARM_RES = [
    re.compile(r"\b(how\s+to|how\s+do\s+i|make|build|create|synthesi[sz]e|manufacture|assemble)\b"
               r"[^.?!\n]{0,40}\b(bomb|explosive|weapon|firearm|silencer|nerve\s+agent|nerve\s+gas|"
               r"meth|methamphetamine|cocaine|heroin|fentanyl|poison)\b", re.I),
    re.compile(r"\b(write|create|build|code|generate|develop|make)\b[^.?!\n]{0,40}\b"
               r"(malware|ransomware|keylogger|botnet|rootkit|backdoor|spyware|computer\s+virus)\b", re.I),
    re.compile(r"\b(hack|ddos|sql\s*inject|phish|brute[-\s]?force|bypass)\b[^.?!\n]{0,40}"
               r"\b(account|password|login|credentials|system|server|firewall|database|wifi)\b", re.I),
    re.compile(r"\b(child|children|minor|underage|infant|kid)s?\b[^.?!\n]{0,25}"
               r"\b(porn|sexual|nude|naked|explicit)\b", re.I),
    re.compile(r"\b(porn|sexual|nude|naked|explicit)\b[^.?!\n]{0,25}"
               r"\b(child|children|minor|underage|infant|kid)s?\b", re.I),
]
# Domains that get an answer PLUS a caveat (never a refusal). Applied ONLY on the
# knowledge/advisory answer paths, never on data lookups — so "how much tax did I
# collect" (a data query) is not caveated, but "how should I handle my taxes" is.
# Leading \b anchors to a word start; no trailing \b so stems like "incorporat",
# "liabilit", "regulat", "diagnos", "bankruptc" match their inflections.
_ADVICE_RES = [
    re.compile(r"\b(incorporat|llc\b|sole\s+proprietor|register\s+(my\s+)?business|lawsuit|sue\b|"
               r"legal|contract|liabilit|comply|complian|regulat)", re.I),
    re.compile(r"\b(diagnos|medical|medicine|prescri|symptom|disease|health\s+condition)", re.I),
    re.compile(r"\b(invest(?!igat)|stock\b|stocks\b|shares\b|loan|mortgage|refinanc|bankruptc|audit\s+risk)", re.I),
]


def safety_gate(question: str) -> dict:
    """Classify the REQUEST. Returns {"action": "refuse"|"caveat"|"allow",
    "refusal": str}. Pure, no LLM, no DB — a deterministic control."""
    q = question or ""
    for rx in _HARM_RES:
        if rx.search(q):
            return {"action": "refuse", "refusal": SAFE_REFUSAL}
    for rx in _ADVICE_RES:
        if rx.search(q):
            return {"action": "caveat", "refusal": ""}
    return {"action": "allow", "refusal": ""}


def add_advice_caveat(text: str) -> str:
    """Append the one-line professional-advice caveat once (no-op if already present)."""
    if not text:
        return text
    if "not professional advice" in text.lower():
        return text
    return f"{text.rstrip()}\n\n*{_ADVICE_CAVEAT}*"


# Matches a literal '$' immediately adjacent to a number, in either order:
# "$1,234.56" or "1234.56$". Captures the numeric/punctuation part so it can
# be re-emitted with the correct currency symbol substituted in place of '$'.
_WRONG_CURRENCY_RE = re.compile(
    r'(?<![A-Za-z])\$\s?(\d[\d,]*\.?\d*)|(\d[\d,]*\.?\d*)\s?\$(?![A-Za-z])'
)


def fix_currency_symbol(text: Optional[str], correct_currency: str) -> Optional[str]:
    """
    Deterministic post-processing for LLM-generated narrative text: replaces any
    literal '$' adjacent to a number with the tenant's correct currency symbol.

    This exists because LLMs sometimes default to '$' out of training bias when
    writing monetary amounts in free-form prose, even with explicit system-prompt
    instructions not to — system-prompt instructions alone are not reliable
    enough (confirmed via production transcripts showing '$' used for LKR/PHP/ZAR
    tenants despite correct currency being injected into the prompt).

    Only touches '$' immediately adjacent to digits — does not attempt
    word-level replacement (e.g. "dollars", "USD"), which is unreliable and
    prone to false positives, and doesn't match the actual observed failure
    mode (literal '$' glyphs in formatted amounts).

    No-op if correct_currency is '$', empty, or text is empty/None.
    """
    if not text or not correct_currency or correct_currency == "$":
        return text

    def _replace(m):
        amount = m.group(1) or m.group(2)
        return f"{correct_currency}{amount}"

    return _WRONG_CURRENCY_RE.sub(_replace, text)


def synthesize_multi_step_answer(
    original_question: str, step_results: list,
    llm: str, api_key: str, user_email: str,
    currency: str = "$",
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
        "the original question. Use specific numbers from the data. No preamble. "
        f"The user's currency is '{currency}'. When writing any monetary amount, use "
        f"'{currency}' as the currency symbol — never assume USD or '$'."
    )
    prompt = f"Question: {original_question}\n\n" + "\n\n".join(parts) + "\n\nAnswer:"
    try:
        result = call_llm(prompt, system, llm, max_tokens=600, api_key=api_key, user_email=user_email, operation="synthesize")
        return fix_currency_symbol(result, currency) if result else result
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
            f"Never SELECT any column named exactly 'id' or ending in '_id' "
            f"(e.g. customer_id, shop_id, product_id, payment_type_id, receipt_id, variant_id) "
            f"unless the user explicitly asks for an identifier or ID. "
            f"These are internal database keys — users cannot do anything with them. "
            f"Only SELECT columns that have direct business meaning."
        )
    else:
        tenant_hint = ""
    # Conversation history is prepended so the LLM understands follow-up
    # questions ("explain this", "drill down", "compare to last month") without
    # generating a broad unrelated SQL query.
    history_section = (
        f"[Previous conversation — use this to understand follow-up questions. "
        f"When a '[Previous SQL: ...]' line is present, treat it as the authoritative "
        f"prior query: preserve its date range/interval/filters unless the user's new "
        f"question explicitly asks to change them. Do not silently narrow or widen a "
        f"previously-established time window when the user is only asking to change "
        f"presentation (e.g. chart type, grouping) rather than scope.]\n"
        f"{conversation_history}\n\n"
        if conversation_history else ""
    )
    system = (
        history_section +
        "You are an expert MySQL query writer. "
        "Given a database schema (with foreign key relationships) and a plain English question, "
        "write a valid MySQL SELECT query that may JOIN multiple tables as needed. "
        "Return ONLY the raw SQL — no markdown, no backticks, no explanation. "
        "Never use DROP, DELETE, INSERT, UPDATE, or any mutating statement. "
        "Always assign a short alias to every table in the FROM and JOIN clauses "
        "(e.g. FROM sp_receipts r JOIN sp_receipt_line_items li ...) and prefix "
        "every column reference with its alias — never use bare column names when "
        "multiple tables are involved, as this causes ambiguous column errors. "
        "IMPORTANT — ambiguous financial terms: when the question uses a vague word like "
        "'price', 'cost', 'value', or 'amount' and the relevant table has multiple "
        "financial columns (e.g. price, cost, selling_price, unit_price, retail_price), "
        "SELECT all of them rather than guessing which one the user means. "
        "This lets the user see the full picture instead of a potentially misleading single value."
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

    # Determine whether there is any real data to report on. A brand-new account
    # (no sync history) yields empty/zero KPIs and sections with no rows. Without
    # this guard the model invents plausible-looking figures to satisfy the
    # "write N paragraphs" instruction below.
    def _is_meaningful(v) -> bool:
        if v is None:
            return False
        if isinstance(v, (int, float)):
            return v != 0
        s = str(v).strip()
        return s not in ("", "0", "0.0", "None", "null")

    has_kpis = any(_is_meaningful(v) for v in kpis.values())
    total_rows = sum(len(d.get("data", []) or []) for d in section_data.values())
    has_data = has_kpis or total_rows > 0

    if not has_data:
        log.info("Report: insufficient data — skipping LLM narrative",
                 title=title, llm=llm)
        return (
            f"# {title}\n\n"
            "There isn't enough data yet to generate this report. "
            "Once your sales data has finished syncing and a few transactions "
            "have been recorded, run the report again to see a full analysis.\n\n"
            "_No figures are shown here because none are available — this report "
            "will never display estimated or sample numbers._"
        )

    sections_text = ""
    for sid, data in section_data.items():
        sections_text += f"\n\n## {data.get('title', sid)}\n"
        cols = data.get("columns", [])
        rows = data.get("data", [])[:5]
        if rows:
            sections_text += f"Columns: {', '.join(cols)}\n"
            for row in rows:
                sections_text += f"  {row}\n"
        else:
            sections_text += "(no data available for this section)\n"

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
        "Use ONLY the concrete numbers provided in the data below. Be direct and actionable. "
        "CRITICAL: Never invent, estimate, extrapolate, or illustrate with example figures. "
        "Every number, date, and percentage in your report must come directly from the "
        "supplied KPIs or data sections. If a section is marked '(no data available)', "
        "state plainly that there is not yet enough data for it — do NOT make up sample "
        f"values to fill the gap. {_profile}"
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