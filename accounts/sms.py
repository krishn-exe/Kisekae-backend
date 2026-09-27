import requests
from django.conf import settings

FAST2SMS_URL = "https://www.fast2sms.com/dev/bulkV2"


def send_otp_sms(phone_number: str, raw_code: str):
    if not getattr(settings, "FAST2SMS_API_KEY", None):
        raise RuntimeError("FAST2SMS_API_KEY is not configured in settings.")

    digits = "".join(ch for ch in str(phone_number) if ch.isdigit())
    number = digits[-10:]

    if len(number) != 10:
        raise ValueError(
            f"Invalid phone number for SMS delivery: '{phone_number}'. Must contain at least 10 digits."
        )

    response = requests.get(
        FAST2SMS_URL,
        params={
            "authorization": settings.FAST2SMS_API_KEY,
            "variables_values": raw_code,
            "route": "otp",
            "numbers": number,
        },
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()

    if not data.get("return"):
        raise RuntimeError(f"Fast2SMS failed to send OTP: {data}")