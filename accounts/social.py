import logging
from typing import Tuple

from allauth.socialaccount.models import SocialAccount
from allauth.socialaccount.providers.oauth2.client import OAuth2Client, OAuth2Error
from django.contrib.auth import get_user_model

from .adapter import sanitize_social_name

logger = logging.getLogger(__name__)
User = get_user_model()


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

    email = social_login.user.email
    if not email:
        for email_address in social_login.email_addresses:
            if email_address.email:
                email = email_address.email
                break
    if not email:
        email = social_login.account.extra_data.get("email")

    if not email:
        raise ValueError(f"{provider.name} account did not provide an email address.")

    email = email.strip().lower()

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
            user.is_email_verified = True
            updated_fields.append("is_email_verified")
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

    return user, created, provider.name
