from django.contrib import admin
from .models import ProductReviewSummary, Review, ReviewImage


class ReviewImageInline(admin.TabularInline):
    model = ReviewImage
    extra = 0
    fields = ("image", "created_at")
    readonly_fields = ("created_at",)


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("product", "user", "rating", "title", "is_verified_buyer", "images_count", "created_at")
    list_filter = ("rating", "is_verified_buyer", "created_at")
    search_fields = ("title", "description", "product__name", "user__email", "user__name")
    autocomplete_fields = ["product", "user"]
    readonly_fields = ("is_verified_buyer", "created_at", "updated_at")
    inlines = [ReviewImageInline]

    @admin.display(description="Images")
    def images_count(self, obj):
        return obj.images.count()


@admin.register(ReviewImage)
class ReviewImageAdmin(admin.ModelAdmin):
    list_display = ("id", "review", "created_at")
    search_fields = ("review__title", "review__product__name", "review__user__email")
    readonly_fields = ("created_at",)


@admin.register(ProductReviewSummary)
class ProductReviewSummaryAdmin(admin.ModelAdmin):
    list_display = ("product", "review_count_at_summary", "sizing_verdict", "generated_at")
    search_fields = ("product__name", "summary_text")
    readonly_fields = ("generated_at",)
