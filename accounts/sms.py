import logging
from django.conf import settings
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

logger = logging.getLogger(__name__)


def send_otp_sms(phone_number: str, raw_code: str):
    account_sid = getattr(settings, "TWILIO_ACCOUNT_SID", None)
    auth_token = getattr(settings, "TWILIO_AUTH_TOKEN", None)
    from_number = getattr(settings, "TWILIO_PHONE_NUMBER", None)
    messaging_service_sid = getattr(settings, "TWILIO_MESSAGING_SERVICE_SID", None)

    if not account_sid or not auth_token or (not from_number and not messaging_service_sid):
        raise RuntimeError(
            "Twilio credentials (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_PHONE_NUMBER or "
            "TWILIO_MESSAGING_SERVICE_SID) are not configured in settings."
        )

    to_number = str(phone_number).strip()
    if not to_number.startswith("+"):
        digits = "".join(ch for ch in to_number if ch.isdigit())
        if len(digits) == 10:
            to_number = f"+91{digits}"
        else:
            to_number = f"+{digits}"

    client = Client(account_sid, auth_token)

    msg_payload = {
        "body": f"Your Kisekae login code is {raw_code}. It expires in 5 minutes.",
        "to": to_number,
    }

    if messaging_service_sid:
        msg_payload["messaging_service_sid"] = messaging_service_sid
    else:
        msg_payload["from_"] = from_number

    try:
        message = client.messages.create(**msg_payload)
        logger.info("Twilio SMS sent to %s with SID %s", to_number, message.sid)
        return message.sid
    except TwilioRestException as e:
        logger.error("Twilio error sending SMS to %s: %s (code %s)", to_number, e.msg, e.code)
        raise RuntimeError(f"Twilio error: {e.msg} (Code: {e.code})") from e