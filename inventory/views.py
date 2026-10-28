from django.db.models import Q
from django.core.exceptions import ValidationError
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.responses import error_response, success_response
from products.models import Product, ProductCategory
from products.serializers import ProductSerializer
from stores.models import StoreStatus
from stores.pagination import StorePagination
from stores.permissions import IsBusinessVerified, IsSeller, get_store_membership

from .models import StockAlert, StockChangeReason, StockUpdate
from .serializers import (
    BulkStockDeltaSerializer,
    BulkStockUpdateSerializer,
    LowStockProductSerializer,
    SetStockAlertSerializer,
    StockAdjustSerializer,
    StockAlertSerializer,
    StockDeltaSerializer,
    StockUpdateSerializer,
)
from .services import InventoryService

from KiseKaeDjango.schema.helpers import api_response, paginated_response, ERROR_SCHEMA

INVENTORY_ERROR_SCHEMA = ERROR_SCHEMA
STOCK_ADJUST_RESPONSE_SCHEMA = api_response(
    inline_serializer(name="StockAdjustData", fields={"product": ProductSerializer(), "update": StockUpdateSerializer()}),
    "StockAdjustResponse",
)
STOCK_DELTA_RESPONSE_SCHEMA = api_response(
    inline_serializer(name="StockDeltaData", fields={"product": ProductSerializer(), "update": StockUpdateSerializer()}),
    "StockDeltaResponse",
)
BULK_STOCK_UPDATE_RESPONSE_SCHEMA = api_response(StockUpdateSerializer, "BulkStockUpdateResponse", many=True)
BULK_STOCK_DELTA_RESPONSE_SCHEMA = api_response(StockUpdateSerializer, "BulkStockDeltaResponse", many=True)
STOCK_UPDATE_HISTORY_PAGINATED_RESPONSE_SCHEMA = paginated_response(StockUpdateSerializer, "StockUpdateHistoryPaginatedResponse")
PRODUCT_STOCK_ALERT_GET_RESPONSE_SCHEMA = api_response(
    inline_serializer(
        name="ProductStockAlertGetData",
        fields={
            "configured": serializers.BooleanField(help_text="True if a custom alert threshold is configured for this product."),
            "default_threshold": serializers.IntegerField(required=False, help_text="Default system threshold (5) if unconfigured."),
            "alert": StockAlertSerializer(allow_null=True),
        },
    ),
    "ProductStockAlertGetResponse",
)
STOCK_ALERT_RESPONSE_SCHEMA = api_response(StockAlertSerializer, "StockAlertResponse")
STOCK_ALERT_LIST_RESPONSE_SCHEMA = api_response(StockAlertSerializer, "StockAlertListResponse", many=True)
LOW_STOCK_PRODUCTS_PAGINATED_SCHEMA = paginated_response(LowStockProductSerializer, "LowStockProductsPaginatedResponse")


