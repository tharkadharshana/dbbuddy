"""
report_cache.jobs — background job layer (PLAN 04).

Import the public helpers from the submodule directly (kept out of this
__init__ on purpose: re-exporting a function named `enqueue` would shadow the
`enqueue` submodule of the same name):
    from report_cache.jobs.enqueue import enqueue, request_backfill
Run the worker with:
    python -m report_cache.jobs.worker
"""
