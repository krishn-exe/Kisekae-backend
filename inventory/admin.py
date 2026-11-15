from django.contrib import admin
from .models import StockAlert, StockUpdate


@admin.register(StockUpdate)
class StockUpdateAdmin(admin.ModelAdmin):
    list_display = (
        "product",
        "variant",
        "variant_size",
        "previous_stock",
        "new_stock",
        "change_amount",
        "reason",
        "created_by",
        "created_at",
    )
    list_filter = ("reason", "created_at")
    search_fields = (
        "product__name",
        "product__sku",
        "variant__color_name",
        "created_by__email",
        "notes",
    )
    autocomplete_fields = ["product", "variant", "variant_size", "created_by"]
    readonly_fields = ("created_at",)


@admin.register(StockAlert)
class StockAlertAdmin(admin.ModelAdmin):
    list_display = (
        "product",
        "threshold",
        "is_active",
        "is_currently_triggered",
        "last_triggered_at",
        "updated_at",
        "created_at",
    )
    list_filter = ("is_active", "created_at")
    search_fields = ("product__name", "product__sku")
    autocomplete_fields = ["product"]
    readonly_fields = ("created_at", "updated_at")

    @admin.display(boolean=True, description="Triggered / Low Stock")
    def is_currently_triggered(self, obj):
        return obj.is_triggered
