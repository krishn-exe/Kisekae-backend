from decimal import Decimal
import json
import logging
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from accounts.models import Address
from accounts.responses import error_response, success_response
from cart.services import CartService
from products.models import Product, ProductVariant
from stores.models import StoreStatus
from stores.pagination import StorePagination
from stores.permissions import IsBusinessVerified, IsSeller, get_store_membership

from .models import Order, OrderStatus, PaymentStatus
from .razorpay_service import RazorpayService
from .serializers import (
    BulkOrderStatusUpdateSerializer,
    BuyNowSerializer,
    CouponValidateRequestSerializer,
    CouponValidateResponseSerializer,
    OrderSummaryRequestSerializer,
    OrderSummaryResponseSerializer,
    CustomerOrderTrackSerializer,
    OrderCancelSerializer,
    OrderDeliverSerializer,
    OrderDetailSerializer,
    OrderListSerializer,
    OrderRefundApproveSerializer,
    OrderRefundRequestSerializer,
    OrderStatusUpdateSerializer,
    PaymentVerifySerializer,
)
from .services import OrderService

from KiseKaeDjango.schema.helpers import api_response, paginated_response, ERROR_SCHEMA

logger = logging.getLogger(__name__)

ORDER_ERROR_SCHEMA = ERROR_SCHEMA
ORDER_DETAIL_RESPONSE_SCHEMA = api_response(OrderDetailSerializer, "OrderDetailResponse")
STORE_ORDER_PAGINATED_RESPONSE_SCHEMA = paginated_response(OrderListSerializer, "StoreOrderPaginatedResponse")
CUSTOMER_ORDER_PAGINATED_RESPONSE_SCHEMA = paginated_response(OrderListSerializer, "CustomerOrderPaginatedResponse")
CUSTOMER_ORDER_TRACK_RESPONSE_SCHEMA = api_response(CustomerOrderTrackSerializer, "CustomerOrderTrackResponse")
ORDER_SUMMARY_RESPONSE_SCHEMA = api_response(OrderSummaryResponseSerializer, "OrderSummaryApiResponse")
COUPON_VALIDATE_RESPONSE_SCHEMA = api_response(CouponValidateResponseSerializer, "CouponValidateResponse")
BULK_ORDER_UPDATE_RESPONSE_SCHEMA = api_response(
    inline_serializer(
        name="BulkOrderUpdateData",
        fields={
            "updated_count": serializers.IntegerField(),
            "updated_ids": serializers.ListField(child=serializers.IntegerField()),
            "errors": serializers.ListField(),
        },
    ),
    "BulkOrderUpdateResponse",
)
REORDER_RESPONSE_SCHEMA = api_response(
    inline_serializer(
        name="ReorderResponseData",
        fields={
            "reordered_items_count": serializers.IntegerField(),
            "skipped_items": serializers.ListField(),
        },
    ),
    "ReorderResponse",
)


