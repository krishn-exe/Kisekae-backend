from django.urls import path

from cart.views import CartCheckoutView
from .views import (
    BuyNowView,
    CustomerOrderCancelView,
    CustomerOrderDetailView,
    CustomerOrderListView,
    CustomerOrderRefundRequestView,
    CustomerOrderReorderView,
    CustomerOrderTrackByNumberView,
    CustomerOrderTrackView,
    OrderSummaryView,
    PaymentVerifyView,
    RazorpayWebhookView,
)

urlpatterns = [
    # Customer Orders List & Detail
    path("", CustomerOrderListView.as_view(), name="customer-order-list"),
    path("summary/", OrderSummaryView.as_view(), name="order-summary"),
    path("checkout/", CartCheckoutView.as_view(), name="order-checkout"),
    path("buy-now/", BuyNowView.as_view(), name="order-buy-now"),
    path("verify-payment/", PaymentVerifyView.as_view(), name="order-verify-payment"),
    path("webhook/razorpay/", RazorpayWebhookView.as_view(), name="order-razorpay-webhook"),
    path("<int:order_id>/", CustomerOrderDetailView.as_view(), name="customer-order-detail"),
    path("<int:order_id>/track/", CustomerOrderTrackView.as_view(), name="customer-order-track-id"),
    path("track/<str:order_number>/", CustomerOrderTrackByNumberView.as_view(), name="customer-order-track-number"),
    path("<int:order_id>/cancel/", CustomerOrderCancelView.as_view(), name="customer-order-cancel"),
    path("<int:order_id>/request-refund/", CustomerOrderRefundRequestView.as_view(), name="customer-order-request-refund"),
    path("<int:order_id>/reorder/", CustomerOrderReorderView.as_view(), name="customer-order-reorder"),
]
