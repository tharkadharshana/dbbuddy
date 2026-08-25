#!/usr/bin/env python3
"""
add_brand.py
============
Register a whitelabel brand. Adding a partner is data, not a deploy.

A brand is one embed_partners row. Many brands share one provider_id --
Salesplay, Sellmo and any future whitelabel all run provider_id='salesplay'
and reuse that integration unchanged. Only the skin differs.

Usage (from datamind/backend/):
    python scripts/add_brand.py brands/sellmo.json
    python scripts/add_brand.py brands/sellmo.json --update

The JSON file holds the row plus its branding, e.g.:

    {
      "partner_name":    "Sellmo",
      "provider_id":     "salesplay",
      "key_prefix":      "sl_live_",
      "allowed_origins": "https://app.sellmo.com,https://backoffice.sellmo.com",
      "branding": {
        "product_name":  "Sellmo AI",
        "company_name":  "Sellmo",
        "brand_slug":    "sellmo",
        "logo_url":      "https://cdn.sellmo.com/ai-logo.svg",
        "favicon_url":   "https://cdn.sellmo.com/favicon.svg",
        "app_url":       "https://ai.sellmo.com",
        "app_domains":   ["ai.sellmo.com", "dev.sellmo.com"],
        "terms_url":     "https://sellmo.com/legal/terms.pdf",
        "privacy_url":   "https://sellmo.com/legal/privacy.pdf",
        "support_email": "support@sellmo.com",
        "primary_color": "#0058BE",
        "subscription_free": true
      }
    }

subscription_free is per-brand: a new whitelabel can launch free while an
established brand is already charging. Omit it to inherit the process default.
"""

import argparse
import json
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

REQUIRED = ("partner_name", "provider_id", "allowed_origins")


def _load(path):
    with open(path, encoding="utf-8") as f:
        spec = json.load(f)
    missing = [k for k in REQUIRED if not spec.get(k)]
    if missing:
        raise SystemExit("Missing required field(s): " + ", ".join(missing))
    spec.setdefault("branding", {})
    spec.setdefault("key_prefix", "br_live_")
    if not spec["branding"].get("brand_slug"):
        raise SystemExit("branding.brand_slug is required -- it labels the brand in logs and domain mapping.")
    return spec


def _check_domains_are_unique(cur, spec, self_name):
    """A hostname must map to exactly one brand.

    Host is how the standalone app decides which brand a login belongs to. Two
    brands claiming one hostname would make that ambiguous, and a merchant
    could land in the wrong account.
    """
    wanted = {str(d).strip().lower() for d in spec["branding"].get("app_domains") or []}
    if not wanted:
        return
    cur.execute("SELECT partner_name, branding FROM embed_partners")
    for row in cur.fetchall():
        if row["partner_name"] == self_name:
            continue
        raw = row["branding"] or {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                continue
        theirs = {str(d).strip().lower() for d in raw.get("app_domains") or []}
        clash = wanted & theirs
        if clash:
            raise SystemExit(
                "REFUSING: {} already claims {}. A hostname must map to exactly "
                "one brand.".format(row["partner_name"], ", ".join(sorted(clash)))
            )


def _check_slug_is_stable(cur, spec):
    """brand_slug is immutable once the brand exists.

    It labels the brand everywhere it is logged and mapped. Silently changing
    it on an existing brand makes historical records unreadable, so require
    the change to be deliberate rather than a side effect of editing a file.
    """
    cur.execute(
        "SELECT branding FROM embed_partners WHERE partner_name = %s",
        (spec["partner_name"],),
    )
    row = cur.fetchone()
    if not row:
        return
    raw = row["branding"] or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return
    old = raw.get("brand_slug")
    new = spec["branding"].get("brand_slug")
    if old and new and old != new:
        raise SystemExit(
            "REFUSING: brand_slug for {} is '{}' and cannot become '{}'. It "
            "labels this brand in logs and domain mapping.".format(
                spec["partner_name"], old, new)
        )


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", help="path to the brand JSON file")
    ap.add_argument("--update", action="store_true",
                    help="update an existing brand instead of refusing")
    args = ap.parse_args()

    spec = _load(args.spec)

    from pool import get_internal_conn

    conn = get_internal_conn()
    try:
        cur = conn.cursor(dictionary=True)
        _check_domains_are_unique(cur, spec, spec["partner_name"])
        _check_slug_is_stable(cur, spec)

        cur.execute(
            "SELECT partner_key FROM embed_partners WHERE partner_name = %s",
            (spec["partner_name"],),
        )
        existing = cur.fetchone()

        if existing and not args.update:
            raise SystemExit(
                "{} already exists with key {}. Re-run with --update to change "
                "it.".format(spec["partner_name"], existing["partner_key"])
            )

        if existing:
            key = existing["partner_key"]
            cur.execute(
                "UPDATE embed_partners SET provider_id=%s, allowed_origins=%s, "
                "branding=%s WHERE partner_key=%s",
                (spec["provider_id"], spec["allowed_origins"],
                 json.dumps(spec["branding"]), key),
            )
            action = "Updated"
        else:
            key = spec["key_prefix"] + secrets.token_urlsafe(18)
            cur.execute(
                "INSERT INTO embed_partners "
                "(partner_key, partner_name, provider_id, allowed_origins, branding, active) "
                "VALUES (%s, %s, %s, %s, %s, 1)",
                (key, spec["partner_name"], spec["provider_id"],
                 spec["allowed_origins"], json.dumps(spec["branding"])),
            )
            action = "Created"

        conn.commit()
        cur.close()
    finally:
        conn.close()

    brand = spec["branding"]
    app_url = brand.get("app_url") or "https://YOUR-APP-DOMAIN"
    print("\n{} brand: {}".format(action, spec["partner_name"]))
    print("  partner_key : {}".format(key))
    print("  provider    : {} (integration reused unchanged)".format(spec["provider_id"]))
    print("  free mode   : {}".format(brand.get("subscription_free", "inherits process default")))
    print("\n  Iframe tag for {}:".format(spec["partner_name"]))
    print('    <iframe')
    print('      src="{}/embed.html?pk={}"'.format(app_url.rstrip("/"), key))
    print('      width="420" height="680" frameborder="0"')
    print('      allow="clipboard-write"')
    print('      style="border-radius:12px;"')
    print('    ></iframe>')
    print("\n  Point these hostnames at the app before going live:")
    for d in brand.get("app_domains") or ["(none configured)"]:
        print("    " + str(d))
    print()


if __name__ == "__main__":
    main()
