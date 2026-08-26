"""
report_cache/filter_key.py — X-Filter-Key sender for SalesPlay's internal
report/data-fetch API (docs/salesplay-encrypted-param.md).

SalesPlay's internal report API now requires this encrypted header to prove
which day-range our app-side user is entitled to. All our plans give
unlimited historical data, so we always send the "no cap" sentinel —
this exists only because SalesPlay's gate expects an integer, not because we
ration anything ourselves.
"""

import base64
import hashlib
import os
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from logger import get_logger

log = get_logger(__name__)

# Placeholder used only while testing against SalesPlay's predev environment,
# before they hand over a real pre-shared secret. Loud warning below so this
# never goes unnoticed if it's still in effect once SALESPLAY_SHARED_SECRET
# is actually supposed to be set in a real environment.
_DEV_FALLBACK_SECRET = "12345"

# Every DataMind plan (trial + paid) grants unlimited historical data — there
# is no real day cap to send. Try the literal "UNLIMITED" string first; if
# SalesPlay's decoder turns out to require a numeric package_day_range (their
# reference PHP casts it with (int), which silently gives 0 for a non-numeric
# string), fall back to sending UNLIMITED_DAY_RANGE_FALLBACK instead.
UNLIMITED_DAY_RANGE = "UNLIMITED"
UNLIMITED_DAY_RANGE_FALLBACK = 999999


def build_filter_key(package_day_range=UNLIMITED_DAY_RANGE) -> str:
    """base64(nonce || ciphertext || tag) per docs/salesplay-encrypted-param.md.
    Falls back to _DEV_FALLBACK_SECRET (with a loud warning) if
    SALESPLAY_SHARED_SECRET is unset — replace that env var with the real
    secret before this ever reaches a real environment."""
    secret = os.environ.get("SALESPLAY_SHARED_SECRET")
    if not secret:
        log.warning(
            "SALESPLAY_SHARED_SECRET not set — using dev placeholder key. "
            "X-Filter-Key is NOT actually authenticated. Set the real secret "
            "before this runs anywhere but local/predev testing."
        )
        secret = _DEV_FALLBACK_SECRET
    key = hashlib.sha256(secret.encode()).digest()
    nonce = os.urandom(12)
    plaintext = f"{package_day_range}|{int(time.time())}".encode()

    aesgcm = AESGCM(key)
    ct_with_tag = aesgcm.encrypt(nonce, plaintext, None)

    return base64.b64encode(nonce + ct_with_tag).decode()
