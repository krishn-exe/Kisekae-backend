import logging
import re
from typing import Tuple

from django.conf import settings
from django.contrib.auth import get_user_model
from google.auth.exceptions import GoogleAuthError
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
import requests

from .utils import NAME_REGEX

logger = logging.getLogger(__name__)
User = get_user_model()


def sanitize_google_name(raw_name: str | None) -> str:
    """Sanitizes Google profile name to comply with User.name_validator regex.

    Falls back to an empty string if the sanitized name does not meet requirements.
    """
    if not raw_name or not isinstance(raw_name, str):
        return ""

    name = raw_name.strip()
    if NAME_REGEX.match(name):
        return name

    # Strip characters outside allowed set [A-Za-z\s\.\'-]
    cleaned = re.sub(r"[^A-Za-z\s\.\'-]", "", name).strip()
    # Normalize multiple whitespace characters
    cleaned = re.sub(r"\s+", " ", cleaned)

    if len(cleaned) >= 2 and len(cleaned) <= 150 and NAME_REGEX.match(cleaned):
        return cleaned

    return ""


def verify_google_id_token(token: str) -> dict:
    """Verifies a Google ID token using Google's public certificates.

    Validates signature, expiration, issuer, and audience (if configured).
    """
    client_id = getattr(settings, "GOOGLE_OAUTH_CLIENT_ID", None)

    if not client_id and not getattr(settings, "DEBUG", False):
        raise ValueError("GOOGLE_OAUTH_CLIENT_ID must be configured in production.")

    audience = client_id if client_id else None

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


def exchange_google_code(code: str, redirect_uri: str | None = None) -> dict:
    """Exchanges a Google authorization code for tokens via Google OAuth token endpoint."""
    client_id = getattr(settings, "GOOGLE_OAUTH_CLIENT_ID", None)
    client_secret = getattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", None)

    if not client_id or not client_secret:
        raise ValueError("Google OAuth credentials (GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET) are not configured on the server.")

    endpoint = "https://oauth2.googleapis.com/token"
    effective_redirect_uri = redirect_uri or getattr(settings, "GOOGLE_OAUTH_REDIRECT_URI", "postmessage")

    data = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": effective_redirect_uri,
        "grant_type": "authorization_code",
    }

    try:
        response = requests.post(endpoint, data=data, timeout=10)
        data = response.json()
        if not response.ok:
            error_desc = data.get("error_description", data.get("error", "Failed to exchange authorization code"))
            logger.warning("Google code exchange error: %s", error_desc)
            raise ValueError(error_desc)
        return data
    except requests.RequestException as exc:
        logger.error("Failed to connect to Google OAuth token endpoint: %s", exc)
        raise ValueError("Could not connect to Google OAuth servers.") from exc


def get_or_create_google_user(payload: dict) -> Tuple[User, bool]:
    """Finds an existing user by Google email or registers a new user.

    Returns a (user, created) tuple.
    """
    email = payload.get("email")
    if not email:
        raise ValueError("Google account did not provide an email address.")

    email_verified = payload.get("email_verified", False)
    if not email_verified:
        raise ValueError("Google account email is not verified.")

    email = email.strip().lower()
    raw_name = payload.get("name") or payload.get("given_name")
    name = sanitize_google_name(raw_name)

    user = User.objects.filter(email__iexact=email).first()
    if user:
        # Existing user: log them in and update verified status and missing name if needed
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

    # New user: register
    user = User.objects.create_user(
        email=email,
        name=name,
        is_email_verified=True,
    )
    return user, True
