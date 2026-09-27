import logging
import secrets

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
import requests

from .utils import normalize_identifier

logger = logging.getLogger(__name__)


class RedisOTP:

    TTL_SECONDS = 300
    MAX_ATTEMPTS = 5
    COOLDOWN_SECONDS = 60

    def __init__(self, identifier: str, purpose: str = "login"):
        self.identifier = normalize_identifier(identifier)
        self.purpose = purpose
        self.key = f"otp:{purpose}:{self.identifier}"
        self.cooldown_key = f"otp:cooldown:{purpose}:{self.identifier}"

    def can_issue(self) -> tuple[bool, int]:
        """Checks if a new code can be issued, enforcing cooldown between requests.

        Returns (can_issue: bool, wait_seconds: int).
        """
        if cache.get(self.cooldown_key):
            ttl_func = getattr(cache, "ttl", None)
            rem = ttl_func(self.cooldown_key) if callable(ttl_func) else None
            return False, max(rem or self.COOLDOWN_SECONDS, 1)
        return True, 0

    def issue(self) -> str:
        """Generates a 6-digit code, stores its hash in cache, sets cooldown,

        and returns the raw code.
        """
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


def send_otp_whatsapp(phone_number: str, raw_code: str) -> bool:
    """Dispatches OTP via Meta WhatsApp Cloud API if credentials are set, or logs in development."""
    access_token = getattr(settings, "META_WHATSAPP_ACCESS_TOKEN", None)
    phone_number_id = getattr(settings, "META_WHATSAPP_PHONE_NUMBER_ID", None)

    if not access_token or not phone_number_id:
        logger.info(
            "Meta WhatsApp credentials not set. OTP for %s: %s",
            phone_number,
            raw_code,
        )
        return False

    digits = "".join(ch for ch in str(phone_number) if ch.isdigit())
    url = f"https://graph.facebook.com/v20.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": digits,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": f"Your Kisekae verification code is {raw_code}. It expires in 5 minutes.",
        },
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        data = response.json()
        logger.info("Meta WhatsApp message sent to %s: %s", digits, data)
        return True
    except Exception as e:
        logger.error("Failed to send Meta WhatsApp message to %s: %s", digits, e)
        raise RuntimeError(f"Meta WhatsApp delivery failed: {e}") from e


# Backward compatibility aliases
send_otp_phone = send_otp_whatsapp
send_otp_sms = send_otp_whatsapp