from django.contrib import admin

from .models import (
    Product,
    ProductEmbedding,
    ProductImage,
    ProductSize,
    ProductVariant,
    ProductView,
    SearchHistory,
    SearchSuggestion,
    SeasonalSale,
    SeasonalSaleProduct,
    UserTasteProfile,
)


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


@admin.register(ProductEmbedding)
class ProductEmbeddingAdmin(admin.ModelAdmin):
    list_display = ("product", "product_store", "updated_at")
    search_fields = ("product__name", "product__store__name")
    readonly_fields = ("updated_at",)
    autocomplete_fields = ["product"]

    def product_store(self, obj):
        return obj.product.store.name if obj.product and obj.product.store else "-"
    product_store.short_description = "Store"


@admin.register(SearchHistory)
class SearchHistoryAdmin(admin.ModelAdmin):
    list_display = ("user", "query", "search_count", "has_embedding", "last_searched_at", "created_at")
    list_filter = ("last_searched_at", "created_at")
    search_fields = ("user__email", "query")
    readonly_fields = ("last_searched_at", "created_at")
    autocomplete_fields = ["user"]

    def has_embedding(self, obj):
        return bool(obj.embedding is not None)
    has_embedding.boolean = True
    has_embedding.short_description = "Vectorized"


@admin.register(ProductView)
class ProductViewAdmin(admin.ModelAdmin):
    list_display = ("user", "product", "view_count", "last_viewed_at", "first_viewed_at")
    list_filter = ("last_viewed_at", "first_viewed_at")
    search_fields = ("user__email", "product__name")
    readonly_fields = ("last_viewed_at", "first_viewed_at")
    autocomplete_fields = ["user", "product"]


@admin.register(UserTasteProfile)
class UserTasteProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "interaction_count", "updated_at")
    search_fields = ("user__email",)
    readonly_fields = ("updated_at",)
    autocomplete_fields = ["user"]


@admin.register(SearchSuggestion)
class SearchSuggestionAdmin(admin.ModelAdmin):
    list_display = ("query", "score", "category", "created_at", "updated_at")
    list_filter = ("category", "created_at")
    search_fields = ("query",)
    readonly_fields = ("created_at", "updated_at")


class SeasonalSaleProductInline(admin.TabularInline):
    model = SeasonalSaleProduct
    extra = 0
    autocomplete_fields = ["product", "store"]
    fields = ("store", "product", "original_price", "discounted_price", "is_active", "created_at")
    readonly_fields = ("original_price", "discounted_price", "created_at")


@admin.register(SeasonalSale)
class SeasonalSaleAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "discount_percentage",
        "start_time",
        "end_time",
        "is_active",
        "is_live_display",
        "participating_products_count",
        "created_at",
    )
    list_filter = ("is_active", "start_time", "end_time", "created_at")
    search_fields = ("title", "description", "slug")
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ("created_at", "updated_at")
    inlines = [SeasonalSaleProductInline]

    def is_live_display(self, obj):
        return obj.is_live
    is_live_display.boolean = True
    is_live_display.short_description = "Live Now"

    def participating_products_count(self, obj):
        return obj.participating_products.filter(is_active=True).count()
    participating_products_count.short_description = "Active Products"


@admin.register(SeasonalSaleProduct)
class SeasonalSaleProductAdmin(admin.ModelAdmin):
    list_display = ("sale", "store", "product", "original_price", "discounted_price", "is_active", "created_at")
    list_filter = ("sale", "is_active", "created_at")
    search_fields = ("product__name", "store__name", "sale__title")
    autocomplete_fields = ["sale", "store", "product"]
    readonly_fields = ("original_price", "discounted_price", "created_at", "updated_at")
