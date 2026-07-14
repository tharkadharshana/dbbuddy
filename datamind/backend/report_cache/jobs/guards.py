"""
report_cache/jobs/guards.py
=============================
Backpressure guards for ingestion jobs (PLAN 04 Step 1): a circuit breaker
and a per-tenant rate limiter. Both protect the shared POS report backend —
reports are 90s-class calls (doc 09 Part 7), so one misbehaving tenant or a
POS outage must not turn into a hammering storm.

ponytail: in-process state (module-level dicts under a lock), not Redis —
this repo has no Redis, and PLAN 04's fallback is APScheduler + a single
worker process, so process-local state is the correct scope. Upgrade path if
the worker is ever horizontally scaled: move `_failures`/`_open_until`/
`_last_call` into the report_cache_job DB or Redis and key by tenant there.
"""

import os
import threading
import time

from logger import get_logger

log = get_logger(__name__)

_BREAKER_THRESHOLD = int(os.getenv("REPORT_CACHE_BREAKER_THRESHOLD", "5"))
# Global breaker (POS backend down across tenants) needs a higher bar so one
# unlucky tenant can't halt ingestion for everyone — default 4× the per-tenant one.
_BREAKER_GLOBAL_THRESHOLD = int(os.getenv("REPORT_CACHE_BREAKER_GLOBAL_THRESHOLD", str(_BREAKER_THRESHOLD * 4)))
_BREAKER_COOLDOWN = int(os.getenv("REPORT_CACHE_BREAKER_COOLDOWN_SECONDS", "300"))
_TENANT_MIN_INTERVAL = float(os.getenv("REPORT_CACHE_TENANT_MIN_INTERVAL_SECONDS", "1.0"))

_GLOBAL = "__global__"


class BreakerOpen(Exception):
    """Raised by CircuitBreaker.check() when the breaker for a key is open."""


class CircuitBreaker:
    """Opens per-tenant after N consecutive failures, and globally if the POS
    backend fails across tenants. While open, check() raises BreakerOpen so a
    job short-circuits (records an 'error' sync-state) instead of calling the
    already-struggling backend. A single success closes the key again."""

    def __init__(self, threshold: int = _BREAKER_THRESHOLD, cooldown: int = _BREAKER_COOLDOWN,
                 global_threshold: int = _BREAKER_GLOBAL_THRESHOLD):
        self.threshold = threshold
        self.global_threshold = max(global_threshold, threshold)
        self.cooldown = cooldown
        self._failures: dict = {}
        self._open_until: dict = {}
        self._lock = threading.Lock()

    def _threshold_for(self, key: str) -> int:
        return self.global_threshold if key == _GLOBAL else self.threshold

    def _is_open(self, key: str, now: float) -> bool:
        until = self._open_until.get(key, 0.0)
        if until and now < until:
            return True
        if until and now >= until:  # cooldown elapsed — half-open: clear and allow a probe
            self._open_until.pop(key, None)
            self._failures[key] = 0
        return False

    def check(self, tenant_id: str) -> None:
        now = time.monotonic()
        with self._lock:
            for key in (_GLOBAL, tenant_id):
                if self._is_open(key, now):
                    raise BreakerOpen(f"circuit breaker open for {key!r}")

    def record_success(self, tenant_id: str) -> None:
        with self._lock:
            for key in (_GLOBAL, tenant_id):
                self._failures[key] = 0
                self._open_until.pop(key, None)

    def record_failure(self, tenant_id: str) -> None:
        now = time.monotonic()
        with self._lock:
            for key in (_GLOBAL, tenant_id):
                self._failures[key] = self._failures.get(key, 0) + 1
                if self._failures[key] >= self._threshold_for(key):
                    self._open_until[key] = now + self.cooldown
                    log.warning("Circuit breaker opened", key=key,
                                failures=self._failures[key], cooldown_s=self.cooldown)


class RateLimiter:
    """Minimum interval between POS calls per tenant (token-bucket of size 1).
    Blocks the calling worker thread — fine, ingestion runs off the request
    thread by design."""

    def __init__(self, min_interval: float = _TENANT_MIN_INTERVAL):
        self.min_interval = min_interval
        self._last_call: dict = {}
        self._lock = threading.Lock()

    def acquire(self, tenant_id: str) -> None:
        with self._lock:
            now = time.monotonic()
            earliest = self._last_call.get(tenant_id, 0.0) + self.min_interval
            slot = max(now, earliest)
            self._last_call[tenant_id] = slot  # reserve the slot before releasing the lock
        wait = slot - now
        if wait > 0:
            time.sleep(wait)  # sleep outside the lock so other tenants aren't blocked


# Module-level singletons shared by all task functions in this worker process.
breaker = CircuitBreaker()
rate_limiter = RateLimiter()
