import re
from decimal import Decimal
from django.core.cache import cache
from django.db import connection
from django.db.models import Case, Count, FloatField, IntegerField, Prefetch, Q, Value, When
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    inline_serializer,
)
from pgvector.django import CosineDistance
from rest_framework import serializers, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from accounts.responses import error_response, success_response
from stores.models import StoreStatus
from stores.pagination import StorePagination
from stores.permissions import IsBusinessVerified, IsSeller, get_store_membership
from stores.validators import validate_store_image

from .models import (
    Product,
    ProductCategory,
    ProductImage,
    ProductStatus,
    ProductType,
    SearchHistory,
    ProductVariant,
    SearchSuggestion,
    normalize_category_param)
from .serializers import (
    AutocompleteResponseSerializer,
    BulkProductUpdateSerializer,
    CategoryItemSerializer,
    ProductCardSerializer,
    ProductCreateSerializer,
    ProductListingStatusSerializer,
    ProductSerializer,
    ProductUpdateSerializer,
    ProductVariantImageSerializer,
    ProductVariantSerializer,
    PublicProductDetailSerializer,
    RecommendedComboSerializer,
    SearchHistorySerializer,
    VariantImageUploadSerializer)
from .embedding_service import EmbeddingService
from .recommendation_service import RecommendationService
from .services import ProductService
from .tasks import record_product_view_task, record_search_query_task

from KiseKaeDjango.schema.helpers import api_response, paginated_response, ERROR_SCHEMA

PRODUCT_ERROR_SCHEMA = ERROR_SCHEMA
PRODUCT_ACTION_SUCCESS_SCHEMA = api_response(serializers.JSONField(default=None, allow_null=True), "ProductActionSuccessResponse")
STORE_PRODUCT_DETAIL_RESPONSE_SCHEMA = api_response(ProductSerializer, "StoreProductDetailResponse")
STORE_PRODUCT_LIST_RESPONSE_SCHEMA = api_response(ProductSerializer, "StoreProductListResponse", many=True)
CATEGORY_LIST_RESPONSE_SCHEMA = api_response(CategoryItemSerializer, "CategoryListResponse", many=True, message="Categories fetched successfully.")
VARIANT_IMAGE_LIST_RESPONSE_SCHEMA = api_response(ProductVariantImageSerializer, "VariantImageListResponse", many=True)
PRODUCT_LISTING_STATUS_RESPONSE_SCHEMA = api_response(ProductListingStatusSerializer, "ProductListingStatusResponse")
PRODUCT_LIST_PAGINATED_RESPONSE_SCHEMA = paginated_response(ProductCardSerializer, "ProductListPaginatedData")
PRODUCT_SEARCH_PAGINATED_RESPONSE_SCHEMA = api_response(
    inline_serializer(
        name="ProductSearchPaginatedData",
        fields={
            "query": serializers.CharField(allow_null=True, help_text="Search query string executed."),
            "count": serializers.IntegerField(help_text="Total search results count."),
            "next": serializers.CharField(allow_null=True, help_text="URL for next page of search results."),
            "previous": serializers.CharField(allow_null=True, help_text="URL for previous page of search results."),
            "results": ProductCardSerializer(many=True),
        },
    ),
    "ProductSearchPaginatedResponse",
)
PUBLIC_PRODUCT_DETAIL_RESPONSE_SCHEMA = api_response(PublicProductDetailSerializer, "PublicProductDetailResponse")
SEARCH_HISTORY_LIST_RESPONSE_SCHEMA = api_response(SearchHistorySerializer, "SearchHistoryListResponse", many=True)
RECOMMENDATION_FEED_PAGINATED_RESPONSE_SCHEMA = paginated_response(ProductCardSerializer, "RecommendationFeedPaginatedData")
PRODUCT_SIMILAR_RESPONSE_SCHEMA = api_response(ProductCardSerializer, "ProductSimilarListResponse", many=True)
PRODUCT_RECOMMENDED_COMBOS_RESPONSE_SCHEMA = api_response(RecommendedComboSerializer, "ProductRecommendedCombosListResponse", many=True)
PRODUCT_AUTOCOMPLETE_RESPONSE_SCHEMA = api_response(AutocompleteResponseSerializer, "ProductAutocompleteResponse")

MAX_PRODUCT_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB


class ProductPagination(StorePagination):
    page_size = 20
    max_page_size = 100


class ProductMethodThrottleMixin:
    def get_throttles(self):
        write_methods = {"POST", "PUT", "PATCH", "DELETE"}
        self.throttle_scope = "store_write" if self.request.method in write_methods else "store_read"
        return super().get_throttles()


def normalize_category_param(val):
    """Safely normalizes input category string to ProductCategory enum code."""
    if not val:
        return None
    val_clean = str(val).strip().replace(" ", "_").replace("-", "_").upper()
    for choice in ProductCategory:
        if val_clean == choice.value or val_clean == choice.label.replace(" ", "_").replace("-", "_").upper():
            return choice.value
    return None


def get_optimized_product_queryset():
    """Returns base public product queryset with store and related images/sizes/variants prefetched to prevent N+1 queries."""
    return (
        Product.objects.filter(
            status=ProductStatus.ACTIVE,
            store__status=StoreStatus.ACTIVE,
            store__archived_at__isnull=True)
        .select_related("store")
        .prefetch_related(
            Prefetch("images", queryset=ProductImage.objects.order_by("order", "id")),
            "sizes",
            Prefetch(
                "variants",
                queryset=ProductVariant.objects.filter(is_active=True).prefetch_related("images", "sizes"),
            ),
        )
    )


def apply_product_filters(queryset, request):
    """
    Applies shared query filters for search and public browsing:
    - category / type
    - size (checks availability in stock)
    - color
    - min_price / max_price
    - in_stock
    - letter
    """
    category_param = request.query_params.get("category")
    if category_param:
        cat_code = normalize_category_param(category_param)
        if cat_code:
            queryset = queryset.filter(category=cat_code)

    type_param = request.query_params.get("product_type") or request.query_params.get("type")
    if type_param:
        t_clean = str(type_param).strip().upper()
        if t_clean in [c.value for c in ProductType]:
            queryset = queryset.filter(product_type=t_clean)
        elif not category_param:
            cat_code = normalize_category_param(type_param)
            if cat_code:
                queryset = queryset.filter(category=cat_code)

    size_param = request.query_params.get("size")
    if size_param:
        s_clean = str(size_param).strip().upper()
        queryset = queryset.filter(
            Q(sizes__size__iexact=s_clean, sizes__stock__gt=0)
            | Q(variants__sizes__size__iexact=s_clean, variants__sizes__stock__gt=0)
        )

    color_param = request.query_params.get("color")
    if color_param:
        c_clean = str(color_param).strip()
        queryset = queryset.filter(
            Q(color__icontains=c_clean) | Q(variants__color_name__icontains=c_clean)
        )

    min_price = request.query_params.get("min_price")
    if min_price:
        try:
            queryset = queryset.filter(price__gte=Decimal(str(min_price).strip()))
        except Exception:
            pass

    max_price = request.query_params.get("max_price")
    if max_price:
        try:
            queryset = queryset.filter(price__lte=Decimal(str(max_price).strip()))
        except Exception:
            pass

    in_stock = request.query_params.get("in_stock")
    if in_stock is not None and str(in_stock).lower() in ("true", "1", "yes"):
        queryset = queryset.filter(stock__gt=0)

    letter = request.query_params.get("letter", "").strip()
    if letter:
        queryset = queryset.filter(name__istartswith=letter[0])

    return queryset


