from django.urls import path
from .views import (
    CustomerOrderCancelView,
    CustomerOrderDetailView,
    CustomerOrderListView,
    CustomerMyOrdersAliasView,
    CustomerOrderTrackView,
    CustomerOrderTrackByNumberView,
)

urlpatterns = [
    path("", CustomerOrderListView.as_view(), name="customer-order-list"),
    path("my-orders/", CustomerMyOrdersAliasView.as_view(), name="customer-my-orders"),
    path("<int:order_id>/", CustomerOrderDetailView.as_view(), name="customer-order-detail"),
    path("<int:order_id>/track/", CustomerOrderTrackView.as_view(), name="customer-order-track-id"),
    path("track/<str:order_number>/", CustomerOrderTrackByNumberView.as_view(), name="customer-order-track-number"),
    path("<int:order_id>/cancel/", CustomerOrderCancelView.as_view(), name="customer-order-cancel"),
]
