from django.urls import path

from .views import (
    CategoryListView,
    ProductAutocompleteView,
    ProductSearchView,
    PublicProductDetailView,
    PublicProductListView,
    PublicProductRecommendedCombosView,
    PublicSeasonalSaleDetailView,
    PublicSeasonalSaleListView,
    PublicSeasonalSaleProductsView,
    PublicProductSimilarView,
    RecommendedFeedView,
    SearchHistoryDetailView,
    SearchHistoryListView,
)
from reviews.views import (
    ProductReviewChatView,
    ProductReviewListCreateView,
    ProductReviewSummaryView,
)

urlpatterns = [
    # Public Seasonal Sales (Hero Banners & Discounted Product Showcase)
    path("sales/", PublicSeasonalSaleListView.as_view(), name="public-seasonal-sales-list"),
    path("sales/<int:sale_pk>/", PublicSeasonalSaleDetailView.as_view(), name="public-seasonal-sales-detail"),
    path("sales/<int:sale_pk>/products/", PublicSeasonalSaleProductsView.as_view(), name="public-seasonal-sales-products"),

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

    # Autocomplete Suggestions (ultra-fast query completions & instant product cards)
    path("autocomplete/", ProductAutocompleteView.as_view(), name="public-product-autocomplete"),

    # Public Product Detail
    path("<int:product_pk>/", PublicProductDetailView.as_view(), name="public-product-detail"),

    # Similar Products Recommendations (pgvector cosine matching)
    path("<int:product_pk>/similar/", PublicProductSimilarView.as_view(), name="public-product-similar"),

    # Recommended Combos (Complete the Look: 3 curated outfits)
    path("<int:product_pk>/recommended-combos/", PublicProductRecommendedCombosView.as_view(), name="public-product-recommended-combos"),

    # Product Reviews & Ratings (List, Filter & Create)
    path("<int:product_pk>/reviews/", ProductReviewListCreateView.as_view(), name="product-reviews-list-create"),

    # AI Review Consensus Summary (24-Hour Refresh)
    path("<int:product_pk>/reviews/summary/", ProductReviewSummaryView.as_view(), name="product-reviews-summary"),

    # Ephemeral Guardrailed Review Chatbot (Stateless)
    path("<int:product_pk>/reviews/chat/", ProductReviewChatView.as_view(), name="product-reviews-chat"),
]