# ============================================================================
# CATEGORY BROWSING
# ============================================================================

class CategoryListView(APIView):
    """
    Public API: Browse available product categories with active product counts.
    Categories: One-Piece, Top, Bottom, Footwear, Headwear, Eyewear, Outerwear, Accessories.
    """
    throttle_scope = "store_read"
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Public Products"],
        summary="List all product categories",
        description=(
            "Returns list of all available fashion categories with active product counts. "
            "The 'code' field corresponds to the ProductCategory enum value (ONE_PIECE, TOP, BOTTOM, FOOTWEAR, "
            "HEADWEAR, EYEWEAR, OUTERWEAR, ACCESSORIES) and is used for filtering products via '?category=<code>' or '?type=<code>'."
        ),
        responses={200: CATEGORY_LIST_RESPONSE_SCHEMA})
    def get(self, request):
        active_counts = dict(
            Product.objects.filter(
                status=ProductStatus.ACTIVE,
                store__status=StoreStatus.ACTIVE,
                store__archived_at__isnull=True)
            .values("category")
            .annotate(cnt=Count("id"))
            .values_list("category", "cnt")
        )

        categories_data = [
            {
                "code": choice.value,
                "name": choice.label,
                "products_count": active_counts.get(choice.value, 0),
            }
            for choice in ProductCategory
        ]

        return success_response(
            message="Categories fetched successfully.",
            data=categories_data)


# ============================================================================
# SELLER PRODUCT CATALOG VIEWS
# ============================================================================

