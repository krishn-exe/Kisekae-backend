from decimal import Decimal
import logging
from django.core.exceptions import ValidationError
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.responses import error_response, success_response
from orders.razorpay_service import RazorpayService
from stores.pagination import StorePagination
from KiseKaeDjango.schema.helpers import api_response, paginated_response, ERROR_SCHEMA

from .models import Wallet, WalletTransaction
from .wallet_serializers import (
    WalletAddFundsResponseSerializer,
    WalletAddFundsSerializer,
    WalletSerializer,
    WalletTransactionSerializer,
    WalletVerifyPaymentSerializer,
)
from .wallet_service import WalletService

logger = logging.getLogger(__name__)

WALLET_DETAIL_SCHEMA = api_response(WalletSerializer, "WalletDetailResponse")
WALLET_ADD_FUNDS_SCHEMA = api_response(WalletAddFundsResponseSerializer, "WalletAddFundsResponse")
WALLET_TRANSACTIONS_SCHEMA = paginated_response(WalletTransactionSerializer, "WalletTransactionsResponse")


class WalletDetailView(APIView):
    """
    Retrieve the current customer's wallet balance, remaining limits, and recent activity.
    """
    permission_classes = [IsAuthenticated]
    throttle_scope = "user_detail"

    @extend_schema(
        tags=["Profile - Wallet"],
        summary="Get customer wallet details",
        description="Retrieves current wallet balance, 10k INR maximum limit, and recent transactions.",
        responses={200: WALLET_DETAIL_SCHEMA, 401: ERROR_SCHEMA},
    )
    def get(self, request):
        wallet = WalletService.get_or_create(request.user)
        return success_response(
            message="Wallet details retrieved successfully.",
            data=WalletSerializer(wallet).data,
        )


class WalletAddFundsView(APIView):
    """
    Initiates adding funds to user's wallet up to the ₹10,000 balance limit.
    Creates a Razorpay order for client checkout.
    """
    permission_classes = [IsAuthenticated]
    throttle_scope = "user_detail"

    @extend_schema(
        tags=["Profile - Wallet"],
        summary="Initiate wallet top-up",
        description="Creates a Razorpay payment order to top up wallet funds. Enforces maximum limit of ₹10,000.",
        request=WalletAddFundsSerializer,
        responses={200: WALLET_ADD_FUNDS_SCHEMA, 400: ERROR_SCHEMA, 401: ERROR_SCHEMA},
    )
    def post(self, request):
        serializer = WalletAddFundsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        amount = serializer.validated_data["amount"]

        wallet = WalletService.get_or_create(request.user)
        if wallet.balance + amount > WalletService.LIMIT:
            max_allowed = max(Decimal("0.00"), WalletService.LIMIT - wallet.balance)
            return error_response(
                f"Cannot add ₹{amount:.2f}. Your wallet limit is ₹{WalletService.LIMIT:.2f} "
                f"and current balance is ₹{wallet.balance:.2f}. Maximum you can add is ₹{max_allowed:.2f}.",
                code="WALLET_LIMIT_EXCEEDED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        try:
            rzp_order = RazorpayService.create_order(
                amount_in_inr=amount,
                currency="INR",
                receipt=f"WLT-{request.user.id}",
                notes={"type": "wallet_topup", "user_id": request.user.id},
            )
        except ValidationError as exc:
            return error_response(str(exc), code="GATEWAY_ERROR", status_code=status.HTTP_400_BAD_REQUEST)

        key_id, _, _ = RazorpayService.get_credentials()
        amount_paise = int(amount * 100)

        return success_response(
            message="Razorpay payment order created for wallet top-up.",
            data={
                "razorpay_order_id": rzp_order.get("id", ""),
                "amount": amount,
                "amount_in_paise": amount_paise,
                "currency": "INR",
                "key_id": key_id,
            },
        )


class WalletAddFundsVerifyView(APIView):
    """
    Verifies the Razorpay payment signature and credits the user's wallet.
    """
    permission_classes = [IsAuthenticated]
    throttle_scope = "user_detail"

    @extend_schema(
        tags=["Profile - Wallet"],
        summary="Verify wallet top-up payment",
        description="Verifies the Razorpay HMAC signature and credits the top-up amount to customer's wallet.",
        request=WalletVerifyPaymentSerializer,
        responses={200: WALLET_DETAIL_SCHEMA, 400: ERROR_SCHEMA, 401: ERROR_SCHEMA},
    )
    def post(self, request):
        serializer = WalletVerifyPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        is_valid = RazorpayService.verify_payment_signature(
            razorpay_order_id=data["razorpay_order_id"],
            razorpay_payment_id=data["razorpay_payment_id"],
            razorpay_signature=data["razorpay_signature"],
        )
        if not is_valid:
            return error_response(
                "Invalid payment signature from payment gateway.",
                code="INVALID_PAYMENT_SIGNATURE",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # Check if transaction was already credited for this payment ID to prevent double credits
        existing = WalletTransaction.objects.filter(razorpay_payment_id=data["razorpay_payment_id"]).first()
        if existing:
            wallet = WalletService.get_or_create(request.user)
            return success_response(
                message="Funds have already been credited.",
                data=WalletSerializer(wallet).data,
            )

        try:
            wallet = WalletService.add_funds(
                user=request.user,
                amount=data["amount"],
                razorpay_payment_id=data["razorpay_payment_id"],
                razorpay_order_id=data["razorpay_order_id"],
            )
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc)
            return error_response(msg, code=getattr(exc, "code", "TOPUP_FAILED"), status_code=status.HTTP_400_BAD_REQUEST)

        return success_response(
            message=f"₹{data['amount']} credited to wallet successfully.",
            data=WalletSerializer(wallet).data,
        )


class WalletTransactionListView(APIView):
    """
    Paginated list of all wallet transactions (credits, debits, refunds).
    """
    permission_classes = [IsAuthenticated]
    throttle_scope = "user_detail"
    pagination_class = StorePagination

    @extend_schema(
        tags=["Profile - Wallet"],
        summary="List customer wallet transactions",
        description="Returns paginated transaction history for customer's wallet.",
        responses={200: WALLET_TRANSACTIONS_SCHEMA, 401: ERROR_SCHEMA},
    )
    def get(self, request):
        wallet = WalletService.get_or_create(request.user)
        queryset = wallet.transactions.all()

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request)
        serializer = WalletTransactionSerializer(page, many=True)

        return success_response(
            message="Wallet transactions retrieved successfully.",
            data={
                "count": paginator.page.paginator.count,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "results": serializer.data,
            },
        )
