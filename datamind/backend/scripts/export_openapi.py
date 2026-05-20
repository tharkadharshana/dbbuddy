"""
export_openapi.py
=================
Generate openapi.yaml from the FastAPI app without running a server.

Usage (run from datamind/backend/):
    python scripts/export_openapi.py

Writes openapi.yaml to the repo root (datamind/).
Requires: pyyaml  (pip install pyyaml)
"""

import sys
import os

# Ensure backend modules are importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

# Suppress startup side-effects (pool init, scheduler) by monkey-patching before import
import unittest.mock as _mock

with (
    _mock.patch("pool.get_pool",                    return_value=None),
    _mock.patch("integrations.bootstrap_integration_tables", return_value=None),
    _mock.patch("billing.bootstrap_billing_tables",          return_value=None),
    _mock.patch("embed.bootstrap_embed_tables",              return_value=None),
    _mock.patch("integrations.start_scheduler",              return_value=None),
):
    from main import app

spec = app.openapi()

out_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "openapi.yaml")
with open(out_path, "w", encoding="utf-8") as f:
    yaml.dump(spec, f, allow_unicode=True, sort_keys=False)

print(f"Written: {out_path}")
