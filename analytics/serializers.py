from rest_framework import serializers


class AnalyticsDateFilterSerializer(serializers.Serializer):
    from_date = serializers.DateField(required=False, format="%Y-%m-%d")
    to_date = serializers.DateField(required=False, format="%Y-%m-%d")

    def validate(self, attrs):
        from_d = attrs.get("from_date")
        to_d = attrs.get("to_date")
        if from_d and to_d and from_d > to_d:
            raise serializers.ValidationError({"from_date": "from_date cannot be later than to_date."})
        return attrs


class RevenueAnalyticsQuerySerializer(AnalyticsDateFilterSerializer):
    period = serializers.ChoiceField(
        choices=["daily", "weekly", "monthly"],
        default="daily",
        required=False,
    )


class ProductPerformanceQuerySerializer(AnalyticsDateFilterSerializer):
    sort_by = serializers.ChoiceField(
        choices=["revenue", "units_sold"],
        default="revenue",
        required=False,
    )
    limit = serializers.IntegerField(
        default=20,
        min_value=1,
        max_value=100,
        required=False,
    )


class ReportDownloadQuerySerializer(AnalyticsDateFilterSerializer):
    report_type = serializers.ChoiceField(
        choices=["sales", "inventory", "orders"],
        default="sales",
        required=False,
    )


# ==========================================
# Response Serializers for OpenAPI / Swagger
# ==========================================

class DashboardMetricsDataSerializer(serializers.Serializer):
    total_revenue = serializers.FloatField(
        help_text="Total revenue generated from non-cancelled orders.",
    )
    currency = serializers.CharField(
        default="INR",
        help_text="Currency code, e.g. INR.",
    )
    total_orders = serializers.IntegerField(
        help_text="Total count of all orders placed in this time window.",
    )
    delivered_orders = serializers.IntegerField(
        help_text="Count of successfully delivered orders.",
    )
    pending_orders = serializers.IntegerField(
        help_text="Count of orders currently in pending or confirmed states.",
    )
    shipped_orders = serializers.IntegerField(
        help_text="Count of orders currently in transit (shipped).",
    )
    cancelled_orders = serializers.IntegerField(
        help_text="Count of cancelled orders.",
    )
    total_units_sold = serializers.IntegerField(
        help_text="Total number of individual item units sold across non-cancelled orders.",
    )
    average_order_value = serializers.FloatField(
        help_text="Average monetary value per non-cancelled order.",
    )
    cancellation_rate = serializers.FloatField(
        help_text="Cancellation rate percentage (cancelled orders / total orders * 100).",
    )
    low_stock_products_count = serializers.IntegerField(
        help_text="Count of store products currently at or below low stock threshold.",
    )


class DashboardMetricsResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = DashboardMetricsDataSerializer()


class RevenueTimeSeriesPointSerializer(serializers.Serializer):
    date = serializers.CharField(
        help_text="Date string for this interval bucket (format: YYYY-MM-DD, YYYY-WW, or YYYY-MM).",
    )
    revenue = serializers.FloatField(
        help_text="Total revenue generated during this time interval.",
    )
    orders_count = serializers.IntegerField(
        help_text="Count of non-cancelled orders placed during this interval.",
    )


class RevenueAnalyticsDataSerializer(serializers.Serializer):
    period = serializers.CharField(
        help_text="Aggregation interval: daily, weekly, or monthly.",
    )
    currency = serializers.CharField(
        default="INR",
        help_text="Currency code, e.g. INR.",
    )
    total_revenue = serializers.FloatField(
        help_text="Total revenue across the selected period.",
    )
    total_orders = serializers.IntegerField(
        help_text="Total non-cancelled orders count across the selected period.",
    )
    average_revenue_per_order = serializers.FloatField(
        help_text="Average revenue generated per non-cancelled order.",
    )
    time_series = serializers.ListSerializer(
        child=RevenueTimeSeriesPointSerializer(),
        help_text="Chronological time series points of revenue and orders count.",
    )


class RevenueAnalyticsResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = RevenueAnalyticsDataSerializer()


class ProductPerformanceItemSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(
        allow_null=True,
        help_text="Product ID (or null if product was permanently deleted).",
    )
    product_name = serializers.CharField(
        help_text="Display name of the product.",
    )
    sku = serializers.CharField(
        allow_blank=True,
        help_text="Stock keeping unit identifier.",
    )
    units_sold = serializers.IntegerField(
        help_text="Total units of this product sold.",
    )
    gross_revenue = serializers.FloatField(
        help_text="Total revenue generated by sales of this product.",
    )


class ProductPerformanceDataSerializer(serializers.Serializer):
    sort_by = serializers.CharField(
        help_text="Sorting criteria used: revenue or units_sold.",
    )
    total_products_sold = serializers.IntegerField(
        help_text="Total number of unique products sold in this period.",
    )
    products = serializers.ListSerializer(
        child=ProductPerformanceItemSerializer(),
        help_text="Ranked list of products based on sort criteria.",
    )


class ProductPerformanceResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = ProductPerformanceDataSerializer()


class OrderStatusBreakdownSerializer(serializers.Serializer):
    PENDING = serializers.IntegerField(help_text="Count of pending orders awaiting payment.")
    CONFIRMED = serializers.IntegerField(help_text="Count of confirmed orders awaiting packing/shipping.")
    SHIPPED = serializers.IntegerField(help_text="Count of orders shipped and in transit.")
    DELIVERED = serializers.IntegerField(help_text="Count of successfully delivered orders.")
    CANCELLED = serializers.IntegerField(help_text="Count of cancelled orders.")


class OrderMetricsDataSerializer(serializers.Serializer):
    total_orders = serializers.IntegerField(
        help_text="Total number of orders placed in the period.",
    )
    status_breakdown = OrderStatusBreakdownSerializer(
        help_text="Breakdown of order counts across each lifecycle status.",
    )
    cancellation_rate = serializers.FloatField(
        help_text="Percentage of orders that were cancelled.",
    )
    average_fulfillment_hours = serializers.FloatField(
        allow_null=True,
        help_text="Average duration in hours from order creation to delivery (null if no deliveries).",
    )


class OrderMetricsResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    message = serializers.CharField()
    data = OrderMetricsDataSerializer()

