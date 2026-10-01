import logging
import re

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

from .utils import NAME_REGEX

logger = logging.getLogger(__name__)


def sanitize_social_name(raw_name: str | None) -> str:
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


class CustomAccountAdapter(DefaultAccountAdapter):
    def unstash_verified_email(self, request):
        if not hasattr(request, "session") or not isinstance(request.session, dict) and not hasattr(request.session, "get"):
            return None
        return super().unstash_verified_email(request)


class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        raw_name = (
            data.get("name")
            or data.get("login")
            or f"{data.get('first_name', '')} {data.get('last_name', '')}".strip()
        )
        user.name = sanitize_social_name(raw_name)
        user.is_email_verified = True
        return user

    def send_notification_mail(self, *args, **kwargs):
        pass
