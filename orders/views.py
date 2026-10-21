from django.core.exceptions import ValidationError
from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.responses import error_response, success_response
from stores.models import StoreStatus
from stores.pagination import StorePagination
from stores.permissions import IsBusinessVerified, IsSeller, get_store_membership

from .models import Order, OrderStatus, ReturnRequest, ReturnStatus
from .serializers import (
    BulkOrderStatusUpdateSerializer,
    OrderCreateSerializer,
    OrderDeliverSerializer,
    OrderDetailSerializer,
    OrderListSerializer,
    OrderStatusUpdateSerializer,
    ReturnRequestCreateSerializer,
    ReturnRequestSerializer,
    CustomerOrderTrackSerializer,
    ReturnStatusUpdateSerializer,
)
from .services import OrderService

ORDER_ERROR_SCHEMA = inline_serializer(
    name="OrderErrorEnvelope",
    fields={
        "success": serializers.BooleanField(default=False),
        "message": serializers.CharField(),
        "error": inline_serializer(
            name="OrderErrorDetail",
            fields={
                "code": serializers.CharField(),
                "details": serializers.JSONField(allow_null=True),
            },
        ),
    },
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


class OrderListCreateView(BaseOrderView):
    pagination_class = StorePagination

    @extend_schema(
        tags=["Orders"],
        summary="List orders for store",
        description="Returns paginated orders for the store. Filterable by status and customer search.",
        parameters=[
            OpenApiParameter(name="status", description="Filter by OrderStatus", required=False, type=str),
            OpenApiParameter(name="search", description="Search by customer name, email, or order number", required=False, type=str),
        ],
        responses={200: OrderListSerializer(many=True)},
    )
    def get(self, request, pk):
        store, _, err = self._get_store_and_check(request.user, pk, require_active=False)
        if err:
            return err

        queryset = Order.objects.filter(store=store).prefetch_related("items")

        status_filter = request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter.upper())

        search = request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                Q(order_number__icontains=search)
                | Q(customer_name__icontains=search)
                | Q(customer_email__icontains=search)
            )

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

    @extend_schema(
        tags=["Orders"],
        summary="Create a new order",
        description="Creates a new customer order and reserves inventory.",
        request=OrderCreateSerializer,
        responses={201: OrderDetailSerializer, 400: ORDER_ERROR_SCHEMA, 404: ORDER_ERROR_SCHEMA},
    )
    def post(self, request, pk):
        self.throttle_scope = "store_write"
        store, _, err = self._get_store_and_check(request.user, pk, require_active=True)
        if err:
            return err

        serializer = OrderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            order = OrderService.create_order(
                store=store,
                customer_data={
                    "customer_name": data["customer_name"],
                    "customer_email": data["customer_email"],
                    "customer_phone": data["customer_phone"],
                    "shipping_address": data["shipping_address"],
                    "notes": data.get("notes", ""),
                },
                items_data=data["items"],
                user=request.user,
            )
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc)
            return error_response(
                message=msg,
                code="ORDER_CREATION_FAILED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        return success_response(
            message=f"Order {order.order_number} created successfully.",
            data=OrderDetailSerializer(order).data,
            status_code=status.HTTP_201_CREATED,
        )


class OrderDetailView(BaseOrderView):
    @extend_schema(
        tags=["Orders"],
        summary="Get order details",
        description="Returns detailed information for a specific order including items and history.",
        responses={200: OrderDetailSerializer, 404: ORDER_ERROR_SCHEMA},
    )
    def get(self, request, pk, order_id):
        store, _, err = self._get_store_and_check(request.user, pk, require_active=False)
        if err:
            return err

        order = Order.objects.filter(store=store, pk=order_id).prefetch_related(
            "items", "status_history", "return_requests"
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
        tags=["Orders"],
        summary="Update order status",
        description="Advances or transitions an order status according to the lifecycle state machine.",
        request=OrderStatusUpdateSerializer,
        responses={200: OrderDetailSerializer, 400: ORDER_ERROR_SCHEMA, 404: ORDER_ERROR_SCHEMA},
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
                notes=data.get("notes", ""),
                tracking_number=data.get("tracking_number", ""),
                tracking_url=data.get("tracking_url", ""),
            )
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc)
            return error_response(
                message=msg,
                code="STATUS_TRANSITION_FAILED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        return success_response(
            message=f"Order status updated to {updated_order.status}.",
            data=OrderDetailSerializer(updated_order).data,
        )




