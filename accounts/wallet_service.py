from decimal import Decimal
import logging
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Wallet, WalletTransaction, WalletTransactionType

logger = logging.getLogger(__name__)


class WalletService:
    LIMIT = Decimal("10000.00")

    @staticmethod
    def get_or_create(user) -> Wallet:
        wallet, _ = Wallet.objects.get_or_create(user=user)
        return wallet

    @staticmethod
    @transaction.atomic
    def add_funds(user, amount: Decimal, razorpay_payment_id="", razorpay_order_id="") -> Wallet:
        """
        Top-up customer wallet.
        Enforces maximum limit of ₹10,000.
        """
        amount = Decimal(str(amount))
        if amount <= Decimal("0.00"):
            raise ValidationError("Top-up amount must be greater than zero.", code="INVALID_AMOUNT")

        wallet = Wallet.objects.select_for_update().filter(user=user).first()
        if not wallet:
            wallet = Wallet.objects.create(user=user)

        if wallet.balance + amount > WalletService.LIMIT:
            raise ValidationError(
                f"Adding ₹{amount:.2f} exceeds your wallet balance limit of ₹{WalletService.LIMIT:.2f}. "
                f"Your current balance is ₹{wallet.balance:.2f}.",
                code="WALLET_LIMIT_EXCEEDED",
            )

        wallet.balance += amount
        wallet.save(update_fields=["balance", "updated_at"])

        WalletTransaction.objects.create(
            wallet=wallet,
            amount=amount,
            txn_type=WalletTransactionType.CREDIT,
            razorpay_payment_id=razorpay_payment_id,
            razorpay_order_id=razorpay_order_id,
            description="Wallet funds top-up",
        )
        return wallet

    @staticmethod
    @transaction.atomic
    def debit(user, amount_to_cover: Decimal, order=None) -> Decimal:
        """
        Debits the entire available wallet balance up to amount_to_cover.
        Returns the actual amount deducted.
        """
        amount_to_cover = Decimal(str(amount_to_cover))
        if amount_to_cover <= Decimal("0.00"):
            return Decimal("0.00")

        wallet = Wallet.objects.select_for_update().filter(user=user).first()
        if not wallet or wallet.balance <= Decimal("0.00"):
            return Decimal("0.00")

        debit_amount = min(wallet.balance, amount_to_cover)
        if debit_amount <= Decimal("0.00"):
            return Decimal("0.00")

        wallet.balance -= debit_amount
        wallet.save(update_fields=["balance", "updated_at"])

        order_desc = f" for Order {order.order_number}" if order and getattr(order, "order_number", None) else ""
        WalletTransaction.objects.create(
            wallet=wallet,
            amount=debit_amount,
            txn_type=WalletTransactionType.DEBIT,
            order=order,
            description=f"Debited{order_desc}",
        )
        return debit_amount

    @staticmethod
    @transaction.atomic
    def refund_to_wallet(user, amount: Decimal, order=None, description="") -> Wallet:
        """
        Re-credits amount back to user's wallet on cancellation, reservation expiry, or refund.
        """
        amount = Decimal(str(amount))
        if amount <= Decimal("0.00"):
            return None

        wallet = Wallet.objects.select_for_update().filter(user=user).first()
        if not wallet:
            wallet = Wallet.objects.create(user=user)

        wallet.balance = min(wallet.balance + amount, WalletService.LIMIT)
        wallet.save(update_fields=["balance", "updated_at"])

        order_desc = f" for Order {order.order_number}" if order and getattr(order, "order_number", None) else ""
        desc = description or f"Refund{order_desc}"
        WalletTransaction.objects.create(
            wallet=wallet,
            amount=amount,
            txn_type=WalletTransactionType.REFUND,
            order=order,
            description=desc,
        )
        return wallet