class ProductListCreateView(ProductMethodThrottleMixin, APIView):
    throttle_scope = "store_read"
    permission_classes = [IsAuthenticated, IsSeller]
    pagination_class = ProductPagination

    @extend_schema(
        operation_id="seller_store_product_list",
        tags=["Store - Products"],
        summary="List products for a store",
        description=(
            "Returns all products for the specified store. Filterable by category, demographic product type, "
            "status, size, color, price range, stock levels, and refundability, with versatile sorting options "
            "including stock count, price, newest, and ratings."
        ),
        parameters=[
            OpenApiParameter(
                name="category",
                description="Filter by apparel category",
                required=False,
                type=str,
                enum=[c.value for c in ProductCategory]),
            OpenApiParameter(
                name="product_type",
                description="Filter by demographic product type",
                required=False,
                type=str,
                enum=[c.value for c in ProductType]),
            OpenApiParameter(
                name="status",
                description="Filter by product listing status",
                required=False,
                type=str,
                enum=[s.value for s in ProductStatus]),
            OpenApiParameter(
                name="search",
                description="Search products by name, SKU, variant SKU, or description",
                required=False,
                type=str),
            OpenApiParameter(
                name="color",
                description="Filter by color name or variant color descriptor (e.g. Navy, Emerald, Black)",
                required=False,
                type=str),
            OpenApiParameter(
                name="size",
                description="Filter by available size in stock (e.g. XS, S, M, L, XL for clothing; 6-12 for shoes)",
                required=False,
                type=str),
            OpenApiParameter(
                name="min_price",
                description="Minimum price filter (INR)",
                required=False,
                type=float),
            OpenApiParameter(
                name="max_price",
                description="Maximum price filter (INR)",
                required=False,
                type=float),
            OpenApiParameter(
                name="in_stock",
                description="Filter by stock presence: 'true' for stock > 0, 'false' for out of stock",
                required=False,
                type=bool),
            OpenApiParameter(
                name="min_stock",
                description="Minimum inventory stock count",
                required=False,
                type=int),
            OpenApiParameter(
                name="max_stock",
                description="Maximum inventory stock count",
                required=False,
                type=int),
            OpenApiParameter(
                name="low_stock",
                description="Filter products at or below the low stock threshold (total stock < 250 or variant size < 20)",
                required=False,
                type=bool),
            OpenApiParameter(
                name="is_refundable",
                description="Filter by customer return and refund eligibility (true/false)",
                required=False,
                type=bool),
            OpenApiParameter(
                name="ordering",
                description="Sort products by attribute",
                required=False,
                type=str,
                enum=["stock", "-stock", "price", "-price", "newest", "oldest", "name", "-name", "top_rated", "reviews"]),
            OpenApiParameter(name="page", type=int, required=False),
            OpenApiParameter(name="page_size", type=int, required=False),
        ],
        responses={200: STORE_PRODUCT_LIST_RESPONSE_SCHEMA, 404: PRODUCT_ERROR_SCHEMA})
    def get(self, request, pk):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND)

        queryset = (
            Product.objects.for_store(store)
            .select_related("store", "tryon_asset")
            .prefetch_related(
                "variants__sizes",
                Prefetch("variants__images", queryset=ProductImage.objects.order_by("order", "id")),
                "sizes",
                Prefetch("images", queryset=ProductImage.objects.order_by("order", "id")),
            )
        )

        # Status filter
        status_filter = request.query_params.get("status")
        if status_filter:
            st_clean = status_filter.strip().upper()
            if st_clean in [s.value for s in ProductStatus]:
                queryset = queryset.filter(status=st_clean)

        # Category filter
        category_param = request.query_params.get("category")
        if category_param:
            cat_code = normalize_category_param(category_param)
            if cat_code:
                queryset = queryset.filter(category=cat_code)

        # Product Type filter
        product_type = request.query_params.get("product_type") or request.query_params.get("type")
        if product_type:
            pt_clean = product_type.strip().upper()
            if pt_clean in [c.value for c in ProductType]:
                queryset = queryset.filter(product_type=pt_clean)

        # Search query (name, sku, description, variant sku)
        search = request.query_params.get("search") or request.query_params.get("q")
        if search:
            s_clean = search.strip()
            queryset = queryset.filter(
                Q(name__icontains=s_clean)
                | Q(sku__icontains=s_clean)
                | Q(description__icontains=s_clean)
                | Q(variants__sku__icontains=s_clean)
            )

        # Color filter
        color = request.query_params.get("color")
        if color:
            c_clean = color.strip()
            queryset = queryset.filter(
                Q(color__icontains=c_clean) | Q(variants__color_name__icontains=c_clean)
            )

        # Size filter (checks available stock > 0 in size)
        size = request.query_params.get("size")
        if size:
            s_clean = size.strip().upper()
            queryset = queryset.filter(
                Q(sizes__size__iexact=s_clean, sizes__stock__gt=0)
                | Q(variants__sizes__size__iexact=s_clean, variants__sizes__stock__gt=0)
            )

        # Price range filters
        min_price = request.query_params.get("min_price")
        if min_price:
            try:
                queryset = queryset.filter(price__gte=Decimal(str(min_price).strip()))
            except Exception:
                pass

        max_price = request.query_params.get("max_price")
        if max_price:
            try:
                queryset = queryset.filter(price__lte=Decimal(str(max_price).strip()))
            except Exception:
                pass

        # In-stock filter
        in_stock = request.query_params.get("in_stock")
        if in_stock is not None:
            val = str(in_stock).strip().lower()
            if val in ("true", "1", "yes"):
                queryset = queryset.filter(stock__gt=0)
            elif val in ("false", "0", "no"):
                queryset = queryset.filter(stock=0)

        # Specific stock count range
        min_stock = request.query_params.get("min_stock")
        if min_stock is not None:
            try:
                queryset = queryset.filter(stock__gte=int(min_stock))
            except (ValueError, TypeError):
                pass

        max_stock = request.query_params.get("max_stock")
        if max_stock is not None:
            try:
                queryset = queryset.filter(stock__lte=int(max_stock))
            except (ValueError, TypeError):
                pass

        # Low stock filter (total stock < 250 or variant size < 20)
        low_stock = request.query_params.get("low_stock")
        if low_stock is not None and str(low_stock).strip().lower() in ("true", "1", "yes"):
            queryset = queryset.filter(Q(stock__lt=250) | Q(sizes__stock__lt=20)).distinct()

        # Refundable filter
        is_refundable = request.query_params.get("is_refundable")
        if is_refundable is not None:
            val = str(is_refundable).strip().lower() in ("true", "1", "yes")
            queryset = queryset.filter(is_refundable=val)

        # Sorting / Ordering (including stock sorting)
        ordering = request.query_params.get("ordering") or request.query_params.get("sort") or "-created_at"
        ordering = ordering.strip().lower()
        valid_orderings = {
            "stock": "stock",
            "stock_asc": "stock",
            "-stock": "-stock",
            "stock_desc": "-stock",
            "price": "price",
            "price_asc": "price",
            "-price": "-price",
            "price_desc": "-price",
            "created_at": "created_at",
            "oldest": "created_at",
            "-created_at": "-created_at",
            "newest": "-created_at",
            "name": "name",
            "a-z": "name",
            "-name": "-name",
            "z-a": "-name",
            "rating": "-average_rating",
            "-rating": "-average_rating",
            "top_rated": "-average_rating",
            "reviews": "-review_count",
            "-reviews": "-review_count",
        }
        order_field = valid_orderings.get(ordering, "-created_at")
        queryset = queryset.order_by(order_field)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset.distinct(), request)
        serializer = ProductSerializer(page, many=True)

        return success_response(
            message="Products fetched successfully.",
            data={
                "count": paginator.page.paginator.count,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "results": serializer.data,
            })

    @extend_schema(
        operation_id="seller_store_product_create",
        tags=["Store - Products"],
        summary="Create a new product",
        description=(
            "Creates a new product catalog entry under this store. "
            "Supports product_type (MEN, WOMEN, KIDS, ETHNIC, TRADITIONAL, FORMAL, SPORTS), "
            "category (ONE_PIECE, TOP, BOTTOM, FOOTWEAR, HEADWEAR, EYEWEAR, OUTERWEAR, ACCESSORIES), "
            "and nested color variants with hex codes (#RRGGBB) and category-specific sizes "
            "(Clothing: XS-XL, Footwear: 6-12, Eyewear/Accessories: no sizes)."
        ),
        request=ProductCreateSerializer,
        responses={
            201: STORE_PRODUCT_DETAIL_RESPONSE_SCHEMA,
            400: PRODUCT_ERROR_SCHEMA,
            403: PRODUCT_ERROR_SCHEMA,
            404: PRODUCT_ERROR_SCHEMA,
        },
        examples=[
            OpenApiExample(
                "Clothing Product with Color Variants & Sizes",
                value={
                    "name": "Oversized Vintage Heavyweight Tee",
                    "product_type": "MEN",
                    "category": "TOP",
                    "description": "240 GSM combed cotton vintage wash boxy fit t-shirt.",
                    "price": "1499.00",
                    "compare_at_price": "2199.00",
                    "currency": "INR",
                    "status": "ACTIVE",
                    "variants": [
                        {
                            "color_name": "Washed Charcoal",
                            "color_hex": "#2B2B2B",
                            "sku": "TEE-CHAR-01",
                            "is_active": True,
                            "sizes": [
                                {"size": "S", "stock": 15},
                                {"size": "M", "stock": 25},
                                {"size": "L", "stock": 20},
                                {"size": "XL", "stock": 10}
                            ]
                        },
                        {
                            "color_name": "Off White",
                            "color_hex": "#F8F8F6",
                            "sku": "TEE-WHT-01",
                            "is_active": True,
                            "sizes": [
                                {"size": "S", "stock": 10},
                                {"size": "M", "stock": 20},
                                {"size": "L", "stock": 15},
                                {"size": "XL", "stock": 5}
                            ]
                        }
                    ]
                },
                request_only=True,
            ),
            OpenApiExample(
                "Footwear Product with Sizes (6-12)",
                value={
                    "name": "Retro Leather Court Sneakers",
                    "product_type": "MEN",
                    "category": "FOOTWEAR",
                    "description": "Full-grain calfskin leather low-top sneakers with vulcanized rubber sole.",
                    "price": "4999.00",
                    "compare_at_price": "6499.00",
                    "currency": "INR",
                    "status": "ACTIVE",
                    "variants": [
                        {
                            "color_name": "White & Forest Green",
                            "color_hex": "#1E3F20",
                            "sku": "SNK-GRN-01",
                            "is_active": True,
                            "sizes": [
                                {"size": "7", "stock": 5},
                                {"size": "8", "stock": 12},
                                {"size": "9", "stock": 15},
                                {"size": "10", "stock": 8},
                                {"size": "11", "stock": 4}
                            ]
                        }
                    ]
                },
                request_only=True,
            ),
            OpenApiExample(
                "Eyewear / Accessories Product (No Sizes)",
                value={
                    "name": "Aviator Polarized Sunglasses",
                    "product_type": "MEN",
                    "category": "EYEWEAR",
                    "description": "Classic gold frame aviator sunglasses with UV400 polarized green lenses.",
                    "price": "2999.00",
                    "compare_at_price": "3999.00",
                    "currency": "INR",
                    "status": "ACTIVE",
                    "variants": [
                        {
                            "color_name": "Gold / Green G-15",
                            "color_hex": "#D4AF37",
                            "sku": "SUN-GLD-01",
                            "stock": 40,
                            "is_active": True
                        }
                    ]
                },
                request_only=True,
            ),
        ],
    )
    def post(self, request, pk):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND)

        if store.is_archived:
            return error_response(
                message="Cannot add products to an archived store.",
                code="STORE_ARCHIVED",
                status_code=status.HTTP_400_BAD_REQUEST)
        if store.status == StoreStatus.SUSPENDED:
            return error_response(
                message="Cannot add products to a suspended store.",
                code="STORE_SUSPENDED",
                status_code=status.HTTP_403_FORBIDDEN)

        if not store.is_business_verified:
            return error_response(
                message="Store business verification must be completed before adding products.",
                code="BUSINESS_VERIFICATION_REQUIRED",
                status_code=status.HTTP_403_FORBIDDEN)

        serializer = ProductCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        product = ProductService.create_product(
            store=store,
            user=request.user,
            **serializer.validated_data)

        return success_response(
            message="Product created successfully.",
            data=ProductSerializer(product).data,
            status_code=status.HTTP_201_CREATED)


