from django.contrib import admin
from .models import Combo, ComboItem, TryOnProductAsset, TryOnRequest


class ComboItemInline(admin.TabularInline):
    model = ComboItem
    extra = 0
    autocomplete_fields = ["product"]
    fields = ("product", "category", "created_at")
    readonly_fields = ("created_at",)


@admin.register(Combo)
class ComboAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "user",
        "share_code",
        "items_count_display",
        "share_url_link",
        "created_at",
        "updated_at",
    )
    search_fields = ("name", "user__email", "user__name", "share_code")
    readonly_fields = ("share_code", "created_at", "updated_at")
    autocomplete_fields = ["user"]
    inlines = [ComboItemInline]

    @admin.display(description="Items Count")
    def items_count_display(self, obj):
        return obj.items_count

    @admin.display(description="Universal Link")
    def share_url_link(self, obj):
        return obj.share_url or "-"


@admin.register(ComboItem)
class ComboItemAdmin(admin.ModelAdmin):
    list_display = ("combo", "product", "category", "created_at")
    list_filter = ("category", "created_at")
    search_fields = ("combo__name", "product__name", "combo__user__email")
    autocomplete_fields = ["combo", "product"]
    readonly_fields = ("created_at",)


@admin.register(TryOnProductAsset)
class TryOnProductAssetAdmin(admin.ModelAdmin):
    list_display = (
        "product",
        "status",
        "detected_category",
        "angles_count_display",
        "has_all_angles_display",
        "updated_at",
    )
    list_filter = ("status", "detected_category", "created_at")
    search_fields = ("product__name", "product__sku", "rejection_reason")
    autocomplete_fields = ["product"]
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Angles Provided")
    def angles_count_display(self, obj):
        return f"{obj.angles_count}/3"

    @admin.display(boolean=True, description="Ready for AR")
    def has_all_angles_display(self, obj):
        return obj.has_all_angles


@admin.register(TryOnRequest)
class TryOnRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "target_display",
        "angle",
        "status",
        "created_at",
        "completed_at",
    )
    list_filter = ("status", "angle", "created_at")
    search_fields = ("id", "user__email", "product__name", "combo__name", "error_message")
    autocomplete_fields = ["user", "product", "combo"]
    readonly_fields = ("id", "created_at", "completed_at")

    @admin.display(description="Product / Combo Target")
    def target_display(self, obj):
        if obj.product:
            return f"Product: {obj.product.name}"
        if obj.combo:
            return f"Combo: {obj.combo.name}"
        return "-"
