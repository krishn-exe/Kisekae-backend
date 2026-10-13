from rest_framework import serializers

from .models import StockAlert, StockChangeReason, StockUpdate


class StockUpdateSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    created_by_name = serializers.CharField(source="created_by.name", read_only=True, allow_null=True)

    class Meta:
        model = StockUpdate
        fields = [
            "id",
            "product",
            "product_name",
            "product_sku",
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


class BulkStockItemSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(min_value=1)
    stock = serializers.IntegerField(
        required=False,
        min_value=0,
    )
    adjustment = serializers.IntegerField(
        required=False,
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


class BulkStockUpdateSerializer(serializers.Serializer):
    items = serializers.ListField(
        child=BulkStockItemSerializer(),
        allow_empty=False,
        help_text="List of product stock updates.",
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
        help_text="Stock count threshold at or below which alerts trigger.",
    )
    is_active = serializers.BooleanField(
        default=True,
        required=False,
    )


class LowStockProductSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    product_name = serializers.CharField()
    sku = serializers.CharField()
    current_stock = serializers.IntegerField()
    threshold = serializers.IntegerField()
    status = serializers.CharField()
