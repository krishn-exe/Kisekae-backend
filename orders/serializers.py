from decimal import Decimal
from rest_framework import serializers

from .models import Order, OrderItem, OrderStatus, OrderStatusHistory, ReturnRequest, ReturnStatus


class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = [
            "id",
            "product",
            "product_name",
            "product_sku",
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


class ReturnRequestSerializer(serializers.ModelSerializer):
    resolved_by_name = serializers.CharField(source="resolved_by.name", read_only=True, allow_null=True)
    item_product_name = serializers.CharField(source="item.product_name", read_only=True, allow_null=True)

    class Meta:
        model = ReturnRequest
        fields = [
            "id",
            "order",
            "item",
            "item_product_name",
            "reason",
            "customer_notes",
            "status",
            "admin_notes",
            "resolved_by",
            "resolved_by_name",
            "created_at",
            "resolved_at",
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
    return_requests = ReturnRequestSerializer(many=True, read_only=True)
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
            "return_requests",
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


class OrderCreateSerializer(serializers.Serializer):
    customer_name = serializers.CharField(max_length=150)
    customer_email = serializers.EmailField()
    customer_phone = serializers.CharField(max_length=20)
    shipping_address = serializers.CharField(min_length=5)
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    items = serializers.ListField(
        child=OrderItemInputSerializer(),
        allow_empty=False,
    )


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


class ReturnRequestCreateSerializer(serializers.Serializer):
    item_id = serializers.IntegerField(required=False, allow_null=True)
    reason = serializers.CharField(max_length=255)
    customer_notes = serializers.CharField(required=False, allow_blank=True, default="")


class ReturnStatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[ReturnStatus.APPROVED, ReturnStatus.REJECTED, ReturnStatus.COMPLETED]
    )
    admin_notes = serializers.CharField(required=False, allow_blank=True, default="")
