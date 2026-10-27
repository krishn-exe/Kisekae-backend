from rest_framework import serializers

from .models import Order, OrderItem, OrderStatus, OrderStatusHistory, PaymentStatus


class OrderItemSerializer(serializers.ModelSerializer):
    variant_id = serializers.IntegerField(source="variant.id", read_only=True, allow_null=True)

    class Meta:
        model = OrderItem
        fields = [
            "id",
            "product",
            "variant_id",
            "product_name",
            "product_sku",
            "color_name",
            "color_hex",
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
    items_count = serializers.SerializerMethodField()
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
            "payment_status",
            "total_amount",
            "currency",
            "items_count",
            "razorpay_order_id",
            "expires_at",
            "delivered_at",
            "created_at",
            "updated_at",
        ]

    def get_items_count(self, obj) -> int:
        if hasattr(obj, "items_count"):
            return obj.items_count
        return obj.items.count()


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
            "payment_status",
            "total_amount",
            "currency",
            "razorpay_order_id",
            "razorpay_payment_id",
            "expires_at",
            "tracking_number",
            "tracking_url",
            "notes",
            "delivered_at",
            "refund_id",
            "refund_amount",
            "refund_reason",
            "cancelled_at",
            "refunded_at",
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


class BuyNowSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(min_value=1, help_text="ID of product to buy directly.")
    variant_id = serializers.IntegerField(required=False, allow_null=True, default=None, help_text="Specific color variant ID.")
    size = serializers.CharField(required=False, allow_blank=True, default="", help_text="Apparel/shoe size.")
    quantity = serializers.IntegerField(default=1, min_value=1, help_text="Quantity to purchase.")
    address_id = serializers.IntegerField(required=False, allow_null=True, help_text="Saved profile address ID.")
    customer_name = serializers.CharField(required=False, allow_blank=True)
    customer_email = serializers.EmailField(required=False, allow_blank=True)
    customer_phone = serializers.CharField(required=False, allow_blank=True)
    shipping_address = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class PaymentVerifySerializer(serializers.Serializer):
    order_id = serializers.IntegerField(min_value=1, help_text="Internal ID of the pending order.")
    razorpay_payment_id = serializers.CharField(help_text="Captured payment ID from Razorpay SDK.")
    razorpay_order_id = serializers.CharField(required=False, allow_blank=True, default="", help_text="Razorpay Order ID.")
    razorpay_signature = serializers.CharField(help_text="Cryptographic signature from Razorpay.")


class OrderCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="", help_text="Reason for cancellation.")


class OrderRefundRequestSerializer(serializers.Serializer):
    reason = serializers.CharField(min_length=3, help_text="Reason for return/refund request (must be within 10 days of delivery).")


class OrderRefundApproveSerializer(serializers.Serializer):
    notes = serializers.CharField(required=False, allow_blank=True, default="", help_text="Store approval notes.")


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
            "payment_status",
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
