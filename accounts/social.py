import logging
from typing import Tuple

import requests
from allauth.socialaccount.models import SocialAccount
from allauth.socialaccount.providers.oauth2.client import OAuth2Client, OAuth2Error
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from .adapter import sanitize_social_name

logger = logging.getLogger(__name__)
User = get_user_model()


def extract_verified_oauth_email(social_login, provider) -> str:
    """
    Safely extract a verified email address from the OAuth social login.
    Rejects unverified email addresses to prevent account takeover attacks.
    """
    email_addresses = getattr(social_login, "email_addresses", []) or []

    # 1. Check allauth's parsed EmailAddress list (populated for GitHub and Google)
    if email_addresses:
        # Prioritize primary verified email
        for ea in email_addresses:
            if getattr(ea, "verified", False) and getattr(ea, "primary", False) and getattr(ea, "email", None):
                return ea.email.strip().lower()
        # Fallback to any verified email
        for ea in email_addresses:
            if getattr(ea, "verified", False) and getattr(ea, "email", None):
                return ea.email.strip().lower()
        # If email_addresses were returned but none are verified, reject!
        raise ValueError(f"The email address associated with this {provider.name} account is not verified.")

    # 2. Check extra_data (e.g., Google ID token / userinfo)
    extra_data = getattr(social_login.account, "extra_data", {}) or {}
    email_verified = extra_data.get("email_verified")
    if email_verified is None:
        email_verified = extra_data.get("verified_email")

    candidate_email = (
        getattr(social_login.user, "email", None)
        or extra_data.get("email")
    )

    if not candidate_email:
        raise ValueError(f"{provider.name} account did not provide an email address.")

    provider_id = (getattr(provider, "id", "") or getattr(provider, "name", "")).lower()
    # For GitHub, email_addresses should always be present; public profile email alone is untrusted
    if "github" in provider_id:
        raise ValueError(f"The email address associated with this {provider.name} account is not verified.")

    # For Google and OIDC providers with email_verified boolean
    if email_verified is False:
        raise ValueError(f"The email address associated with this {provider.name} account is not verified.")

    return candidate_email.strip().lower()


def exchange_google_pkce_tokens(
    code: str,
    code_verifier: str,
    client_id: str,
    redirect_uri: str,
) -> dict:
    """
    Exchanges an authorization code for tokens with Google using PKCE (RFC 7636).
    Does NOT send a client_secret since mobile clients (Android/iOS)
    are public clients without client secrets.
    """
    token_url = "https://oauth2.googleapis.com/token"
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": code_verifier,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    try:
        response = requests.post(token_url, data=payload, headers=headers, timeout=10)
    except requests.RequestException as exc:
        logger.warning("Failed to reach Google token endpoint: %s", exc)
        raise ValueError("Unable to reach Google authorization server. Please try again.") from exc

    if not response.ok:
        logger.warning(
            "Google PKCE token exchange failed with status %d: %s",
            response.status_code,
            response.text,
        )
        raise ValueError("Invalid or expired Google authorization code.")

    try:
        return response.json()
    except Exception as exc:
        raise ValueError("Invalid response received from Google authorization server.") from exc


def verify_google_id_token(
    id_token_str: str,
    expected_client_id: str | None = None,
) -> dict:
    """
    Verifies a Google ID token JWT using Google's public JWKS certificates.
    Ensures signature, expiration, issuer, audience, and email verification.
    """
    allowed_client_ids = getattr(settings, "GOOGLE_ALLOWED_CLIENT_IDS", [])
    audience = allowed_client_ids if allowed_client_ids else (expected_client_id or getattr(settings, "GOOGLE_OAUTH_CLIENT_ID", None))

    try:
        id_info = google_id_token.verify_oauth2_token(
            id_token_str,
            google_requests.Request(),
            audience=audience,
        )
    except Exception as exc:
        logger.warning("Google ID token verification failed: %s", exc)
        raise ValueError(f"Google ID token verification failed: {exc}") from exc

    issuer = id_info.get("iss", "")
    if issuer not in ("accounts.google.com", "https://accounts.google.com"):
        logger.warning("Invalid Google ID token issuer: %s", issuer)
        raise ValueError("Invalid Google ID token issuer.")

    if not id_info.get("email_verified", False):
        raise ValueError("The email address associated with this Google account is not verified.")

    return id_info


