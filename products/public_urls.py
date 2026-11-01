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
    path("sales/", PublicSeasonalSaleListView.as_view(), name="public-seasonal-sales-list"),
    path("sales/<int:sale_pk>/", PublicSeasonalSaleDetailView.as_view(), name="public-seasonal-sales-detail"),
    path("sales/<int:sale_pk>/products/", PublicSeasonalSaleProductsView.as_view(), name="public-seasonal-sales-products"),

    path("", PublicProductListView.as_view(), name="public-product-list"),

    path("categories/", CategoryListView.as_view(), name="public-category-list"),

    path("recommended/", RecommendedFeedView.as_view(), name="public-product-recommendations"),

    path("search/history/", SearchHistoryListView.as_view(), name="public-search-history-list"),
    path("search/history/<int:history_id>/", SearchHistoryDetailView.as_view(), name="public-search-history-detail"),

    path("search/", ProductSearchView.as_view(), name="public-product-search"),

    path("autocomplete/", ProductAutocompleteView.as_view(), name="public-product-autocomplete"),

    path("<int:product_pk>/", PublicProductDetailView.as_view(), name="public-product-detail"),

    path("<int:product_pk>/similar/", PublicProductSimilarView.as_view(), name="public-product-similar"),

    path("<int:product_pk>/recommended-combos/", PublicProductRecommendedCombosView.as_view(), name="public-product-recommended-combos"),

    path("<int:product_pk>/reviews/", ProductReviewListCreateView.as_view(), name="product-reviews-list-create"),

    path("<int:product_pk>/reviews/summary/", ProductReviewSummaryView.as_view(), name="product-reviews-summary"),

    path("<int:product_pk>/reviews/chat/", ProductReviewChatView.as_view(), name="product-reviews-chat"),
]
