from django.contrib import admin

from .models import CouponCode, CouponUsage, Order, OrderItem, OrderStatusHistory


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ("product_name", "product_sku", "color_name", "size", "unit_price", "quantity", "total_price")


class OrderStatusHistoryInline(admin.TabularInline):
    model = OrderStatusHistory
    extra = 0
    readonly_fields = ("from_status", "to_status", "changed_by", "notes", "created_at")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "order_number",
        "user",
        "store",
        "status",
        "payment_status",
        "coupon",
        "coupon_discount",
        "total_amount",
        "wallet_amount_used",
        "currency",
        "created_at",
    )
    list_filter = ("status", "payment_status", "currency", "created_at")
    search_fields = ("order_number", "customer_name", "customer_email", "razorpay_order_id", "razorpay_payment_id")
    readonly_fields = (
        "order_number",
        "compare_at_total",
        "subtotal_amount",
        "coupon",
        "coupon_discount",
        "wallet_amount_used",
        "total_amount",
        "razorpay_order_id",
        "razorpay_payment_id",
        "created_at",
        "updated_at",
    )
    inlines = [OrderItemInline, OrderStatusHistoryInline]


@admin.register(CouponCode)
class CouponCodeAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "discount_type",
        "discount_value",
        "min_order_amount",
        "uses_count",
        "max_uses",
        "is_active",
        "store",
        "valid_until",
    )
    list_filter = ("discount_type", "is_active", "store")
    search_fields = ("code", "description")


@admin.register(CouponUsage)
class CouponUsageAdmin(admin.ModelAdmin):
    list_display = ("coupon", "user", "order", "discount_applied", "created_at")
    search_fields = ("coupon__code", "user__email", "order__order_number")
    readonly_fields = ("created_at",)

