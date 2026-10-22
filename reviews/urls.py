from django.urls import path

from .views import (
    CustomerMyReviewsView,
    ProductReviewListCreateView,
    ReviewDetailView,
)

urlpatterns = [
    path("my-reviews/", CustomerMyReviewsView.as_view(), name="customer-my-reviews"),
    path("<int:review_pk>/", ReviewDetailView.as_view(), name="review-detail"),
    path("products/<int:product_pk>/", ProductReviewListCreateView.as_view(), name="product-reviews-direct"),
]
