from django.contrib import admin

from .models import Product


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "store", "price", "stock", "status", "created_at")
    list_filter = ("status", "store", "created_at")
    search_fields = ("name", "slug", "sku", "store__name")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ["store", "created_by"]
    list_select_related = ("store",)