class BaseOrderView(APIView):
    throttle_scope = "store_read"
    permission_classes = [IsAuthenticated, IsSeller, IsBusinessVerified]

    def _get_store_and_check(self, user, store_pk, require_active=True):
        store, membership = get_store_membership(user, store_pk)
        if not store:
            return None, None, error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if not store.is_business_verified:
            return None, None, error_response(
                message="Store business verification must be approved before accessing orders.",
                code="BUSINESS_VERIFICATION_REQUIRED",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        if require_active:
            if store.is_archived:
                return None, None, error_response(
                    message="Cannot manage orders for an archived store.",
                    code="STORE_ARCHIVED",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            if store.status == StoreStatus.SUSPENDED:
                return None, None, error_response(
                    message="Cannot manage orders for a suspended store.",
                    code="STORE_SUSPENDED",
                    status_code=status.HTTP_403_FORBIDDEN,
                )

        return store, membership, None


# ==============================================================================
# SELLER STORE ORDER MANAGEMENT VIEWS
# ==============================================================================

class OrderListView(BaseOrderView):
    pagination_class = StorePagination

    @extend_schema(
        operation_id="seller_store_orders_list",
        tags=["Store - Orders"],
        summary="List orders for store",
        description="Returns paginated orders for the store. Filterable by status, payment status, customer search, date range, and monetary amount.",
        parameters=[
            OpenApiParameter(name="status", description="Filter by order lifecycle status", required=False, type=str, enum=[s.value for s in OrderStatus]),
            OpenApiParameter(name="payment_status", description="Filter by payment status", required=False, type=str, enum=[p.value for p in PaymentStatus]),
            OpenApiParameter(name="from_date", description="Filter orders created on or after date (YYYY-MM-DD)", required=False, type=str),
            OpenApiParameter(name="to_date", description="Filter orders created on or before date (YYYY-MM-DD)", required=False, type=str),
            OpenApiParameter(name="min_amount", description="Minimum order total amount (INR)", required=False, type=float),
            OpenApiParameter(name="max_amount", description="Maximum order total amount (INR)", required=False, type=float),
            OpenApiParameter(name="search", description="Search by customer name, email, or order number", required=False, type=str),
            OpenApiParameter(name="ordering", description="Sort order for order list", required=False, type=str, enum=["-created_at", "created_at", "-total_amount", "total_amount"]),
        ],
        responses={200: STORE_ORDER_PAGINATED_RESPONSE_SCHEMA},
    )
    def get(self, request, pk):
        store, _, err = self._get_store_and_check(request.user, pk, require_active=False)
        if err:
            return err

        queryset = Order.objects.filter(store=store).annotate(items_count=Count("items"))

        status_filter = request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter.upper())

        payment_status = request.query_params.get("payment_status")
        if payment_status:
            queryset = queryset.filter(payment_status=payment_status.upper())

        from_date = request.query_params.get("from_date")
        if from_date:
            try:
                queryset = queryset.filter(created_at__date__gte=from_date.strip())
            except Exception:
                pass

        to_date = request.query_params.get("to_date")
        if to_date:
            try:
                queryset = queryset.filter(created_at__date__lte=to_date.strip())
            except Exception:
                pass

        min_amount = request.query_params.get("min_amount")
        if min_amount:
            try:
                queryset = queryset.filter(total_amount__gte=Decimal(str(min_amount).strip()))
            except Exception:
                pass

        max_amount = request.query_params.get("max_amount")
        if max_amount:
            try:
                queryset = queryset.filter(total_amount__lte=Decimal(str(max_amount).strip()))
            except Exception:
                pass

        search = request.query_params.get("search")
        if search:
            s_clean = search.strip()
            queryset = queryset.filter(
                Q(order_number__icontains=s_clean)
                | Q(customer_name__icontains=s_clean)
                | Q(customer_email__icontains=s_clean)
            )

        ordering = request.query_params.get("ordering", "-created_at").strip().lower()
        valid_orderings = {
            "-created_at": "-created_at",
            "created_at": "created_at",
            "newest": "-created_at",
            "oldest": "created_at",
            "-total_amount": "-total_amount",
            "total_amount": "total_amount",
        }
        queryset = queryset.order_by(valid_orderings.get(ordering, "-created_at"))

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request)
        serializer = OrderListSerializer(page, many=True)

        return success_response(
            message="Orders fetched successfully.",
            data={
                "count": paginator.page.paginator.count,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "results": serializer.data,
            },
        )


OrderListCreateView = OrderListView


class OrderDetailView(BaseOrderView):
    @extend_schema(
        operation_id="seller_store_order_detail",
        tags=["Store - Orders"],
        summary="Get order details",
        description="Returns detailed information for a specific order including items and history.",
        responses={200: ORDER_DETAIL_RESPONSE_SCHEMA, 404: ORDER_ERROR_SCHEMA},
    )
    def get(self, request, pk, order_id):
        store, _, err = self._get_store_and_check(request.user, pk, require_active=False)
        if err:
            return err

        order = Order.objects.filter(store=store, pk=order_id).prefetch_related(
            "items", "status_history"
        ).first()

        if not order:
            return error_response(
                message="Order not found in this store.",
                code="ORDER_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        return success_response(
            message="Order details fetched successfully.",
            data=OrderDetailSerializer(order).data,
        )


class OrderStatusUpdateView(BaseOrderView):
    throttle_scope = "store_write"

    @extend_schema(
        tags=["Store - Orders"],
        summary="Update order status (Seller)",
        description="Transitions order status according to the lifecycle state machine (SHIPPED, OUT_FOR_DELIVERY, DELIVERED, CANCELLED). Automatically records the transition timestamp.",
        request=OrderStatusUpdateSerializer,
        responses={200: ORDER_DETAIL_RESPONSE_SCHEMA, 400: ORDER_ERROR_SCHEMA, 404: ORDER_ERROR_SCHEMA},
    )
    def patch(self, request, pk, order_id):
        store, _, err = self._get_store_and_check(request.user, pk, require_active=True)
        if err:
            return err

        order = Order.objects.filter(store=store, pk=order_id).first()
        if not order:
            return error_response(
                message="Order not found in this store.",
                code="ORDER_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        serializer = OrderStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            updated_order = OrderService.update_order_status(
                order=order,
                new_status=data["status"],
                user=request.user,
            )
        except ValidationError as exc:
            return error_response(str(exc), code="INVALID_TRANSITION", status_code=status.HTTP_400_BAD_REQUEST)

        return success_response(
            message=f"Order status updated to {updated_order.status}.",
            data=OrderDetailSerializer(updated_order).data,
        )

    @extend_schema(
        tags=["Store - Orders"],
        summary="Update order status (POST alias)",
        description="Alternative POST endpoint to transition an order status according to the lifecycle state machine.",
        request=OrderStatusUpdateSerializer,
        responses={200: ORDER_DETAIL_RESPONSE_SCHEMA, 400: ORDER_ERROR_SCHEMA, 404: ORDER_ERROR_SCHEMA},
    )
    def post(self, request, pk, order_id):
        return self.patch(request, pk, order_id)


