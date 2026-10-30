from decimal import Decimal
from django.db import models
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.responses import error_response, success_response
from KiseKaeDjango.schema.helpers import ERROR_SCHEMA, api_response
from products.models import Product, ProductStatus, SeasonalSale, SeasonalSaleProduct

from .models import StoreStatus
from .permissions import IsSeller, get_store_membership
from .serializers import (
    StoreEnrollSeasonalSaleProductsSerializer,
    StoreSeasonalSaleDetailSerializer,
    StoreSeasonalSaleListSerializer,
    StoreSeasonalSaleProductItemSerializer,
    StoreUpdateSeasonalSaleProductSerializer,
)

STORE_SEASONAL_SALE_LIST_RESPONSE_SCHEMA = api_response(
    StoreSeasonalSaleListSerializer,
    "StoreSeasonalSaleListResponse",
    many=True,
)
STORE_SEASONAL_SALE_DETAIL_RESPONSE_SCHEMA = api_response(
    StoreSeasonalSaleDetailSerializer,
    "StoreSeasonalSaleDetailResponse",
)
STORE_SEASONAL_SALE_PRODUCTS_RESPONSE_SCHEMA = api_response(
    StoreSeasonalSaleProductItemSerializer,
    "StoreSeasonalSaleProductsResponse",
    many=True,
)
STORE_SEASONAL_SALE_PRODUCT_ITEM_RESPONSE_SCHEMA = api_response(
    StoreSeasonalSaleProductItemSerializer,
    "StoreSeasonalSaleProductItemResponse",
)

MAX_PRODUCTS_PER_STORE_SALE = 5


class StoreSeasonalSaleListView(APIView):
    """
    Store Seasonal Sales Discovery.
    Enables store users to browse available platform seasonal sales,
    review eligibility, and check their current participation status.
    Note: Seasonal sales are created exclusively by platform administrators.
    """
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        operation_id="store_list_seasonal_sales",
        tags=["Store - Seasonal Sales"],
        summary="List Seasonal Sales for Store",
        description="Returns all platform seasonal sales with store participation status and product counts. Filter by status: active (default), upcoming, all, or expired.",
        parameters=[
            OpenApiParameter(
                name="status",
                description="Filter sales: 'active' (default), 'upcoming', 'all', 'expired'",
                required=False,
                type=str,
            ),
        ],
        responses={
            200: STORE_SEASONAL_SALE_LIST_RESPONSE_SCHEMA,
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

        now = timezone.now()
        status_filter = request.query_params.get("status", "active").strip().lower()

        sales_qs = SeasonalSale.objects.filter(is_active=True)
        if status_filter == "upcoming":
            sales_qs = sales_qs.filter(start_time__gt=now)
        elif status_filter == "expired":
            sales_qs = SeasonalSale.objects.filter(end_time__lt=now)
        elif status_filter == "all":
            sales_qs = SeasonalSale.objects.all()
        else:  # default 'active'
            sales_qs = sales_qs.filter(start_time__lte=now, end_time__gte=now)

        sales_qs = sales_qs.order_by("-start_time", "-created_at")

        # Map store participation
        store_participations = {
            item["sale_id"]: item["count"]
            for item in SeasonalSaleProduct.objects.filter(store=store)
            .values("sale_id")
            .annotate(count=models.Count("id"))
        }

        results = []
        for sale in sales_qs:
            count = store_participations.get(sale.id, 0)
            sale_status = "EXPIRED" if sale.is_expired else ("ACTIVE" if sale.is_live else "UPCOMING")
            results.append({
                "id": sale.id,
                "title": sale.title,
                "slug": sale.slug,
                "description": sale.description,
                "banner_image": sale.banner_url,
                "discount_percentage": sale.discount_percentage,
                "start_time": sale.start_time,
                "end_time": sale.end_time,
                "is_live": sale.is_live,
                "status": sale_status,
                "time_remaining_seconds": sale.time_remaining_seconds,
                "is_participating": count > 0,
                "enrolled_products_count": count,
            })

        serializer = StoreSeasonalSaleListSerializer(results, many=True)
        return success_response(
            data=serializer.data,
            message="Seasonal sales retrieved successfully.",
        )


class StoreSeasonalSaleDetailView(APIView):
    """
    Detailed view of a seasonal sale from a store's perspective,
    including all products enrolled by this store.
    """
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        operation_id="store_get_seasonal_sale_detail",
        tags=["Store - Seasonal Sales"],
        summary="Get Seasonal Sale Details for Store",
        description="Retrieves a specific seasonal sale and lists all products enrolled in it by this store.",
        responses={
            200: STORE_SEASONAL_SALE_DETAIL_RESPONSE_SCHEMA,
            404: ERROR_SCHEMA,
        },
    )
    def get(self, request, pk, sale_id):
        store, membership = get_store_membership(request.user, pk)
        if not store or not membership:
            return error_response(
                message="Store not found or you are not a member of this store.",
                status_code=status.HTTP_404_NOT_FOUND,
                code="STORE_NOT_FOUND",
            )

        sale = SeasonalSale.objects.filter(pk=sale_id).first()
        if not sale:
            return error_response(
                message="Seasonal sale not found.",
                status_code=status.HTTP_404_NOT_FOUND,
                code="SALE_NOT_FOUND",
            )

        enrolled_qs = (
            SeasonalSaleProduct.objects.filter(sale=sale, store=store)
            .select_related("product", "sale")
            .order_by("-created_at")
        )

        sale_status = "EXPIRED" if sale.is_expired else ("ACTIVE" if sale.is_live else "UPCOMING")
        payload = {
            "id": sale.id,
            "title": sale.title,
            "slug": sale.slug,
            "description": sale.description,
            "banner_image": sale.banner_url,
            "discount_percentage": sale.discount_percentage,
            "start_time": sale.start_time,
            "end_time": sale.end_time,
            "is_live": sale.is_live,
            "status": sale_status,
            "time_remaining_seconds": sale.time_remaining_seconds,
            "is_participating": enrolled_qs.exists(),
            "enrolled_products_count": enrolled_qs.count(),
            "enrolled_products": enrolled_qs,
        }

        serializer = StoreSeasonalSaleDetailSerializer(payload)
        return success_response(
            data=serializer.data,
            message="Seasonal sale details retrieved successfully.",
        )


