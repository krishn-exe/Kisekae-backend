from decimal import Decimal
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import Wallet, WalletTransaction
from .wallet_service import WalletService


class WalletTransactionSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source="order.order_number", read_only=True, allow_null=True)

    class Meta:
        model = WalletTransaction
        fields = [
            "id",
            "amount",
            "txn_type",
            "order_number",
            "razorpay_payment_id",
            "description",
            "created_at",
        ]


class WalletSerializer(serializers.ModelSerializer):
    balance_limit = serializers.SerializerMethodField()
    remaining_limit = serializers.SerializerMethodField()
    recent_transactions = serializers.SerializerMethodField()

    class Meta:
        model = Wallet
        fields = [
            "id",
            "balance",
            "balance_limit",
            "remaining_limit",
            "recent_transactions",
            "updated_at",
        ]

    def get_balance_limit(self, obj) -> str:
        return str(WalletService.LIMIT)

    def get_remaining_limit(self, obj) -> str:
        remaining = max(Decimal("0.00"), WalletService.LIMIT - obj.balance)
        return str(remaining)

    @extend_schema_field(WalletTransactionSerializer(many=True))
    def get_recent_transactions(self, obj):
        txns = obj.transactions.all()[:10]
        return WalletTransactionSerializer(txns, many=True).data


class WalletAddFundsSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("1.00"),
        help_text="Amount in INR to add to wallet (balance limit: ₹10,000).",
    )


class WalletAddFundsResponseSerializer(serializers.Serializer):
    razorpay_order_id = serializers.CharField()
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    amount_in_paise = serializers.IntegerField()
    currency = serializers.CharField()
    key_id = serializers.CharField()


class WalletVerifyPaymentSerializer(serializers.Serializer):
    razorpay_payment_id = serializers.CharField(help_text="Payment ID captured by Razorpay SDK.")
    razorpay_order_id = serializers.CharField(help_text="Razorpay Order ID.")
    razorpay_signature = serializers.CharField(help_text="HMAC signature from Razorpay SDK.")
    amount = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("1.00"),
        help_text="Amount in INR to credit.",
    )
