from django.urls import path

from .views import (
    ProductSearchView,
    PublicProductDetailView,
    PublicProductListView,
)

urlpatterns = [
    # Public Product Browsing & Listing (with lettering & pagination)
    path("", PublicProductListView.as_view(), name="public-product-list"),

    # Public Product Search (basic DB scan across title & description)
    path("search/", ProductSearchView.as_view(), name="public-product-search"),

    # Public Product Detail
    path("<int:product_pk>/", PublicProductDetailView.as_view(), name="public-product-detail"),
]
