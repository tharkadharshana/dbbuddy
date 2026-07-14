"""
report_cache/tiers.py
=======================
Resolves the AI subscription tier — NOT the POS back-office subscription
(see PLAN_02_Profile_And_Subscription_Sync.md's warning: the /app/profile
payload carries the merchant's POS plan, which is a completely different
thing and must never be read for this).

Wraps billing.py's existing get_plan_history_limit()/get_user_subscription()
rather than re-deriving months/cutoff-date logic — that's the same source
already used to enforce the AI's free-tier row limit everywhere else in the
app (billing.check_ai_limit, billing.get_plan_history_limit). Reusing it here
means report_cache's ingestion/retention window can never silently disagree
with the history window the rest of the app already enforces for that user
(the exact class of bug fixed in docs/plan/CHANGELOG.md's "Post-review fixes").

Plan facts, read from billing.py (2026-07-14), not guessed:
  - Plan names (subscription_plans.name, seeded in billing.py):
    "Starter" / "Growth" / "Pro".
  - History months (billing.py:_PLAN_HISTORY): Starter=3, Growth=12, Pro=200.
    Pro's 200 months (~16.7yr) is billing.py's own deliberate stand-in for
    "unlimited" — its comment says "there's no unlimited sentinel". This
    module mirrors that choice (PLAN_NAME_TO_TIER maps Pro -> "unlimited"
    as a *label*, but the concrete months/cutoff always comes from billing.py,
    never a None/NULL sentinel) rather than inventing a second, divergent
    definition of "unlimited" for the same Pro user.
  - Missing subscription -> billing.py defaults to "Starter" (fails open).

tenant_profile.subscription_tier is a display/grouping ENUM
('basic'|'standard'|'unlimited', scripts/migrations/2026_07_report_cache.sql)
— PLAN_NAME_TO_TIER is the mapping from billing.py's real plan names to that
ENUM's labels.
"""

from datetime import date, timedelta
from typing import Optional

from logger import get_logger
from report_cache.auth import get_tenant_user_email

log = get_logger(__name__)

PLAN_NAME_TO_TIER = {
    "Starter": "basic",
    "Growth": "standard",
    "Pro": "unlimited",
}
_DEFAULT_TIER = "basic"
_DEFAULT_PLAN_NAME = "Starter"
_DEFAULT_MONTHS = 3


def _plan_history_limit(user_email: str) -> dict:
    """Thin wrapper around billing.get_plan_history_limit — imported lazily
    to avoid a hard import-time dependency from report_cache on billing.py
    (mirrors how billing.py itself lazy-imports integrations for the same
    reason)."""
    from billing import get_plan_history_limit
    return get_plan_history_limit(user_email)


def get_ai_tier(tenant_id: str) -> str:
    """basic | standard | unlimited, from the tenant's DataMind (AI) subscription
    plan — never from the POS profile payload. Missing tenant/subscription/any
    billing error defaults to 'basic' (billing.get_plan_history_limit already
    fails open to "Starter" on any billing error — see its docstring)."""
    user_email = get_tenant_user_email(tenant_id)
    if not user_email:
        return _DEFAULT_TIER
    try:
        plan_name = _plan_history_limit(user_email).get("plan_name") or _DEFAULT_PLAN_NAME
    except Exception as exc:
        log.warning("get_ai_tier: billing lookup failed, defaulting to basic",
                    tenant=tenant_id, error=str(exc))
        plan_name = _DEFAULT_PLAN_NAME
    return PLAN_NAME_TO_TIER.get(plan_name, _DEFAULT_TIER)


def history_months_for(tenant_id: str) -> int:
    """Months of history the tenant's AI plan allows — the same number
    billing.py uses for row-limit filtering (3 / 12 / 200), so report_cache
    ingestion/retention never disagrees with it."""
    user_email = get_tenant_user_email(tenant_id)
    if not user_email:
        return _DEFAULT_MONTHS
    try:
        return int(_plan_history_limit(user_email)["months"])
    except Exception as exc:
        log.warning("history_months_for: billing lookup failed, defaulting to 3mo",
                    tenant=tenant_id, error=str(exc))
        return _DEFAULT_MONTHS


def window_start(tenant_id: str, today: Optional[date] = None) -> date:
    """The tenant's history cutoff date. Delegates to
    billing.get_plan_history_limit()'s cutoff_date (today - months*30 days)
    rather than reimplementing month arithmetic, so this can never compute a
    different cutoff than the row-limit the rest of the app already enforces
    for the same user."""
    user_email = get_tenant_user_email(tenant_id)
    base = today or date.today()
    if not user_email:
        return base - timedelta(days=_DEFAULT_MONTHS * 30)
    try:
        return _plan_history_limit(user_email)["cutoff_date"]
    except Exception as exc:
        log.warning("window_start: billing lookup failed, defaulting to 3mo window",
                    tenant=tenant_id, error=str(exc))
        return base - timedelta(days=_DEFAULT_MONTHS * 30)
