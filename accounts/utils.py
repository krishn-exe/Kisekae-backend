import re
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator, validate_email

# Strict Regex Rules
PHONE_REGEX = re.compile(r"^\+91[6-9]\d{9}$")
NAME_REGEX = re.compile(r"^[A-Za-z\s\.\'-]{2,150}$")
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")

phone_validator = RegexValidator(
    regex=PHONE_REGEX,
    message="Phone number must be a valid 10-digit Indian number with '+91' prefix (e.g. +919876543210).",
)

name_validator = RegexValidator(
    regex=NAME_REGEX,
    message="Name must be 2-150 characters and can only contain letters, spaces, hyphens, periods, and apostrophes.",
)


def identify_channel(identifier: str) -> str | None:
    """Strictly identifies whether identifier is a valid 'email', 'phone', or None.

    Does NOT loosely strip symbols or re-parse digits.
    """
    if not identifier or not isinstance(identifier, str):
        return None

    cleaned = identifier.strip()

    # Strict check for phone: must match +91 followed by 10 digits starting with 6-9
    if PHONE_REGEX.match(cleaned):
        return "phone"

    # Strict check for email
    if EMAIL_REGEX.match(cleaned):
        try:
            validate_email(cleaned)
            return "email"
        except ValidationError:
            pass

    return None


def normalize_identifier(identifier: str) -> str:
    """Returns normalized identifier: lowercased for email, stripped for phone."""
    if not identifier or not isinstance(identifier, str):
        return ""
    cleaned = identifier.strip()
    channel = identify_channel(cleaned)
    if channel == "email":
        return cleaned.lower()
    return cleaned


def get_user_by_identifier(identifier: str):
    """Looks up a user by email (case-insensitive) or phone (exact strict match).

    Returns (user, channel) tuple.
    """
    User = get_user_model()
    channel = identify_channel(identifier)
    if not channel:
        return None, None

    cleaned = identifier.strip()
    if channel == "email":
        user = User.objects.filter(email__iexact=cleaned.lower()).first()
        return user, "email"
    else:
        user = User.objects.filter(phone=cleaned).first()
        return user, "phone"