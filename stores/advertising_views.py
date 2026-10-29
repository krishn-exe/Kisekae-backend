from drf_spectacular.utils import extend_schema, OpenApiParameter
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from django.core.exceptions import ValidationError as DjangoValidationError

from accounts.responses import error_response, success_response
from KiseKaeDjango.schema.helpers import api_response, ERROR_SCHEMA
from products.models import Product

from .models import PromotedProduct, StorePaymentSettings
from .payment_service import StorePaymentService
from .advertising_service import AdvertisingService
from .permissions import IsSeller, get_store_membership
from .serializers import (
    PromoteProductsRequestSerializer,
    PromotedProductItemSerializer,
    PromotedProductUpdateSerializer,
    StoreAdvertisingAnalyticsSerializer,
    StoreAdvertisingBudgetSerializer,
)

STORE_ADVERTISING_BUDGET_RESPONSE_SCHEMA = api_response(
    StoreAdvertisingBudgetSerializer,
    "StoreAdvertisingBudgetResponse",
)
STORE_PROMOTED_PRODUCTS_RESPONSE_SCHEMA = api_response(
    PromotedProductItemSerializer,
    "StorePromotedProductsResponse",
)
STORE_ADVERTISING_ANALYTICS_RESPONSE_SCHEMA = api_response(
    StoreAdvertisingAnalyticsSerializer,
    "StoreAdvertisingAnalyticsResponse",
)


class StoreAdvertisingBudgetView(APIView):
    """
    Store Advertising Budget Management.
    Enables store users to view and update their monthly advertising budget.
    """
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        operation_id="store_get_advertising_budget",
        tags=["Store - Advertising"],
        summary="Get Store Monthly Advertising Budget",
        description="Retrieves the store's configured monthly advertising budget.",
        responses={
            200: STORE_ADVERTISING_BUDGET_RESPONSE_SCHEMA,
            404: ERROR_SCHEMA,
        },
    )
    def get(self, request, pk):
        store, membership = get_store_membership(request.user, pk)
        if not store or not membership:
            return error_response(
                message="Store not found or you are not a member of this store.",
                status_code=status.HTTP_404_NOT_FOUND,
                code="STORE_NOT_FOUND",
            )

        settings_obj, _ = StorePaymentSettings.objects.get_or_create(store=store)
        serializer = StoreAdvertisingBudgetSerializer(settings_obj)
        return success_response(
            data=serializer.data,
            message="Advertising budget retrieved successfully.",
        )

    @extend_schema(
        operation_id="store_update_advertising_budget",
        tags=["Store - Advertising"],
        summary="Set Store Monthly Advertising Budget",
        description="Allows store users to set or update their monthly advertising budget.",
        request=StoreAdvertisingBudgetSerializer,
        responses={
            200: STORE_ADVERTISING_BUDGET_RESPONSE_SCHEMA,
            400: ERROR_SCHEMA,
            404: ERROR_SCHEMA,
        },
    )
    def patch(self, request, pk):
        return self._update_budget(request, pk)

    @extend_schema(
        operation_id="store_replace_advertising_budget",
        tags=["Store - Advertising"],
        summary="Replace Store Monthly Advertising Budget",
        description="Allows store users to replace their monthly advertising budget.",
        request=StoreAdvertisingBudgetSerializer,
        responses={
            200: STORE_ADVERTISING_BUDGET_RESPONSE_SCHEMA,
            400: ERROR_SCHEMA,
            404: ERROR_SCHEMA,
        },
    )
    def put(self, request, pk):
        return self._update_budget(request, pk)

    def _update_budget(self, request, pk):
        store, membership = get_store_membership(request.user, pk)
        if not store or not membership:
            return error_response(
                message="Store not found or you are not a member of this store.",
                status_code=status.HTTP_404_NOT_FOUND,
                code="STORE_NOT_FOUND",
            )

        serializer = StoreAdvertisingBudgetSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return error_response(
                message="Validation failed for advertising budget.",
                details=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST,
                code="VALIDATION_ERROR",
            )

        try:
            updated_settings = StorePaymentService.update_payment_settings(
                store=store,
                user=request.user,
                data=serializer.validated_data,
            )
        except DjangoValidationError as exc:
            msg = exc.message if hasattr(exc, "message") else str(exc)
            details = exc.message_dict if hasattr(exc, "message_dict") else None
            return error_response(
                message=msg,
                details=details,
                status_code=status.HTTP_400_BAD_REQUEST,
                code="VALIDATION_ERROR",
            )

        return success_response(
            data=StoreAdvertisingBudgetSerializer(updated_settings).data,
            message="Advertising budget updated successfully.",
        )


