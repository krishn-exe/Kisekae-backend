import secrets
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class OrderStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    CONFIRMED = "CONFIRMED", "Confirmed"
    PROCESSING = "PROCESSING", "Processing"
    SHIPPED = "SHIPPED", "Shipped"
    DELIVERED = "DELIVERED", "Delivered"
    CANCELLED = "CANCELLED", "Cancelled"
    RETURN_REQUESTED = "RETURN_REQUESTED", "Return Requested"
    RETURNED = "RETURNED", "Returned"


class ReturnStatus(models.TextChoices):
    REQUESTED = "REQUESTED", "Requested"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"
    COMPLETED = "COMPLETED", "Completed"


# Valid state transitions
VALID_TRANSITIONS = {
    OrderStatus.PENDING: {OrderStatus.CONFIRMED, OrderStatus.CANCELLED},
    OrderStatus.CONFIRMED: {OrderStatus.PROCESSING, OrderStatus.SHIPPED, OrderStatus.CANCELLED},
    OrderStatus.PROCESSING: {OrderStatus.SHIPPED, OrderStatus.CANCELLED},
    OrderStatus.SHIPPED: {OrderStatus.DELIVERED, OrderStatus.RETURN_REQUESTED},
    OrderStatus.DELIVERED: {OrderStatus.RETURN_REQUESTED},
    OrderStatus.RETURN_REQUESTED: {OrderStatus.RETURNED, OrderStatus.DELIVERED},
    OrderStatus.CANCELLED: set(),
    OrderStatus.RETURNED: set(),
}


def generate_order_number():
    date_str = timezone.now().strftime("%Y%m%d")
    random_hex = secrets.token_hex(3).upper()
    return f"ORD-{date_str}-{random_hex}"


class Order(models.Model):
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
    tracking_number = models.CharField(max_length=100, blank=True, default="")
    tracking_url = models.URLField(blank=True, default="")
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.order_number} ({self.store.name}) - {self.status}"

    def can_transition_to(self, new_status):
        allowed = VALID_TRANSITIONS.get(self.status, set())
        return new_status in allowed


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
    product_name = models.CharField(max_length=255)
    product_sku = models.CharField(max_length=100, blank=True, default="")
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


class ReturnRequest(models.Model):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="return_requests",
    )
    item = models.ForeignKey(
        OrderItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="return_requests",
    )
    reason = models.CharField(max_length=255)
    customer_notes = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=30,
        choices=ReturnStatus.choices,
        default=ReturnStatus.REQUESTED,
        db_index=True,
    )
    admin_notes = models.TextField(blank=True, default="")
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_returns",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Return for {self.order.order_number} ({self.status})"