class OrderDeliverView(BaseOrderView):
    throttle_scope = "store_write"

    @extend_schema(
        tags=["Store - Orders"],
        summary="Mark order as delivered",
        description="Shortcut to transition an order from SHIPPED directly to DELIVERED status.",
        request=OrderDeliverSerializer,
        responses={200: ORDER_DETAIL_RESPONSE_SCHEMA, 400: ORDER_ERROR_SCHEMA, 404: ORDER_ERROR_SCHEMA},
    )
    def post(self, request, pk, order_id):
        store, _, err = self._get_store_and_check(request.user, pk, require_active=True)
        if err:
            return err

        order = Order.objects.filter(store=store, pk=order_id).first()
        if not order:
            return error_response(
                message="Order not found in this store.",
                code="ORDER_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        serializer = OrderDeliverSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        notes = serializer.validated_data.get("notes", "")

        try:
            delivered_order = OrderService.update_order_status(
                order=order,
                new_status=OrderStatus.DELIVERED,
                user=request.user,
                notes=notes or "Order marked as delivered.",
            )
        except ValidationError as exc:
            return error_response(str(exc), code="DELIVERY_TRANSITION_FAILED", status_code=status.HTTP_400_BAD_REQUEST)

        return success_response(
            message=f"Order {delivered_order.order_number} marked as DELIVERED successfully.",
            data=OrderDetailSerializer(delivered_order).data,
        )


class BulkOrderStatusUpdateView(BaseOrderView):
    throttle_scope = "store_write"

    @extend_schema(
        tags=["Store - Orders"],
        summary="Bulk update order statuses",
        description="Updates status for multiple orders simultaneously.",
        request=BulkOrderStatusUpdateSerializer,
        responses={200: BULK_ORDER_UPDATE_RESPONSE_SCHEMA, 400: ORDER_ERROR_SCHEMA},
    )
    def post(self, request, pk):
        store, _, err = self._get_store_and_check(request.user, pk, require_active=True)
        if err:
            return err

        serializer = BulkOrderStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        orders_data = serializer.validated_data["orders"]

        updated = []
        errors = []
        for item in orders_data:
            order = Order.objects.filter(store=store, pk=item["order_id"]).first()
            if not order:
                errors.append({"order_id": item["order_id"], "error": "Order not found"})
                continue
            try:
                OrderService.update_order_status(
                    order=order,
                    new_status=item["status"],
                    user=request.user,
                    notes=item.get("notes", "Bulk update"),
                )
                updated.append(order.id)
            except ValidationError as exc:
                errors.append({"order_id": order.id, "error": str(exc)})

        return success_response(
            message=f"Bulk update completed: {len(updated)} updated, {len(errors)} errors.",
            data={
                "updated_count": len(updated),
                "updated_ids": updated,
                "errors": errors,
            },
        )


class StoreOrderRefundApproveView(BaseOrderView):
    throttle_scope = "store_write"

    @extend_schema(
        tags=["Store - Orders"],
        summary="Approve customer refund request (Seller)",
        description="Approves a return/refund request for a delivered order. Triggers Razorpay refund and restocks variant items.",
        request=OrderRefundApproveSerializer,
        responses={200: ORDER_DETAIL_RESPONSE_SCHEMA, 400: ORDER_ERROR_SCHEMA, 404: ORDER_ERROR_SCHEMA},
    )
    def post(self, request, pk, order_id):
        store, _, err = self._get_store_and_check(request.user, pk, require_active=True)
        if err:
            return err

        order = Order.objects.filter(store=store, pk=order_id).first()
        if not order:
            return error_response("Order not found in this store.", "ORDER_NOT_FOUND", status.HTTP_404_NOT_FOUND)

        serializer = OrderRefundApproveSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        notes = serializer.validated_data.get("notes", "")

        try:
            refunded_order = OrderService.approve_refund(order=order, actor_user=request.user, notes=notes)
        except ValidationError as exc:
            return error_response(str(exc), code="REFUND_APPROVAL_FAILED", status_code=status.HTTP_400_BAD_REQUEST)

        return success_response(
            message=f"Order {refunded_order.order_number} refund approved and processed.",
            data=OrderDetailSerializer(refunded_order).data,
        )


class StoreOrderRefundRejectView(BaseOrderView):
    throttle_scope = "store_write"

    @extend_schema(
        tags=["Store - Orders"],
        summary="Reject customer refund request (Seller)",
        description="Rejects a return/refund request and reverts the order status back to DELIVERED.",
        request=OrderRefundApproveSerializer,
        responses={200: ORDER_DETAIL_RESPONSE_SCHEMA, 400: ORDER_ERROR_SCHEMA, 404: ORDER_ERROR_SCHEMA},
    )
    def post(self, request, pk, order_id):
        store, _, err = self._get_store_and_check(request.user, pk, require_active=True)
        if err:
            return err

        order = Order.objects.filter(store=store, pk=order_id).first()
        if not order:
            return error_response("Order not found in this store.", "ORDER_NOT_FOUND", status.HTTP_404_NOT_FOUND)

        serializer = OrderRefundApproveSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        notes = serializer.validated_data.get("notes", "")

        try:
            reverted_order = OrderService.reject_refund(order=order, actor_user=request.user, notes=notes)
        except ValidationError as exc:
            return error_response(str(exc), code="REFUND_REJECTION_FAILED", status_code=status.HTTP_400_BAD_REQUEST)

        return success_response(
            message=f"Order {reverted_order.order_number} refund request rejected.",
            data=OrderDetailSerializer(reverted_order).data,
        )


# ==============================================================================
# CONSUMER ORDER CHECKOUT, PAYMENT & SELF-SERVICE VIEWS
# ==============================================================================

class BuyNowView(APIView):
    """
    Direct 'Buy Now' purchase skipping the cart. Reserves stock for 5 minutes
    and returns a Razorpay order payload.
    """
    throttle_scope = "store_write"
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Orders - Consumer"],
        summary="Buy Now (Instant Order Placement)",
        description="Directly places an order for a single variant item with 5-minute stock reservation. Returns Razorpay order details for immediate checkout.",
        request=BuyNowSerializer,
        responses={201: ORDER_DETAIL_RESPONSE_SCHEMA, 400: ORDER_ERROR_SCHEMA, 404: ORDER_ERROR_SCHEMA},
    )
    def post(self, request):
        user = request.user
        serializer = BuyNowSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        product_id = data["product_id"]
        product = Product.objects.select_related("store").filter(pk=product_id).first()
        if not product:
            return error_response("Product not found.", "PRODUCT_NOT_FOUND", status.HTTP_404_NOT_FOUND)

        try:
            customer_data = OrderService.resolve_shipping_for_user(user, address_id=data.get("address_id"))
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc)
            return error_response(msg, code=getattr(exc, "code", "SHIPPING_ADDRESS_REQUIRED"), status_code=status.HTTP_400_BAD_REQUEST)

        items_payload = [
            {
                "product_id": product.id,
                "variant_id": data.get("variant_id"),
                "size": data.get("size", ""),
                "quantity": data.get("quantity", 1),
            }
        ]

        try:
            order = OrderService.create_order(
                store=product.store,
                customer_data=customer_data,
                items_data=items_payload,
                user=user,
                use_wallet=data.get("use_wallet", False),
                coupon_code=data.get("coupon_code", "").strip() or None,
            )
        except ValidationError as exc:
            return error_response(str(exc), code="ORDER_CREATION_FAILED", status_code=status.HTTP_400_BAD_REQUEST)

        return success_response(
            message="Order placed and fully paid using your wallet balance." if order.total_amount == 0 else "Order placed with 5-minute stock reservation. Complete payment via Razorpay.",
            data=OrderDetailSerializer(order).data,
            status_code=status.HTTP_201_CREATED,
        )


