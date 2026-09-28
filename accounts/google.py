import logging
import re
from typing import Tuple

from django.conf import settings
from django.contrib.auth import get_user_model
from google.auth.exceptions import GoogleAuthError
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from .utils import NAME_REGEX

logger = logging.getLogger(__name__)
User = get_user_model()


def sanitize_google_name(raw_name: str | None) -> str:
    if not raw_name or not isinstance(raw_name, str):
        return ""

    name = raw_name.strip()
    if NAME_REGEX.match(name):
        return name

    cleaned = re.sub(r"[^A-Za-z\s\.'-]", "", name).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)

    if len(cleaned) >= 2 and len(cleaned) <= 150 and NAME_REGEX.match(cleaned):
        return cleaned

    return ""


def verify_google_id_token(token: str) -> dict:
    client_id = getattr(settings, "GOOGLE_OAUTH_CLIENT_ID", None)
    audience = client_id.strip() if client_id else None

    try:
        payload = google_id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            audience=audience,
        )
        return payload
    except (ValueError, GoogleAuthError) as exc:
        logger.warning("Google ID token verification failed: %s", exc)
        raise ValueError(f"Invalid Google token: {exc}") from exc


def get_or_create_google_user(payload: dict) -> Tuple[User, bool]:
    email = payload.get("email")
    if not email:
        raise ValueError("Google account did not provide an email address.")

    email_verified = payload.get("email_verified", False)
    if isinstance(email_verified, str):
        email_verified = email_verified.lower() == "true"

    if not email_verified:
        raise ValueError("Google account email is not verified.")

    email = email.strip().lower()
    raw_name = payload.get("name") or payload.get("given_name")
    name = sanitize_google_name(raw_name)

    user = User.objects.filter(email__iexact=email).first()
    if user:
        updated_fields = []
        if not user.is_email_verified:
            user.is_email_verified = True
            updated_fields.append("is_email_verified")
        if not user.name and name:
            user.name = name
            updated_fields.append("name")
        if updated_fields:
            user.save(update_fields=updated_fields)
        return user, False

    user = User.objects.create_user(
        email=email,
        name=name,
        is_email_verified=True,
    )
    return user, True
