from decimal import Decimal
import logging
from django.core.exceptions import ValidationError
from django.db.models import F
from django.utils import timezone

from .models import CouponCode, CouponUsage, DiscountType

logger = logging.getLogger(__name__)


class CouponService:
    @staticmethod
    def calculate_discount(coupon: CouponCode, subtotal: Decimal) -> Decimal:
        """
        Pure calculation of discount amount based on coupon type and caps.
        """
        subtotal = Decimal(str(subtotal))
        if subtotal <= Decimal("0.00"):
            return Decimal("0.00")

        if coupon.discount_type == DiscountType.PERCENTAGE:
            discount = (subtotal * coupon.discount_value) / Decimal("100.00")
            if coupon.max_discount_cap is not None:
                discount = min(discount, coupon.max_discount_cap)
        else:  # FLAT
            discount = coupon.discount_value

        # Discount cannot exceed subtotal
        return min(discount, subtotal).quantize(Decimal("0.01"))

    @staticmethod
    def validate_and_calculate(coupon_code: str, subtotal: Decimal, user=None, store=None):
        """
        Validates coupon usability and calculates the applicable discount.
        Returns:
            dict: {
                "coupon": CouponCode,
                "discount": Decimal,
                "is_valid": bool,
                "error": str or None
            }
        """
        code_clean = str(coupon_code or "").strip().upper()
        if not code_clean:
            return {"coupon": None, "discount": Decimal("0.00"), "is_valid": False, "error": "Coupon code is empty."}

        coupon = CouponCode.objects.filter(code__iexact=code_clean).first()
        if not coupon:
            return {"coupon": None, "discount": Decimal("0.00"), "is_valid": False, "error": f"Invalid coupon code '{code_clean}'."}

        if not coupon.is_active:
            return {"coupon": coupon, "discount": Decimal("0.00"), "is_valid": False, "error": "This coupon code is currently inactive."}

        now = timezone.now()
        if coupon.valid_from and now < coupon.valid_from:
            return {"coupon": coupon, "discount": Decimal("0.00"), "is_valid": False, "error": "This coupon is not active yet."}

        if coupon.valid_until and now > coupon.valid_until:
            return {"coupon": coupon, "discount": Decimal("0.00"), "is_valid": False, "error": "This coupon has expired."}

        if coupon.store is not None and store is not None:
            if coupon.store_id != getattr(store, "id", store):
                return {"coupon": coupon, "discount": Decimal("0.00"), "is_valid": False, "error": f"This coupon is only valid for store '{coupon.store.name}'."}

        subtotal = Decimal(str(subtotal))
        if coupon.min_order_amount and subtotal < coupon.min_order_amount:
            return {
                "coupon": coupon,
                "discount": Decimal("0.00"),
                "is_valid": False,
                "error": f"Minimum order amount of ₹{coupon.min_order_amount} required to use this coupon.",
            }

        if coupon.max_uses is not None and coupon.uses_count >= coupon.max_uses:
            return {"coupon": coupon, "discount": Decimal("0.00"), "is_valid": False, "error": "This coupon usage limit has been reached."}

        if user and user.is_authenticated and coupon.max_uses_per_user:
            user_uses = CouponUsage.objects.filter(coupon=coupon, user=user).count()
            if user_uses >= coupon.max_uses_per_user:
                return {
                    "coupon": coupon,
                    "discount": Decimal("0.00"),
                    "is_valid": False,
                    "error": f"You have already redeemed this coupon the maximum allowed times ({coupon.max_uses_per_user}).",
                }

        discount = CouponService.calculate_discount(coupon, subtotal)
        return {
            "coupon": coupon,
            "discount": discount,
            "is_valid": True,
            "error": None,
        }

    @staticmethod
    def apply_coupon_to_order(coupon: CouponCode, order, discount_amount: Decimal, user=None):
        """
        Records coupon usage on confirmed order and increments redemption counter.
        """
        CouponUsage.objects.create(
            coupon=coupon,
            user=user or order.user,
            order=order,
            discount_applied=discount_amount,
        )
        CouponCode.objects.filter(pk=coupon.pk).update(uses_count=F("uses_count") + 1)
        coupon.refresh_from_db(fields=["uses_count"])

    @staticmethod
    def release_coupon_from_order(order):
        """
        Decrements coupon usage counter if an order is cancelled or expires.
        """
        usage = CouponUsage.objects.filter(order=order).first()
        if usage:
            coupon = usage.coupon
            usage.delete()
            if coupon.uses_count > 0:
                CouponCode.objects.filter(pk=coupon.pk).update(uses_count=F("uses_count") - 1)
