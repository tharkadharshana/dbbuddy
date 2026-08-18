"""
mcp_server/llm_tool_calling.py
===============================
Native function/tool-calling support for the 3 LLM providers. `llm.py`'s
`call_openai` / `call_gemini` / `call_deepseek` are plain text-completion
wrappers — none of them can send `tools` or read back a tool-call request,
which is what the MCP orchestrator loop (orchestrator.py) needs on every turn.

Reuses llm.py's key-pool/cooldown machinery (`_build_key_pool`, `_park_key`,
`_unpark_key`, `LLMTransientError`) instead of duplicating it, and charges
tokens via `billing.charge_ai_usage` exactly like `llm.call_llm` already does,
so this new path shows up in the existing token-usage/billing views.

Scope note: unlike `llm.call_llm`, this module does NOT fall back across
providers mid-loop (e.g. OpenAI -> Gemini) — switching providers mid
conversation would require re-translating the whole tool-calling transcript
into a different wire format on every hop, which is a lot of added
complexity for a first version. If every key for the requested provider is
exhausted, the call raises and orchestrator.py's caller (main.py) falls back
to the legacy one-shot `query_to_sql` pipeline instead — which still has full
cross-provider fallback via `llm.call_llm`.

Unified internal message shape used by orchestrator.py (translated to each
provider's native wire format just before the HTTP call):
  {"role": "system", "content": str}
  {"role": "user", "content": str}
  {"role": "assistant", "content": str | None, "tool_calls": [
      {"id": str, "name": str, "arguments": dict}, ...
  ]}
  {"role": "tool", "tool_call_id": str, "name": str,
   "content": str,     # stringified result, for OpenAI/DeepSeek
   "result": Any}       # native Python value, for Gemini's functionResponse
"""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests

from logger import get_logger
from llm import (
    _build_key_pool, _park_key, _unpark_key, LLMTransientError,
    _TRANSIENT_STATUS, _KEY_RL_COOLDOWN, _KEY_AUTH_COOLDOWN,
    OPENAI_MODEL, GEMINI_MODELS,
)

log = get_logger(__name__)


@dataclass
class ToolCallRequest:
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class ToolCallTurn:
    text: Optional[str]
    tool_calls: List[ToolCallRequest] = field(default_factory=list)


# ── Key-pool retry (single provider — no cross-provider fallback, see module docstring) ──

def _run_with_key_pool(provider: str, api_key: str, user_email: Optional[str],
                        operation: str, fn) -> ToolCallTurn:
    pool = _build_key_pool(provider, (api_key or "").strip())
    if not pool:
        raise ValueError(f"No {provider} API key available for MCP tool-calling.")
    last_error = None
    for key in pool:
        try:
            turn, tokens, model_id = fn(key)
            _unpark_key(provider, key)
            if user_email:
                try:
                    from billing import charge_ai_usage, invalidate_sub_cache
                    charge_ai_usage(user_email, tokens or 0, provider, model_id, operation)
                    invalidate_sub_cache(user_email)
                except Exception as _ce:
                    log.warning("charge_ai_usage failed silently", error=str(_ce))
            return turn
        except LLMTransientError as e:
            _park_key(provider, key, _KEY_RL_COOLDOWN)
            log.warning("MCP tool-call key rate-limited, cycling to next key", provider=provider, error=str(e))
            last_error = e
        except ValueError as e:
            _park_key(provider, key, _KEY_AUTH_COOLDOWN)
            log.warning("MCP tool-call key auth failed, cycling to next key", provider=provider, error=str(e))
            last_error = e
    raise last_error or ValueError(f"No usable {provider} key for MCP tool-calling.")


# ── Tool schema translation (MCP -> provider-native) ──────────────────────────

def _tools_to_openai_schema(tools) -> list:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.inputSchema or {"type": "object", "properties": {}},
            },
        }
        for t in tools
    ]


# Gemini's function-calling schema is a JSON-Schema subset. As of early 2026,
# `additionalProperties` is accepted for structured *output* but still causes
# a MALFORMED_FUNCTION_CALL error when present in a function *declaration*'s
# parameters — confirmed via Google's own issue tracker/forum, not guessed.
# `$schema`/`title`/`default` are similarly non-standard for this subset and
# are stripped defensively.
_GEMINI_UNSUPPORTED_SCHEMA_KEYS = {"additionalProperties", "$schema", "title", "default"}


