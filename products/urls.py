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
    # Product List & Create (nested under stores/<pk>/products/)
    path("", ProductListCreateView.as_view(), name="product-list-create"),

    # Catalog Listing Status Overview
    path("listing-status/", ProductListingStatusView.as_view(), name="product-listing-status"),

    # Bulk Product Update
    path("bulk/", ProductBulkUpdateView.as_view(), name="product-bulk-update"),

    # Product Detail, Replace (PUT), Update (PATCH), Delete
    path("<int:product_pk>/", ProductDetailView.as_view(), name="product-detail"),

    # Variant Images Gallery Management (Approach B)
    path("<int:product_pk>/variants/<int:variant_pk>/images/", ProductVariantImageView.as_view(), name="product-variant-images"),
    path("<int:product_pk>/variants/<int:variant_pk>/images/<int:image_id>/", ProductVariantImageDetailView.as_view(), name="product-variant-image-detail"),
]
