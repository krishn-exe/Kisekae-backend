from rest_framework import serializers

from products.models import Product
from stores.models import Store

from .models import Cart, CartItem


class CartItemStoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = Store
        fields = ["id", "name", "slug", "status"]


class CartItemProductSerializer(serializers.ModelSerializer):
    thumbnail = serializers.CharField(read_only=True, allow_null=True)
    image_url = serializers.CharField(read_only=True, allow_null=True)
    store = CartItemStoreSerializer(read_only=True)
    available_sizes = serializers.ListField(child=serializers.CharField(), read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "slug",
            "category",
            "color",
            "price",
            "compare_at_price",
            "currency",
            "thumbnail",
            "image_url",
            "stock",
            "status",
            "available_sizes",
            "store",
        ]


class CartItemSerializer(serializers.ModelSerializer):
    product = CartItemProductSerializer(read_only=True)
    variant_id = serializers.IntegerField(source="variant.id", read_only=True, allow_null=True)
    color_name = serializers.CharField(source="variant.color_name", read_only=True, allow_null=True)
    color_hex = serializers.CharField(source="variant.color_hex", read_only=True, allow_null=True)
    size = serializers.CharField(read_only=True)
    unit_price = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    total_price = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    is_available = serializers.BooleanField(read_only=True)

    class Meta:
        model = CartItem
        fields = [
            "id",
            "product",
            "variant_id",
            "color_name",
            "color_hex",
            "size",
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
    variant_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        default=None,
        help_text="Optional ID of the specific color variant. If omitted, defaults to first active variant.",
    )
    size = serializers.CharField(
        max_length=20,
        required=False,
        allow_blank=True,
        default="",
        help_text="Product size (e.g. S, M, L, XL for clothing; 6-10 for shoes).",
    )
    quantity = serializers.IntegerField(default=1, min_value=1, help_text="Quantity to add (default: 1).")


class UpdateCartItemSerializer(serializers.Serializer):
    quantity = serializers.IntegerField(
        min_value=0,
        help_text="Target quantity for this item. Setting to 0 removes the item.",
    )


class CartCheckoutSerializer(serializers.Serializer):
    address_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        default=None,
        help_text="Optional ID of saved delivery address. If omitted, user's default saved profile address is used.",
    )


class CheckoutOrderSummarySerializer(serializers.Serializer):
    order_id = serializers.IntegerField()
    order_number = serializers.CharField()
    store_id = serializers.IntegerField()
    store_name = serializers.CharField()
    total_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    currency = serializers.CharField()
    items_count = serializers.IntegerField()
    status = serializers.CharField()
    razorpay_order_id = serializers.CharField(allow_blank=True, default="")
    expires_at = serializers.DateTimeField(allow_null=True)


class CartCheckoutResponseSerializer(serializers.Serializer):
    orders = CheckoutOrderSummarySerializer(many=True)
    total_orders_placed = serializers.IntegerField()
    total_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    currency = serializers.CharField()
