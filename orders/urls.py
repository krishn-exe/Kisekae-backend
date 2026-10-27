from django.urls import path

from .views import (
    BulkOrderStatusUpdateView,
    OrderDeliverView,
    OrderDetailView,
    OrderListCreateView,
    OrderStatusUpdateView,
)

urlpatterns = [
    # Orders List & Create
    path("orders/", OrderListCreateView.as_view(), name="order-list-create"),

    # Bulk order update
    path("orders/bulk-update/", BulkOrderStatusUpdateView.as_view(), name="order-bulk-update"),

    # Order Detail & Status Transition
    path("orders/<int:order_id>/", OrderDetailView.as_view(), name="order-detail"),
    path("orders/<int:order_id>/status/", OrderStatusUpdateView.as_view(), name="order-status-update"),
    path("orders/<int:order_id>/deliver/", OrderDeliverView.as_view(), name="order-deliver"),
]