class ProductDetailView(ProductMethodThrottleMixin, APIView):
    throttle_scope = "store_read"
    permission_classes = [IsAuthenticated, IsSeller]

    def _get_product(self, user, store_pk, product_pk):
        store, membership = get_store_membership(user, store_pk)
        if not store:
            return None, None, None
        product = (
            Product.objects.filter(store=store, pk=product_pk)
            .select_related("store", "tryon_asset")
            .prefetch_related(
                "variants__sizes",
                Prefetch("variants__images", queryset=ProductImage.objects.order_by("order", "id")),
                "sizes",
                Prefetch("images", queryset=ProductImage.objects.order_by("order", "id")),
            )
            .first()
        )
        return store, membership, product

    @extend_schema(
        operation_id="seller_store_product_detail",
        tags=["Store - Products"],
        summary="Get product details",
        description="Returns details of a specific product with its images and size stocks.",
        responses={200: STORE_PRODUCT_DETAIL_RESPONSE_SCHEMA, 404: PRODUCT_ERROR_SCHEMA})
    def get(self, request, pk, product_pk):
        store, _, product = self._get_product(request.user, pk, product_pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND)
        if not product:
            return error_response(
                message="Product not found.",
                code="PRODUCT_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND)

        return success_response(
            message="Product details fetched successfully.",
            data=ProductSerializer(product).data)

    @extend_schema(
        operation_id="seller_store_product_replace",
        tags=["Store - Products"],
        summary="Replace product details",
        description="Replaces product metadata, variants, and pricing completely.",
        request=ProductUpdateSerializer,
        responses={200: STORE_PRODUCT_DETAIL_RESPONSE_SCHEMA, 400: PRODUCT_ERROR_SCHEMA, 403: PRODUCT_ERROR_SCHEMA, 404: PRODUCT_ERROR_SCHEMA},
    )
    def put(self, request, pk, product_pk):
        return self.patch(request, pk, product_pk)

    @extend_schema(
        operation_id="seller_store_product_update",
        tags=["Store - Products"],
        summary="Update product details",
        description="Partially updates product attributes, variants, and status.",
        request=ProductUpdateSerializer,
        responses={200: STORE_PRODUCT_DETAIL_RESPONSE_SCHEMA, 400: PRODUCT_ERROR_SCHEMA, 403: PRODUCT_ERROR_SCHEMA, 404: PRODUCT_ERROR_SCHEMA},
    )
    def patch(self, request, pk, product_pk):
        store, _, product = self._get_product(request.user, pk, product_pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND)
        if not product:
            return error_response(
                message="Product not found.",
                code="PRODUCT_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND)

        if store.is_archived:
            return error_response(
                message="Cannot modify products in an archived store.",
                code="STORE_ARCHIVED",
                status_code=status.HTTP_400_BAD_REQUEST)
        if store.status == StoreStatus.SUSPENDED:
            return error_response(
                message="Cannot modify products in a suspended store.",
                code="STORE_SUSPENDED",
                status_code=status.HTTP_403_FORBIDDEN)

        serializer = ProductUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        updated_product = ProductService.update_product(product, **serializer.validated_data)

        return success_response(
            message="Product updated successfully.",
            data=ProductSerializer(updated_product).data)

    @extend_schema(
        tags=["Store - Products"],
        summary="Delete a product",
        description="Permanently deletes a product and cleans up gallery images.",
        responses={200: PRODUCT_ACTION_SUCCESS_SCHEMA, 400: PRODUCT_ERROR_SCHEMA, 403: PRODUCT_ERROR_SCHEMA, 404: PRODUCT_ERROR_SCHEMA})
    def delete(self, request, pk, product_pk):
        store, _, product = self._get_product(request.user, pk, product_pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND)
        if not product:
            return error_response(
                message="Product not found.",
                code="PRODUCT_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND)

        if store.is_archived:
            return error_response(
                message="Cannot delete products from an archived store.",
                code="STORE_ARCHIVED",
                status_code=status.HTTP_400_BAD_REQUEST)
        if store.status == StoreStatus.SUSPENDED:
            return error_response(
                message="Cannot delete products from a suspended store.",
                code="STORE_SUSPENDED",
                status_code=status.HTTP_403_FORBIDDEN)

        ProductService.delete_product(product)
        return success_response(
            message="Product deleted successfully.",
            data=None)


# ============================================================================
# MULTIPLE IMAGES MANAGEMENT (UP TO 10 IMAGES)
# ============================================================================

class ProductVariantImageView(ProductMethodThrottleMixin, APIView):
    throttle_scope = "store_write"
    permission_classes = [IsAuthenticated, IsSeller]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        tags=["Store - Products"],
        summary="List variant images",
        description="Returns list of all images for this specific color variant.",
        responses={200: VARIANT_IMAGE_LIST_RESPONSE_SCHEMA, 404: PRODUCT_ERROR_SCHEMA},
    )
    def get(self, request, pk, product_pk, variant_pk):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response("Store not found.", "STORE_NOT_FOUND", status_code=status.HTTP_404_NOT_FOUND)
        product = Product.objects.filter(store=store, pk=product_pk).first()
        if not product:
            return error_response("Product not found.", "PRODUCT_NOT_FOUND", status_code=status.HTTP_404_NOT_FOUND)
        variant = product.variants.filter(pk=variant_pk).first()
        if not variant:
            return error_response("Product variant not found.", "VARIANT_NOT_FOUND", status_code=status.HTTP_404_NOT_FOUND)

        images = variant.images.order_by("order", "id")
        return success_response(
            message="Variant images fetched successfully.",
            data=ProductVariantImageSerializer(images, many=True).data,
        )

    @extend_schema(
        tags=["Store - Products"],
        summary="Upload variant image (max 10)",
        description="Uploads an image for a specific color variant (JPEG, PNG, WEBP; max 5MB). Image at order 0 is the variant thumbnail.",
        request=VariantImageUploadSerializer,
        responses={201: STORE_PRODUCT_DETAIL_RESPONSE_SCHEMA, 400: PRODUCT_ERROR_SCHEMA, 403: PRODUCT_ERROR_SCHEMA, 404: PRODUCT_ERROR_SCHEMA},
    )
    def post(self, request, pk, product_pk, variant_pk):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response("Store not found.", "STORE_NOT_FOUND", status_code=status.HTTP_404_NOT_FOUND)
        if store.is_archived:
            return error_response("Cannot modify products in an archived store.", "STORE_ARCHIVED", status_code=status.HTTP_400_BAD_REQUEST)
        if store.status == StoreStatus.SUSPENDED:
            return error_response("Cannot modify products in a suspended store.", "STORE_SUSPENDED", status_code=status.HTTP_403_FORBIDDEN)

        product = Product.objects.filter(store=store, pk=product_pk).first()
        if not product:
            return error_response("Product not found.", "PRODUCT_NOT_FOUND", status_code=status.HTTP_404_NOT_FOUND)
        variant = product.variants.filter(pk=variant_pk).first()
        if not variant:
            return error_response("Product variant not found.", "VARIANT_NOT_FOUND", status_code=status.HTTP_404_NOT_FOUND)

        serializer = VariantImageUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        image_file = serializer.validated_data["image"]
        order = serializer.validated_data.get("order")

        validate_store_image(image_file, MAX_PRODUCT_IMAGE_SIZE, "variant image")
        ProductService.add_variant_image(variant, image_file, order=order)

        product.refresh_from_db()
        return success_response(
            message="Variant image uploaded successfully.",
            data=ProductSerializer(product).data,
            status_code=status.HTTP_201_CREATED,
        )


class ProductVariantImageDetailView(ProductMethodThrottleMixin, APIView):
    throttle_scope = "store_write"
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        tags=["Store - Products"],
        summary="Delete a variant image",
        description="Deletes a specific photo from a color variant gallery.",
        responses={200: STORE_PRODUCT_DETAIL_RESPONSE_SCHEMA, 400: PRODUCT_ERROR_SCHEMA, 403: PRODUCT_ERROR_SCHEMA, 404: PRODUCT_ERROR_SCHEMA},
    )
    def delete(self, request, pk, product_pk, variant_pk, image_id):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response("Store not found.", "STORE_NOT_FOUND", status_code=status.HTTP_404_NOT_FOUND)
        if store.is_archived:
            return error_response("Cannot modify products in an archived store.", "STORE_ARCHIVED", status_code=status.HTTP_400_BAD_REQUEST)
        if store.status == StoreStatus.SUSPENDED:
            return error_response("Cannot modify products in a suspended store.", "STORE_SUSPENDED", status_code=status.HTTP_403_FORBIDDEN)

        product = Product.objects.filter(store=store, pk=product_pk).first()
        if not product:
            return error_response("Product not found.", "PRODUCT_NOT_FOUND", status_code=status.HTTP_404_NOT_FOUND)
        variant = product.variants.filter(pk=variant_pk).first()
        if not variant:
            return error_response("Product variant not found.", "VARIANT_NOT_FOUND", status_code=status.HTTP_404_NOT_FOUND)

        ProductService.remove_variant_image(variant, image_id)
        product.refresh_from_db()
        return success_response(
            message="Variant image removed successfully.",
            data=ProductSerializer(product).data,
        )


class ProductListingStatusView(APIView):
    throttle_scope = "store_read"
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        tags=["Store - Products"],
        summary="Get catalog listing status overview",
        description="Returns breakdown of product counts by status (ACTIVE, DRAFT, INACTIVE) and stock health.",
        responses={200: PRODUCT_LISTING_STATUS_RESPONSE_SCHEMA, 404: PRODUCT_ERROR_SCHEMA})
    def get(self, request, pk):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response("Store not found.", "STORE_NOT_FOUND", status_code=status.HTTP_404_NOT_FOUND)

        # Single aggregation query to prevent 6 separate round-trips
        counts = Product.objects.for_store(store).aggregate(
            total=Count("id"),
            active=Count("id", filter=Q(status=ProductStatus.ACTIVE)),
            draft=Count("id", filter=Q(status=ProductStatus.DRAFT)),
            inactive=Count("id", filter=Q(status=ProductStatus.INACTIVE)),
            in_stock=Count("id", filter=Q(stock__gt=0)),
            out_of_stock=Count("id", filter=Q(stock=0)))

        return success_response(
            message="Listing status fetched successfully.",
            data=counts)


