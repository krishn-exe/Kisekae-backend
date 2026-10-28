import secrets
from django.conf import settings
from django.db import models
from django.utils import timezone


class OrderStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    CONFIRMED = "CONFIRMED", "Confirmed"
    SHIPPED = "SHIPPED", "Shipped"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY", "Out For Delivery"
    DELIVERED = "DELIVERED", "Delivered"
    CANCELLED = "CANCELLED", "Cancelled"
    REFUND_REQUESTED = "REFUND_REQUESTED", "Refund Requested"
    REFUNDED = "REFUNDED", "Refunded"
    FAILED = "FAILED", "Failed"


class PaymentStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    PAID = "PAID", "Paid"
    FAILED = "FAILED", "Failed"
    REFUNDED = "REFUNDED", "Refunded"


# Valid state transitions
VALID_TRANSITIONS = {
    OrderStatus.PENDING: {OrderStatus.CONFIRMED, OrderStatus.FAILED, OrderStatus.CANCELLED},
    OrderStatus.CONFIRMED: {OrderStatus.SHIPPED, OrderStatus.OUT_FOR_DELIVERY, OrderStatus.DELIVERED, OrderStatus.CANCELLED},
    OrderStatus.SHIPPED: {OrderStatus.OUT_FOR_DELIVERY, OrderStatus.DELIVERED},
    OrderStatus.OUT_FOR_DELIVERY: {OrderStatus.DELIVERED},
    OrderStatus.DELIVERED: {OrderStatus.REFUND_REQUESTED},
    OrderStatus.REFUND_REQUESTED: {OrderStatus.REFUNDED, OrderStatus.DELIVERED},
    OrderStatus.CANCELLED: set(),
    OrderStatus.REFUNDED: set(),
    OrderStatus.FAILED: set(),
}


def generate_order_number():
    date_str = timezone.now().strftime("%Y%m%d")
    random_hex = secrets.token_hex(3).upper()
    return f"ORD-{date_str}-{random_hex}"


class Order(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="orders",
        help_text="Customer user account that placed this order.",
    )
    store = models.ForeignKey(
        "stores.Store",
        on_delete=models.CASCADE,
        related_name="orders",
    )
    order_number = models.CharField(
        max_length=50,
        unique=True,
        default=generate_order_number,
        db_index=True,
        editable=False,
    )
    customer_name = models.CharField(max_length=150)
    customer_email = models.EmailField()
    customer_phone = models.CharField(max_length=20)
    shipping_address = models.TextField()
    status = models.CharField(
        max_length=30,
        choices=OrderStatus.choices,
        default=OrderStatus.PENDING,
        db_index=True,
    )
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default="INR")
    notes = models.TextField(blank=True, default="")
    shipped_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when order was marked as shipped.",
    )
    out_for_delivery_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when order was marked as out for delivery.",
    )
    delivered_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when order was delivered to the customer.",
    )
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Timestamp when the 5-minute pending payment reservation expires.",
    )
    razorpay_order_id = models.CharField(
        max_length=100,
        blank=True,
        default="",
        db_index=True,
        help_text="Razorpay Order ID generated for payment capture.",
    )
    razorpay_payment_id = models.CharField(
        max_length=100,
        blank=True,
        default="",
        db_index=True,
        help_text="Captured payment transaction ID from Razorpay.",
    )
    razorpay_signature = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Cryptographic signature verification token from Razorpay.",
    )
    payment_status = models.CharField(
        max_length=30,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PENDING,
        db_index=True,
    )
    refund_id = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Razorpay refund transaction ID.",
    )
    refund_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Total refund amount processed.",
    )
    refund_reason = models.TextField(
        blank=True,
        default="",
        help_text="Reason provided for refund request or cancellation.",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.order_number} ({self.store.name}) - {self.status}"

    def can_transition_to(self, new_status):
        allowed = VALID_TRANSITIONS.get(self.status, set())
        return new_status in allowed

    def check_refund_eligibility(self):
        """
        Validates whether this order can be returned for a refund:
        1. Must be DELIVERED.
        2. Must be within 10 days of delivery.
        3. All ordered products must have is_refundable == True.
        """
        if self.status != OrderStatus.DELIVERED or not self.delivered_at:
            return False, "Order must be in DELIVERED status to request a refund."

        cutoff = self.delivered_at + timezone.timedelta(days=10)
        if timezone.now() > cutoff:
            return False, "Return window has expired. Refunds can only be requested within 10 days of delivery."

        for item in self.items.select_related("product").all():
            if item.product and not item.product.is_refundable:
                return False, f"Product '{item.product_name}' is non-refundable per seller policy."

        return True, ""


class OrderItem(models.Model):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(
        "products.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_items",
    )
    variant = models.ForeignKey(
        "products.ProductVariant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_items",
        help_text="Associated color variant purchased.",
    )
    variant_size = models.ForeignKey(
        "products.ProductSize",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_items",
        help_text="Associated variant inventory size record.",
    )
    product_name = models.CharField(max_length=255)
    product_sku = models.CharField(max_length=100, blank=True, default="")
    color_name = models.CharField(max_length=50, blank=True, default="")
    color_hex = models.CharField(max_length=7, blank=True, default="")
    size = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text="Selected apparel/shoe size (e.g., S, M, L, XL or 6-10).",
    )
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1)
    total_price = models.DecimalField(max_digits=10, decimal_places=2)

    def save(self, *args, **kwargs):
        self.total_price = self.unit_price * self.quantity
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.product_name} x {self.quantity} ({self.order.order_number})"


class OrderStatusHistory(models.Model):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="status_history",
    )
    from_status = models.CharField(max_length=30)
    to_status = models.CharField(max_length=30)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_status_changes",
    )
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.order.order_number}: {self.from_status} -> {self.to_status}"


