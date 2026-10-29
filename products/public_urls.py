from django.urls import path

from .views import (
    CategoryListView,
    ProductSearchView,
    PublicProductDetailView,
    PublicProductListView,
    PublicProductSimilarView,
    RecommendedFeedView,
    SearchHistoryDetailView,
    SearchHistoryListView,
)
from reviews.views import ProductReviewListCreateView

urlpatterns = [
    # Public Product Browsing & Listing (with filters & pagination)
    path("", PublicProductListView.as_view(), name="public-product-list"),

    # Category List & Choices
    path("categories/", CategoryListView.as_view(), name="public-category-list"),

    # Curated / Recommended Products Feed (Personalized ML + Trending Cold-Start)
    path("recommended/", RecommendedFeedView.as_view(), name="public-product-recommendations"),

    # User Search History APIs (Feeds into Recommendation ML)
    path("search/history/", SearchHistoryListView.as_view(), name="public-search-history-list"),
    path("search/history/<int:history_id>/", SearchHistoryDetailView.as_view(), name="public-search-history-detail"),

    # Public Product Search (with semantic/lexical search, filters & pagination)
    path("search/", ProductSearchView.as_view(), name="public-product-search"),

    # Public Product Detail
    path("<int:product_pk>/", PublicProductDetailView.as_view(), name="public-product-detail"),

    # Similar Products Recommendations (pgvector cosine matching)
    path("<int:product_pk>/similar/", PublicProductSimilarView.as_view(), name="public-product-similar"),

    # Product Reviews & Ratings (List, Filter & Create)
    path("<int:product_pk>/reviews/", ProductReviewListCreateView.as_view(), name="product-reviews-list-create"),
]
