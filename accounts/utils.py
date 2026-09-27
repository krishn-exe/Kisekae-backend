import re
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import models

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_phone(phone: str) -> str | None:
    if not phone or not isinstance(phone, str):
        return None

    cleaned = re.sub(r"[\s\-\(\)\.]", "", phone.strip())
    digits = "".join(ch for ch in cleaned if ch.isdigit())

    if len(digits) == 10:
        return f"+91{digits}"

    if len(digits) == 11 and digits.startswith("0"):
        return f"+91{digits[1:]}"

    if len(digits) == 12 and digits.startswith("91"):
        return f"+91{digits[2:]}"

    if cleaned.startswith("+") and 7 <= len(digits) <= 15:
        return f"+{digits}"

    if 7 <= len(digits) <= 15:
        return f"+{digits}"

    return None


def identify_channel(identifier: str) -> str | None:
    if not identifier or not isinstance(identifier, str):
        return None

    cleaned = identifier.strip()

    # Check email
    if EMAIL_RE.match(cleaned):
        try:
            validate_email(cleaned)
            return "email"
        except ValidationError:
            pass

    # Check phone
    if normalize_phone(cleaned) is not None:
        return "phone"

    return None


def normalize_identifier(identifier: str) -> str:
    if not identifier or not isinstance(identifier, str):
        return ""

    cleaned = identifier.strip()
    channel = identify_channel(cleaned)
    if channel == "email":
        return cleaned.lower()
    if channel == "phone":
        norm = normalize_phone(cleaned)
        return norm if norm else cleaned
    return cleaned


def get_user_by_identifier(identifier: str):
    User = get_user_model()
    channel = identify_channel(identifier)
    if not channel:
        return None, None

    if channel == "email":
        email = identifier.strip().lower()
        user = User.objects.filter(email__iexact=email).first()
        return user, "email"
    else:
        norm_phone = normalize_phone(identifier)
        digits = "".join(ch for ch in identifier if ch.isdigit())
        bare_10 = digits[-10:] if len(digits) >= 10 else digits

        q = models.Q(phone=norm_phone)
        if bare_10:
            q |= models.Q(phone=bare_10)
        q |= models.Q(phone=identifier.strip())

        user = User.objects.filter(q).first()
        return user, "phone"