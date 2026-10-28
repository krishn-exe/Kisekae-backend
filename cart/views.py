from django.core.exceptions import ValidationError
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from accounts.responses import error_response, success_response

from .models import Cart
from .serializers import (
    AddToCartSerializer,
    CartSerializer,
    CartCheckoutResponseSerializer,
    CartCheckoutSerializer,
    UpdateCartItemSerializer,
)
from .services import CartService

from KiseKaeDjango.schema.helpers import api_response, ERROR_SCHEMA

CART_ERROR_SCHEMA = ERROR_SCHEMA
CART_DETAIL_RESPONSE_SCHEMA = api_response(CartSerializer, "CartDetailResponse")
CART_CHECKOUT_RESPONSE_SCHEMA = api_response(CartCheckoutResponseSerializer, "CartCheckoutSuccessResponse")
CART_VALIDATE_RESPONSE_SCHEMA = api_response(
    inline_serializer(
        name="CartValidateData",
        fields={
            "is_valid": serializers.BooleanField(help_text="Whether all cart items are currently available and purchasable."),
            "issues_count": serializers.IntegerField(help_text="Number of stock or status issues discovered in the cart."),
            "issues": serializers.ListField(help_text="List of issues with product names and reason descriptions."),
        },
    ),
    "CartValidateResponse",
)


class BaseCartView(APIView):
    throttle_scope = "store_read"
    permission_classes = [IsAuthenticated]

    def _get_cart(self, request):
        return CartService.get_or_create_cart(user=request.user)

    def _load_cart_prefetched(self, cart_id):
        return (
            Cart.objects.filter(pk=cart_id)
            .prefetch_related(
                "items__variant",
                "items__product__store",
                "items__product__images",
                "items__product__sizes",
            )
            .first()
        )


class CartDetailView(BaseCartView):
    """
    Retrieve the current authenticated user's cart.
    """

    @extend_schema(
        tags=["Profile - Cart"],
        summary="Get current cart",
        description="Returns the contents, item count, and subtotal of the authenticated user's active cart with item sizes and thumbnails.",
        responses={200: CART_DETAIL_RESPONSE_SCHEMA, 401: CART_ERROR_SCHEMA},
    )
    def get(self, request):
        cart = self._get_cart(request)
        cart = self._load_cart_prefetched(cart.id) or cart
        serializer = CartSerializer(cart)
        return success_response(
            message="Cart fetched successfully.",
            data=serializer.data,
        )


class AddToCartView(BaseCartView):
    """
    Add a product to the cart with optional size selection. If the product with matching size already exists, its quantity is incremented.
    """

    @extend_schema(
        tags=["Profile - Cart"],
        summary="Add item to cart",
        description="Adds a product to the cart with an optional size. Validates product status, store availability, and size-specific stock.",
        request=AddToCartSerializer,
        responses={200: CART_DETAIL_RESPONSE_SCHEMA, 400: CART_ERROR_SCHEMA},
    )
    def post(self, request):
        serializer = AddToCartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        cart = self._get_cart(request)
        product_id = serializer.validated_data["product_id"]
        variant_id = serializer.validated_data.get("variant_id")
        quantity = serializer.validated_data.get("quantity", 1)
        size = serializer.validated_data.get("size", "")

        CartService.add_item(cart=cart, product_id=product_id, variant_id=variant_id, quantity=quantity, size=size)

        cart = self._load_cart_prefetched(cart.id) or cart
        return success_response(
            message="Item added to cart successfully.",
            data=CartSerializer(cart).data,
            status_code=status.HTTP_200_OK,
        )


