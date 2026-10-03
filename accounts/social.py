import logging
from typing import Tuple

from allauth.socialaccount.models import SocialAccount
from allauth.socialaccount.providers.oauth2.client import OAuth2Client, OAuth2Error
from django.contrib.auth import get_user_model
from django.core.cache import cache

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


def process_social_login(
    request,
    adapter_class,
    code: str | None = None,
    callback_url: str | None = None,
    access_token: str | None = None,
) -> Tuple[User, bool, str]:
    raw_request = getattr(request, "_request", request)
    if not hasattr(raw_request, "session"):
        raw_request.session = {}

    adapter = adapter_class(raw_request)
    provider = adapter.get_provider()
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

