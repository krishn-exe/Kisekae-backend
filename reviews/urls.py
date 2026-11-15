from django.urls import path

from .views import (
    CustomerMyReviewsView,
    ProductReviewListCreateView,
    ReviewDetailView,
    ReviewImageDetailView,
    ReviewImageUploadView,
)

urlpatterns = [
    path("my-reviews/", CustomerMyReviewsView.as_view(), name="customer-my-reviews"),
    path("<int:review_pk>/", ReviewDetailView.as_view(), name="review-detail"),
    path("<int:review_pk>/images/", ReviewImageUploadView.as_view(), name="review-image-upload"),
    path("<int:review_pk>/images/<int:image_pk>/", ReviewImageDetailView.as_view(), name="review-image-detail"),
    path("products/<int:product_pk>/", ProductReviewListCreateView.as_view(), name="product-reviews-direct"),
]
