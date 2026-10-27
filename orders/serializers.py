from rest_framework import serializers

from .models import Order, OrderItem, OrderStatus, OrderStatusHistory


class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = [
            "id",
            "product",
            "product_name",
            "product_sku",
            "size",
            "unit_price",
            "quantity",
            "total_price",
        ]


class OrderStatusHistorySerializer(serializers.ModelSerializer):
    changed_by_name = serializers.CharField(source="changed_by.name", read_only=True, allow_null=True)

    class Meta:
        model = OrderStatusHistory
        fields = [
            "id",
            "from_status",
            "to_status",
            "notes",
            "changed_by",
            "changed_by_name",
            "created_at",
        ]


class OrderListSerializer(serializers.ModelSerializer):
    items_count = serializers.IntegerField(source="items.count", read_only=True)
    user_id = serializers.IntegerField(source="user.id", read_only=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "user_id",
            "order_number",
            "customer_name",
            "customer_email",
            "customer_phone",
            "status",
            "total_amount",
            "currency",
            "items_count",
            "delivered_at",
            "created_at",
            "updated_at",
        ]


class OrderDetailSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    status_history = OrderStatusHistorySerializer(many=True, read_only=True)
    user_id = serializers.IntegerField(source="user.id", read_only=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "user_id",
            "order_number",
            "customer_name",
            "customer_email",
            "customer_phone",
            "shipping_address",
            "status",
            "total_amount",
            "currency",
            "tracking_number",
            "tracking_url",
            "notes",
            "delivered_at",
            "items",
            "status_history",
            "created_at",
            "updated_at",
        ]


class OrderDeliverSerializer(serializers.Serializer):
    notes = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        help_text="Optional delivery notes or confirmation details from the seller.",
    )


class OrderItemInputSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(min_value=1, default=1)
    size = serializers.CharField(required=False, allow_blank=True, default="")


class OrderCreateSerializer(serializers.ModelSerializer):
    items = serializers.ListField(
        child=OrderItemInputSerializer(),
        allow_empty=False,
    )

    class Meta:
        model = Order
        fields = [
            "customer_name",
            "customer_email",
            "customer_phone",
            "shipping_address",
            "notes",
            "items",
        ]
        extra_kwargs = {
            "shipping_address": {"min_length": 5},
            "notes": {"required": False, "allow_blank": True, "default": ""},
        }


class OrderStatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=OrderStatus.choices)
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    tracking_number = serializers.CharField(required=False, allow_blank=True)
    tracking_url = serializers.URLField(required=False, allow_blank=True)


class BulkOrderItemStatusSerializer(serializers.Serializer):
    order_id = serializers.IntegerField(min_value=1)
    status = serializers.ChoiceField(choices=OrderStatus.choices)
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class BulkOrderStatusUpdateSerializer(serializers.Serializer):
    orders = serializers.ListField(
        child=BulkOrderItemStatusSerializer(),
        allow_empty=False,
    )


class CustomerOrderTrackSerializer(serializers.ModelSerializer):
    store_name = serializers.CharField(source="store.name", read_only=True)
    items = OrderItemSerializer(many=True, read_only=True)
    status_history = OrderStatusHistorySerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "store_name",
            "status",
            "total_amount",
            "currency",
            "tracking_number",
            "tracking_url",
            "delivered_at",
            "created_at",
            "updated_at",
            "items",
            "status_history",
        ]