class BaseInventoryView(APIView):
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
                message="Store business verification must be completed before managing inventory.",
                code="BUSINESS_VERIFICATION_REQUIRED",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        if require_active:
            if store.is_archived:
                return None, None, error_response(
                    message="Cannot manage inventory for an archived store.",
                    code="STORE_ARCHIVED",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            if store.status == StoreStatus.SUSPENDED:
                return None, None, error_response(
                    message="Cannot manage inventory for a suspended store.",
                    code="STORE_SUSPENDED",
                    status_code=status.HTTP_403_FORBIDDEN,
                )

        return store, membership, None


class ProductStockView(BaseInventoryView):
    throttle_scope = "store_write"

    @extend_schema(
        tags=["Store - Inventory"],
        summary="Adjust product stock level (Absolute or signed delta)",
        description="Updates stock for a product directly or by delta, recording an audit entry. For live concurrent stock operations, prefer the dedicated /add/ and /remove/ delta endpoints.",
        request=StockAdjustSerializer,
        responses={200: STOCK_ADJUST_RESPONSE_SCHEMA, 400: INVENTORY_ERROR_SCHEMA, 404: INVENTORY_ERROR_SCHEMA},
    )
    def patch(self, request, pk, product_pk):
        store, _, err = self._get_store_and_check(request.user, pk, require_active=True)
        if err:
            return err

        product = Product.objects.filter(store=store, pk=product_pk).first()
        if not product:
            return error_response(
                message="Product not found in this store.",
                code="PRODUCT_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        serializer = StockAdjustSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            updated_product, update_record = InventoryService.update_stock(
                product=product,
                new_stock=data.get("stock"),
                adjustment=data.get("adjustment"),
                reason=data.get("reason"),
                notes=data.get("notes", ""),
                user=request.user,
            )
        except ValidationError as exc:
            return error_response(str(exc), code="STOCK_UPDATE_FAILED", status_code=status.HTTP_400_BAD_REQUEST)

        return success_response(
            message=f"Stock updated to {updated_product.stock}.",
            data={
                "product": ProductSerializer(updated_product).data,
                "update": StockUpdateSerializer(update_record).data,
            },
        )


class ProductStockAddView(BaseInventoryView):
    throttle_scope = "store_write"

    @extend_schema(
        tags=["Store - Inventory"],
        summary="Add stock units (Atomic delta increment)",
        description="Safely adds stock units (+delta) to a product or specific color variant size. Uses database row locks to prevent clobbering live customer orders.",
        request=StockDeltaSerializer,
        responses={200: STOCK_DELTA_RESPONSE_SCHEMA, 400: INVENTORY_ERROR_SCHEMA, 404: INVENTORY_ERROR_SCHEMA},
    )
    def post(self, request, pk, product_pk):
        store, _, err = self._get_store_and_check(request.user, pk, require_active=True)
        if err:
            return err

        product = Product.objects.filter(store=store, pk=product_pk).first()
        if not product:
            return error_response("Product not found in this store.", "PRODUCT_NOT_FOUND", status.HTTP_404_NOT_FOUND)

        serializer = StockDeltaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            updated_product, update_record = InventoryService.add_stock(
                product=product,
                delta=data["delta"],
                variant_id=data.get("variant_id"),
                size=data.get("size"),
                reason=data.get("reason"),
                notes=data.get("notes", ""),
                user=request.user,
            )
        except ValidationError as exc:
            return error_response(str(exc), code="ADD_STOCK_FAILED", status_code=status.HTTP_400_BAD_REQUEST)

        return success_response(
            message=f"Added {data['delta']} units. New total stock: {updated_product.stock}.",
            data={
                "product": ProductSerializer(updated_product).data,
                "update": StockUpdateSerializer(update_record).data,
            },
        )


class ProductStockRemoveView(BaseInventoryView):
    throttle_scope = "store_write"

    @extend_schema(
        tags=["Store - Inventory"],
        summary="Remove stock units (Atomic delta decrement)",
        description="Safely removes stock units (-delta) from a product or specific color variant size. Verifies sufficient stock exists before decrementing under a row lock.",
        request=StockDeltaSerializer,
        responses={200: STOCK_DELTA_RESPONSE_SCHEMA, 400: INVENTORY_ERROR_SCHEMA, 404: INVENTORY_ERROR_SCHEMA},
    )
    def post(self, request, pk, product_pk):
        store, _, err = self._get_store_and_check(request.user, pk, require_active=True)
        if err:
            return err

        product = Product.objects.filter(store=store, pk=product_pk).first()
        if not product:
            return error_response("Product not found in this store.", "PRODUCT_NOT_FOUND", status.HTTP_404_NOT_FOUND)

        serializer = StockDeltaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            updated_product, update_record = InventoryService.remove_stock(
                product=product,
                delta=data["delta"],
                variant_id=data.get("variant_id"),
                size=data.get("size"),
                reason=data.get("reason"),
                notes=data.get("notes", ""),
                user=request.user,
            )
        except ValidationError as exc:
            return error_response(str(exc), code="REMOVE_STOCK_FAILED", status_code=status.HTTP_400_BAD_REQUEST)

        return success_response(
            message=f"Removed {data['delta']} units. New total stock: {updated_product.stock}.",
            data={
                "product": ProductSerializer(updated_product).data,
                "update": StockUpdateSerializer(update_record).data,
            },
        )


class BulkStockDeltaView(BaseInventoryView):
    throttle_scope = "store_write"

    @extend_schema(
        tags=["Store - Inventory"],
        summary="Bulk atomic stock delta updates",
        description="Atomically applies positive (+N) or negative (-N) stock deltas across multiple products/variants in a single transaction.",
        request=BulkStockDeltaSerializer,
        responses={200: BULK_STOCK_DELTA_RESPONSE_SCHEMA, 400: INVENTORY_ERROR_SCHEMA, 404: INVENTORY_ERROR_SCHEMA},
    )
    def post(self, request, pk):
        store, _, err = self._get_store_and_check(request.user, pk, require_active=True)
        if err:
            return err

        serializer = BulkStockDeltaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        items_data = serializer.validated_data["items"]

        try:
            results = InventoryService.bulk_delta_stock(store=store, items_data=items_data, user=request.user)
        except ValidationError as exc:
            return error_response(str(exc), code="BULK_DELTA_FAILED", status_code=status.HTTP_400_BAD_REQUEST)

        update_records = [record for _, record in results]
        return success_response(
            message=f"Successfully applied deltas to {len(results)} item(s).",
            data=StockUpdateSerializer(update_records, many=True).data,
        )


class BulkStockUpdateView(BaseInventoryView):
    throttle_scope = "store_write"

    @extend_schema(
        tags=["Store - Inventory"],
        summary="Bulk update stock for multiple products",
        description="Atomically updates stock levels across multiple products in the store.",
        request=BulkStockUpdateSerializer,
        responses={200: BULK_STOCK_UPDATE_RESPONSE_SCHEMA, 400: INVENTORY_ERROR_SCHEMA, 404: INVENTORY_ERROR_SCHEMA},
    )
    def post(self, request, pk):
        store, _, err = self._get_store_and_check(request.user, pk, require_active=True)
        if err:
            return err

        serializer = BulkStockUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        items_data = serializer.validated_data["items"]

        try:
            results = InventoryService.bulk_update_stock(
                store=store,
                items_data=items_data,
                user=request.user,
            )
        except ValidationError as exc:
            return error_response(str(exc), code="BULK_STOCK_FAILED", status_code=status.HTTP_400_BAD_REQUEST)

        update_records = [record for _, record in results]
        return success_response(
            message=f"Successfully updated stock for {len(results)} product(s).",
            data=StockUpdateSerializer(update_records, many=True).data,
        )


class StockUpdateHistoryView(BaseInventoryView):
    throttle_scope = "store_read"
    pagination_class = StorePagination

    @extend_schema(
        tags=["Store - Inventory"],
        summary="Get stock change audit history",
        description="Returns paginated audit log of all stock adjustments made across store products with filters for date range, variant, reason, and text search.",
        parameters=[
            OpenApiParameter(name="product_id", description="Filter by product ID", required=False, type=int),
            OpenApiParameter(name="variant_id", description="Filter by variant ID", required=False, type=int),
            OpenApiParameter(name="reason", description="Filter by stock adjustment reason", required=False, type=str, enum=[r.value for r in StockChangeReason]),
            OpenApiParameter(name="from_date", description="Filter adjustments on or after date (YYYY-MM-DD)", required=False, type=str),
            OpenApiParameter(name="to_date", description="Filter adjustments on or before date (YYYY-MM-DD)", required=False, type=str),
            OpenApiParameter(name="search", description="Search notes or product name", required=False, type=str),
            OpenApiParameter(name="ordering", description="Sort order for history log", required=False, type=str, enum=["-created_at", "created_at", "-change_amount", "change_amount"]),
        ],
        responses={200: STOCK_UPDATE_HISTORY_PAGINATED_RESPONSE_SCHEMA},
    )
    def get(self, request, pk):
        store, _, err = self._get_store_and_check(request.user, pk, require_active=False)
        if err:
            return err

        queryset = (
            StockUpdate.objects.filter(product__store=store)
            .select_related("product", "variant", "variant_size", "created_by")
        )

        product_id = request.query_params.get("product_id")
        if product_id:
            queryset = queryset.filter(product_id=product_id)

        variant_id = request.query_params.get("variant_id")
        if variant_id:
            queryset = queryset.filter(variant_id=variant_id)

        reason = request.query_params.get("reason")
        if reason:
            queryset = queryset.filter(reason=reason.upper())

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
                Q(notes__icontains=s_clean) | Q(product__name__icontains=s_clean)
            )

        ordering = request.query_params.get("ordering", "-created_at").strip().lower()
        valid_orderings = {
            "-created_at": "-created_at",
            "created_at": "created_at",
            "-change_amount": "-change_amount",
            "change_amount": "change_amount",
        }
        queryset = queryset.order_by(valid_orderings.get(ordering, "-created_at"))

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request)
        serializer = StockUpdateSerializer(page, many=True)

        return success_response(
            message="Stock update history fetched successfully.",
            data={
                "count": paginator.page.paginator.count,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "results": serializer.data,
            },
        )


