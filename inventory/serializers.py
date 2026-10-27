from rest_framework import serializers

from .models import StockAlert, StockChangeReason, StockUpdate


class StockUpdateSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    color_name = serializers.CharField(source="variant.color_name", read_only=True, allow_null=True)
    size = serializers.CharField(source="variant_size.size", read_only=True, allow_null=True)
    created_by_name = serializers.CharField(source="created_by.name", read_only=True, allow_null=True)

    class Meta:
        model = StockUpdate
        fields = [
            "id",
            "product",
            "product_name",
            "product_sku",
            "variant",
            "color_name",
            "size",
            "previous_stock",
            "new_stock",
            "change_amount",
            "reason",
            "notes",
            "created_by",
            "created_by_name",
            "created_at",
        ]


class StockAdjustSerializer(serializers.Serializer):
    stock = serializers.IntegerField(
        required=False,
        min_value=0,
        help_text="Set absolute new stock level.",
    )
    adjustment = serializers.IntegerField(
        required=False,
        help_text="Signed delta to add/subtract (e.g. +10, -5).",
    )
    reason = serializers.ChoiceField(
        choices=StockChangeReason.choices,
        default=StockChangeReason.MANUAL_ADJUSTMENT,
        required=False,
    )
    notes = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
    )

    def validate(self, attrs):
        if "stock" not in attrs and "adjustment" not in attrs:
            raise serializers.ValidationError("Either 'stock' or 'adjustment' must be provided.")
        if "stock" in attrs and "adjustment" in attrs:
            raise serializers.ValidationError("Provide either 'stock' or 'adjustment', not both.")
        return attrs


class StockDeltaSerializer(serializers.Serializer):
    delta = serializers.IntegerField(min_value=1, help_text="Number of units (positive integer).")
    variant_id = serializers.IntegerField(required=False, allow_null=True, help_text="Optional color variant ID.")
    size = serializers.CharField(required=False, allow_blank=True, default="", help_text="Optional apparel/shoe size.")
    reason = serializers.ChoiceField(
        choices=StockChangeReason.choices,
        default=StockChangeReason.RESTOCK,
        required=False,
    )
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class BulkStockItemSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(min_value=1)
    stock = serializers.IntegerField(required=False, min_value=0)
    adjustment = serializers.IntegerField(required=False)
    reason = serializers.ChoiceField(
        choices=StockChangeReason.choices,
        default=StockChangeReason.MANUAL_ADJUSTMENT,
        required=False,
    )
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, attrs):
        if "stock" not in attrs and "adjustment" not in attrs:
            raise serializers.ValidationError("Either 'stock' or 'adjustment' must be provided.")
        if "stock" in attrs and "adjustment" in attrs:
            raise serializers.ValidationError("Provide either 'stock' or 'adjustment', not both.")
        return attrs


class BulkStockUpdateSerializer(serializers.Serializer):
    items = serializers.ListField(
        child=BulkStockItemSerializer(),
        allow_empty=False,
        help_text="List of product stock updates.",
    )


class BulkStockDeltaItemSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(min_value=1)
    variant_id = serializers.IntegerField(required=False, allow_null=True)
    size = serializers.CharField(required=False, allow_blank=True, default="")
    delta = serializers.IntegerField(help_text="Signed delta (+N to add stock, -N to remove stock).")
    reason = serializers.ChoiceField(
        choices=StockChangeReason.choices,
        default=StockChangeReason.RESTOCK,
        required=False,
    )
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class BulkStockDeltaSerializer(serializers.Serializer):
    items = serializers.ListField(
        child=BulkStockDeltaItemSerializer(),
        allow_empty=False,
        help_text="Batch list of atomic stock increments/decrements.",
    )


class StockAlertSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    current_stock = serializers.IntegerField(source="product.stock", read_only=True)
    is_triggered = serializers.BooleanField(read_only=True)

    class Meta:
        model = StockAlert
        fields = [
            "id",
            "product",
            "product_name",
            "product_sku",
            "current_stock",
            "threshold",
            "is_active",
            "is_triggered",
            "last_triggered_at",
            "created_at",
            "updated_at",
        ]


class SetStockAlertSerializer(serializers.Serializer):
    threshold = serializers.IntegerField(
        min_value=0,
        default=5,
        help_text="Trigger alert at or below this count.",
    )
    is_active = serializers.BooleanField(
        default=True,
        required=False,
        help_text="Enable or disable this alert.",
    )


class LowStockProductSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    product_name = serializers.CharField()
    sku = serializers.CharField(allow_blank=True, default="")
    current_stock = serializers.IntegerField()
    threshold = serializers.IntegerField()
    status = serializers.CharField()

