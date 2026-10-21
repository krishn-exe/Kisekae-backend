from django.urls import path

from .views import (
    CategoryListView,
    ProductSearchView,
    PublicProductDetailView,
    PublicProductListView,
)

urlpatterns = [
    # Public Product Browsing & Listing (with filters & pagination)
    path("", PublicProductListView.as_view(), name="public-product-list"),

    # Category List & Choices
    path("categories/", CategoryListView.as_view(), name="public-category-list"),

    # Public Product Search (with semantic/lexical search, filters & pagination)
    path("search/", ProductSearchView.as_view(), name="public-product-search"),

    # Public Product Detail
    path("<int:product_pk>/", PublicProductDetailView.as_view(), name="public-product-detail"),
]
