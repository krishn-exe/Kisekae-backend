from django.urls import path

from .advertising_views import (
    StoreAdvertisingAnalyticsView,
    StoreAdvertisingBudgetView,
    StorePromotedProductDetailView,
    StorePromotedProductsView,
)

urlpatterns = [
    path("budget/", StoreAdvertisingBudgetView.as_view(), name="store-advertising-budget"),
    path("products/", StorePromotedProductsView.as_view(), name="store-promoted-products"),
    path("products/<int:product_id>/", StorePromotedProductDetailView.as_view(), name="store-promoted-product-detail"),
    path("analytics/", StoreAdvertisingAnalyticsView.as_view(), name="store-advertising-analytics"),
]
