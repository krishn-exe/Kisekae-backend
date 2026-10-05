from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.core.management import call_command


@shared_task
def flush_expired_tokens_task():
    call_command('flushexpiredtokens')


@shared_task(autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def send_otp_email_task(target_email, raw_code, purpose="login"):
    if purpose == "password_reset":
        subject = "Your Kisekae password reset code"
        action_desc = "password reset"
    elif purpose == "verify_email":
        subject = "Your Kisekae email verification code"
        action_desc = "email verification"
    else:
        subject = "Your Kisekae login code"
        action_desc = "login"

    send_mail(
        subject=subject,
        message=f"Your {action_desc} code is {raw_code}. It expires in 5 minutes.",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[target_email],
    )