class ProductBulkUpdateView(APIView):
    throttle_scope = "store_write"
    permission_classes = [IsAuthenticated, IsSeller, IsBusinessVerified]

    @extend_schema(
        tags=["Store - Products"],
        summary="Bulk update products status and price",
        description="Updates status, category, color, price, and compare-at price in bulk across multiple store products.",
        request=BulkProductUpdateSerializer,
        responses={200: inline_serializer(
            name="BulkProductUpdateResponse",
            fields={
                "success": serializers.BooleanField(default=True),
                "message": serializers.CharField(),
                "data": inline_serializer(
                    name="BulkProductUpdateData",
                    fields={"updated_count": serializers.IntegerField()}),
            }), 400: PRODUCT_ERROR_SCHEMA})
    def patch(self, request, pk):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response("Store not found.", "STORE_NOT_FOUND", status_code=status.HTTP_404_NOT_FOUND)

        if not store.is_business_verified:
            return error_response("Store business verification must be completed before modifying products.", "BUSINESS_VERIFICATION_REQUIRED", status_code=status.HTTP_403_FORBIDDEN)
        if store.is_archived:
            return error_response("Cannot modify products in an archived store.", "STORE_ARCHIVED", status_code=status.HTTP_400_BAD_REQUEST)
        if store.status == StoreStatus.SUSPENDED:
            return error_response("Cannot modify products in a suspended store.", "STORE_SUSPENDED", status_code=status.HTTP_403_FORBIDDEN)

        serializer = BulkProductUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        items = serializer.validated_data["items"]

        updated_ids = []
        errors = []
        with transaction.atomic():
            for item in items:
                product = Product.objects.filter(store=store, pk=item["product_id"]).first()
                if not product:
                    errors.append({"product_id": item["product_id"], "error": "Product not found"})
                    continue
                update_data = {}
                for field in ["status", "price", "compare_at_price", "category", "product_type", "color", "is_refundable"]:
                    if field in item:
                        update_data[field] = item[field]
                if update_data:
                    ProductService.update_product(product, **update_data)
                    updated_ids.append(product.id)

        return success_response(
            message=f"Bulk update completed: {len(updated_ids)} updated, {len(errors)} errors.",
            data={"updated_count": len(updated_ids), "updated_ids": updated_ids, "errors": errors})


# ============================================================================
# PUBLIC PRODUCT BROWSING & ADVANCED SEARCH
# ============================================================================

