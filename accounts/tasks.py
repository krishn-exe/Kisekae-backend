from celery import shared_task
from django.core.management import call_command
from notifications.email_service import NotificationEmailService


@shared_task
def flush_expired_tokens_task():
    call_command('flushexpiredtokens')
    from datetime import timedelta
    from django.utils import timezone
    from .models import PendingRegistration
    cutoff = timezone.now() - timedelta(minutes=15)
    PendingRegistration.objects.filter(created_at__lt=cutoff).delete()



@shared_task(autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def send_otp_email_task(target_email, raw_code, purpose="login"):
    if purpose == "password_reset":
        subject = "Your Kisekae password reset code"
        action_desc = "password reset"
        heading = "Reset Your Password"
    elif purpose == "verify_email":
        subject = "Your Kisekae email verification code"
        action_desc = "email verification"
        heading = "Verify Your Email Address"
    else:
        subject = "Your Kisekae login code"
        action_desc = "login"
        heading = "Log In to Kisekae"

    plain_message = f"Your {action_desc} code is {raw_code}. It expires in 5 minutes."
    context = {
        "heading": heading,
        "action_desc": action_desc,
        "raw_code": raw_code,
    }

    NotificationEmailService.send_multipart_email(
        to_email=target_email,
        subject=subject,
        template_name="emails/otp_email.html",
        context=context,
        plain_text_fallback=plain_message,
    )
