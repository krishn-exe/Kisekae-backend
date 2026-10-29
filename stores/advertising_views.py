from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from django.core.exceptions import ValidationError as DjangoValidationError

from accounts.responses import error_response, success_response
from KiseKaeDjango.schema.helpers import api_response, ERROR_SCHEMA

from .models import StorePaymentSettings
from .payment_service import StorePaymentService
from .permissions import IsSeller, get_store_membership
from .serializers import StoreAdvertisingBudgetSerializer

STORE_ADVERTISING_BUDGET_RESPONSE_SCHEMA = api_response(
    StoreAdvertisingBudgetSerializer,
    "StoreAdvertisingBudgetResponse",
)


class StoreAdvertisingBudgetView(APIView):
    """
    Store Advertising Budget Management.
    Enables store users to view and update their monthly advertising budget.
    """
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        operation_id="store_get_advertising_budget",
        tags=["Store - Advertising"],
        summary="Get Store Monthly Advertising Budget",
        description="Retrieves the store's configured monthly advertising budget.",
        responses={
            200: STORE_ADVERTISING_BUDGET_RESPONSE_SCHEMA,
            404: ERROR_SCHEMA,
        },
    )
    def get(self, request, pk):
        store, membership = get_store_membership(request.user, pk)
        if not store or not membership:
            return error_response(
                message="Store not found or you are not a member of this store.",
                status_code=status.HTTP_404_NOT_FOUND,
                code="STORE_NOT_FOUND",
            )

        settings_obj, _ = StorePaymentSettings.objects.get_or_create(store=store)
        serializer = StoreAdvertisingBudgetSerializer(settings_obj)
        return success_response(
            data=serializer.data,
            message="Advertising budget retrieved successfully.",
        )

    @extend_schema(
        operation_id="store_update_advertising_budget",
        tags=["Store - Advertising"],
        summary="Set Store Monthly Advertising Budget",
        description="Allows store users to set or update their monthly advertising budget.",
        request=StoreAdvertisingBudgetSerializer,
        responses={
            200: STORE_ADVERTISING_BUDGET_RESPONSE_SCHEMA,
            400: ERROR_SCHEMA,
            404: ERROR_SCHEMA,
        },
    )
    def patch(self, request, pk):
        return self._update_budget(request, pk)

    @extend_schema(
        operation_id="store_replace_advertising_budget",
        tags=["Store - Advertising"],
        summary="Replace Store Monthly Advertising Budget",
        description="Allows store users to replace their monthly advertising budget.",
        request=StoreAdvertisingBudgetSerializer,
        responses={
            200: STORE_ADVERTISING_BUDGET_RESPONSE_SCHEMA,
            400: ERROR_SCHEMA,
            404: ERROR_SCHEMA,
        },
    )
    def put(self, request, pk):
        return self._update_budget(request, pk)

    def _update_budget(self, request, pk):
        store, membership = get_store_membership(request.user, pk)
        if not store or not membership:
            return error_response(
                message="Store not found or you are not a member of this store.",
                status_code=status.HTTP_404_NOT_FOUND,
                code="STORE_NOT_FOUND",
            )

        serializer = StoreAdvertisingBudgetSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return error_response(
                message="Validation failed for advertising budget.",
                details=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST,
                code="VALIDATION_ERROR",
            )

        try:
            updated_settings = StorePaymentService.update_payment_settings(
                store=store,
                user=request.user,
                data=serializer.validated_data,
            )
        except DjangoValidationError as exc:
            msg = exc.message if hasattr(exc, "message") else str(exc)
            details = exc.message_dict if hasattr(exc, "message_dict") else None
            return error_response(
                message=msg,
                details=details,
                status_code=status.HTTP_400_BAD_REQUEST,
                code="VALIDATION_ERROR",
            )

        return success_response(
            data=StoreAdvertisingBudgetSerializer(updated_settings).data,
            message="Advertising budget updated successfully.",
        )
