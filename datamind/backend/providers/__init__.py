"""
providers/__init__.py
=====================
Provider Registry.

To register a new provider, import it and add to REGISTRY.
That is the ONLY file you touch when adding a new external system.

Example — adding Shopify:
  from providers.shopify.provider import ShopifyProvider
  REGISTRY["shopify"] = ShopifyProvider()
"""

from providers.loyverse.provider  import LoyverseProvider
from providers.salesplay.provider import SalesPlayProvider

# ── Registry ──────────────────────────────────────────────────────────────────
# key = provider_id (must match manifest.json provider_id)
# value = instantiated provider object

REGISTRY: dict = {
    "salesplay": SalesPlayProvider(),
    "loyverse":  LoyverseProvider(),
    # "shopify":  ShopifyProvider(),
    # "square":   SquareProvider(),
    # "woocommerce": WooCommerceProvider(),
}


def get_provider(provider_id: str):
    """Return a provider instance by ID, or raise ValueError."""
    p = REGISTRY.get(provider_id)
    if not p:
        raise ValueError(
            f"Unknown provider: '{provider_id}'. "
            f"Available: {list(REGISTRY.keys())}"
        )
    return p


def list_providers() -> list:
    """Return all provider manifests as dicts (for the API)."""
    import dataclasses
    return [dataclasses.asdict(p.manifest) for p in REGISTRY.values()]
