from django.urls import path

from .views import (
    BulkOrderStatusUpdateView,
    OrderDeliverView,
    OrderDetailView,
    OrderListView,
    OrderStatusUpdateView,
    StoreOrderRefundApproveView,
    StoreOrderRefundRejectView,
)

urlpatterns = [
    path("orders/", OrderListView.as_view(), name="order-list"),

    path("orders/bulk-update/", BulkOrderStatusUpdateView.as_view(), name="order-bulk-update"),

    path("orders/<int:order_id>/", OrderDetailView.as_view(), name="order-detail"),
    path("orders/<int:order_id>/status/", OrderStatusUpdateView.as_view(), name="order-status-update"),
    path("orders/<int:order_id>/deliver/", OrderDeliverView.as_view(), name="order-deliver"),

    path("orders/<int:order_id>/refund/", StoreOrderRefundApproveView.as_view(), name="store-order-refund-approve"),
    path("orders/<int:order_id>/reject-refund/", StoreOrderRefundRejectView.as_view(), name="store-order-refund-reject"),
]
