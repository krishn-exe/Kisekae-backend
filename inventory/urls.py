from django.urls import path

from .views import (
    BulkStockUpdateView,
    LowStockProductsView,
    ProductStockAlertView,
    ProductStockView,
    StockAlertListView,
    StockUpdateHistoryView,
)

urlpatterns = [
    # Per-product stock adjustment
    path("products/<int:product_pk>/stock/", ProductStockView.as_view(), name="product-stock-adjust"),

    # Per-product stock alert threshold
    path("products/<int:product_pk>/alert/", ProductStockAlertView.as_view(), name="product-stock-alert"),

    # Bulk stock adjustment
    path("inventory/stock/bulk/", BulkStockUpdateView.as_view(), name="bulk-stock-update"),

    # Stock change audit history
    path("inventory/stock-history/", StockUpdateHistoryView.as_view(), name="stock-update-history"),

    # All configured alerts
    path("inventory/alerts/", StockAlertListView.as_view(), name="stock-alerts-list"),

    # Products with low / zero stock
    path("inventory/low-stock/", LowStockProductsView.as_view(), name="low-stock-products"),
]
