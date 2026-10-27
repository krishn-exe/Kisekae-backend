import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models

from products.models import Product, ProductStatus
from stores.models import StoreStatus


class Cart(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="cart",
        null=True,
        blank=True,
    )
    cart_token = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        if self.user:
            return f"Cart of {self.user.email}"
        return f"Guest Cart {self.cart_token}"

    @property
    def total_items(self):
        return sum(item.quantity for item in self.items.all())

    @property
    def total_unique_items(self):
        return self.items.count()

    @property
    def subtotal(self):
        return sum(item.total_price for item in self.items.all())

    @property
    def currency(self):
        first_item = self.items.select_related("product").first()
        if first_item:
            return first_item.product.currency
        return "INR"


class CartItem(models.Model):
    cart = models.ForeignKey(
        Cart,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="cart_items",
    )
    variant = models.ForeignKey(
        "products.ProductVariant",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="cart_items",
        help_text="Selected color variant.",
    )
    size = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text="Selected size for apparel or footwear (e.g. S, M, L, XL, 8).",
    )
    quantity = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["cart", "product", "variant", "size"],
                name="unique_cart_product_variant_size",
            )
        ]

    def __str__(self):
        variant_label = f" ({self.variant.color_name})" if self.variant else ""
        size_label = f" [{self.size}]" if self.size else ""
        return f"{self.product.name}{variant_label}{size_label} x {self.quantity}"

    @property
    def unit_price(self):
        return self.product.price

    @property
    def total_price(self):
        return Decimal(str(self.unit_price)) * self.quantity

    @property
    def is_available(self):
        base_ok = (
            self.product.status == ProductStatus.ACTIVE
            and self.product.store.status == StoreStatus.ACTIVE
            and self.product.store.archived_at is None
        )
        if not base_ok:
            return False

        if self.variant:
            if not self.variant.is_active:
                return False
            if self.size:
                size_obj = self.variant.sizes.filter(size=self.size).first()
                return size_obj is not None and size_obj.stock >= self.quantity
            total_variant_stock = sum(s.stock for s in self.variant.sizes.all())
            return total_variant_stock >= self.quantity

        if self.size and self.product.sizes.exists():
            size_obj = self.product.sizes.filter(size=self.size).first()
            return size_obj is not None and size_obj.stock >= self.quantity

        return self.product.stock >= self.quantity