class StorePromotedProductsView(APIView):
    """
    Manage products selected by the seller for sponsored promotion.
    """
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        operation_id="store_list_promoted_products",
        tags=["Store - Advertising"],
        summary="List Promoted Products",
        description="Returns all products enrolled in sponsored advertising by this store.",
        responses={
            200: STORE_PROMOTED_PRODUCTS_RESPONSE_SCHEMA,
            404: ERROR_SCHEMA,
        },
    )
    def get(self, request, pk):
        store, membership = get_store_membership(request.user, pk)
        if not store or not membership:
            return error_response(
                message="Store not found or you are not a member of this store.",
                status_code=status.HTTP_404_NOT_FOUND,
                code="STORE_NOT_FOUND",
            )

        promoted_qs = (
            PromotedProduct.objects.filter(store=store)
            .select_related("product")
            .order_by("-updated_at")
        )
        serializer = PromotedProductItemSerializer(promoted_qs, many=True)
        return success_response(
            data=serializer.data,
            message="Promoted products retrieved successfully.",
        )

    @extend_schema(
        operation_id="store_promote_products",
        tags=["Store - Advertising"],
        summary="Promote Products",
        description="Enrolls one or more products of this store into sponsored promotion.",
        request=PromoteProductsRequestSerializer,
        responses={
            201: STORE_PROMOTED_PRODUCTS_RESPONSE_SCHEMA,
            400: ERROR_SCHEMA,
            404: ERROR_SCHEMA,
        },
    )
    def post(self, request, pk):
        store, membership = get_store_membership(request.user, pk)
        if not store or not membership:
            return error_response(
                message="Store not found or you are not a member of this store.",
                status_code=status.HTTP_404_NOT_FOUND,
                code="STORE_NOT_FOUND",
            )

        serializer = PromoteProductsRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                message="Validation failed for promoting products.",
                details=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST,
                code="VALIDATION_ERROR",
            )

        product_ids = serializer.validated_data["product_ids"]
        valid_products = list(Product.objects.filter(store=store, id__in=product_ids))
        valid_product_ids = {p.id for p in valid_products}

        invalid_ids = [pid for pid in product_ids if pid not in valid_product_ids]
        if invalid_ids:
            return error_response(
                message=f"Product IDs {invalid_ids} do not belong to this store or do not exist.",
                status_code=status.HTTP_400_BAD_REQUEST,
                code="INVALID_PRODUCTS",
            )

        created_or_updated = []
        for product in valid_products:
            obj, _ = PromotedProduct.objects.update_or_create(
                store=store,
                product=product,
                defaults={"is_active": True},
            )
            created_or_updated.append(obj)

        return success_response(
            data=PromotedProductItemSerializer(created_or_updated, many=True).data,
            message=f"Successfully promoted {len(created_or_updated)} product(s).",
            status_code=status.HTTP_201_CREATED,
        )


class StorePromotedProductDetailView(APIView):
    """
    Manage status or remove a specific promoted product.
    """
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        operation_id="store_update_promoted_product",
        tags=["Store - Advertising"],
        summary="Update Promoted Product Status",
        description="Enable or disable active promotion for an individual product.",
        request=PromotedProductUpdateSerializer,
        responses={
            200: api_response(PromotedProductItemSerializer, "StorePromotedProductItemResponse"),
            400: ERROR_SCHEMA,
            404: ERROR_SCHEMA,
        },
    )
    def patch(self, request, pk, product_id):
        store, membership = get_store_membership(request.user, pk)
        if not store or not membership:
            return error_response(
                message="Store not found or you are not a member of this store.",
                status_code=status.HTTP_404_NOT_FOUND,
                code="STORE_NOT_FOUND",
            )

        promo = PromotedProduct.objects.filter(store=store, product_id=product_id).select_related("product").first()
        if not promo:
            return error_response(
                message="Promoted product not found.",
                status_code=status.HTTP_404_NOT_FOUND,
                code="NOT_FOUND",
            )

        serializer = PromotedProductUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                message="Validation failed.",
                details=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST,
                code="VALIDATION_ERROR",
            )

        promo.is_active = serializer.validated_data["is_active"]
        promo.save(update_fields=["is_active", "updated_at"])

        return success_response(
            data=PromotedProductItemSerializer(promo).data,
            message="Promoted product status updated successfully.",
        )

    @extend_schema(
        operation_id="store_delete_promoted_product",
        tags=["Store - Advertising"],
        summary="Remove Product from Promotion",
        description="Removes a product from sponsored advertising enrollment.",
        responses={
            200: api_response(None, "StorePromotedProductDeleteResponse"),
            404: ERROR_SCHEMA,
        },
    )
    def delete(self, request, pk, product_id):
        store, membership = get_store_membership(request.user, pk)
        if not store or not membership:
            return error_response(
                message="Store not found or you are not a member of this store.",
                status_code=status.HTTP_404_NOT_FOUND,
                code="STORE_NOT_FOUND",
            )

        deleted_count, _ = PromotedProduct.objects.filter(store=store, product_id=product_id).delete()
        if deleted_count == 0:
            return error_response(
                message="Promoted product not found.",
                status_code=status.HTTP_404_NOT_FOUND,
                code="NOT_FOUND",
            )

        return success_response(
            message="Product removed from advertising promotions successfully.",
        )


class StoreAdvertisingAnalyticsView(APIView):
    """
    Store Advertising Analytics & Performance Dashboard.
    Provides CTR, spend, conversion rates, and per-product metrics.
    """
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        operation_id="store_get_advertising_analytics",
        tags=["Store - Advertising"],
        summary="Get Advertising Analytics",
        description="Retrieves comprehensive advertising performance analytics, spend, clicks, and orders.",
        parameters=[
            OpenApiParameter(name="year", description="Calendar year (e.g. 2026)", required=False, type=int),
            OpenApiParameter(name="month", description="Calendar month (1-12)", required=False, type=int),
        ],
        responses={
            200: STORE_ADVERTISING_ANALYTICS_RESPONSE_SCHEMA,
            404: ERROR_SCHEMA,
        },
    )
    def get(self, request, pk):
        store, membership = get_store_membership(request.user, pk)
        if not store or not membership:
            return error_response(
                message="Store not found or you are not a member of this store.",
                status_code=status.HTTP_404_NOT_FOUND,
                code="STORE_NOT_FOUND",
            )

        year = request.query_params.get("year")
        month = request.query_params.get("month")

        analytics = AdvertisingService.get_store_advertising_analytics(store, year=year, month=month)
        return success_response(
            data=analytics,
            message="Advertising analytics retrieved successfully.",
        )
