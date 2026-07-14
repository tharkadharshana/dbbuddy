"""
report_cache/router.py
=======================
The lightweight question router (PLAN 05 Step 4, doc 07 Part 3.1). Replaces the
giant classify_question prompt for SalesPlay tenants with a tiny decision: what
KIND of question is this? No SQL rules, no correctness rules — those live in the
report tools now.

Types:
  business_data   — about the merchant's own numbers (sales, products, customers…)
  forecast        — asks about the future (predict/forecast/next month)
  insight         — advice/why/what-should-I-do that leans on general reasoning
  general_knowledge — a business question not about THIS merchant's data
  conversational  — greeting / small talk / thanks
  clarification   — too vague to answer

Uses the existing call_llm JSON idiom (same as classify_question) — the repo
has no structured-output helper, and prompt-JSON with a safe default is the
established, reliable pattern here. Never raises; defaults to business_data
(the most common case, and the one with a live fallback in main.py).
"""

import json
import re

from llm import call_llm
from logger import get_logger

log = get_logger(__name__)

_TYPES = ("business_data", "forecast", "insight", "general_knowledge",
          "conversational", "clarification")
_DEFAULT = "business_data"

_SYSTEM = (
    "You route a merchant's chat message to one handler. Respond ONLY with valid JSON: "
    '{"type": "<one of: business_data | forecast | insight | general_knowledge | '
    'conversational | clarification>"}. No markdown, no explanation.\n'
    "- business_data: about THIS merchant's own numbers — sales, revenue, profit, products, "
    "categories, customers, receipts, refunds, taxes, charges, for any time period (today, "
    "last month, this quarter, a date range).\n"
    "- forecast: asks to predict/forecast/project the future (next month's sales, expected demand).\n"
    "- insight: asks for advice, a recommendation, or WHY something happened, needing reasoning on "
    "top of data (why did Tuesday drop, should I stock more X, how do I grow margin).\n"
    "- general_knowledge: a business/market question NOT about this merchant's own data "
    "(what's a good gross margin for a cafe, what is FIFO).\n"
    "- conversational: greeting, thanks, small talk.\n"
    "- clarification: about their data but too vague to act on (no metric/period discernible).\n"
    "Use conversation history to resolve follow-ups and pronouns."
)


def route(question: str, conversation_history: str, tenant_profile: dict,
          llm: str, api_key: str, user_email: str) -> dict:
    """Return {"type": ...}. Best-effort; defaults to business_data on any failure."""
    history_block = f"\nConversation so far:\n{conversation_history}\n" if conversation_history else ""
    prompt = f"{history_block}\nMessage: {question}"
    try:
        raw = call_llm(prompt, _SYSTEM, llm, max_tokens=60, api_key=api_key,
                       user_email=user_email, operation="route").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()
        result = json.loads(raw)
        qtype = result.get("type")
        if qtype not in _TYPES:
            log.debug("router: unexpected type, defaulting", got=qtype)
            return {"type": _DEFAULT}
        return {"type": qtype}
    except Exception as exc:
        log.debug("router: failed, defaulting to business_data", error=str(exc))
        return {"type": _DEFAULT}
