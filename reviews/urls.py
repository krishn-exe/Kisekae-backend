from django.urls import path

from .views import (
    CustomerMyReviewsView,
    ProductReviewChatView,
    ProductReviewListCreateView,
    ProductReviewSummaryView,
    ReviewDetailView,
)

urlpatterns = [
    path("my-reviews/", CustomerMyReviewsView.as_view(), name="customer-my-reviews"),
    path("<int:review_pk>/", ReviewDetailView.as_view(), name="review-detail"),
    path("products/<int:product_pk>/", ProductReviewListCreateView.as_view(), name="product-reviews-direct"),
    path("products/<int:product_pk>/summary/", ProductReviewSummaryView.as_view(), name="product-reviews-summary-direct"),
    path("products/<int:product_pk>/chat/", ProductReviewChatView.as_view(), name="product-reviews-chat-direct"),
]
