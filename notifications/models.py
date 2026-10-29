from django.conf import settings
from django.db import models


class DevicePlatform(models.TextChoices):
    ANDROID = "ANDROID", "Android"
    IOS = "IOS", "iOS"
    WEB = "WEB", "Web"


class CustomerDevice(models.Model):
    """
    Stores FCM registration tokens for customer mobile devices.
    Used exclusively for dispatching Firebase Cloud Messaging push notifications to consumers.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="fcm_devices",
    )
    registration_token = models.CharField(
        max_length=512,
        unique=True,
        db_index=True,
        help_text="Firebase Cloud Messaging (FCM) registration token.",
    )
    device_type = models.CharField(
        max_length=20,
        choices=DevicePlatform.choices,
        default=DevicePlatform.ANDROID,
        help_text="Target operating platform (Android, iOS, Web).",
    )
    device_name = models.CharField(
        max_length=150,
        blank=True,
        default="",
        help_text="Optional device hardware model (e.g. Pixel 8, iPhone 15).",
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Whether this device token is active and eligible to receive push notifications.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["user", "is_active"]),
        ]

    def __str__(self):
        return f"{self.user.email} - {self.device_type} ({self.registration_token[:16]}...)"
