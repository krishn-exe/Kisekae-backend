import hashlib
import hmac
import logging
import secrets
import sys
from decimal import Decimal
import requests
from django.conf import settings
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)


class RazorpayService:
    """
    Razorpay integration client handling order generation, HMAC SHA256 signature
    verification, automated refunds, and webhook validation.
    """

    BASE_URL = "https://api.razorpay.com/v1"

    @classmethod
    def get_credentials(cls):
        key_id = getattr(settings, "RAZORPAY_KEY_ID", "rzp_test_placeholder")
        key_secret = getattr(settings, "RAZORPAY_KEY_SECRET", "placeholder_secret")
        webhook_secret = getattr(settings, "RAZORPAY_WEBHOOK_SECRET", "")
        return key_id, key_secret, webhook_secret

    @classmethod
    def is_mock_mode(cls):
        key_id, key_secret, _ = cls.get_credentials()
        return (
            "test" in sys.argv
            or key_id.startswith("rzp_test_placeholder")
            or not key_id
            or not key_secret
        )

    @classmethod
    def create_order(cls, amount_in_inr, currency="INR", receipt=None, notes=None):
        """
        Creates a payment order on Razorpay for the given amount in INR.
        Amount is converted to paise (1 INR = 100 paise).
        """
        key_id, key_secret, _ = cls.get_credentials()
        amount_paise = int(Decimal(str(amount_in_inr)) * 100)

        if cls.is_mock_mode():
            mock_order_id = f"order_mock_{secrets.token_hex(8)}"
            logger.info("Razorpay mock order created: %s for amount %s", mock_order_id, amount_paise)
            return {
                "id": mock_order_id,
                "amount": amount_paise,
                "currency": currency,
                "receipt": receipt or "",
                "status": "created",
            }

        payload = {
            "amount": amount_paise,
            "currency": currency,
            "receipt": receipt or "",
            "notes": notes or {},
        }

        try:
            response = requests.post(
                f"{cls.BASE_URL}/orders",
                auth=(key_id, key_secret),
                json=payload,
                timeout=10,
            )
            data = response.json()
            if response.status_code != 200:
                err_msg = data.get("error", {}).get("description", "Failed to create Razorpay order.")
                logger.error("Razorpay order creation failed: %s", err_msg)
                raise ValidationError(f"Payment gateway error: {err_msg}")
            return data
        except requests.RequestException as exc:
            logger.exception("Network error communicating with Razorpay: %s", exc)
            raise ValidationError("Unable to reach payment gateway. Please try again.")

    @classmethod
    def verify_payment_signature(cls, razorpay_order_id, razorpay_payment_id, razorpay_signature):
        """
        Cryptographically verifies the Razorpay payment signature using HMAC SHA256.
        """
        _, key_secret, _ = cls.get_credentials()

        if cls.is_mock_mode():
            # In testing or mock mode, accept mock_signature or any non-empty test signature
            if razorpay_signature in {"mock_signature", "test_signature"} or razorpay_signature.startswith("mock_"):
                return True

        if not razorpay_order_id or not razorpay_payment_id or not razorpay_signature:
            return False

        message = f"{razorpay_order_id}|{razorpay_payment_id}".encode("utf-8")
        generated_signature = hmac.new(
            key_secret.encode("utf-8"),
            message,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(generated_signature, razorpay_signature)

    @classmethod
    def initiate_refund(cls, razorpay_payment_id, amount_in_inr=None, notes=None):
        """
        Initiates a full or partial refund against a captured payment.
        """
        key_id, key_secret, _ = cls.get_credentials()

        amount_paise = None
        if amount_in_inr is not None:
            amount_paise = int(Decimal(str(amount_in_inr)) * 100)

        if cls.is_mock_mode() or not razorpay_payment_id or razorpay_payment_id.startswith("pay_mock"):
            mock_refund_id = f"rfnd_mock_{secrets.token_hex(8)}"
            logger.info("Razorpay mock refund processed: %s for payment %s", mock_refund_id, razorpay_payment_id)
            return {
                "id": mock_refund_id,
                "payment_id": razorpay_payment_id,
                "amount": amount_paise or 0,
                "currency": "INR",
                "status": "processed",
            }

        payload = {"notes": notes or {}}
        if amount_paise is not None:
            payload["amount"] = amount_paise
        else:
            payload["reverse_all"] = 1

        try:
            response = requests.post(
                f"{cls.BASE_URL}/payments/{razorpay_payment_id}/refund",
                auth=(key_id, key_secret),
                json=payload,
                timeout=10,
            )
            data = response.json()
            if response.status_code not in (200, 201):
                err_msg = data.get("error", {}).get("description", "Failed to initiate refund.")
                logger.error("Razorpay refund error: %s", err_msg)
                raise ValidationError(f"Payment refund failed: {err_msg}")
            return data
        except requests.RequestException as exc:
            logger.exception("Network error initiating refund with Razorpay: %s", exc)
            raise ValidationError("Unable to reach payment gateway for refund. Please try again.")

    @classmethod
    def verify_webhook_signature(cls, body_bytes, signature):
        """
        Verifies an incoming webhook payload using the webhook secret.
        """
        _, _, webhook_secret = cls.get_credentials()
        if not webhook_secret or not signature:
            return cls.is_mock_mode()

        generated_signature = hmac.new(
            webhook_secret.encode("utf-8"),
            body_bytes,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(generated_signature, signature)
