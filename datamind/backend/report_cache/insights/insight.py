"""
report_cache/insights/insight.py
==================================
Grounded business-suggestion synthesis (PLAN 06 Step 3). NOT a single tool call
— an orchestration: gather a small fixed "insight pack" of grounded facts from
the cache, load the tenant profile, then ask the LLM for advice that separates
data findings (real numbers) from general reasoning, with a numeric-provenance
check afterward (provenance.py).

The pack is cache-first and mostly token-free (growth + forecast come from
cached daily facts); the top-products lookup is best-effort (needs a live token
that the chat path may not have — skipped silently if unavailable, doc:
PLAN 02/04 token wrinkle). The whole pack is memoised per (tenant, day) so a
repeated "give me insights" is cheap (plan Step 5).
"""

from datetime import date, timedelta
from typing import Optional

from llm import call_llm, fix_currency_symbol
from logger import get_logger
from report_cache.insights import provenance
from report_cache.insights.forecast import forecast_metric
from report_cache.insights.prompts import build_insight_system_prompt
from report_cache.insights.trends import growth_summary
from report_cache.periods import month_bounds

log = get_logger(__name__)

_pack_cache: dict = {}   # ponytail: process-local {(tenant, isodate): pack}; fine for per-day memoisation


def _last_full_month():
    first_of_this = date.today().replace(day=1)
    return month_bounds((first_of_this - timedelta(days=1)))   # (first, last) of previous month


def _build_insight_pack(conn, tenant_id: str, token: Optional[str], tier: Optional[str]) -> dict:
    cache_key = (tenant_id, date.today().isoformat())
    if cache_key in _pack_cache:
        return _pack_cache[cache_key]

    facts: list = []
    numbers = set()
    table_columns: list = []
    table_data: list = []

    growth = growth_summary(conn, tenant_id, "net_sales", months=6, tier=tier)
    if growth.get("status") == "ok" and growth["months"]:
        facts.append("Monthly net sales (most recent last): " + "; ".join(
            f"{m['month']} = {m['value']}" + (f" ({m['mom_pct']}% MoM)" if m["mom_pct"] is not None else "")
            for m in growth["months"]))
        numbers |= provenance.collect_numbers(growth["months"])
        table_columns = ["month", "value", "mom_pct"]
        table_data = growth["months"]

    fc = forecast_metric(conn, tenant_id, "net_sales", horizon_days=30, token=token, tier=tier)
    if fc.get("status") == "ok":
        s = fc["summary"]
        facts.append(
            f"Forecast (next 30 days): average daily net sales ~{s['next_30_avg']}, "
            f"predicted change {s['predicted_growth_pct']}% vs last actual {s['last_actual']}.")
        numbers |= provenance.collect_numbers(s)

    # Best-effort grounded extra: top products last full month (needs a live token).
    try:
        from report_cache.answer import answer_metric_query
        first, last = _last_full_month()
        top = answer_metric_query(conn, tenant_id, "sales_by_products", None,
                                  first, last, "all", token, tier or "basic", top_n=5)
        if not top.get("refusal") and top.get("data"):
            facts.append("Top products last month: " + "; ".join(
                f"{r.get('name')} ({r.get('net_sale')})" for r in top["data"][:5]))
            numbers |= provenance.collect_numbers(top["data"][:5])
    except Exception as exc:
        log.debug("insight pack: top-products lookup skipped", tenant=tenant_id, error=str(exc))

    pack = {"facts": facts, "numbers": numbers,
            "table_columns": table_columns, "table_data": table_data}
    _pack_cache[cache_key] = pack
    return pack


_INSUFFICIENT = (
    "There isn't enough sales history yet for me to give data-backed suggestions. "
    "Once a few more weeks of sales are recorded I'll be able to spot trends and advise. "
    "In the meantime, focus on consistently recording every sale so the numbers are complete."
)


def generate_insight(conn, tenant_id: str, question: str, token: Optional[str],
                     tier: Optional[str], currency: str, tenant_profile: Optional[dict],
                     llm: str, api_key: str, user_email: str,
                     conversation_history: str = "") -> dict:
    """Return the unified answer dict {answer, columns, data, provenance, source,
    refusal} — same shape as the report loop, so main.py handles it uniformly."""
    pack = _build_insight_pack(conn, tenant_id, token, tier)

    if not pack["facts"]:
        return {"answer": _INSUFFICIENT, "columns": [], "data": [],
                "provenance": None, "source": "insight", "refusal": False}

    system = build_insight_system_prompt(currency, tenant_profile)
    data_block = "DATA (use only these numbers for data claims):\n" + \
                 "\n".join(f"- {f}" for f in pack["facts"])
    history_block = f"[Previous conversation]\n{conversation_history}\n\n" if conversation_history else ""
    prompt = f"{history_block}{data_block}\n\nQuestion: {question}"

    text = call_llm(prompt, system, llm, max_tokens=900, api_key=api_key,
                    user_email=user_email, operation="insight")
    text = fix_currency_symbol(text, currency)

    unsupported = provenance.unsupported_numbers(text, pack["numbers"])
    if unsupported:
        # Soft signal only — general advice legitimately introduces benchmark numbers.
        log.warning("insight: figures not traceable to the data pack (general advice or fabrication)",
                    tenant=tenant_id, count=len(unsupported), values=unsupported[:8])

    return {"answer": text, "columns": pack["table_columns"], "data": pack["table_data"],
            "provenance": "from_cache", "source": "insight", "refusal": False}
