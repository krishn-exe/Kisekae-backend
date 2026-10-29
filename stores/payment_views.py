from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.responses import error_response, success_response
from KiseKaeDjango.schema.helpers import api_response, ERROR_SCHEMA, paginated_response

from .models import (
    StoreBankAccount,
    StorePaymentSettings,
    StoreRole,
    StoreSettlement,
)
from .pagination import StorePagination
from .payment_service import StorePaymentService
from .permissions import IsSeller, get_store_membership
from .serializers import (
    StoreBankAccountSerializer,
    StoreDirectSettleSerializer,
    StorePaymentDashboardSerializer,
    StorePaymentSettingsSerializer,
    StoreSettlementSerializer,
)

PAYMENTS_DASHBOARD_RESPONSE_SCHEMA = api_response(StorePaymentDashboardSerializer, "StorePaymentDashboardResponse")
STORE_BANK_ACCOUNT_RESPONSE_SCHEMA = api_response(StoreBankAccountSerializer, "StoreBankAccountResponse")
STORE_PAYMENT_SETTINGS_RESPONSE_SCHEMA = api_response(StorePaymentSettingsSerializer, "StorePaymentSettingsResponse")
STORE_SETTLEMENT_RESPONSE_SCHEMA = api_response(StoreSettlementSerializer, "StoreSettlementResponse")
STORE_SETTLEMENT_LIST_RESPONSE_SCHEMA = paginated_response(StoreSettlementSerializer, "StoreSettlementListResponse")


