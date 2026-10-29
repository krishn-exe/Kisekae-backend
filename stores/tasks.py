import logging
from celery import shared_task
from django.conf import settings
from django.utils import timezone
from notifications.email_service import NotificationEmailService

logger = logging.getLogger(__name__)


@shared_task(autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def send_store_invitation_email_task(target_email, store_name, inviter_name, raw_token):
    """
    Sends an invitation email with a deep link and raw token to manage a store.
    """
    subject = f"You're invited to manage {store_name} on Kisekae"
    accept_url = f"kisekae://invitations/accept?token={raw_token}"
    backend_base = (getattr(settings, "BACKEND_BASE_URL", None) or os.getenv("BACKEND_BASE_URL", "https://api.kisekae.live")).rstrip("/")
    web_redirect_url = f"{backend_base}/stores/invitations/redirect/?token={raw_token}"
    expires_days = getattr(settings, 'STORE_INVITATION_TTL_DAYS', 7)
    plain_message = (
        f"Hello,\n\n"
        f"{inviter_name} has invited you to join the team managing '{store_name}' on Kisekae.\n\n"
        f"Click the link below to accept the invitation in the Kisekae Seller app:\n"
        f"{web_redirect_url}\n\n"
        f"Or enter this invitation token manually in the Kisekae Seller app:\n"
        f"{raw_token}\n\n"
        f"Note: This invitation will expire in {expires_days} days.\n\n"
        f"If you did not expect this invitation, you can safely ignore this email."
    )

    context = {
        "target_email": target_email,
        "store_name": store_name,
        "inviter_name": inviter_name,
        "raw_token": raw_token,
        "accept_url": accept_url,
        "web_redirect_url": web_redirect_url,
        "expires_days": expires_days,
    }

    NotificationEmailService.send_multipart_email(
        to_email=target_email,
        subject=subject,
        template_name="emails/store_invitation.html",
        context=context,
        plain_text_fallback=plain_message,
    )
    logger.info("Store invitation email dispatched to %s for store %s", target_email, store_name)


@shared_task
def expire_stale_store_invitations_task():
    """
    Periodic task to mark expired store invitations.
    """
    from .models import InvitationStatus, StoreInvitation

    expired_count = StoreInvitation.objects.filter(
        status=InvitationStatus.PENDING,
        expires_at__lt=timezone.now(),
    ).update(status=InvitationStatus.EXPIRED)

    if expired_count:
        logger.info("Marked %d store invitations as EXPIRED", expired_count)
