from django.conf import settings
from django.db import models


class StockChangeReason(models.TextChoices):
    MANUAL_ADJUSTMENT = "MANUAL_ADJUSTMENT", "Manual Adjustment"
    RESTOCK = "RESTOCK", "Restock"
    ORDER_PLACED = "ORDER_PLACED", "Order Placed"
    ORDER_CANCELLED = "ORDER_CANCELLED", "Order Cancelled"
    RESERVATION_HOLD = "RESERVATION_HOLD", "Reservation Hold"
    RESERVATION_RELEASE = "RESERVATION_RELEASE", "Reservation Released"
    RETURN_RESTOCK = "RETURN_RESTOCK", "Return Restocked"
    REFUND_RESTOCK = "REFUND_RESTOCK", "Refund Restocked"
    DAMAGED_OR_LOST = "DAMAGED_OR_LOST", "Damaged or Lost"
    AUDIT_CORRECTION = "AUDIT_CORRECTION", "Audit Correction"


class StockUpdate(models.Model):
    product = models.ForeignKey(
        "products.Product",
        on_delete=models.CASCADE,
        related_name="stock_updates",
    )
    variant = models.ForeignKey(
        "products.ProductVariant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_updates",
        help_text="Variant associated with stock adjustment.",
    )
    variant_size = models.ForeignKey(
        "products.ProductSize",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_updates",
        help_text="Specific variant size associated with stock adjustment.",
    )
    previous_stock = models.PositiveIntegerField()
    new_stock = models.PositiveIntegerField()
    change_amount = models.IntegerField(
        help_text="Signed change quantity: e.g. +10 or -3.",
    )
    reason = models.CharField(
        max_length=50,
        choices=StockChangeReason.choices,
        default=StockChangeReason.MANUAL_ADJUSTMENT,
        db_index=True,
    )
    notes = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_stock_updates",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.product.name}: {self.previous_stock} -> {self.new_stock} ({self.reason})"


class StockAlert(models.Model):
    product = models.OneToOneField(
        "products.Product",
        on_delete=models.CASCADE,
        related_name="stock_alert",
    )
    threshold = models.PositiveIntegerField(
        default=5,
        help_text="Stock count at or below which an alert is triggered.",
    )
    is_active = models.BooleanField(default=True)
    last_triggered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"StockAlert for {self.product.name} (<= {self.threshold})"

    @property
    def is_triggered(self):
        return self.is_active and self.product.stock <= self.threshold
