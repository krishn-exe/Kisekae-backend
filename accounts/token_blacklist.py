import time

from django.core.cache import cache

ACCESS_TOKEN_BLACKLIST_PREFIX = "blacklist:access"


def blacklist_access_token(jti: str, exp_timestamp: float | int) -> bool:
    if not jti:
        return False
    current_time = time.time()
    ttl = int(exp_timestamp - current_time)
    if ttl > 0:
        key = f"{ACCESS_TOKEN_BLACKLIST_PREFIX}:{jti}"
        cache.set(key, "1", timeout=ttl)
        return True
    return False


def is_access_token_blacklisted(jti: str) -> bool:
    """Check if an access token JTI is present in the Redis blacklist."""
    if not jti:
        return False
    key = f"{ACCESS_TOKEN_BLACKLIST_PREFIX}:{jti}"
    return bool(cache.get(key))
