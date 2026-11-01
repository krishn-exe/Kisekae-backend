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
    path("products/<int:product_pk>/stock/", ProductStockView.as_view(), name="product-stock-adjust"),

    path("products/<int:product_pk>/stock/add/", ProductStockAddView.as_view(), name="product-stock-add"),
    path("products/<int:product_pk>/stock/remove/", ProductStockRemoveView.as_view(), name="product-stock-remove"),

    path("inventory/stock/bulk/", BulkStockUpdateView.as_view(), name="bulk-stock-update"),
    path("inventory/stock/bulk-delta/", BulkStockDeltaView.as_view(), name="bulk-stock-delta"),

    path("inventory/stock-history/", StockUpdateHistoryView.as_view(), name="stock-update-history"),
]
