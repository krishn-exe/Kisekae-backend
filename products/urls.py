from django.urls import path

from .views import (
    ProductBulkUpdateView,
    ProductDetailView,
    ProductImageView,
    ProductListCreateView,
    ProductListingStatusView,
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

    # Product Image
    path("<int:product_pk>/image/", ProductImageView.as_view(), name="product-image"),
]