class ProductStockAlertView(BaseInventoryView):
    throttle_scope = "store_write"

    @extend_schema(
        tags=["Store - Inventory"],
        summary="Get stock alert setting for a product",
        description="Retrieves current low-stock alert configuration for a product. Returns default threshold (5) if unconfigured.",
        responses={200: PRODUCT_STOCK_ALERT_GET_RESPONSE_SCHEMA, 404: INVENTORY_ERROR_SCHEMA},
    )
    def get(self, request, pk, product_pk):
        store, _, err = self._get_store_and_check(request.user, pk, require_active=False)
        if err:
            return err

        product = Product.objects.filter(store=store, pk=product_pk).first()
        if not product:
            return error_response(
                message="Product not found in this store.",
                code="PRODUCT_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        alert = StockAlert.objects.filter(product=product).first()
        if not alert:
            return success_response(
                message="No alert configured for this product (default threshold is 5).",
                data={
                    "configured": False,
                    "default_threshold": 5,
                    "alert": None,
                },
            )

        return success_response(
            message="Stock alert fetched successfully.",
            data={
                "configured": True,
                "alert": StockAlertSerializer(alert).data,
            },
        )

    @extend_schema(
        tags=["Store - Inventory"],
        summary="Set or update stock alert threshold for a product",
        description="Configures or updates the low-stock alert threshold count for this product.",
        request=SetStockAlertSerializer,
        responses={200: STOCK_ALERT_RESPONSE_SCHEMA, 400: INVENTORY_ERROR_SCHEMA, 404: INVENTORY_ERROR_SCHEMA},
    )
    def put(self, request, pk, product_pk):
        store, _, err = self._get_store_and_check(request.user, pk, require_active=True)
        if err:
            return err

        product = Product.objects.filter(store=store, pk=product_pk).first()
        if not product:
            return error_response(
                message="Product not found in this store.",
                code="PRODUCT_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        serializer = SetStockAlertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        threshold = serializer.validated_data["threshold"]
        is_active = serializer.validated_data.get("is_active", True)

        alert = InventoryService.set_alert(product, threshold=threshold, is_active=is_active)
        return success_response(
            message=f"Stock alert threshold set to {threshold}.",
            data=StockAlertSerializer(alert).data,
        )


class StockAlertListView(BaseInventoryView):
    throttle_scope = "store_read"

    @extend_schema(
        tags=["Store - Inventory"],
        summary="List all configured stock alerts for store",
        description="Lists all configured stock alert thresholds for products in the store with active status filter and search.",
        parameters=[
            OpenApiParameter(name="is_active", description="Filter active (true) or disabled (false) alert rules", required=False, type=bool),
            OpenApiParameter(name="search", description="Search by product name or SKU", required=False, type=str),
            OpenApiParameter(name="ordering", description="Sort order for alerts list", required=False, type=str, enum=["-threshold", "threshold", "name", "-name", "-created_at", "created_at"]),
        ],
        responses={200: STOCK_ALERT_LIST_RESPONSE_SCHEMA},
    )
    def get(self, request, pk):
        store, _, err = self._get_store_and_check(request.user, pk, require_active=False)
        if err:
            return err

        alerts = StockAlert.objects.filter(product__store=store).select_related("product")

        is_active = request.query_params.get("is_active")
        if is_active is not None:
            val = str(is_active).strip().lower() in ("true", "1", "yes")
            alerts = alerts.filter(is_active=val)

        search = request.query_params.get("search")
        if search:
            s_clean = search.strip()
            alerts = alerts.filter(Q(product__name__icontains=s_clean) | Q(product__sku__icontains=s_clean))

        ordering = request.query_params.get("ordering", "-created_at").strip().lower()
        valid_orderings = {
            "-threshold": "-threshold",
            "threshold": "threshold",
            "name": "product__name",
            "-name": "-product__name",
            "-created_at": "-created_at",
            "created_at": "created_at",
        }
        alerts = alerts.order_by(valid_orderings.get(ordering, "-created_at"))

        return success_response(
            message="Stock alerts fetched successfully.",
            data=StockAlertSerializer(alerts, many=True).data,
        )


class LowStockProductsView(BaseInventoryView):
    throttle_scope = "store_read"
    pagination_class = StorePagination

    @extend_schema(
        tags=["Store - Inventory"],
        summary="List products with low or zero stock",
        description="Returns paginated list of all products currently at or below their configured or custom alert threshold. Filterable by category and search.",
        parameters=[
            OpenApiParameter(name="threshold", description="Override alert threshold cutoff (e.g. 10 or 20)", required=False, type=int),
            OpenApiParameter(name="category", description="Filter by apparel category", required=False, type=str, enum=[c.value for c in ProductCategory]),
            OpenApiParameter(name="search", description="Search by product name or SKU", required=False, type=str),
            OpenApiParameter(name="ordering", description="Sort order: lowest stock first, highest stock, or name", required=False, type=str, enum=["stock", "-stock", "name", "-name"]),
        ],
        responses={200: LOW_STOCK_PRODUCTS_PAGINATED_SCHEMA},
    )
    def get(self, request, pk):
        store, _, err = self._get_store_and_check(request.user, pk, require_active=False)
        if err:
            return err

        queryset = InventoryService.get_low_stock_products_queryset(store)

        custom_thresh = request.query_params.get("threshold")
        if custom_thresh:
            try:
                c_val = int(custom_thresh)
                queryset = queryset.filter(stock__lte=c_val)
            except (ValueError, TypeError):
                pass

        category = request.query_params.get("category")
        if category:
            queryset = queryset.filter(category=category.upper())

        search = request.query_params.get("search")
        if search:
            s_clean = search.strip()
            queryset = queryset.filter(Q(name__icontains=s_clean) | Q(sku__icontains=s_clean))

        ordering = request.query_params.get("ordering", "stock").strip().lower()
        valid_orderings = {
            "stock": "stock",
            "-stock": "-stock",
            "name": "name",
            "-name": "-name",
        }
        queryset = queryset.order_by(valid_orderings.get(ordering, "stock"))

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request)

        serialized_data = [
            {
                "product_id": p.id,
                "product_name": p.name,
                "sku": p.sku or "",
                "current_stock": p.stock,
                "threshold": getattr(p, "effective_threshold", 5),
                "status": p.status,
            }
            for p in page
        ]

        return success_response(
            message=f"Found {paginator.page.paginator.count} product(s) with low stock.",
            data={
                "count": paginator.page.paginator.count,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "results": serialized_data,
            },
        )
