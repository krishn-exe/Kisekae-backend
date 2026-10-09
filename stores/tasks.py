import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def send_store_invitation_email_task(target_email, store_name, inviter_name, raw_token):
    """
    Sends an invitation email with a deep link and raw token to manage a store.
    """
    subject = f"You're invited to manage {store_name} on Kisekae"
    accept_url = f"kisekae-seller://invitations/accept?token={raw_token}"
    message = (
        f"Hello,\n\n"
        f"{inviter_name} has invited you to join the team managing '{store_name}' on Kisekae.\n\n"
        f"If you have the Kisekae Seller app installed, open this link to accept the invitation:\n"
        f"{accept_url}\n\n"
        f"Or accept directly inside the app by entering this invitation token:\n"
        f"{raw_token}\n\n"
        f"Note: This invitation will expire in {getattr(settings, 'STORE_INVITATION_TTL_DAYS', 7)} days.\n\n"
        f"If you did not expect this invitation, you can ignore this email."
    )
    send_mail(
        subject=subject,
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[target_email],
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