class OrderDeliverView(BaseOrderView):
    throttle_scope = "store_write"

    @extend_schema(
        tags=["Orders"],
        summary="Mark order as delivered",
        description="Seller endpoint to mark an order as DELIVERED. Updates status, records delivered_at timestamp, and logs status history.",
        request=OrderDeliverSerializer,
        responses={200: OrderDetailSerializer, 400: ORDER_ERROR_SCHEMA, 404: ORDER_ERROR_SCHEMA},
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

        serializer = OrderDeliverSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        notes = serializer.validated_data.get("notes", "")

        try:
            delivered_order = OrderService.mark_order_delivered(
                order=order,
                user=request.user,
                notes=notes,
            )
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc)
            return error_response(
                message=msg,
                code="DELIVERY_FAILED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        return success_response(
            message=f"Order {delivered_order.order_number} marked as delivered.",
            data=OrderDetailSerializer(delivered_order).data,
        )

class BulkOrderStatusUpdateView(BaseOrderView):
    throttle_scope = "store_write"

    @extend_schema(
        tags=["Orders"],
        summary="Bulk update order statuses",
        description="Updates status for multiple orders simultaneously.",
        request=BulkOrderStatusUpdateSerializer,
        responses={200: inline_serializer(name="BulkOrderResponse", fields={"updated_count": serializers.IntegerField()}), 400: ORDER_ERROR_SCHEMA},
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
            message=f"Updated {len(updated)} order(s).",
            data={
                "updated_ids": updated,
                "errors": errors,
            },
        )


class ReturnRequestListCreateView(BaseOrderView):
    @extend_schema(
        tags=["Orders & Returns"],
        summary="List return and replacement requests",
        description="Returns all return/replacement requests for the store.",
        responses={200: ReturnRequestSerializer(many=True)},
    )
    def get(self, request, pk):
        store, _, err = self._get_store_and_check(request.user, pk, require_active=False)
        if err:
            return err

        returns = ReturnRequest.objects.filter(order__store=store).select_related("order", "item", "resolved_by")
        return success_response(
            message="Return requests fetched successfully.",
            data=ReturnRequestSerializer(returns, many=True).data,
        )

    @extend_schema(
        tags=["Orders & Returns"],
        summary="Request a return or replacement for an order",
        request=ReturnRequestCreateSerializer,
        responses={201: ReturnRequestSerializer, 400: ORDER_ERROR_SCHEMA, 404: ORDER_ERROR_SCHEMA},
    )
    def post(self, request, pk, order_id):
        self.throttle_scope = "store_write"
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

        serializer = ReturnRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        item = None
        if data.get("item_id"):
            item = order.items.filter(pk=data["item_id"]).first()
            if not item:
                return error_response(
                    message="OrderItem not found in this order.",
                    code="ITEM_NOT_FOUND",
                    status_code=status.HTTP_404_NOT_FOUND,
                )

        try:
            return_req = OrderService.create_return_request(
                order=order,
                item=item,
                reason=data["reason"],
                customer_notes=data.get("customer_notes", ""),
            )
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc)
            return error_response(
                message=msg,
                code="RETURN_REQUEST_FAILED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        return success_response(
            message="Return request submitted successfully.",
            data=ReturnRequestSerializer(return_req).data,
            status_code=status.HTTP_201_CREATED,
        )


class ReturnRequestDetailView(BaseOrderView):
    throttle_scope = "store_write"

    @extend_schema(
        tags=["Orders & Returns"],
        summary="Resolve return request (Approve / Reject / Complete)",
        request=ReturnStatusUpdateSerializer,
        responses={200: ReturnRequestSerializer, 400: ORDER_ERROR_SCHEMA, 404: ORDER_ERROR_SCHEMA},
    )
    def patch(self, request, pk, return_id):
        store, _, err = self._get_store_and_check(request.user, pk, require_active=True)
        if err:
            return err

        return_req = ReturnRequest.objects.filter(order__store=store, pk=return_id).first()
        if not return_req:
            return error_response(
                message="Return request not found.",
                code="RETURN_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        serializer = ReturnStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            resolved_req = OrderService.resolve_return_request(
                return_request=return_req,
                new_status=data["status"],
                user=request.user,
                admin_notes=data.get("admin_notes", ""),
            )
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc)
            return error_response(
                message=msg,
                code="RETURN_RESOLUTION_FAILED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        return success_response(
            message=f"Return request status updated to {resolved_req.status}.",
            data=ReturnRequestSerializer(resolved_req).data,
        )


