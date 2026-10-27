from django.urls import path

from .views import (
    BulkStockDeltaView,
    BulkStockUpdateView,
    LowStockProductsView,
    ProductStockAddView,
    ProductStockAlertView,
    ProductStockRemoveView,
    ProductStockView,
    StockAlertListView,
    StockUpdateHistoryView,
)

urlpatterns = [
    # Per-product absolute / signed stock adjustment (legacy)
    path("products/<int:product_pk>/stock/", ProductStockView.as_view(), name="product-stock-adjust"),

    # Per-product atomic delta endpoints (add / remove units safely)
    path("products/<int:product_pk>/stock/add/", ProductStockAddView.as_view(), name="product-stock-add"),
    path("products/<int:product_pk>/stock/remove/", ProductStockRemoveView.as_view(), name="product-stock-remove"),

    # Per-product stock alert threshold
    path("products/<int:product_pk>/alert/", ProductStockAlertView.as_view(), name="product-stock-alert"),

    # Bulk stock adjustment (absolute & delta)
    path("inventory/stock/bulk/", BulkStockUpdateView.as_view(), name="bulk-stock-update"),
    path("inventory/stock/bulk-delta/", BulkStockDeltaView.as_view(), name="bulk-stock-delta"),

    # Stock change audit history
    path("inventory/stock-history/", StockUpdateHistoryView.as_view(), name="stock-update-history"),

    # All configured alerts
    path("inventory/alerts/", StockAlertListView.as_view(), name="stock-alerts-list"),

    # Products with low / zero stock (paginated)
    path("inventory/low-stock/", LowStockProductsView.as_view(), name="low-stock-products"),
]