class StorePaymentsDashboardView(APIView):
    """
    Payments dashboard providing monthly breakdown:
    - total revenue
    - 15% platform cut
    - absorbed coupons
    - advertising cost
    - final gross profit
    - unsettled balance & automatic settlement schedule
    """
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        operation_id="store_payments_dashboard",
        tags=["Store - Payments"],
        summary="Store Payments Dashboard & Monthly Summary",
        description="Returns financial metrics for the store for a specific month (defaults to current month). "
                    "Reflects total revenue, 15% platform cut, coupon costs absorbed by the platform from the cut, "
                    "advertising budget/cost, and final gross profit, plus settlement balances and bank info.",
        parameters=[
            OpenApiParameter(
                name="year",
                description="Target calendar year (e.g. 2026)",
                required=False,
                type=int,
            ),
            OpenApiParameter(
                name="month",
                description="Target calendar month (1 to 12)",
                required=False,
                type=int,
            ),
        ],
        responses={
            200: PAYMENTS_DASHBOARD_RESPONSE_SCHEMA,
            400: ERROR_SCHEMA,
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

        year_param = request.query_params.get("year")
        month_param = request.query_params.get("month")

        try:
            summary = StorePaymentService.get_monthly_summary(
                store=store,
                year=year_param,
                month=month_param,
            )
        except DjangoValidationError as exc:
            return error_response(
                message=exc.message if hasattr(exc, "message") else str(exc),
                status_code=status.HTTP_400_BAD_REQUEST,
                code="INVALID_DATE_PARAM",
            )
        except ValueError:
            return error_response(
                message="Year and month must be valid integers.",
                status_code=status.HTTP_400_BAD_REQUEST,
                code="INVALID_DATE_FORMAT",
            )

        return success_response(
            data=summary,
            message="Payments dashboard summary retrieved successfully.",
        )


class StoreBankAccountView(APIView):
    """
    Bank account management for store payouts.
    Only the store owner can modify account number, IFSC code, and account details.
    """
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        operation_id="store_get_bank_account",
        tags=["Store - Payments"],
        summary="Get Store Bank Account Details",
        description="Retrieves the store's linked bank account details. Members see masked account number; owner sees full account details.",
        responses={
            200: STORE_BANK_ACCOUNT_RESPONSE_SCHEMA,
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

        bank_account = StoreBankAccount.objects.filter(store=store).first()
        if not bank_account:
            return success_response(
                data=None,
                message="No bank account linked to this store.",
            )

        serializer = StoreBankAccountSerializer(
            bank_account,
            context={"request": request, "show_full_account": membership.role == StoreRole.OWNER},
        )
        return success_response(
            data=serializer.data,
            message="Bank account details retrieved successfully.",
        )

    @extend_schema(
        operation_id="store_create_bank_account",
        tags=["Store - Payments"],
        summary="Create Store Bank Account",
        description="Only the store owner can add bank account details (account number, IFSC code, etc.). "
                    "When bank details are first added, the automatic monthly settlement date is set by default to the current day of month.",
        request=StoreBankAccountSerializer,
        responses={
            200: STORE_BANK_ACCOUNT_RESPONSE_SCHEMA,
            400: ERROR_SCHEMA,
            403: ERROR_SCHEMA,
            404: ERROR_SCHEMA,
        },
    )
    def post(self, request, pk):
        return self._save_bank_account(request, pk)

    @extend_schema(
        operation_id="store_replace_bank_account",
        tags=["Store - Payments"],
        summary="Replace Store Bank Account",
        description="Only the store owner can modify bank account details (account number, IFSC code, etc.).",
        request=StoreBankAccountSerializer,
        responses={
            200: STORE_BANK_ACCOUNT_RESPONSE_SCHEMA,
            400: ERROR_SCHEMA,
            403: ERROR_SCHEMA,
            404: ERROR_SCHEMA,
        },
    )
    def put(self, request, pk):
        return self._save_bank_account(request, pk)

    @extend_schema(
        operation_id="store_update_bank_account",
        tags=["Store - Payments"],
        summary="Partially Update Store Bank Account",
        description="Only the store owner can modify bank account details (account number, IFSC code, etc.).",
        request=StoreBankAccountSerializer,
        responses={
            200: STORE_BANK_ACCOUNT_RESPONSE_SCHEMA,
            400: ERROR_SCHEMA,
            403: ERROR_SCHEMA,
            404: ERROR_SCHEMA,
        },
    )
    def patch(self, request, pk):
        return self._save_bank_account(request, pk)

    def _save_bank_account(self, request, pk):
        store, membership = get_store_membership(request.user, pk)
        if not store or not membership:
            return error_response(
                message="Store not found or you are not a member of this store.",
                status_code=status.HTTP_404_NOT_FOUND,
                code="STORE_NOT_FOUND",
            )

        # STRICT PERMISSION: ONLY STORE OWNER
        if membership.role != StoreRole.OWNER:
            return error_response(
                message="Only the store owner can modify bank account details.",
                status_code=status.HTTP_403_FORBIDDEN,
                code="OWNER_PERMISSION_REQUIRED",
            )

        serializer = StoreBankAccountSerializer(data=request.data, partial=(request.method == "PATCH"))
        if not serializer.is_valid():
            return error_response(
                message="Bank account validation failed.",
                details=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST,
                code="VALIDATION_ERROR",
            )

        try:
            bank_account = StorePaymentService.set_bank_account(
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

        out_serializer = StoreBankAccountSerializer(
            bank_account,
            context={"request": request, "show_full_account": True},
        )
        return success_response(
            data=out_serializer.data,
            message="Bank account details saved successfully.",
        )


class StorePaymentSettingsView(APIView):
    """
    Payment settings management:
    - Settlement day of month (1 to 31)
    - Auto-settlement toggle
    - Monthly advertising budget
    """
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        operation_id="store_get_payment_settings",
        tags=["Store - Payments"],
        summary="Get Store Payment Settings",
        description="Retrieves the automatic settlement schedule and monthly advertising budget.",
        responses={
            200: STORE_PAYMENT_SETTINGS_RESPONSE_SCHEMA,
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
        serializer = StorePaymentSettingsSerializer(settings_obj)
        return success_response(
            data=serializer.data,
            message="Payment settings retrieved successfully.",
        )

    @extend_schema(
        operation_id="store_update_payment_settings",
        tags=["Store - Payments"],
        summary="Update Store Payment Settings & Advertising Budget",
        description="Allows store users to modify the automatic settlement day of month, "
                    "toggle auto-settlement, and set the monthly advertising budget.",
        request=StorePaymentSettingsSerializer,
        responses={
            200: STORE_PAYMENT_SETTINGS_RESPONSE_SCHEMA,
            400: ERROR_SCHEMA,
            404: ERROR_SCHEMA,
        },
    )
    def patch(self, request, pk):
        return self._update_settings(request, pk)

    @extend_schema(
        operation_id="store_replace_payment_settings",
        tags=["Store - Payments"],
        summary="Replace Store Payment Settings & Advertising Budget",
        description="Allows store users to replace the automatic settlement day of month, "
                    "toggle auto-settlement, and set the monthly advertising budget.",
        request=StorePaymentSettingsSerializer,
        responses={
            200: STORE_PAYMENT_SETTINGS_RESPONSE_SCHEMA,
            400: ERROR_SCHEMA,
            404: ERROR_SCHEMA,
        },
    )
    def put(self, request, pk):
        return self._update_settings(request, pk)

    def _update_settings(self, request, pk):
        store, membership = get_store_membership(request.user, pk)
        if not store or not membership:
            return error_response(
                message="Store not found or you are not a member of this store.",
                status_code=status.HTTP_404_NOT_FOUND,
                code="STORE_NOT_FOUND",
            )

        serializer = StorePaymentSettingsSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return error_response(
                message="Validation failed for payment settings.",
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
            data=StorePaymentSettingsSerializer(updated_settings).data,
            message="Payment settings updated successfully.",
        )


class StoreDirectSettleView(APIView):
    """
    Allows store users to trigger a direct on-demand settlement into the linked bank account.
    """
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        operation_id="store_direct_settlement",
        tags=["Store - Payments"],
        summary="Settle Amount Directly into Linked Bank Account",
        description="Dispatches available unsettled earnings directly into the store's linked bank account. "
                    "Optionally accepts a custom amount; if omitted, settles the full available unsettled balance.",
        request=StoreDirectSettleSerializer,
        responses={
            201: STORE_SETTLEMENT_RESPONSE_SCHEMA,
            400: ERROR_SCHEMA,
            404: ERROR_SCHEMA,
        },
    )
    def post(self, request, pk):
        store, membership = get_store_membership(request.user, pk)
        if not store or not membership:
            return error_response(
                message="Store not found or you are not a member of this store.",
                status_code=status.HTTP_404_NOT_FOUND,
                code="STORE_NOT_FOUND",
            )

        serializer = StoreDirectSettleSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                message="Validation failed for settlement request.",
                details=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST,
                code="VALIDATION_ERROR",
            )

        amount = serializer.validated_data.get("amount")
        notes = serializer.validated_data.get("notes", "")

        try:
            settlement = StorePaymentService.settle_directly(
                store=store,
                user=request.user,
                amount=amount,
                notes=notes,
            )
        except DjangoValidationError as exc:
            code = getattr(exc, "code", None) or "SETTLEMENT_FAILED"
            msg = exc.message if hasattr(exc, "message") else str(exc)
            details = exc.message_dict if hasattr(exc, "message_dict") else None
            return error_response(
                message=msg,
                details=details,
                status_code=status.HTTP_400_BAD_REQUEST,
                code=code,
            )

        return success_response(
            data=StoreSettlementSerializer(settlement).data,
            message=f"Settlement of INR {settlement.amount} initiated successfully.",
            status_code=status.HTTP_201_CREATED,
        )


class StoreSettlementListView(APIView):
    """
    Lists historical payouts / settlements for the store.
    """
    permission_classes = [IsAuthenticated, IsSeller]
    pagination_class = StorePagination

    @extend_schema(
        operation_id="store_list_settlements",
        tags=["Store - Payments"],
        summary="List Store Settlements",
        description="Returns historical payout transactions (direct and automatic) for the store.",
        responses={
            200: STORE_SETTLEMENT_LIST_RESPONSE_SCHEMA,
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

        settlements = StoreSettlement.objects.filter(store=store).order_by("-created_at")
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(settlements, request)
        serializer = StoreSettlementSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)
