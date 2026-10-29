from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.responses import error_response, success_response
from KiseKaeDjango.schema.helpers import api_response, ERROR_SCHEMA

from .models import CustomerDevice
from .serializers import (
    CustomerDeviceSerializer,
    RegisterDeviceSerializer,
    UnregisterDeviceSerializer,
)

CUSTOMER_DEVICE_RESPONSE_SCHEMA = api_response(CustomerDeviceSerializer, "CustomerDeviceResponse")
CUSTOMER_DEVICE_LIST_RESPONSE_SCHEMA = api_response(CustomerDeviceSerializer, "CustomerDeviceListResponse", many=True)


class CustomerDeviceView(APIView):
    """
    Device management endpoints for customer Firebase Cloud Messaging (FCM) push notifications.
    Allows consumer mobile apps to register, refresh, and unregister push tokens upon login/logout.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Profile - Personal Info"],
        summary="List customer registered devices",
        description="Returns list of all active FCM device tokens registered for the authenticated customer.",
        responses={200: CUSTOMER_DEVICE_LIST_RESPONSE_SCHEMA},
    )
    def get(self, request):
        devices = CustomerDevice.objects.filter(user=request.user, is_active=True)
        return success_response(
            message="Active devices fetched successfully.",
            data=CustomerDeviceSerializer(devices, many=True).data,
            status_code=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["Profile - Personal Info"],
        summary="Register or update customer FCM device token",
        description="Registers an FCM push notification token for the customer's mobile device. "
                    "If the token already exists, re-assigns ownership to the current user and marks it active.",
        request=RegisterDeviceSerializer,
        responses={200: CUSTOMER_DEVICE_RESPONSE_SCHEMA, 400: ERROR_SCHEMA},
    )
    def post(self, request):
        serializer = RegisterDeviceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        token = serializer.validated_data["registration_token"]
        device_type = serializer.validated_data.get("device_type", "ANDROID")
        device_name = serializer.validated_data.get("device_name", "")

        device, created = CustomerDevice.objects.update_or_create(
            registration_token=token,
            defaults={
                "user": request.user,
                "device_type": device_type,
                "device_name": device_name,
                "is_active": True,
            },
        )

        msg = "Device registered for push notifications." if created else "Device token refreshed successfully."
        return success_response(
            message=msg,
            data=CustomerDeviceSerializer(device).data,
            status_code=status.HTTP_200_OK if not created else status.HTTP_201_CREATED,
        )

    @extend_schema(
        tags=["Profile - Personal Info"],
        summary="Unregister customer FCM device token",
        description="Deactivates an FCM device token (e.g. upon customer logout) so future push notifications are stopped.",
        request=UnregisterDeviceSerializer,
        responses={200: api_response(name="UnregisterDeviceResponse"), 400: ERROR_SCHEMA},
    )
    def delete(self, request):
        serializer = UnregisterDeviceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        token = serializer.validated_data["registration_token"]
        updated = CustomerDevice.objects.filter(
            user=request.user,
            registration_token=token,
        ).update(is_active=False)

        if not updated:
            return error_response(
                message="Device token not found or already deactivated.",
                code="DEVICE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        return success_response(
            message="Device token unregistered successfully.",
            data={"unregistered": True},
            status_code=status.HTTP_200_OK,
        )
