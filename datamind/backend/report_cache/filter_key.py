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

# Every DataMind plan (trial + paid) grants unlimited historical data — there
# is no real day cap to send. Try the literal "UNLIMITED" string first; if
# SalesPlay's decoder turns out to require a numeric package_day_range (their
# reference PHP casts it with (int), which silently gives 0 for a non-numeric
# string), fall back to sending UNLIMITED_DAY_RANGE_FALLBACK instead.
UNLIMITED_DAY_RANGE = "UNLIMITED"
UNLIMITED_DAY_RANGE_FALLBACK = 999999


def build_filter_key(package_day_range=UNLIMITED_DAY_RANGE) -> str:
    """base64(nonce || ciphertext || tag) per docs/salesplay-encrypted-param.md.
    Raises if SALESPLAY_SHARED_SECRET is unset — callers must not send a
    header built from an empty key."""
    secret = os.environ["SALESPLAY_SHARED_SECRET"]
    key = hashlib.sha256(secret.encode()).digest()
    nonce = os.urandom(12)
    plaintext = f"{package_day_range}|{int(time.time())}".encode()

    aesgcm = AESGCM(key)
    ct_with_tag = aesgcm.encrypt(nonce, plaintext, None)

    return base64.b64encode(nonce + ct_with_tag).decode()
