from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.responses import error_response, success_response
from products.models import Product
from products.serializers import ProductSerializer
from stores.models import StoreStatus
from stores.pagination import StorePagination
from stores.permissions import IsBusinessVerified, IsSeller, get_store_membership

from .models import StockAlert, StockUpdate
from .serializers import (
    BulkStockUpdateSerializer,
    LowStockProductSerializer,
    SetStockAlertSerializer,
    StockAdjustSerializer,
    StockAlertSerializer,
    StockUpdateSerializer,
)
from .services import InventoryService

from KiseKaeDjango.schema.helpers import api_response, paginated_response, ERROR_SCHEMA

INVENTORY_ERROR_SCHEMA = ERROR_SCHEMA
STOCK_ADJUST_RESPONSE_SCHEMA = api_response(
    inline_serializer(name="StockAdjustData", fields={"product": ProductSerializer(), "update": StockUpdateSerializer()}),
    "StockAdjustResponse",
)
BULK_STOCK_UPDATE_RESPONSE_SCHEMA = api_response(StockUpdateSerializer, "BulkStockUpdateResponse", many=True)
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
LOW_STOCK_PRODUCTS_RESPONSE_SCHEMA = api_response(LowStockProductSerializer, "LowStockProductsResponse", many=True)


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
        summary="Adjust product stock level",
        description="Updates stock for a product directly or by delta, recording an audit entry.",
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

        updated_product, update_record = InventoryService.update_stock(
            product=product,
            new_stock=data.get("stock"),
            adjustment=data.get("adjustment"),
            reason=data.get("reason"),
            notes=data.get("notes", ""),
            user=request.user,
        )

        return success_response(
            message=f"Stock updated to {updated_product.stock}.",
            data={
                "product": ProductSerializer(updated_product).data,
                "update": StockUpdateSerializer(update_record).data,
            },
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

        results = InventoryService.bulk_update_stock(
            store=store,
            items_data=items_data,
            user=request.user,
        )

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
        summary="List stock change history (audit ledger)",
        description="Lists paginated stock change history for store products. Filterable by product_id.",
        parameters=[
            OpenApiParameter(
                name="product_id",
                description="Filter history by a specific product ID",
                required=False,
                type=int,
            ),
        ],
        responses={200: STOCK_UPDATE_HISTORY_PAGINATED_RESPONSE_SCHEMA},
    )
    def get(self, request, pk):
        store, _, err = self._get_store_and_check(request.user, pk, require_active=False)
        if err:
            return err

        queryset = StockUpdate.objects.filter(product__store=store).select_related("product", "created_by")

        product_id = request.query_params.get("product_id")
        if product_id:
            queryset = queryset.filter(product_id=product_id)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request)
        serializer = StockUpdateSerializer(page, many=True)

        return success_response(
            message="Stock history fetched successfully.",
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
        description="Lists all configured stock alert thresholds for products in the store.",
        responses={200: STOCK_ALERT_LIST_RESPONSE_SCHEMA},
    )
    def get(self, request, pk):
        store, _, err = self._get_store_and_check(request.user, pk, require_active=False)
        if err:
            return err

        alerts = StockAlert.objects.filter(product__store=store).select_related("product")
        return success_response(
            message="Stock alerts fetched successfully.",
            data=StockAlertSerializer(alerts, many=True).data,
        )


class LowStockProductsView(BaseInventoryView):
    throttle_scope = "store_read"

    @extend_schema(
        tags=["Store - Inventory"],
        summary="List products with low or zero stock",
        description="Returns all products currently at or below their configured or default alert threshold.",
        responses={200: LOW_STOCK_PRODUCTS_RESPONSE_SCHEMA},
    )
    def get(self, request, pk):
        store, _, err = self._get_store_and_check(request.user, pk, require_active=False)
        if err:
            return err

        low_stock_list = InventoryService.get_low_stock_products(store)
        return success_response(
            message=f"Found {len(low_stock_list)} product(s) with low stock.",
            data=low_stock_list,
        )