class PaymentVerifyView(APIView):
    """
    Verifies Razorpay payment signature after customer completes payment in frontend SDK.
    Advances order from PENDING to CONFIRMED.
    """
    throttle_scope = "store_write"
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Orders - Consumer"],
        summary="Verify Razorpay payment",
        description="Verifies the HMAC SHA256 payment signature from Razorpay SDK. Advances order from PENDING to CONFIRMED.",
        request=PaymentVerifySerializer,
        responses={200: ORDER_DETAIL_RESPONSE_SCHEMA, 400: ORDER_ERROR_SCHEMA},
    )
    def post(self, request):
        serializer = PaymentVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            order = OrderService.verify_payment(
                order_id=data["order_id"],
                razorpay_payment_id=data["razorpay_payment_id"],
                razorpay_order_id=data.get("razorpay_order_id", ""),
                razorpay_signature=data["razorpay_signature"],
                user=request.user,
            )
        except ValidationError as exc:
            return error_response(str(exc), code=getattr(exc, "code", "PAYMENT_VERIFICATION_FAILED"), status_code=status.HTTP_400_BAD_REQUEST)

        return success_response(
            message="Payment verified successfully. Order confirmed.",
            data=OrderDetailSerializer(order).data,
        )


@method_decorator(csrf_exempt, name="dispatch")
class RazorpayWebhookView(APIView):
    """
    Async webhook receiver for Razorpay events (payment.captured, payment.failed, refund.processed).
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(exclude=True)
    def post(self, request):
        signature = request.headers.get("X-Razorpay-Signature", "")
        body_bytes = request.body

        if not RazorpayService.verify_webhook_signature(body_bytes, signature):
            logger.warning("Invalid Razorpay webhook signature received.")
            return error_response("Invalid webhook signature.", "INVALID_WEBHOOK_SIGNATURE", status.HTTP_400_BAD_REQUEST)

        try:
            event_data = json.loads(body_bytes.decode("utf-8"))
        except Exception:
            return error_response("Malformed webhook JSON payload.", "INVALID_PAYLOAD", status.HTTP_400_BAD_REQUEST)

        event = event_data.get("event")
        payload = event_data.get("payload", {})
        payment_entity = payload.get("payment", {}).get("entity", {})
        rzp_order_id = payment_entity.get("order_id")
        rzp_payment_id = payment_entity.get("id")

        logger.info("Razorpay webhook event: %s for rzp_order: %s", event, rzp_order_id)

        if event == "payment.captured" and rzp_order_id:
            order = Order.objects.filter(razorpay_order_id=rzp_order_id).first()
            if order and order.status == OrderStatus.PENDING:
                try:
                    OrderService.verify_payment(
                        order_id=order.id,
                        razorpay_payment_id=rzp_payment_id,
                        razorpay_order_id=rzp_order_id,
                        razorpay_signature="mock_signature",
                    )
                except Exception as exc:
                    logger.exception("Error processing webhook payment capture for order %s: %s", order.id, exc)

        return success_response(message="Webhook processed successfully.")


class CustomerOrderListView(APIView):
    throttle_scope = "user_detail"
    permission_classes = [IsAuthenticated]
    pagination_class = StorePagination

    @extend_schema(
        operation_id="customer_orders_list",
        tags=["Orders - Consumer"],
        summary="List customer orders",
        description="Returns paginated list of all orders placed by the current customer with filters for status, date range, search, and store.",
        parameters=[
            OpenApiParameter(name="status", description="Filter by order lifecycle status", required=False, type=str, enum=[s.value for s in OrderStatus]),
            OpenApiParameter(name="search", description="Search by order number or product name in order", required=False, type=str),
            OpenApiParameter(name="store_id", description="Filter orders placed with a specific store ID", required=False, type=int),
            OpenApiParameter(name="from_date", description="Filter orders created on or after date (YYYY-MM-DD)", required=False, type=str),
            OpenApiParameter(name="to_date", description="Filter orders created on or before date (YYYY-MM-DD)", required=False, type=str),
            OpenApiParameter(name="ordering", description="Sort order for order list", required=False, type=str, enum=["-created_at", "created_at", "-total_amount", "total_amount"]),
        ],
        responses={200: CUSTOMER_ORDER_PAGINATED_RESPONSE_SCHEMA},
    )
    def get(self, request):
        queryset = (
            Order.objects.filter(user=request.user)
            .select_related("store")
            .prefetch_related("items")
        )

        status_filter = request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter.upper())

        store_id = request.query_params.get("store_id")
        if store_id:
            try:
                queryset = queryset.filter(store_id=int(store_id))
            except (ValueError, TypeError):
                pass

        from_date = request.query_params.get("from_date")
        if from_date:
            try:
                queryset = queryset.filter(created_at__date__gte=from_date.strip())
            except Exception:
                pass

        to_date = request.query_params.get("to_date")
        if to_date:
            try:
                queryset = queryset.filter(created_at__date__lte=to_date.strip())
            except Exception:
                pass

        search = request.query_params.get("search")
        if search:
            s_clean = search.strip()
            queryset = queryset.filter(
                Q(order_number__icontains=s_clean)
                | Q(items__product_name__icontains=s_clean)
            ).distinct()

        ordering = request.query_params.get("ordering", "-created_at").strip().lower()
        valid_orderings = {
            "-created_at": "-created_at",
            "created_at": "created_at",
            "newest": "-created_at",
            "oldest": "created_at",
            "-total_amount": "-total_amount",
            "total_amount": "total_amount",
        }
        queryset = queryset.order_by(valid_orderings.get(ordering, "-created_at"))

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request)
        serializer = OrderListSerializer(page, many=True)

        return success_response(
            message="Customer orders fetched successfully.",
            data={
                "count": paginator.page.paginator.count,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "results": serializer.data,
            },
        )


class CustomerOrderDetailView(APIView):
    throttle_scope = "user_detail"
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="customer_orders_detail",
        tags=["Orders - Consumer"],
        summary="Get customer order detail",
        description="Returns complete details, line items, and tracking status for a customer's order.",
        responses={200: ORDER_DETAIL_RESPONSE_SCHEMA, 404: ORDER_ERROR_SCHEMA},
    )
    def get(self, request, order_id):
        order = (
            Order.objects.filter(user=request.user, pk=order_id)
            .select_related("store")
            .prefetch_related("items", "status_history")
            .first()
        )
        if not order:
            return error_response("Order not found.", "ORDER_NOT_FOUND", status_code=status.HTTP_404_NOT_FOUND)

        return success_response(
            message="Order details fetched successfully.",
            data=OrderDetailSerializer(order).data,
        )


class CustomerOrderCancelView(APIView):
    throttle_scope = "user_detail"
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Orders - Consumer"],
        summary="Cancel customer order (Pre-shipment)",
        description="Cancels an order if it has not yet reached SHIPPED status. Automatically initiates Razorpay refund and restores reserved inventory.",
        request=OrderCancelSerializer,
        responses={200: ORDER_DETAIL_RESPONSE_SCHEMA, 400: ORDER_ERROR_SCHEMA, 404: ORDER_ERROR_SCHEMA},
    )
    def post(self, request, order_id):
        order = Order.objects.filter(user=request.user, pk=order_id).first()
        if not order:
            return error_response("Order not found.", "ORDER_NOT_FOUND", status_code=status.HTTP_404_NOT_FOUND)

        serializer = OrderCancelSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data.get("reason", "")

        try:
            cancelled_order = OrderService.cancel_order(order=order, user=request.user, reason=reason)
        except ValidationError as exc:
            return error_response(str(exc), code="ORDER_CANNOT_BE_CANCELLED", status_code=status.HTTP_400_BAD_REQUEST)

        return success_response(
            message=f"Order {cancelled_order.order_number} has been cancelled successfully. Any payment captured has been refunded.",
            data=OrderDetailSerializer(cancelled_order).data,
        )


class CustomerOrderRefundRequestView(APIView):
    throttle_scope = "user_detail"
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Orders - Consumer"],
        summary="Request refund / return (Post-delivery)",
        description="Customer requests return and refund for a delivered order. Allowed only within 10 days of delivery, and only for products with is_refundable == True.",
        request=OrderRefundRequestSerializer,
        responses={200: ORDER_DETAIL_RESPONSE_SCHEMA, 400: ORDER_ERROR_SCHEMA, 404: ORDER_ERROR_SCHEMA},
    )
    def post(self, request, order_id):
        order = Order.objects.filter(user=request.user, pk=order_id).first()
        if not order:
            return error_response("Order not found.", "ORDER_NOT_FOUND", status_code=status.HTTP_404_NOT_FOUND)

        serializer = OrderRefundRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data["reason"]

        try:
            updated_order = OrderService.request_refund(order=order, user=request.user, reason=reason)
        except ValidationError as exc:
            return error_response(str(exc), code="REFUND_REQUEST_FAILED", status_code=status.HTTP_400_BAD_REQUEST)

        return success_response(
            message="Refund request submitted successfully. Awaiting seller review.",
            data=OrderDetailSerializer(updated_order).data,
        )


class CustomerOrderReorderView(APIView):
    throttle_scope = "user_detail"
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Orders - Consumer"],
        summary="Re-add order items to cart",
        description="Re-adds in-stock variant items from a past order back into the customer's active shopping cart.",
        request=None,
        responses={200: REORDER_RESPONSE_SCHEMA, 404: ORDER_ERROR_SCHEMA},
    )
    def post(self, request, order_id):
        order = Order.objects.filter(user=request.user, pk=order_id).first()
        if not order:
            return error_response("Order not found.", "ORDER_NOT_FOUND", status_code=status.HTTP_404_NOT_FOUND)

        result = CartService.reorder(order=order, user=request.user)

        return success_response(
            message=f"Reordered {result['reordered_items_count']} item(s) to cart.",
            data={
                "reordered_items_count": result["reordered_items_count"],
                "skipped_items": result["skipped_items"],
            },
        )


class CustomerOrderTrackView(APIView):
    throttle_scope = "user_detail"
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="customer_orders_track_by_id",
        tags=["Orders - Consumer"],
        summary="Track order status by order ID",
        description="Returns tracking status, transition timestamps, and the sequential lifecycle events (Order Confirmed, Shipped, Out For Delivery, Delivered) by internal database order ID.",
        parameters=[
            OpenApiParameter(name="order_id", description="Internal integer database ID of the order", required=True, type=int, location=OpenApiParameter.PATH),
        ],
        responses={200: CUSTOMER_ORDER_TRACK_RESPONSE_SCHEMA, 404: ORDER_ERROR_SCHEMA},
    )
    def get(self, request, order_number=None, order_id=None):
        queryset = (
            Order.objects.filter(user=request.user)
            .select_related("store")
            .prefetch_related("items", "status_history")
        )
        if order_number:
            order = queryset.filter(order_number=order_number).first()
        elif order_id:
            order = queryset.filter(pk=order_id).first()
        else:
            return error_response(
                message="Order number or ID required.",
                code="INVALID_REQUEST",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        if not order:
            return error_response(
                message="Order not found.",
                code="ORDER_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        return success_response(
            message="Order tracking details retrieved successfully.",
            data=CustomerOrderTrackSerializer(order).data,
        )


class CustomerOrderTrackByNumberView(CustomerOrderTrackView):
    @extend_schema(
        operation_id="customer_orders_track_by_order_number",
        tags=["Orders - Consumer"],
        summary="Track order status by order number",
        description="Returns tracking status, transition timestamps, and the sequential lifecycle events (Order Confirmed, Shipped, Out For Delivery, Delivered) by order number (e.g. ORD-20261022-A1B2C3).",
        parameters=[
            OpenApiParameter(name="order_number", description="Public order number string (e.g. ORD-20261022-A1B2C3)", required=True, type=str, location=OpenApiParameter.PATH),
        ],
        responses={200: CUSTOMER_ORDER_TRACK_RESPONSE_SCHEMA, 404: ORDER_ERROR_SCHEMA},
    )
    def get(self, request, order_number=None, order_id=None):
        return super().get(request, order_number=order_number, order_id=order_id)


class OrderSummaryView(APIView):
    """
    Calculates cost breakdown before checkout:
    - total_cost: sum of compare_at prices
    - subtotal: sum of actual product prices
    - discount: compare price sum - actual sum
    - wallet_covered_amount: amount covered by wallet if use_wallet=True
    - final_due_amount: remaining amount to be paid
    - requires_razorpay: false if final_due_amount == 0
    """
    permission_classes = [IsAuthenticated]
    throttle_scope = "user_detail"

    @extend_schema(
        tags=["Orders - Consumer"],
        summary="Calculate order cost summary",
        description="Returns an exact price calculation summary before order placement, including compare-at savings, wallet deduction, and whether payment gateway redirection is required.",
        request=OrderSummaryRequestSerializer,
        responses={200: ORDER_SUMMARY_RESPONSE_SCHEMA, 400: ORDER_ERROR_SCHEMA},
    )
    def post(self, request):
        serializer = OrderSummaryRequestSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        use_wallet = data.get("use_wallet", False)
        store_id = data.get("store_id")
        product_id = data.get("product_id")

        items_summary = []

        if product_id:
            # Direct "Buy Now" preview mode
            product = Product.objects.select_related("store").filter(pk=product_id).first()
            if not product:
                return error_response("Product not found.", "PRODUCT_NOT_FOUND", status.HTTP_404_NOT_FOUND)

            qty = data.get("quantity", 1)
            variant_id = data.get("variant_id")
            variant = None
            if variant_id:
                variant = product.variants.filter(pk=variant_id).first()

            unit_price = product.price
            compare_price = product.compare_at_price
            effective_compare = compare_price if compare_price is not None else unit_price

            total_item_price = unit_price * qty
            total_item_compare = effective_compare * qty

            items_summary.append({
                "product_id": product.id,
                "product_name": product.name,
                "variant_id": variant.id if variant else None,
                "color_name": variant.color_name if variant else (product.color or ""),
                "size": data.get("size", ""),
                "quantity": qty,
                "unit_price": unit_price,
                "compare_at_price": compare_price,
                "total_price": total_item_price,
                "compare_at_total": total_item_compare,
            })
        else:
            # Active Cart preview mode
            cart = CartService.get_or_create_cart(user=request.user)
            cart_items_qs = cart.items.select_related("product", "variant", "product__store").all()
            if store_id:
                cart_items_qs = cart_items_qs.filter(product__store_id=store_id)

            if not cart_items_qs.exists():
                return error_response(
                    "No items found in cart to summarize.",
                    code="EMPTY_CART",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            for c_item in cart_items_qs:
                prod = c_item.product
                unit_price = prod.price
                compare_price = prod.compare_at_price
                effective_compare = compare_price if compare_price is not None else unit_price

                total_item_price = unit_price * c_item.quantity
                total_item_compare = effective_compare * c_item.quantity

                items_summary.append({
                    "product_id": prod.id,
                    "product_name": prod.name,
                    "variant_id": c_item.variant.id if c_item.variant else None,
                    "color_name": c_item.variant.color_name if c_item.variant else (prod.color or ""),
                    "size": c_item.size,
                    "quantity": c_item.quantity,
                    "unit_price": unit_price,
                    "compare_at_price": compare_price,
                    "total_price": total_item_price,
                    "compare_at_total": total_item_compare,
                })

        total_cost = sum(item["compare_at_total"] for item in items_summary)
        subtotal = sum(item["total_price"] for item in items_summary)
        discount = max(Decimal("0.00"), total_cost - subtotal)

        # Coupon Code calculation
        coupon_code_input = data.get("coupon_code", "").strip()
        coupon_discount = Decimal("0.00")
        coupon_error = None
        applied_coupon_code = ""

        if coupon_code_input:
            from .coupon_service import CouponService
            # Resolve store context if single store item or store_id passed
            target_store_id = store_id
            if not target_store_id and items_summary:
                # Check if all items belong to same store
                first_prod = Product.objects.filter(pk=items_summary[0]["product_id"]).first()
                if first_prod:
                    target_store_id = first_prod.store_id

            c_result = CouponService.validate_and_calculate(
                coupon_code=coupon_code_input,
                subtotal=subtotal,
                user=request.user,
                store=target_store_id,
            )
            if c_result["is_valid"]:
                coupon_discount = c_result["discount"]
                applied_coupon_code = c_result["coupon"].code
            else:
                coupon_error = c_result["error"]

        amount_after_coupon = max(Decimal("0.00"), subtotal - coupon_discount)

        from accounts.wallet_service import WalletService
        wallet = WalletService.get_or_create(request.user)
        wallet_balance = wallet.balance

        if use_wallet:
            wallet_covered_amount = min(wallet_balance, amount_after_coupon)
        else:
            wallet_covered_amount = Decimal("0.00")

        final_due_amount = amount_after_coupon - wallet_covered_amount
        requires_razorpay = final_due_amount > Decimal("0.00")

        summary_payload = {
            "items": items_summary,
            "total_cost": total_cost,
            "subtotal": subtotal,
            "discount": discount,
            "coupon_code": applied_coupon_code,
            "coupon_discount": coupon_discount,
            "coupon_error": coupon_error,
            "wallet_balance": wallet_balance,
            "wallet_covered_amount": wallet_covered_amount,
            "final_due_amount": final_due_amount,
            "requires_razorpay": requires_razorpay,
            "currency": "INR",
        }
        return success_response(
            message="Order summary calculated successfully.",
            data=OrderSummaryResponseSerializer(summary_payload).data,
        )


class CouponValidateView(APIView):
    """
    Validates a coupon code against current subtotal and store.
    """
    permission_classes = [IsAuthenticated]
    throttle_scope = "user_detail"

    @extend_schema(
        tags=["Orders - Consumer"],
        summary="Validate coupon code",
        description="Checks coupon validity, expiration, usage thresholds, and calculates applicable discount.",
        request=CouponValidateRequestSerializer,
        responses={200: COUPON_VALIDATE_RESPONSE_SCHEMA, 400: ORDER_ERROR_SCHEMA},
    )
    def post(self, request):
        serializer = CouponValidateRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        from .coupon_service import CouponService
        result = CouponService.validate_and_calculate(
            coupon_code=data["coupon_code"],
            subtotal=data.get("subtotal", Decimal("0.00")),
            user=request.user,
            store=data.get("store_id"),
        )

        coupon = result["coupon"]
        response_payload = {
            "is_valid": result["is_valid"],
            "coupon_code": coupon.code if coupon else data["coupon_code"].strip().upper(),
            "discount_type": coupon.discount_type if coupon else "",
            "discount_value": coupon.discount_value if coupon else Decimal("0.00"),
            "discount_amount": result["discount"],
            "error": result["error"],
        }

        return success_response(
            message="Coupon validated." if result["is_valid"] else result["error"],
            data=CouponValidateResponseSerializer(response_payload).data,
        )