class PublicProductListView(APIView):
    """
    Public API: Browse all available products across all active stores.
    Supports filters for category/type, size, color, price range, stock, and lettering.
    Optimized with prefetching to avoid N+1 database queries.
    """
    throttle_scope = "store_read"
    permission_classes = [AllowAny]
    pagination_class = ProductPagination

    @extend_schema(
        operation_id="public_product_list",
        tags=["Public Products"],
        summary="Browse all available products",
        description="Returns all active products with filters for category/type, size, color, price range, and in-stock status. Optimized to return full product cards without N+1 query overhead.",
        parameters=[
            OpenApiParameter(name="category", description="Filter by category (One-Piece, Top, Bottom, Footwear, Headwear, Eyewear, Outerwear, Accessories)", required=False, type=str),
            OpenApiParameter(name="type", description="Alias for category filter", required=False, type=str),
            OpenApiParameter(name="size", description="Filter by size (e.g. S, M, L, XL for clothing; 6-10 for shoes)", required=False, type=str),
            OpenApiParameter(name="color", description="Filter by color (e.g. Black, White, Blue)", required=False, type=str),
            OpenApiParameter(name="min_price", description="Minimum price filter", required=False, type=float),
            OpenApiParameter(name="max_price", description="Maximum price filter", required=False, type=float),
            OpenApiParameter(name="letter", description="Filter products starting with this letter (case-insensitive)", required=False, type=str),
            OpenApiParameter(name="in_stock", description="If true, only return products with stock > 0", required=False, type=bool),
            OpenApiParameter(name="search", description="Basic text query on product name and description", required=False, type=str),
            OpenApiParameter(name="ordering", description="Sort order: 'price', '-price', 'newest', 'oldest', 'name', '-name', 'top_rated', 'lowest_rated'", required=False, type=str),
            OpenApiParameter(name="page", type=int, required=False),
            OpenApiParameter(name="page_size", type=int, required=False),
        ],
        responses={200: PRODUCT_LIST_PAGINATED_RESPONSE_SCHEMA})
    def get(self, request):
        queryset = get_optimized_product_queryset()

        # Search query if present (phrase + multi-token matching)
        search_query = request.query_params.get("search") or request.query_params.get("q")
        if search_query:
            q_str = search_query.strip()
            tokens = [t for t in re.split(r"[\s,\-\+]+", q_str) if len(t) >= 2]
            search_filter = (
                Q(name__icontains=q_str)
                | Q(description__icontains=q_str)
                | Q(color__icontains=q_str)
                | Q(variants__color_name__icontains=q_str)
                | Q(store__name__icontains=q_str)
            )
            if tokens and len(tokens) > 1:
                token_filter = Q()
                for token in tokens:
                    token_filter &= (
                        Q(name__icontains=token)
                        | Q(description__icontains=token)
                        | Q(color__icontains=token)
                        | Q(variants__color_name__icontains=token)
                        | Q(category__icontains=token)
                        | Q(product_type__icontains=token)
                    )
                search_filter |= token_filter
            queryset = queryset.filter(search_filter)
            if request.user and request.user.is_authenticated:
                record_search_query_task.delay(request.user.id, q_str)

        # Apply category, size, color, price range, in-stock, letter filters
        queryset = apply_product_filters(queryset, request)

        # Ordering
        ordering = request.query_params.get("ordering", "name").strip().lower()
        valid_orderings = {
            "name": "name",
            "-name": "-name",
            "a-z": "name",
            "z-a": "-name",
            "price": "price",
            "price_asc": "price",
            "-price": "-price",
            "price_desc": "-price",
            "created_at": "created_at",
            "oldest": "created_at",
            "-created_at": "-created_at",
            "newest": "-created_at",
            "stock": "stock",
            "stock_asc": "stock",
            "-stock": "-stock",
            "stock_desc": "-stock",
            "rating": "-average_rating",
            "-rating": "-average_rating",
            "top_rated": "-average_rating",
            "highest_rated": "-average_rating",
            "lowest_rated": "average_rating",
        }
        order_field = valid_orderings.get(ordering, "name")
        queryset = queryset.order_by(order_field)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset.distinct(), request)
        serializer = ProductCardSerializer(page, many=True)

        return success_response(
            message="Products fetched successfully.",
            data={
                "count": paginator.page.paginator.count,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "results": serializer.data,
            })


class ProductSearchView(APIView):
    """
    Public API: Hybrid keyword + vector semantic search with advanced filters:
    category/type, size, color, price range, and in-stock.
    Returns product cards with thumbnail and available sizes without N+1 query overhead.
    """
    throttle_scope = "store_read"
    permission_classes = [AllowAny]
    pagination_class = ProductPagination

    @extend_schema(
        tags=["Public Products"],
        summary="Search products (hybrid search with filters)",
        description=(
            "Searches active products using hybrid keyword and vector semantic search. "
            "Supports multi-attribute filters for category/type, size, color, price range, and stock. "
            "Returns product cards including first image thumbnail and available sizes."
        ),
        parameters=[
            OpenApiParameter(name="q", description="Search query string (e.g. 'black summer hoodie')", required=False, type=str),
            OpenApiParameter(name="category", description="Filter by category (One-Piece, Top, Bottom, Footwear, Headwear, Eyewear, Outerwear, Accessories)", required=False, type=str),
            OpenApiParameter(name="type", description="Alias for category filter", required=False, type=str),
            OpenApiParameter(name="size", description="Filter by size (e.g. S, M, L, XL for clothing; 6-10 for shoes)", required=False, type=str),
            OpenApiParameter(name="color", description="Filter by color (e.g. Black, White, Red)", required=False, type=str),
            OpenApiParameter(name="min_price", description="Minimum price filter", required=False, type=float),
            OpenApiParameter(name="max_price", description="Maximum price filter", required=False, type=float),
            OpenApiParameter(name="in_stock", description="If true, only return products with stock > 0", required=False, type=bool),
            OpenApiParameter(name="ordering", description="Sort order: 'relevance' (default), 'price', '-price', 'newest', 'oldest', 'name', '-name'", required=False, type=str),
            OpenApiParameter(name="page", type=int, required=False),
            OpenApiParameter(name="page_size", type=int, required=False),
        ],
        responses={200: PRODUCT_SEARCH_PAGINATED_RESPONSE_SCHEMA})
    def get(self, request):
        query = (
            request.query_params.get("q")
            or request.query_params.get("query")
            or request.query_params.get("search", "")
        ).strip()

        threshold = 0.68

        queryset = get_optimized_product_queryset()

        has_vector_search = False
        supports_pgvector = (connection.vendor == "postgresql")

        if query:
            if request.user and request.user.is_authenticated:
                record_search_query_task.delay(request.user.id, query)

            tokens = [w for w in re.split(r"[\s,\-\+]+", query) if len(w) >= 2]

            # 1. Contiguous exact phrase matching across name, description, color, store
            phrase_filter = (
                Q(name__icontains=query)
                | Q(description__icontains=query)
                | Q(color__icontains=query)
                | Q(variants__color_name__icontains=query)
                | Q(store__name__icontains=query)
            )

            # 2. Tokenized multi-word matching (e.g. "black oversize hoodie" matches "Oversized Streetwear Black Hoodie")
            token_filter = Q()
            if tokens and len(tokens) > 1:
                all_tokens_q = Q()
                for token in tokens:
                    all_tokens_q &= (
                        Q(name__icontains=token)
                        | Q(description__icontains=token)
                        | Q(color__icontains=token)
                        | Q(variants__color_name__icontains=token)
                        | Q(category__icontains=token)
                        | Q(product_type__icontains=token)
                        | Q(store__name__icontains=token)
                    )
                token_filter = all_tokens_q

            lexical_filter = phrase_filter | token_filter if token_filter else phrase_filter

            try:
                embedding_service = EmbeddingService.get_instance()
                query_vector = embedding_service.embed_query_cached(query)
            except Exception:
                query_vector = None

            if supports_pgvector and query_vector:
                try:
                    queryset = queryset.annotate(
                        distance=CosineDistance("embedding_record__embedding", query_vector)
                    ).filter(
                        lexical_filter
                        | (Q(embedding_record__isnull=False) & Q(distance__lte=threshold))
                    )
                    has_vector_search = True
                except Exception:
                    queryset = queryset.filter(lexical_filter)
            elif not supports_pgvector and query_vector:
                # SQLite development/testing fallback: evaluate lexical filter, plus semantic match
                candidate_ids = set(queryset.filter(lexical_filter).values_list("id", flat=True))
                # Add products with embedding distance <= threshold in Python
                for prod in queryset.filter(embedding_record__isnull=False):
                    if hasattr(prod, "embedding_record") and prod.embedding_record and prod.embedding_record.embedding:
                        p_emb = list(prod.embedding_record.embedding)
                        dot = sum(a * b for a, b in zip(query_vector, p_emb))
                        dist = 1.0 - dot
                        if dist <= threshold:
                            candidate_ids.add(prod.id)
                queryset = queryset.filter(id__in=candidate_ids)
                has_vector_search = True
            else:
                queryset = queryset.filter(lexical_filter)

        # Apply category, size, color, price range, in_stock, and letter filters
        queryset = apply_product_filters(queryset, request)

        default_ordering = "relevance" if (query and has_vector_search) else "name"
        ordering = request.query_params.get("ordering", default_ordering).strip().lower()

        valid_orderings = {
            "name": ("name",),
            "-name": ("-name",),
            "a-z": ("name",),
            "z-a": ("-name",),
            "price": ("price",),
            "price_asc": ("price",),
            "-price": ("-price",),
            "price_desc": ("-price",),
            "created_at": ("created_at",),
            "oldest": ("created_at",),
            "-created_at": ("-created_at",),
            "newest": ("-created_at",),
        }

        if ordering == "relevance" and has_vector_search and supports_pgvector:
            queryset = queryset.order_by(
                Case(
                    When(name__iexact=query, then=Value(0.0)),
                    When(name__istartswith=query, then=Value(0.1)),
                    When(name__icontains=query, then=Value(0.2)),
                    default=Value(1.0),
                    output_field=FloatField(),
                ),
                "distance",
                "-average_rating",
                "-created_at",
            )
        elif ordering == "relevance":
            queryset = queryset.order_by(
                Case(
                    When(name__iexact=query, then=Value(0.0)),
                    When(name__istartswith=query, then=Value(0.1)),
                    When(name__icontains=query, then=Value(0.2)),
                    When(description__icontains=query, then=Value(0.5)),
                    default=Value(1.0),
                    output_field=FloatField(),
                ),
                "-average_rating",
                "-created_at",
            )
        elif ordering in valid_orderings:
            queryset = queryset.order_by(*valid_orderings[ordering])
        else:
            queryset = queryset.order_by("name")

        paginator = self.pagination_class()
        try:
            page = paginator.paginate_queryset(queryset.distinct(), request)
        except Exception:
            fallback_qs = get_optimized_product_queryset()
            if query:
                fallback_qs = fallback_qs.filter(
                    Q(name__icontains=query) | Q(description__icontains=query) | Q(color__icontains=query)
                )
            fallback_qs = apply_product_filters(fallback_qs, request)
            fallback_qs = fallback_qs.order_by("name")
            page = paginator.paginate_queryset(fallback_qs.distinct(), request)

        serializer = ProductCardSerializer(page, many=True)

        return success_response(
            message=f"Search results for '{query}'" if query else "Products fetched successfully.",
            data={
                "query": query,
                "count": paginator.page.paginator.count,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "results": serializer.data,
            })