# ==============================================================================
# CUSTOMER ORDER TRACKING & HISTORY VIEWS
# ==============================================================================

class CustomerOrderListView(APIView):
    """
    Returns paginated list of orders placed by the authenticated customer.
    """
    throttle_scope = "user_detail"
    permission_classes = [IsAuthenticated]
    pagination_class = StorePagination

    @extend_schema(
        tags=["Customer Orders"],
        summary="List customer orders",
        description="Returns orders placed by the authenticated customer. Supports filtering by status.",
        parameters=[
            OpenApiParameter(name="status", description="Filter by OrderStatus (PENDING, CONFIRMED, PROCESSING, SHIPPED, DELIVERED, CANCELLED, RETURNED)", required=False, type=str),
        ],
        responses={200: OrderListSerializer(many=True)},
    )
    def get(self, request):
        queryset = Order.objects.filter(user=request.user).prefetch_related("items").order_by("-created_at")

        status_param = request.query_params.get("status")
        if status_param and status_param.upper() in OrderStatus.values:
            queryset = queryset.filter(status=status_param.upper())

        paginator = self.pagination_class()
        paginated_orders = paginator.paginate_queryset(queryset, request)
        serializer = OrderListSerializer(paginated_orders, many=True)
        return paginator.get_paginated_response(serializer.data)


class CustomerOrderDetailView(APIView):
    """
    Returns full order details for an order placed by the authenticated customer.
    """
    throttle_scope = "user_detail"
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Customer Orders"],
        summary="Get customer order detail",
        description="Returns detailed information for a specific order owned by the customer, including itemized products, delivery details, and status history.",
        responses={200: OrderDetailSerializer, 404: ORDER_ERROR_SCHEMA},
    )
    def get(self, request, order_id):
        order = (
            Order.objects.filter(user=request.user, pk=order_id)
            .select_related("store")
            .prefetch_related("items", "status_history", "return_requests")
            .first()
        )
        if not order:
            return error_response(
                message="Order not found.",
                code="ORDER_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        return success_response(
            message="Order details retrieved successfully.",
            data=OrderDetailSerializer(order).data,
        )


class CustomerOrderTrackView(APIView):
    """
    Returns live tracking status and timeline for an order by order number or order ID.
    """
    throttle_scope = "user_detail"
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Customer Orders"],
        summary="Track order status & timeline",
        description="Returns tracking status, carrier URL/tracking number, delivered_at timestamp, and transition history for the order.",
        responses={200: CustomerOrderTrackSerializer, 404: ORDER_ERROR_SCHEMA},
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


class CustomerOrderCancelView(APIView):
    """
    Allows customer to cancel an order if it is still in PENDING or CONFIRMED status.
    """
    throttle_scope = "user_detail"
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Customer Orders"],
        summary="Cancel customer order",
        description="Cancels an order if it has not yet reached PROCESSING or SHIPPED status, restoring item inventory.",
        request=None,
        responses={200: OrderDetailSerializer, 400: ORDER_ERROR_SCHEMA, 404: ORDER_ERROR_SCHEMA},
    )
    def post(self, request, order_id):
        order = Order.objects.filter(user=request.user, pk=order_id).first()
        if not order:
            return error_response(
                message="Order not found.",
                code="ORDER_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if order.status not in {OrderStatus.PENDING, OrderStatus.CONFIRMED}:
            return error_response(
                message=f"Cannot cancel order in '{order.status}' status. Only PENDING or CONFIRMED orders can be cancelled.",
                code="ORDER_CANNOT_BE_CANCELLED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        try:
            cancelled_order = OrderService.update_order_status(
                order=order,
                new_status=OrderStatus.CANCELLED,
                user=request.user,
                notes="Cancelled by customer.",
            )
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc)
            return error_response(
                message=msg,
                code="CANCELLATION_FAILED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        return success_response(
            message=f"Order {cancelled_order.order_number} has been cancelled successfully.",
            data=OrderDetailSerializer(cancelled_order).data,
        )
