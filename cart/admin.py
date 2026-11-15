from django.contrib import admin
from .models import Cart, CartItem


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0
    autocomplete_fields = ["product", "variant"]
    fields = ("product", "variant", "size", "quantity", "is_promoted", "created_at")
    readonly_fields = ("created_at",)


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "cart_token",
        "items_count",
        "subtotal_amount",
        "currency_code",
        "created_at",
        "updated_at",
    )
    search_fields = ("user__email", "user__name", "cart_token")
    readonly_fields = ("cart_token", "created_at", "updated_at")
    inlines = [CartItemInline]

    @admin.display(description="Total Items")
    def items_count(self, obj):
        return obj.total_items

    @admin.display(description="Subtotal")
    def subtotal_amount(self, obj):
        return f"{obj.subtotal:,.2f}"

    @admin.display(description="Currency")
    def currency_code(self, obj):
        return obj.currency


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ("id", "cart", "product", "variant", "size", "quantity", "is_promoted", "created_at")
    list_filter = ("is_promoted", "created_at")
    search_fields = ("cart__user__email", "product__name", "variant__color_name", "size")
    autocomplete_fields = ["cart", "product", "variant"]
    readonly_fields = ("created_at", "updated_at")
