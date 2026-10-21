from django.urls import path

from .views import (
    AddToCartView,
    CartClearView,
    CartDetailView,
    CartItemDetailView,
    CartCheckoutView,
    CartValidateView,
)

urlpatterns = [
    # View cart
    path("", CartDetailView.as_view(), name="cart-detail"),

    # Clear entire cart
    path("clear/", CartClearView.as_view(), name="cart-clear"),

    # Checkout cart
    path("checkout/", CartCheckoutView.as_view(), name="cart-checkout"),

    # Validate cart before checkout
    path("validate/", CartValidateView.as_view(), name="cart-validate"),

    # Add item to cart
    path("items/", AddToCartView.as_view(), name="cart-add-item"),

    # Update item quantity or remove item
    path("items/<int:item_id>/", CartItemDetailView.as_view(), name="cart-item-detail"),
]
