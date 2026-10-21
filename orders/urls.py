from django.urls import path

from .views import (
    BulkOrderStatusUpdateView,
    OrderDeliverView,
    OrderDetailView,
    OrderListCreateView,
    OrderStatusUpdateView,
    ReturnRequestDetailView,
    ReturnRequestListCreateView,
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
    path("orders/<int:order_id>/mark-delivered/", OrderDeliverView.as_view(), name="order-mark-delivered"),

    # Returns & Replacements
    path("returns/", ReturnRequestListCreateView.as_view(), name="store-returns-list"),
    path("orders/<int:order_id>/returns/", ReturnRequestListCreateView.as_view(), name="order-return-create"),
    path("returns/<int:return_id>/", ReturnRequestDetailView.as_view(), name="return-detail-update"),
]
