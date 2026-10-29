import json
import logging
import os
import sys
import requests
from django.conf import settings

from .models import CustomerDevice

logger = logging.getLogger(__name__)

FCM_SCOPES = ["https://www.googleapis.com/auth/firebase.messaging"]


class FCMNotificationService:
    """
    Firebase Cloud Messaging (FCM) HTTP v1 service for customer mobile push notifications.
    Customers only: seller notifications are handled strictly via email.
    """

    @classmethod
    def get_project_id(cls):
        return getattr(settings, "FIREBASE_PROJECT_ID", "") or os.getenv("FIREBASE_PROJECT_ID", "")

    @classmethod
    def is_mock_mode(cls):
        """
        Determines whether push notifications should be mocked (unit tests, local dev without credentials).
        """
        if "test" in sys.argv:
            return True

        project_id = cls.get_project_id()
        creds_path = getattr(settings, "FIREBASE_CREDENTIALS_PATH", "") or os.getenv("FIREBASE_CREDENTIALS_PATH", "")
        creds_json = getattr(settings, "FIREBASE_SERVICE_ACCOUNT_JSON", "") or os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "")
        google_creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")

        has_creds = bool((creds_path and os.path.exists(creds_path)) or creds_json or (google_creds and os.path.exists(google_creds)))
        return not (project_id and has_creds)

    @classmethod
    def get_access_token(cls):
        """
        Acquires Google OAuth2 bearer token for FCM v1 API using service account credentials.
        """
        try:
            from google.oauth2 import service_account
            import google.auth.transport.requests

            creds_path = getattr(settings, "FIREBASE_CREDENTIALS_PATH", "") or os.getenv("FIREBASE_CREDENTIALS_PATH", "")
            creds_json = getattr(settings, "FIREBASE_SERVICE_ACCOUNT_JSON", "") or os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "")
            google_creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")

            credentials = None
            if creds_path and os.path.exists(creds_path):
                credentials = service_account.Credentials.from_service_account_file(creds_path, scopes=FCM_SCOPES)
            elif creds_json:
                info = json.loads(creds_json)
                credentials = service_account.Credentials.from_service_account_info(info, scopes=FCM_SCOPES)
            elif google_creds and os.path.exists(google_creds):
                credentials = service_account.Credentials.from_service_account_file(google_creds, scopes=FCM_SCOPES)

            if not credentials:
                logger.error("No valid Firebase service account credentials found.")
                return None

            auth_request = google.auth.transport.requests.Request()
            credentials.refresh(auth_request)
            return credentials.token
        except Exception as exc:
            logger.error("Failed to acquire FCM OAuth2 access token: %s", exc, exc_info=True)
            return None

    @classmethod
    def send_to_token(cls, token: str, title: str, body: str, data: dict = None) -> bool:
        """
        Dispatches a push notification to an individual FCM device token.
        """
        if not token:
            return False

        if cls.is_mock_mode():
            logger.info(
                "[MOCK FCM] Push to token '%s...': Title='%s' Body='%s' Data=%s",
                token[:16],
                title,
                body,
                data or {},
            )
            return True

        project_id = cls.get_project_id()
        access_token = cls.get_access_token()
        if not access_token or not project_id:
            logger.warning("FCM delivery skipped: missing access token or project ID.")
            return False

        endpoint = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; UTF-8",
        }

        # Data values in FCM v1 must be strings.
        # Everything (including title & body) is placed into the data payload (data-only message),
        # allowing the mobile app full control over background display, sounds, and deep link routing.
        str_data = {str(k): str(v) for k, v in (data or {}).items()}
        if title and "title" not in str_data:
            str_data["title"] = str(title)
        if body and "body" not in str_data:
            str_data["body"] = str(body)

        payload = {
            "message": {
                "token": token,
                "data": str_data,
            }
        }

        try:
            resp = requests.post(endpoint, headers=headers, json=payload, timeout=8)
            if resp.status_code == 200:
                logger.info("FCM push sent successfully to %s...", token[:16])
                return True

            logger.error("FCM API error %d: %s", resp.status_code, resp.text)
            resp_json = resp.json() if resp.text else {}
            error_details = resp_json.get("error", {})
            error_code = error_details.get("status")

            # Token no longer valid: deactivate it in DB
            if error_code in ("UNREGISTERED", "INVALID_ARGUMENT") or resp.status_code in (400, 404):
                logger.info("Deactivating invalid or expired FCM token %s...", token[:16])
                CustomerDevice.objects.filter(registration_token=token).update(is_active=False)

            return False
        except Exception as exc:
            logger.error("FCM request failed for token %s...: %s", token[:16], exc, exc_info=True)
            return False

    @classmethod
    def send_customer_push(cls, user, title: str, body: str, data: dict = None) -> int:
        """
        Dispatches push notification to all active devices registered for the customer.
        Respects customer's push_notifications toggle in UserSettings.
        """
        if not user:
            return 0

        # Check customer notification preferences
        if hasattr(user, "settings"):
            if not user.settings.push_notifications:
                logger.info("Push notification suppressed for user %s: push_notifications=False", user.email)
                return 0

        active_tokens = list(
            CustomerDevice.objects.filter(user=user, is_active=True).values_list("registration_token", flat=True)
        )
        if not active_tokens:
            logger.debug("No active FCM tokens found for customer %s", user.email)
            return 0

        sent_count = 0
        for token in active_tokens:
            if cls.send_to_token(token, title=title, body=body, data=data):
                sent_count += 1

        return sent_count

    @classmethod
    def send_order_push_notification(cls, order, event_type: str, notes: str = "") -> int:
        """
        Formats and delivers customer order lifecycle push notifications.
        Only sends to customer if order.user is present and has order_alerts enabled.
        """
        user = order.user
        if not user:
            return 0

        if hasattr(user, "settings"):
            if not user.settings.push_notifications or not user.settings.order_alerts:
                logger.info("Order push notification suppressed for user %s per preferences", user.email)
                return 0

        messages = {
            "CONFIRMED": (
                "Order Confirmed!",
                f"Your order #{order.order_number} has been confirmed by {order.store.name}.",
            ),
            "SHIPPED": (
                "Your Package is on the Way!",
                f"Order #{order.order_number} has shipped and is on its way to you.",
            ),
            "OUT_FOR_DELIVERY": (
                "Out for Delivery!",
                f"Your order #{order.order_number} is out for delivery today.",
            ),
            "DELIVERED": (
                "Order Delivered!",
                f"Your order #{order.order_number} has arrived. Enjoy your purchase!",
            ),
            "CANCELLED": (
                "Order Cancelled",
                f"Order #{order.order_number} has been cancelled. {notes}".strip(),
            ),
            "REFUNDED": (
                "Refund Processed",
                f"Refund for order #{order.order_number} of {order.currency} {order.total_amount} has been initiated.",
            ),
            "REFUND_REJECTED": (
                "Return Request Update",
                f"Return request for order #{order.order_number} could not be approved. {notes}".strip(),
            ),
        }

        title, body = messages.get(
            event_type,
            (f"Order Update: #{order.order_number}", f"Your order status has changed to {event_type}."),
        )

        data = {
            "click_action": "FLUTTER_NOTIFICATION_CLICK",
            "type": "ORDER_STATUS",
            "order_id": str(order.id),
            "order_number": str(order.order_number),
            "status": str(event_type),
            "deep_link": f"kisekae://orders/{order.id}",
        }

        return cls.send_customer_push(user=user, title=title, body=body, data=data)

    @classmethod
    def send_tryon_push_notification(cls, tryon_request) -> int:
        """
        Formats and delivers virtual try-on completion/failure push notifications to consumer.
        """
        user = tryon_request.user
        if not user:
            return 0

        if hasattr(user, "settings"):
            if not user.settings.push_notifications:
                logger.info("TryOn push notification suppressed for user %s per preferences", user.email)
                return 0

        if tryon_request.status == "READY":
            title = "Your Virtual Try-On Look is Ready!"
            body = "Tap to view how this outfit looks on you in 3D AR."
        elif tryon_request.status == "FAILED":
            title = "Virtual Try-On Update"
            body = "Try-on generation could not be completed. Tap to try again with a different photo."
        else:
            return 0

        data = {
            "click_action": "FLUTTER_NOTIFICATION_CLICK",
            "type": "TRYON_STATUS",
            "request_id": str(tryon_request.id),
            "status": str(tryon_request.status),
            "deep_link": f"kisekae://tryon/{tryon_request.id}",
        }

        return cls.send_customer_push(user=user, title=title, body=body, data=data)
