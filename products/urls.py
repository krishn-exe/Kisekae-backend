from django.urls import path

from .views import (
    ProductBulkUpdateView,
    ProductDetailView,
    ProductListCreateView,
    ProductListingStatusView,
    ProductVariantImageDetailView,
    ProductVariantImageView,
)

urlpatterns = [
    path("", ProductListCreateView.as_view(), name="product-list-create"),

    path("listing-status/", ProductListingStatusView.as_view(), name="product-listing-status"),

    path("bulk/", ProductBulkUpdateView.as_view(), name="product-bulk-update"),

    path("<int:product_pk>/", ProductDetailView.as_view(), name="product-detail"),

    path("<int:product_pk>/variants/<int:variant_pk>/images/", ProductVariantImageView.as_view(), name="product-variant-images"),
    path("<int:product_pk>/variants/<int:variant_pk>/images/<int:image_id>/", ProductVariantImageDetailView.as_view(), name="product-variant-image-detail"),
]
