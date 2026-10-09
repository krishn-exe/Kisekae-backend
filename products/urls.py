from django.urls import path

from .views import ProductDetailView, ProductImageView, ProductListCreateView

urlpatterns = [
    # Product List & Create (nested under stores/<pk>/products/)
    path("", ProductListCreateView.as_view(), name="product-list-create"),

    # Product Detail, Update, Archive
    path("<int:product_pk>/", ProductDetailView.as_view(), name="product-detail"),

    # Product Image
    path("<int:product_pk>/image/", ProductImageView.as_view(), name="product-image"),
]