class ProductAutocompleteView(APIView):
    """
    Public API: Sub-10ms Autocomplete Search Suggestions.
    Returns:
      - queries: Ranked query suggestion strings for the typed prefix.
      - instant_products: Top matching instant product cards (up to 3).
    Cached in Redis with short TTL for maximum throughput.
    """
    throttle_scope = "store_read"
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="public_product_autocomplete",
        tags=["Public Products"],
        summary="Search autocomplete suggestions",
        description=(
            "Provides ultra-fast autocomplete suggestions as the user types. "
            "Returns a list of suggested query phrases and top instant product cards."
        ),
        parameters=[
            OpenApiParameter(
                name="q",
                description="Search query prefix (minimum 2 characters).",
                required=True,
                type=str,
            ),
            OpenApiParameter(
                name="limit",
                description="Maximum number of query suggestions (default: 8, max: 15).",
                required=False,
                type=int,
            ),
        ],
        responses={200: PRODUCT_AUTOCOMPLETE_RESPONSE_SCHEMA},
    )
    def get(self, request):
        query = (request.query_params.get("q") or request.query_params.get("query") or "").strip()
        if len(query) < 2:
            return success_response(
                message="Autocomplete suggestions fetched successfully.",
                data={"query": query, "queries": [], "instant_products": []},
            )

        clean_q = query.lower()
        try:
            limit = min(int(request.query_params.get("limit", 8)), 15)
        except (ValueError, TypeError):
            limit = 8

        cache_key = f"autocomplete:{clean_q}:{limit}"
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            return success_response(
                message="Autocomplete suggestions fetched successfully.",
                data=cached_result,
            )

        # 1. Query Suggestions from SearchSuggestion table
        prefix_qs = list(
            SearchSuggestion.objects.filter(query__istartswith=clean_q)
            .order_by("-score", "query")
            .values_list("query", flat=True)[:limit]
        )

        query_suggestions = list(prefix_qs)
        if len(query_suggestions) < limit:
            remaining = limit - len(query_suggestions)
            contains_qs = (
                SearchSuggestion.objects.filter(query__icontains=clean_q)
                .exclude(query__in=query_suggestions)
                .order_by("-score", "query")
                .values_list("query", flat=True)[:remaining]
            )
            query_suggestions.extend(contains_qs)

        # Supplement from active product titles if dictionary is sparse
        if len(query_suggestions) < limit:
            remaining = limit - len(query_suggestions)
            prod_names = (
                Product.objects.filter(status=ProductStatus.ACTIVE, name__icontains=clean_q)
                .exclude(name__in=query_suggestions)
                .values_list("name", flat=True)
                .distinct()[:remaining]
            )
            query_suggestions.extend(prod_names)

        # 2. Instant Top Products (up to 3 products)
        instant_products_qs = (
            get_optimized_product_queryset()
            .filter(Q(name__icontains=clean_q) | Q(description__icontains=clean_q))
            .order_by(
                Case(
                    When(name__iexact=clean_q, then=Value(0)),
                    When(name__istartswith=clean_q, then=Value(1)),
                    When(name__icontains=clean_q, then=Value(2)),
                    default=Value(3),
                    output_field=IntegerField(),
                ),
                "-average_rating",
                "-created_at",
            )[:3]
        )
        instant_serialized = ProductCardSerializer(instant_products_qs, many=True).data

        payload = {
            "query": query,
            "queries": query_suggestions,
            "instant_products": instant_serialized,
        }

        try:
            cache.set(cache_key, payload, timeout=60 * 15)
        except Exception:
            pass

        return success_response(
            message="Autocomplete suggestions fetched successfully.",
            data=payload,
        )


