from rest_framework import serializers

from .models import CustomerDevice, DevicePlatform


class CustomerDeviceSerializer(serializers.ModelSerializer):
    """
    Serializes registered customer FCM device tokens and metadata.
    """
    class Meta:
        model = CustomerDevice
        fields = [
            "id",
            "registration_token",
            "device_type",
            "device_name",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "is_active", "created_at", "updated_at"]


class RegisterDeviceSerializer(serializers.Serializer):
    """
    Payload for registering or updating an FCM registration token for a customer device.
    """
    registration_token = serializers.CharField(
        max_length=512,
        min_length=10,
        help_text="Firebase Cloud Messaging device registration token received from the client SDK.",
    )
    device_type = serializers.ChoiceField(
        choices=DevicePlatform.choices,
        default=DevicePlatform.ANDROID,
        help_text="Operating platform of the client device (ANDROID, IOS, WEB).",
    )
    device_name = serializers.CharField(
        max_length=150,
        required=False,
        allow_blank=True,
        default="",
        help_text="Optional device hardware model or user-facing nickname (e.g. Pixel 8, iPhone 15).",
    )


class UnregisterDeviceSerializer(serializers.Serializer):
    """
    Payload for deregistering or deactivating an FCM token (e.g. upon user logout).
    """
    registration_token = serializers.CharField(
        max_length=512,
        min_length=10,
        help_text="Registration token to deactivate.",
    )
