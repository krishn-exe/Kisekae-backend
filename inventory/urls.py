from django.urls import path

from .views import (
    BulkStockDeltaView,
    BulkStockUpdateView,
    ProductStockAddView,
    ProductStockRemoveView,
    ProductStockView,
    StockUpdateHistoryView,
)

urlpatterns = [
    # Per-product absolute / signed stock adjustment (legacy)
    path("products/<int:product_pk>/stock/", ProductStockView.as_view(), name="product-stock-adjust"),

    # Per-product atomic delta endpoints (add / remove units safely)
    path("products/<int:product_pk>/stock/add/", ProductStockAddView.as_view(), name="product-stock-add"),
    path("products/<int:product_pk>/stock/remove/", ProductStockRemoveView.as_view(), name="product-stock-remove"),

    # Bulk stock adjustment (absolute & delta)
    path("inventory/stock/bulk/", BulkStockUpdateView.as_view(), name="bulk-stock-update"),
    path("inventory/stock/bulk-delta/", BulkStockDeltaView.as_view(), name="bulk-stock-delta"),

    # Stock change audit history
    path("inventory/stock-history/", StockUpdateHistoryView.as_view(), name="stock-update-history"),
]
