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
    quantity = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(fields=["cart", "product"], name="unique_product_per_cart")
        ]

    def __str__(self):
        return f"{self.product.name} x {self.quantity}"

    @property
    def unit_price(self):
        return self.product.price

    @property
    def total_price(self):
        return Decimal(str(self.unit_price)) * self.quantity

    @property
    def is_available(self):
        return (
            self.product.status == ProductStatus.ACTIVE
            and self.product.store.status == StoreStatus.ACTIVE
            and self.product.store.archived_at is None
            and self.product.stock >= self.quantity
        )
