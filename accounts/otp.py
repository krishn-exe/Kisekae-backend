import logging
import secrets

from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache

class RedisOTP:

    TTL_SECONDS = 300
    MAX_ATTEMPTS = 5
    COOLDOWN_SECONDS = 60

    def __init__(self, email: str, purpose: str = "login"):
        self.email = email.strip().lower() if email else ""
        self.purpose = purpose
        self.key = f"otp:{purpose}:{self.email}"
        self.cooldown_key = f"otp:cooldown:{purpose}:{self.email}"

    def can_issue(self) -> tuple[bool, int]:
        if cache.get(self.cooldown_key):
            ttl_func = getattr(cache, "ttl", None)
            rem = ttl_func(self.cooldown_key) if callable(ttl_func) else None
            return False, max(rem or self.COOLDOWN_SECONDS, 1)
        return True, 0

    def issue(self) -> str:
        raw_code = f"{secrets.randbelow(1_000_000):06d}"
        cache.set(
            self.key,
            {"code_hash": make_password(raw_code), "attempts": 0},
            timeout=self.TTL_SECONDS,
        )
        cache.set(self.cooldown_key, 1, timeout=self.COOLDOWN_SECONDS)
        return raw_code

    def verify(self, raw_code: str) -> bool:
        if not raw_code:
            return False

        clean_code = str(raw_code).strip()
        payload = cache.get(self.key)
        if not payload:
            return False

        if payload["attempts"] >= self.MAX_ATTEMPTS:
            cache.delete(self.key)
            return False

        payload["attempts"] += 1
        matched = check_password(clean_code, payload["code_hash"])

        if matched:
            cache.delete(self.key)
            cache.delete(self.cooldown_key)
        else:
            if payload["attempts"] >= self.MAX_ATTEMPTS:
                cache.delete(self.key)
            else:
                ttl_func = getattr(cache, "ttl", None)
                remaining_ttl = ttl_func(self.key) if callable(ttl_func) else None
                if not remaining_ttl or remaining_ttl <= 0:
                    remaining_ttl = self.TTL_SECONDS
                cache.set(self.key, payload, timeout=remaining_ttl)

        return matched