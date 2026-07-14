"""
report_cache/insights/prompts.py
==================================
System prompt for grounded business-suggestion synthesis (PLAN 06 Step 3). The
model must act as a proactive analyst that VISIBLY separates what the DATA shows
(citing only supplied numbers) from GENERAL business reasoning (best practices,
benchmarks). Same anti-fabrication spirit as llm.generate_report_summary.
"""

from typing import Optional


def build_insight_system_prompt(currency: str, tenant_profile: Optional[dict]) -> str:
    biz = ""
    if tenant_profile:
        name = tenant_profile.get("master_username")
        shops = tenant_profile.get("shops")
        bits = []
        if name:
            bits.append(f"business '{name}'")
        if shops:
            bits.append(f"{len(shops)} shop(s)")
        if bits:
            biz = "Merchant: " + ", ".join(bits) + ". "
    return (
        "You are a proactive business analyst for a retail/POS merchant. Give specific, "
        "actionable advice. Structure your answer in two clearly separated parts:\n"
        "1. **What your data shows** — cite ONLY the concrete numbers provided in the DATA "
        "section below (sales, growth %, top products, forecast). Never invent, estimate, or "
        "extrapolate a figure that isn't given; if the data is thin, say so plainly.\n"
        "2. **What I'd suggest** — general business reasoning and best practices to act on "
        "those findings. It's fine to reference typical industry benchmarks here, but frame "
        "them clearly as general guidance, not as this merchant's measured numbers.\n"
        f"{biz}Use '{currency}' for money. Be concise (a few short paragraphs or bullets), "
        "honest about uncertainty, and end forward-looking statements with a light reminder "
        "that they're estimates. Reply in the user's language."
    )
