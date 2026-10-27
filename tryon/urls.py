from django.urls import path

from .views import (
    StoreProductARStatusDetailView,
    StoreProductsARStatusView,
    TryOnGenerateView,
    TryOnHistoryListView,
    TryOnRequestDetailView,
)

urlpatterns = [
    # Customer Virtual Try-On Generation & Status Polling
    path("generate/", TryOnGenerateView.as_view(), name="tryon-generate"),
    path("requests/<uuid:request_id>/", TryOnRequestDetailView.as_view(), name="tryon-request-detail"),
    path("history/", TryOnHistoryListView.as_view(), name="tryon-history-list"),

    # Seller AR Status Endpoints
    path("stores/<int:store_pk>/tryon-status/", StoreProductsARStatusView.as_view(), name="store-products-ar-status"),
    path("stores/<int:store_pk>/products/<int:product_pk>/tryon-status/", StoreProductARStatusDetailView.as_view(), name="store-product-ar-status-detail"),

]
