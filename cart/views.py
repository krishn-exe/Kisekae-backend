from django.core.exceptions import ValidationError
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from accounts.responses import error_response, success_response

from .serializers import (
    AddToCartSerializer,
    CartSerializer,
    UpdateCartItemSerializer,
)
from .services import CartService

CART_ERROR_SCHEMA = inline_serializer(
    name="CartErrorEnvelope",
    fields={
        "success": serializers.BooleanField(default=False),
        "message": serializers.CharField(),
        "error": inline_serializer(
            name="CartErrorDetail",
            fields={
                "code": serializers.CharField(),
                "details": serializers.JSONField(allow_null=True),
            },
        ),
    },
)


class BaseCartView(APIView):
    throttle_scope = "store_read"
    permission_classes = [AllowAny]

    def _get_cart(self, request):
        token = request.headers.get("X-Cart-Token") or request.query_params.get("cart_token")
        user = request.user if request.user and request.user.is_authenticated else None
        return CartService.get_or_create_cart(user=user, cart_token=token)


class CartDetailView(BaseCartView):
    """
    Retrieve the current cart or clear all items.
    Supports authenticated users and guest sessions via the `X-Cart-Token` header.
    """

    @extend_schema(
        tags=["Cart"],
        summary="Get current cart",
        description="Returns the contents, item count, and subtotal of the active cart.",
        parameters=[
            OpenApiParameter(
                name="X-Cart-Token",
                description="Optional guest cart token header",
                required=False,
                type=str,
                location=OpenApiParameter.HEADER,
            ),
        ],
        responses={200: CartSerializer},
    )
    def get(self, request):
        cart = self._get_cart(request)
        serializer = CartSerializer(cart)
        return success_response(
            message="Cart fetched successfully.",
            data=serializer.data,
        )


class AddToCartView(BaseCartView):
    """
    Add a product to the cart. If the product already exists in the cart, its quantity is incremented.
    """

    @extend_schema(
        tags=["Cart"],
        summary="Add item to cart",
        description="Adds a product to the cart. Checks product and store active status and stock.",
        request=AddToCartSerializer,
        responses={200: CartSerializer, 400: CART_ERROR_SCHEMA},
    )
    def post(self, request):
        serializer = AddToCartSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                message="Invalid request data.",
                code="VALIDATION_ERROR",
                status_code=status.HTTP_400_BAD_REQUEST,
                details=serializer.errors,
            )

        cart = self._get_cart(request)
        product_id = serializer.validated_data["product_id"]
        quantity = serializer.validated_data.get("quantity", 1)

        try:
            CartService.add_item(cart=cart, product_id=product_id, quantity=quantity)
        except ValidationError as e:
            msg = e.messages[0] if hasattr(e, "messages") and e.messages else str(e)
            return error_response(
                message=msg,
                code="CART_ADD_FAILED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        cart.refresh_from_db()
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
        tags=["Cart"],
        summary="Update item quantity in cart",
        description="Updates the quantity of an existing cart item. Setting quantity to 0 removes the item.",
        request=UpdateCartItemSerializer,
        responses={200: CartSerializer, 400: CART_ERROR_SCHEMA, 404: CART_ERROR_SCHEMA},
    )
    def patch(self, request, item_id):
        serializer = UpdateCartItemSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                message="Invalid request data.",
                code="VALIDATION_ERROR",
                status_code=status.HTTP_400_BAD_REQUEST,
                details=serializer.errors,
            )

        cart = self._get_cart(request)
        quantity = serializer.validated_data["quantity"]

        try:
            CartService.update_item_quantity(cart=cart, item_id=item_id, quantity=quantity)
        except ValidationError as e:
            msg = e.messages[0] if hasattr(e, "messages") and e.messages else str(e)
            return error_response(
                message=msg,
                code="CART_UPDATE_FAILED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        cart.refresh_from_db()
        return success_response(
            message="Cart updated successfully.",
            data=CartSerializer(cart).data,
        )

    @extend_schema(
        tags=["Cart"],
        summary="Remove item from cart",
        description="Removes an item completely from the cart.",
        responses={200: CartSerializer, 400: CART_ERROR_SCHEMA, 404: CART_ERROR_SCHEMA},
    )
    def delete(self, request, item_id):
        cart = self._get_cart(request)

        try:
            CartService.remove_item(cart=cart, item_id=item_id)
        except ValidationError as e:
            msg = e.messages[0] if hasattr(e, "messages") and e.messages else str(e)
            return error_response(
                message=msg,
                code="CART_REMOVE_FAILED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        cart.refresh_from_db()
        return success_response(
            message="Item removed from cart.",
            data=CartSerializer(cart).data,
        )


class CartClearView(BaseCartView):
    """
    Remove all items from the cart.
    """

    @extend_schema(
        tags=["Cart"],
        summary="Clear all items from cart",
        description="Empties the current cart of all items.",
        responses={200: CartSerializer},
    )
    def delete(self, request):
        cart = self._get_cart(request)
        CartService.clear_cart(cart)
        cart.refresh_from_db()
        return success_response(
            message="Cart cleared successfully.",
            data=CartSerializer(cart).data,
        )


class CartValidateView(BaseCartView):
    """
    Validate availability and stock for all items in the cart before checkout.
    """

    @extend_schema(
        tags=["Cart"],
        summary="Validate cart before checkout",
        description="Checks all items in the cart for store availability and stock. Returns issues if any items are invalid.",
        responses={200: inline_serializer(name="CartValidateResponse", fields={"is_valid": serializers.BooleanField(), "issues_count": serializers.IntegerField(), "issues": serializers.ListField()})},
    )
    def get(self, request):
        cart = self._get_cart(request)
        report = CartService.validate_cart(cart)
        return success_response(
            message="Cart validation completed.",
            data=report,
        )