def _sanitize_schema_for_gemini(schema):
    if isinstance(schema, dict):
        return {
            k: _sanitize_schema_for_gemini(v)
            for k, v in schema.items()
            if k not in _GEMINI_UNSUPPORTED_SCHEMA_KEYS
        }
    if isinstance(schema, list):
        return [_sanitize_schema_for_gemini(v) for v in schema]
    return schema


def _tools_to_gemini_schema(tools) -> list:
    return [
        {
            "name": t.name,
            "description": t.description or "",
            "parameters": _sanitize_schema_for_gemini(t.inputSchema or {"type": "object", "properties": {}}),
        }
        for t in tools
    ]


# ── Message translation (unified -> provider-native) ──────────────────────────

def _messages_to_openai(messages: List[dict]) -> list:
    out = []
    for m in messages:
        if m["role"] == "assistant" and m.get("tool_calls"):
            out.append({
                "role": "assistant",
                "content": m.get("content"),
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])},
                    }
                    for tc in m["tool_calls"]
                ],
            })
        elif m["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
        else:
            out.append({"role": m["role"], "content": m.get("content") or ""})
    return out


def _parse_openai_message(msg: dict) -> ToolCallTurn:
    raw_calls = msg.get("tool_calls") or []
    calls = []
    for tc in raw_calls:
        try:
            args = json.loads(tc.get("function", {}).get("arguments") or "{}")
        except Exception:
            args = {}
        calls.append(ToolCallRequest(id=tc["id"], name=tc["function"]["name"], arguments=args))
    return ToolCallTurn(text=msg.get("content"), tool_calls=calls)


def _messages_to_gemini(messages: List[dict]):
    """Returns (system_text, contents). Gemini's function-response turn uses
    role='function' (confirmed against Google's own REST function-calling
    examples — not the OpenAI-style 'tool' role)."""
    system_text = None
    contents = []
    for m in messages:
        role = m["role"]
        if role == "system":
            system_text = f"{system_text}\n\n{m['content']}" if system_text else m["content"]
        elif role == "user":
            contents.append({"role": "user", "parts": [{"text": m.get("content") or ""}]})
        elif role == "assistant":
            parts = []
            if m.get("content"):
                parts.append({"text": m["content"]})
            for tc in m.get("tool_calls") or []:
                parts.append({"functionCall": {"name": tc["name"], "args": tc["arguments"]}})
            contents.append({"role": "model", "parts": parts})
        elif role == "tool":
            contents.append({
                "role": "function",
                "parts": [{"functionResponse": {
                    "name": m["name"],
                    "response": {"name": m["name"], "content": m.get("result", m.get("content"))},
                }}],
            })
    return system_text, contents


def _parse_gemini_candidate(candidate: dict) -> ToolCallTurn:
    parts = candidate.get("content", {}).get("parts", [])
    text_parts = [p["text"] for p in parts if "text" in p]
    calls = []
    for i, p in enumerate(parts):
        fc = p.get("functionCall")
        if fc:
            calls.append(ToolCallRequest(id=f"gemini_call_{i}", name=fc["name"], arguments=fc.get("args") or {}))
    return ToolCallTurn(text="\n".join(text_parts) if text_parts else None, tool_calls=calls)


# ── Per-provider callers ───────────────────────────────────────────────────────

def call_openai_with_tools(messages: List[dict], tools, api_key: str = "",
                            user_email: Optional[str] = None, max_tokens: int = 1000,
                            operation: str = "mcp_tool_call") -> ToolCallTurn:
    def _do(key):
        url = "https://api.openai.com/v1/chat/completions"
        body = {
            "model": OPENAI_MODEL,
            "messages": _messages_to_openai(messages),
            "tools": _tools_to_openai_schema(tools),
            "tool_choice": "auto",
            "temperature": 0.2,
            "max_tokens": max_tokens,
        }
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=90)
        except requests.exceptions.Timeout:
            raise LLMTransientError("OpenAI timed out during MCP tool-calling.")
        if not resp.ok:
            if resp.status_code in (401, 403):
                raise ValueError(f"OpenAI API key invalid (status {resp.status_code}).")
            if resp.status_code in _TRANSIENT_STATUS:
                raise LLMTransientError(f"OpenAI transient error (status {resp.status_code}).")
            resp.raise_for_status()
        data = resp.json()
        msg = data["choices"][0]["message"]
        usage = data.get("usage", {})
        return _parse_openai_message(msg), usage.get("total_tokens", 0), data.get("model", body["model"])

    return _run_with_key_pool("openai", api_key, user_email, operation, _do)