class StoreEnrollSeasonalSaleProductsView(APIView):
    """
    Enrolls store products into a seasonal sale.
    Maximum of 5 products per store per sale is enforced.
    """
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        operation_id="store_enroll_seasonal_sale_products",
        tags=["Store - Seasonal Sales"],
        summary="Enroll Products in Seasonal Sale",
        description="Enrolls up to 5 products of this store into the designated seasonal sale. Calculates discounted prices automatically.",
        request=StoreEnrollSeasonalSaleProductsSerializer,
        responses={
            201: STORE_SEASONAL_SALE_PRODUCTS_RESPONSE_SCHEMA,
            400: ERROR_SCHEMA,
            404: ERROR_SCHEMA,
        },
    )
    def post(self, request, pk, sale_id):
        store, membership = get_store_membership(request.user, pk)
        if not store or not membership:
            return error_response(
                message="Store not found or you are not a member of this store.",
                status_code=status.HTTP_404_NOT_FOUND,
                code="STORE_NOT_FOUND",
            )

        if store.status != StoreStatus.ACTIVE or store.archived_at is not None:
            return error_response(
                message="Store is currently inactive and cannot enroll products in sales.",
                status_code=status.HTTP_400_BAD_REQUEST,
                code="STORE_INACTIVE",
            )

        sale = SeasonalSale.objects.filter(pk=sale_id, is_active=True).first()
        if not sale:
            return error_response(
                message="Seasonal sale not found or inactive.",
                status_code=status.HTTP_404_NOT_FOUND,
                code="SALE_NOT_FOUND",
            )

        if sale.is_expired:
            return error_response(
                message="Cannot enroll products in an expired seasonal sale.",
                status_code=status.HTTP_400_BAD_REQUEST,
                code="SALE_EXPIRED",
            )

        serializer = StoreEnrollSeasonalSaleProductsSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                message="Validation failed.",
                details=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST,
                code="VALIDATION_ERROR",
            )

        product_ids = serializer.validated_data["product_ids"]

        # Validate 5-product limit
        current_enrolled_count = SeasonalSaleProduct.objects.filter(sale=sale, store=store).count()
        existing_enrolled_pids = set(
            SeasonalSaleProduct.objects.filter(sale=sale, store=store, product_id__in=product_ids).values_list("product_id", flat=True)
        )
        new_pids = [pid for pid in product_ids if pid not in existing_enrolled_pids]

        if current_enrolled_count + len(new_pids) > MAX_PRODUCTS_PER_STORE_SALE:
            return error_response(
                message=(
                    f"Store limit exceeded. Each store can enroll a maximum of {MAX_PRODUCTS_PER_STORE_SALE} products "
                    f"in a seasonal sale. Currently enrolled: {current_enrolled_count}, attempting to add: {len(new_pids)}."
                ),
                status_code=status.HTTP_400_BAD_REQUEST,
                code="SALE_PRODUCT_LIMIT_EXCEEDED",
            )

        # Validate products belong to store and are active
        valid_products = list(
            Product.objects.filter(
                store=store,
                id__in=product_ids,
                status=ProductStatus.ACTIVE,
            )
        )
        valid_pids = {p.id for p in valid_products}
        invalid_pids = [pid for pid in product_ids if pid not in valid_pids]

        if invalid_pids:
            return error_response(
                message=f"Product IDs {invalid_pids} do not belong to this store or are not currently active.",
                status_code=status.HTTP_400_BAD_REQUEST,
                code="INVALID_PRODUCTS",
            )

        created_or_updated = []
        for product in valid_products:
            obj, _ = SeasonalSaleProduct.objects.update_or_create(
                sale=sale,
                store=store,
                product=product,
                defaults={
                    "original_price": product.price,
                    "is_active": True,
                },
            )
            created_or_updated.append(obj)

        return success_response(
            data=StoreSeasonalSaleProductItemSerializer(created_or_updated, many=True).data,
            message=f"Successfully enrolled {len(created_or_updated)} product(s) into '{sale.title}'.",
            status_code=status.HTTP_201_CREATED,
        )