class CartItemDetailView(BaseCartView):
    """
    Update quantity of a cart item or remove it.
    """

    @extend_schema(
        tags=["Profile - Cart"],
        summary="Update item quantity in cart",
        description="Updates the quantity of an existing cart item. Setting quantity to 0 removes the item.",
        request=UpdateCartItemSerializer,
        responses={200: CART_DETAIL_RESPONSE_SCHEMA, 400: CART_ERROR_SCHEMA, 404: CART_ERROR_SCHEMA},
    )
    def patch(self, request, item_id):
        serializer = UpdateCartItemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        cart = self._get_cart(request)
        quantity = serializer.validated_data["quantity"]
        CartService.update_item_quantity(cart=cart, item_id=item_id, quantity=quantity)

        cart = self._load_cart_prefetched(cart.id) or cart
        return success_response(
            message="Cart updated successfully.",
            data=CartSerializer(cart).data,
        )

    @extend_schema(
        tags=["Profile - Cart"],
        summary="Remove item from cart",
        description="Removes an item completely from the cart.",
        responses={200: CART_DETAIL_RESPONSE_SCHEMA, 400: CART_ERROR_SCHEMA, 404: CART_ERROR_SCHEMA},
    )
    def delete(self, request, item_id):
        cart = self._get_cart(request)
        CartService.remove_item(cart=cart, item_id=item_id)
        cart = self._load_cart_prefetched(cart.id) or cart
        return success_response(
            message="Item removed from cart.",
            data=CartSerializer(cart).data,
        )


class CartClearView(BaseCartView):
    """
    Remove all items from the cart.
    """

    @extend_schema(
        tags=["Profile - Cart"],
        summary="Clear all items from cart",
        description="Empties the current cart of all items.",
        responses={200: CART_DETAIL_RESPONSE_SCHEMA},
    )
    def delete(self, request):
        cart = self._get_cart(request)
        CartService.clear_cart(cart)
        cart = self._load_cart_prefetched(cart.id) or cart
        return success_response(
            message="Cart cleared successfully.",
            data=CartSerializer(cart).data,
        )


class CartValidateView(BaseCartView):
    """
    Validate availability and stock for all items in the cart before checkout.
    """

    @extend_schema(
        tags=["Profile - Cart"],
        summary="Validate cart before checkout",
        description="Checks all items in the cart for store availability and size-specific stock. Returns issues if any items are invalid.",
        responses={200: CART_VALIDATE_RESPONSE_SCHEMA},
    )
    def get(self, request):
        cart = self._get_cart(request)
        report = CartService.validate_cart(cart)
        return success_response(
            message="Cart validation completed.",
            data=report,
        )


class CartCheckoutView(APIView):
    """
    Converts active shopping cart into orders with 5-minute stock reservation.
    Requires customer authentication.
    Resolves shipping details strictly from saved Address ID or user default profile address.
    Returns Razorpay order IDs for payment verification.
    """
    throttle_scope = "store_write"
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Orders - Consumer"],
        summary="Checkout shopping cart into orders",
        description="Converts the user's active shopping cart into orders grouped by store with 5-minute stock reservations and Razorpay payment details. Resolves delivery details strictly from saved profile address.",
        request=CartCheckoutSerializer,
        responses={201: CART_CHECKOUT_RESPONSE_SCHEMA, 400: CART_ERROR_SCHEMA, 401: CART_ERROR_SCHEMA},
    )
    def post(self, request):
        user = request.user
        serializer = CartCheckoutSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)

        from orders.services import OrderService
        try:
            customer_data = OrderService.resolve_shipping_for_user(user, address_id=serializer.validated_data.get("address_id"))
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc)
            return error_response(msg, code=getattr(exc, "code", "SHIPPING_ADDRESS_REQUIRED"), status_code=status.HTTP_400_BAD_REQUEST)

        cart = CartService.get_or_create_cart(user=user)
        orders = CartService.checkout_cart(cart=cart, user=user, customer_data=customer_data)

        total_amount = sum(o.total_amount for o in orders)
        currency = orders[0].currency if orders else "INR"

        orders_summary = [
            {
                "order_id": o.id,
                "order_number": o.order_number,
                "store_id": o.store.id,
                "store_name": o.store.name,
                "total_amount": o.total_amount,
                "currency": o.currency,
                "items_count": o.items.count(),
                "status": o.status,
                "razorpay_order_id": o.razorpay_order_id,
                "expires_at": o.expires_at,
            }
            for o in orders
        ]

        return success_response(
            message=f"Checkout completed successfully. {len(orders)} order(s) placed.",
            data={
                "orders": orders_summary,
                "total_orders_placed": len(orders),
                "total_amount": total_amount,
                "currency": currency,
            },
            status_code=status.HTTP_201_CREATED,
        )
