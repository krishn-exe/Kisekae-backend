from django.urls import path

from .views import (
    ProductBulkUpdateView,
    ProductDetailView,
    ProductImageDetailView,
    ProductImageView,
    ProductListCreateView,
    ProductListingStatusView,
    ProductMultipleImageView,
)

urlpatterns = [
    # Product List & Create (nested under stores/<pk>/products/)
    path("", ProductListCreateView.as_view(), name="product-list-create"),

    # Catalog Listing Status Overview
    path("listing-status/", ProductListingStatusView.as_view(), name="product-listing-status"),

    # Bulk Product Update
    path("bulk/", ProductBulkUpdateView.as_view(), name="product-bulk-update"),

    # Product Detail, Update, Archive
    path("<int:product_pk>/", ProductDetailView.as_view(), name="product-detail"),

    # Single Product Primary Image (backward compatibility)
    path("<int:product_pk>/image/", ProductImageView.as_view(), name="product-image"),

    # Product Gallery Images (up to 10 images)
    path("<int:product_pk>/images/", ProductMultipleImageView.as_view(), name="product-gallery-images"),
    path("<int:product_pk>/images/<int:image_id>/", ProductImageDetailView.as_view(), name="product-gallery-image-detail"),
]