def call_deepseek_with_tools(messages: List[dict], tools, api_key: str = "",
                              user_email: Optional[str] = None, max_tokens: int = 1000,
                              operation: str = "mcp_tool_call") -> ToolCallTurn:
    def _do(key):
        url = "https://api.deepseek.com/v1/chat/completions"
        body = {
            "model": "deepseek-chat",
            "messages": _messages_to_openai(messages),  # DeepSeek is OpenAI-compatible
            "tools": _tools_to_openai_schema(tools),
            "tool_choice": "auto",
            "temperature": 0.2,
            "max_tokens": max_tokens,
        }
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=90)
        except requests.exceptions.Timeout:
            raise LLMTransientError("DeepSeek timed out during MCP tool-calling.")
        if not resp.ok:
            if resp.status_code in (401, 403):
                raise ValueError(f"DeepSeek API key invalid (status {resp.status_code}).")
            if resp.status_code in _TRANSIENT_STATUS:
                raise LLMTransientError(f"DeepSeek transient error (status {resp.status_code}).")
            resp.raise_for_status()
        data = resp.json()
        msg = data["choices"][0]["message"]
        usage = data.get("usage", {})
        return _parse_openai_message(msg), usage.get("total_tokens", 0), data.get("model", body["model"])

    return _run_with_key_pool("deepseek", api_key, user_email, operation, _do)


_GEMINI_TOOL_MODELS = GEMINI_MODELS


def call_gemini_with_tools(messages: List[dict], tools, api_key: str = "",
                            user_email: Optional[str] = None, max_tokens: int = 1000,
                            operation: str = "mcp_tool_call") -> ToolCallTurn:
    def _do(key):
        system_text, contents = _messages_to_gemini(messages)
        body = {
            "contents": contents,
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": max_tokens},
            "tools": [{"functionDeclarations": _tools_to_gemini_schema(tools)}],
        }
        if system_text:
            body["systemInstruction"] = {"parts": [{"text": system_text}]}

        base = "https://generativelanguage.googleapis.com/v1beta/models"
        last_err = None
        for model in _GEMINI_TOOL_MODELS:
            url = f"{base}/{model}:generateContent?key={key}"
            try:
                resp = requests.post(url, json=body, timeout=90)
            except requests.exceptions.Timeout:
                last_err = LLMTransientError("Gemini timed out during MCP tool-calling.")
                continue
            if resp.status_code == 404:
                continue  # model not available for this key/region — try next
            if not resp.ok:
                if resp.status_code in (400, 401, 403):
                    raise ValueError(f"Gemini API key error (status {resp.status_code}): {resp.text[:200]}")
                if resp.status_code in _TRANSIENT_STATUS:
                    last_err = LLMTransientError(f"Gemini transient error (status {resp.status_code}).")
                    continue
                resp.raise_for_status()
            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                block_reason = data.get("promptFeedback", {}).get("blockReason", "unknown")
                raise ValueError(f"Gemini blocked the request: {block_reason}")
            usage = data.get("usageMetadata", {})
            return _parse_gemini_candidate(candidates[0]), usage.get("totalTokenCount", 0), model
        raise last_err or LLMTransientError("Gemini is temporarily unavailable for MCP tool-calling.")

    return _run_with_key_pool("gemini", api_key, user_email, operation, _do)


def call_with_tools(llm: str, messages: List[dict], tools, api_key: str = "",
                     user_email: Optional[str] = None, max_tokens: int = 1000,
                     operation: str = "mcp_tool_call") -> ToolCallTurn:
    """Dispatch to the requested provider's tool-calling implementation."""
    provider = (llm or "openai").lower().strip()
    if provider == "openai":
        return call_openai_with_tools(messages, tools, api_key, user_email, max_tokens, operation)
    if provider == "deepseek":
        return call_deepseek_with_tools(messages, tools, api_key, user_email, max_tokens, operation)
    if provider == "gemini":
        return call_gemini_with_tools(messages, tools, api_key, user_email, max_tokens, operation)
    raise ValueError(f"Unknown LLM provider for MCP tool-calling: '{llm}'")
