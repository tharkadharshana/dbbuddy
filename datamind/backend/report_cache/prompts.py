"""
report_cache/prompts.py
========================
Persona system prompt + general-knowledge answerer (PLAN 05 Step 4, doc 07
Part 3.2/3.3). The persona is deliberately SHORT: it defines who the assistant
is and what's in scope, and injects the tenant's profile + currency. It carries
NO correctness rules (VOID/timezone/tenant filters) — those live in the report
tools and the Python safety layer now, never in prompt text.
"""

from typing import Optional

from llm import call_llm, fix_currency_symbol
from logger import get_logger

log = get_logger(__name__)


def tenant_profile_summary(tenant_profile: Optional[dict]) -> str:
    """One short line describing the merchant, from PLAN 02's synced profile."""
    if not tenant_profile:
        return ""
    parts = []
    name = tenant_profile.get("master_username")
    if name:
        parts.append(f"business '{name}'")
    shops = tenant_profile.get("shops")
    if shops:
        parts.append(f"{len(shops)} shop(s)")
    cur = tenant_profile.get("currency_symbol") or tenant_profile.get("currency")
    if cur:
        parts.append(f"currency {cur}")
    return ("Merchant context: " + ", ".join(parts) + ".") if parts else ""


def build_persona_system_prompt(tenant_profile: Optional[dict], currency: str) -> str:
    """The light persona (doc 07 Part 3.2). Lets users ask business / forecast /
    general questions freely; softly steers away from unrelated coding/search."""
    summary = tenant_profile_summary(tenant_profile)
    return (
        "You are the SalesPlay AI business analyst. You help this merchant understand their "
        "sales, customers, products, and trends, and you answer general business and market "
        "questions to give useful context. You can forecast and advise. You are not a general "
        "coding assistant or a web search engine — if asked something outside business and "
        "analytics, briefly and politely steer back to how you can help with their business. "
        f"Answer in the same language the user wrote in. The currency is '{currency}' — use it "
        "for any monetary amount, never assume USD or '$'. "
        + (summary + " " if summary else "")
        + "Keep answers concise and practical."
    )


def persona_answer(question: str, tenant_profile: Optional[dict], currency: str,
                   llm: str, api_key: str, user_email: str,
                   conversation_history: str = "") -> str:
    """Answer a general-knowledge / insight question with NO data tools (doc 07
    Part 3.2) — the model's own reasoning under the persona. Currency-corrected."""
    system = build_persona_system_prompt(tenant_profile, currency)
    prompt = question
    if conversation_history:
        prompt = (f"[Previous conversation]\n{conversation_history}\n\nQuestion: {question}")
    text = call_llm(prompt, system, llm, max_tokens=700, api_key=api_key,
                    user_email=user_email, operation="persona")
    return fix_currency_symbol(text, currency)
