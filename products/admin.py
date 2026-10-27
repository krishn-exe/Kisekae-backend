from django.contrib import admin

from .models import Product, ProductImage, ProductSize, ProductVariant


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1
    fields = ("color_name", "color_hex", "sku", "is_active")


class ProductSizeInline(admin.TabularInline):
    model = ProductSize
    extra = 1
    fields = ("variant", "size", "stock")


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1
    fields = ("variant", "image", "order")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "store", "product_type", "category", "price", "stock", "status", "created_at")
    list_filter = ("status", "product_type", "category", "store", "created_at")
    search_fields = ("name", "slug", "sku", "store__name")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ["store", "created_by"]
    list_select_related = ("store",)
    inlines = [ProductVariantInline, ProductSizeInline, ProductImageInline]


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = ("product", "color_name", "color_hex", "sku", "is_active", "created_at")
    list_filter = ("is_active", "created_at")
    search_fields = ("color_name", "sku", "product__name")
    autocomplete_fields = ["product"]
    inlines = [ProductSizeInline, ProductImageInline]
