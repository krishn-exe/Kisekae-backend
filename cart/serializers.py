from rest_framework import serializers

from products.models import Product
from stores.models import Store

from .models import Cart, CartItem


class CartItemStoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = Store
        fields = ["id", "name", "slug", "status"]


class CartItemProductSerializer(serializers.ModelSerializer):
    image_url = serializers.CharField(read_only=True, allow_null=True)
    store = CartItemStoreSerializer(read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "slug",
            "price",
            "compare_at_price",
            "currency",
            "image_url",
            "stock",
            "status",
            "store",
        ]


class CartItemSerializer(serializers.ModelSerializer):
    product = CartItemProductSerializer(read_only=True)
    unit_price = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    total_price = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    is_available = serializers.BooleanField(read_only=True)

    class Meta:
        model = CartItem
        fields = [
            "id",
            "product",
            "quantity",
            "unit_price",
            "total_price",
            "is_available",
            "created_at",
            "updated_at",
        ]


class CartSerializer(serializers.ModelSerializer):
    items = CartItemSerializer(many=True, read_only=True)
    total_items = serializers.IntegerField(read_only=True)
    total_unique_items = serializers.IntegerField(read_only=True)
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    currency = serializers.CharField(read_only=True)

    class Meta:
        model = Cart
        fields = [
            "id",
            "cart_token",
            "total_items",
            "total_unique_items",
            "subtotal",
            "currency",
            "items",
            "created_at",
            "updated_at",
        ]


class AddToCartSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(min_value=1, help_text="ID of the product to add.")
    quantity = serializers.IntegerField(default=1, min_value=1, help_text="Quantity to add (default: 1).")


class UpdateCartItemSerializer(serializers.Serializer):
    quantity = serializers.IntegerField(
        min_value=0,
        help_text="Target quantity for this item. Setting to 0 removes the item.",
    )