def process_google_pkce_login(
    request,
    code: str,
    code_verifier: str,
    callback_url: str | None = None,
    client_id: str | None = None,
) -> Tuple[User, bool, str]:
    """
    Handles mobile PKCE Google login and registration without a client secret.
    """
    if not code:
        raise ValueError("Authorization code is required when code_verifier is provided.")
    if not code_verifier:
        raise ValueError("code_verifier is required for PKCE flow.")

    effective_client_id = (
        client_id
        or getattr(settings, "GOOGLE_ANDROID_CLIENT_ID", "")
        or getattr(settings, "GOOGLE_IOS_CLIENT_ID", "")
        or getattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "")
    )
    if not effective_client_id:
        raise ValueError("No Google client ID configured on the server.")

    redirect_uri = callback_url or getattr(settings, "GOOGLE_ANDROID_CALLBACK_URL", "kisekae://auth/google/callback")

    token_data = exchange_google_pkce_tokens(
        code=code,
        code_verifier=code_verifier,
        client_id=effective_client_id,
        redirect_uri=redirect_uri,
    )

    id_token_str = token_data.get("id_token")
    if not id_token_str:
        logger.error("Google token response did not contain id_token: %s", token_data)
        raise ValueError("Google authorization response did not include an ID token.")

    id_info = verify_google_id_token(id_token_str, expected_client_id=effective_client_id)

    email = id_info.get("email")
    if not email:
        raise ValueError("Google account did not provide an email address.")
    email = email.strip().lower()

    uid = str(id_info.get("sub"))
    raw_name = (
        id_info.get("name")
        or f"{id_info.get('given_name', '')} {id_info.get('family_name', '')}".strip()
    )
    clean_name = sanitize_social_name(raw_name)

    user = User.objects.filter(email__iexact=email).first()
    created = False

    if user:
        updated_fields = []
        if not user.is_email_verified:
            user.set_unusable_password()
            user.is_email_verified = True
            updated_fields.extend(["password", "is_email_verified"])
        if not user.name and clean_name:
            user.name = clean_name
            updated_fields.append("name")
        if updated_fields:
            user.save(update_fields=updated_fields)

        if not SocialAccount.objects.filter(provider="google", uid=uid).exists():
            SocialAccount.objects.create(
                user=user,
                provider="google",
                uid=uid,
                extra_data=id_info,
            )
    else:
        user = User.objects.create_user(
            email=email,
            name=clean_name,
            is_email_verified=True,
        )
        SocialAccount.objects.create(
            user=user,
            provider="google",
            uid=uid,
            extra_data=id_info,
        )
        created = True

    cache.delete(f"pending_registration:{email}")

    return user, created, "Google"


def process_social_login(
    request,
    adapter_class,
    code: str | None = None,
    callback_url: str | None = None,
    access_token: str | None = None,
    code_verifier: str | None = None,
    client_id: str | None = None,
) -> Tuple[User, bool, str]:
    raw_request = getattr(request, "_request", request)
    if not hasattr(raw_request, "session"):
        raw_request.session = {}

    adapter = adapter_class(raw_request)
    provider = adapter.get_provider()

    # If code_verifier is present, route to PKCE flow (mobile clients without client secret)
    if code_verifier and code:
        if adapter.provider_id == "google":
            return process_google_pkce_login(
                request=request,
                code=code,
                code_verifier=code_verifier,
                callback_url=callback_url,
                client_id=client_id,
            )
        else:
            raise ValueError(f"PKCE flow is not supported for {provider.name}.")

    app = None
    if client_id:
        try:
            for a in provider.get_apps(raw_request):
                if a.client_id == client_id:
                    app = a
                    break
        except Exception:
            pass
    if not app:
        app = provider.app

    if not app or not app.client_id or not app.secret:
        logger.error("OAuth credentials not configured for provider: %s", adapter.provider_id)
        raise ValueError(f"OAuth credentials for {provider.name} are not configured on the server.")

    if code:
        redirect_uri = callback_url or adapter.get_callback_url(raw_request, app)
        client = OAuth2Client(
            request=raw_request,
            consumer_key=app.client_id,
            consumer_secret=app.secret,
            access_token_method=adapter.access_token_method,
            access_token_url=adapter.access_token_url,
            callback_url=redirect_uri,
            scope_delimiter=adapter.scope_delimiter,
            headers=adapter.headers,
            basic_auth=adapter.basic_auth,
        )
        try:
            token_data = client.get_access_token(code)
        except (OAuth2Error, Exception) as exc:
            logger.warning("Failed to exchange %s authorization code: %s", provider.name, exc)
            raise ValueError(f"Invalid or expired {provider.name} authorization code.") from exc
    elif access_token:
        token_data = {"access_token": access_token}
    else:
        raise ValueError("Authorization code or access token is required.")

    token = adapter.parse_token(token_data)
    token.app = app
    social_login = adapter.complete_login(raw_request, app, token, response=token_data)
    social_login.token = token

    email = extract_verified_oauth_email(social_login, provider)

    user = User.objects.filter(email__iexact=email).first()
    created = False

    raw_name = (
        social_login.account.extra_data.get("name")
        or social_login.account.extra_data.get("login")
        or f"{social_login.account.extra_data.get('given_name', '')} {social_login.account.extra_data.get('family_name', '')}".strip()
    )
    clean_name = sanitize_social_name(raw_name)

    if user:
        updated_fields = []
        if not user.is_email_verified:
            # Prevent pre-account takeover: reset any attacker-set password
            user.set_unusable_password()
            user.is_email_verified = True
            updated_fields.extend(["password", "is_email_verified"])
        if not user.name and clean_name:
            user.name = clean_name
            updated_fields.append("name")
        if updated_fields:
            user.save(update_fields=updated_fields)

        if not SocialAccount.objects.filter(provider=adapter.provider_id, uid=social_login.account.uid).exists():
            social_login.user = user
            social_login.save(raw_request, connect=True)
    else:
        user = User.objects.create_user(
            email=email,
            name=clean_name,
            is_email_verified=True,
        )
        social_login.user = user
        social_login.save(raw_request, connect=True)
        created = True

    cache.delete(f"pending_registration:{email}")

    return user, created, provider.name