class PublicProductDetailView(APIView):
    """
    Public API: View full details of a single product including description,
    images gallery, sizes with stock counts, category, and color.
    """
    throttle_scope = "store_read"
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="public_product_detail",
        tags=["Public Products"],
        summary="Get public product detail",
        description="Returns full details for an active product, including all gallery images (up to 10), category, color, and size breakdown.",
        responses={200: PUBLIC_PRODUCT_DETAIL_RESPONSE_SCHEMA, 404: PRODUCT_ERROR_SCHEMA})
    def get(self, request, product_pk):
        product = (
            Product.objects.filter(
                pk=product_pk,
                status=ProductStatus.ACTIVE,
                store__status=StoreStatus.ACTIVE,
                store__archived_at__isnull=True)
            .select_related("store")
            .prefetch_related(
                Prefetch("images", queryset=ProductImage.objects.order_by("order", "id")),
                "sizes")
            .first()
        )
        if not product:
            return error_response(
                message="Product not found or unavailable.",
                code="PRODUCT_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND)

        if request.user and request.user.is_authenticated:
            record_product_view_task.delay(request.user.id, product.id)

        return success_response(
            message="Product details fetched successfully.",
            data=PublicProductDetailSerializer(product).data)



class PublicProductSimilarView(APIView):
    """
    Public API: Find visually and semantically similar active products
    based on the current product's 384-dimensional pgvector embedding.
    Powers 'Similar Styles' / 'You Might Also Like' carousels.
    """
    throttle_scope = "store_read"
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="public_product_similar",
        tags=["Public Products"],
        summary="Get similar products",
        description="Returns active, in-stock products visually and semantically similar to the specified product using pgvector cosine distance.",
        parameters=[
            OpenApiParameter(name="limit", description="Max number of similar products to return (default: 10, max: 20)", required=False, type=int),
            OpenApiParameter(name="same_category", description="If true, only returns products from the same apparel category", required=False, type=bool),
        ],
        responses={200: PRODUCT_SIMILAR_RESPONSE_SCHEMA, 404: PRODUCT_ERROR_SCHEMA})
    def get(self, request, product_pk):
        product = Product.objects.filter(
            pk=product_pk,
            status=ProductStatus.ACTIVE,
            store__status=StoreStatus.ACTIVE,
            store__archived_at__isnull=True,
        ).first()
        if not product:
            return error_response(
                message="Product not found or unavailable.",
                code="PRODUCT_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND)

        try:
            limit = min(int(request.query_params.get("limit", 10)), 20)
        except (ValueError, TypeError):
            limit = 10
        same_cat = request.query_params.get("same_category", "false").lower() in ("true", "1", "yes")

        similar_qs = RecommendationService.get_similar_products(product, limit=limit, same_category=same_cat)
        serializer = ProductCardSerializer(similar_qs, many=True)
        return success_response(
            message="Similar products fetched successfully.",
            data=serializer.data)

class PublicProductRecommendedCombosView(APIView):
    """
    Public API: Complete the Look - Generates 3 curated outfit combinations for the product:
      - At least 1 Regular look (base outfit without outerwear).
      - At least 1 Layered look (with outerwear layer).
      - 1 Dynamic look (highest-scoring look between candidate regular and candidate layered alternatives).
    Strictly enforces try-on category compatibility and ONE_PIECE mutual exclusivity rules.
    """
    throttle_scope = "store_read"
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="public_product_recommended_combos",
        tags=["Public Products"],
        summary="Get recommended combos (Complete the Look)",
        description=(
            "Generates 3 styled outfit combinations for the specified product: at least one regular look, "
            "at least one layered look (with outerwear), and a third dynamic look chosen based on style affinity score. "
            "Strictly enforces Virtual Try-On category compatibility and One-Piece mutual exclusivity."
        ),
        responses={200: PRODUCT_RECOMMENDED_COMBOS_RESPONSE_SCHEMA, 404: PRODUCT_ERROR_SCHEMA},
    )
    def get(self, request, product_pk):
        product = Product.objects.filter(
            pk=product_pk,
            status=ProductStatus.ACTIVE,
            store__status=StoreStatus.ACTIVE,
            store__archived_at__isnull=True,
        ).first()
        if not product:
            return error_response(
                message="Product not found or unavailable.",
                code="PRODUCT_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        combos_data = RecommendationService.get_recommended_combos(product)
        serializer = RecommendedComboSerializer(combos_data, many=True)
        return success_response(
            message="Recommended combos fetched successfully.",
            data=serializer.data,
        )



# ============================================================================
# SEARCH HISTORY & RECOMMENDATIONS VIEWS
# ============================================================================

class SearchHistoryListView(APIView):
    """
    User Search History: List user's recent searches or clear entire search history.
    Searches are embedded and used as direct signals in the ML recommendation model.
    """
    throttle_scope = "store_read"
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Search History"],
        summary="List user's search history",
        description="Returns the user's recent search queries. These queries feed directly into the recommendation ML taste profile.",
        responses={200: SEARCH_HISTORY_LIST_RESPONSE_SCHEMA})
    def get(self, request):
        history = SearchHistory.objects.filter(user=request.user).order_by("-last_searched_at")[:30]
        return success_response(
            message="Search history fetched successfully.",
            data=SearchHistorySerializer(history, many=True).data)

    @extend_schema(
        operation_id="clear_all_search_history",
        tags=["Search History"],
        summary="Clear user's search history",
        description="Clears all stored search queries for the authenticated user and excludes them from future ML taste profile refreshes.",
        responses={200: inline_serializer(name="SearchHistoryClearResponse", fields={"success": serializers.BooleanField(), "message": serializers.CharField()})})
    def delete(self, request):
        SearchHistory.objects.filter(user=request.user).delete()
        return success_response(
            message="Search history cleared successfully.")


class SearchHistoryDetailView(APIView):
    """
    User Search History: Delete a specific search entry.
    """
    throttle_scope = "store_write"
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="delete_search_history_entry",
        tags=["Search History"],
        summary="Delete a search history entry",
        description="Removes a specific search query from the user's history.",
        responses={200: inline_serializer(name="SearchHistoryDeleteResponse", fields={"success": serializers.BooleanField(), "message": serializers.CharField()}), 404: PRODUCT_ERROR_SCHEMA})
    def delete(self, request, history_id):
        deleted, _ = SearchHistory.objects.filter(user=request.user, pk=history_id).delete()
        if not deleted:
            return error_response(
                message="Search history entry not found.",
                code="NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND)
        return success_response(
            message="Search history entry removed successfully.")


class RecommendedFeedView(APIView):
    """
    Public & Authenticated API: Curated and personalized product recommendation feed.
    - Authenticated users with >= 3 signals receive personalized recommendations
      driven by their 384-dimensional taste profile vector via pgvector cosine distance search.
    - Cold-start users and guests receive trending products (highest recent 30-day sales).
    """
    throttle_scope = "store_read"
    permission_classes = [AllowAny]
    pagination_class = ProductPagination

    @extend_schema(
        tags=["Recommendations"],
        summary="Get curated / recommended product feed",
        description=(
            "Returns a curated feed of recommended products for the user. "
            "Utilizes pgvector cosine similarity matching against a multi-signal taste profile "
            "(purchases, cart, wishlist, search history, views). "
            "Cold-start users and guests receive cached trending products."
        ),
        parameters=[
            OpenApiParameter(name="category", description="Optional category filter for recommendations", required=False, type=str),
            OpenApiParameter(name="exclude_cart", description="Whether to exclude items currently in user's cart (default: true)", required=False, type=bool),
            OpenApiParameter(name="page", type=int, required=False),
            OpenApiParameter(name="page_size", type=int, required=False),
        ],
        responses={200: PRODUCT_LIST_PAGINATED_RESPONSE_SCHEMA})
    def get(self, request):
        exclude_cart_param = request.query_params.get("exclude_cart", "true").strip().lower()
        exclude_cart = exclude_cart_param in ("true", "1", "yes")
        category = request.query_params.get("category")

        user = request.user if request.user.is_authenticated else None
        queryset = RecommendationService.get_recommendations(
            user=user,
            limit=100,
            exclude_cart=exclude_cart,
            category=category)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request)
        serializer = ProductCardSerializer(page, many=True)

        return success_response(
            message="Recommended feed fetched successfully.",
            data={
                "count": paginator.page.paginator.count,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "results": serializer.data,
            })