class StoreSeasonalSaleProductItemView(APIView):
    """
    Manage status or remove an enrolled product from a seasonal sale.
    """
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        operation_id="store_update_seasonal_sale_product",
        tags=["Store - Seasonal Sales"],
        summary="Update Enrolled Product Status",
        description="Enable or pause a product's participation in this seasonal sale.",
        request=StoreUpdateSeasonalSaleProductSerializer,
        responses={
            200: STORE_SEASONAL_SALE_PRODUCT_ITEM_RESPONSE_SCHEMA,
            400: ERROR_SCHEMA,
            404: ERROR_SCHEMA,
        },
    )
    def patch(self, request, pk, sale_id, product_id):
        store, membership = get_store_membership(request.user, pk)
        if not store or not membership:
            return error_response(
                message="Store not found or you are not a member of this store.",
                status_code=status.HTTP_404_NOT_FOUND,
                code="STORE_NOT_FOUND",
            )

        entry = SeasonalSaleProduct.objects.filter(
            sale_id=sale_id,
            store=store,
            product_id=product_id,
        ).select_related("product", "sale").first()

        if not entry:
            return error_response(
                message="Enrolled product not found in this sale.",
                status_code=status.HTTP_404_NOT_FOUND,
                code="NOT_FOUND",
            )

        serializer = StoreUpdateSeasonalSaleProductSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                message="Validation failed.",
                details=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST,
                code="VALIDATION_ERROR",
            )

        entry.is_active = serializer.validated_data["is_active"]
        entry.save(update_fields=["is_active", "updated_at"])

        return success_response(
            data=StoreSeasonalSaleProductItemSerializer(entry).data,
            message="Enrolled product status updated successfully.",
        )

    @extend_schema(
        operation_id="store_delete_seasonal_sale_product",
        tags=["Store - Seasonal Sales"],
        summary="Remove Product from Seasonal Sale",
        description="Removes an enrolled product from participating in this seasonal sale.",
        responses={
            200: api_response(None, "StoreSeasonalSaleProductDeleteResponse"),
            404: ERROR_SCHEMA,
        },
    )
    def delete(self, request, pk, sale_id, product_id):
        store, membership = get_store_membership(request.user, pk)
        if not store or not membership:
            return error_response(
                message="Store not found or you are not a member of this store.",
                status_code=status.HTTP_404_NOT_FOUND,
                code="STORE_NOT_FOUND",
            )

        deleted_count, _ = SeasonalSaleProduct.objects.filter(
            sale_id=sale_id,
            store=store,
            product_id=product_id,
        ).delete()

        if deleted_count == 0:
            return error_response(
                message="Enrolled product not found in this sale.",
                status_code=status.HTTP_404_NOT_FOUND,
                code="NOT_FOUND",
            )

        return success_response(
            message="Product removed from seasonal sale successfully.",
        )
