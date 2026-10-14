from django.core.exceptions import ValidationError
from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from accounts.responses import error_response, success_response
from stores.models import StoreStatus
from stores.pagination import StorePagination
from stores.permissions import IsBusinessVerified, IsSeller, get_store_membership
from stores.validators import validate_store_image, MAX_LOGO_SIZE

from .models import Product, ProductStatus
from .serializers import (
    BulkProductUpdateSerializer,
    ProductCreateSerializer,
    ProductListingStatusSerializer,
    ProductSerializer,
    ProductUpdateSerializer,
    PublicProductSerializer,
)
from .services import ProductService

PRODUCT_ERROR_SCHEMA = inline_serializer(
    name="ProductErrorEnvelope",
    fields={
        "success": serializers.BooleanField(default=False),
        "message": serializers.CharField(),
        "error": inline_serializer(
            name="ProductErrorDetail",
            fields={
                "code": serializers.CharField(),
                "details": serializers.JSONField(allow_null=True),
            },
        ),
    },
)

MAX_PRODUCT_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB


class ProductPagination(StorePagination):
    page_size = 20
    max_page_size = 100


class ProductMethodThrottleMixin:
    def get_throttles(self):
        write_methods = {"POST", "PUT", "PATCH", "DELETE"}
        self.throttle_scope = "store_write" if self.request.method in write_methods else "store_read"
        return super().get_throttles()


class ProductListCreateView(ProductMethodThrottleMixin, APIView):
    throttle_scope = "store_read"
    permission_classes = [IsAuthenticated, IsSeller]
    pagination_class = ProductPagination

    @extend_schema(
        tags=["Products"],
        summary="List products for a store",
        description="Returns all products for the specified store. Filterable by status.",
        parameters=[
            OpenApiParameter(
                name="status",
                description="Filter by product status (DRAFT, ACTIVE, INACTIVE)",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="search",
                description="Search products by name or SKU",
                required=False,
                type=str,
            ),
        ],
        responses={200: ProductSerializer(many=True), 404: PRODUCT_ERROR_SCHEMA},
    )
    def get(self, request, pk):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        queryset = Product.objects.for_store(store).select_related("store")

        status_filter = request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter.upper())

        search = request.query_params.get("search")
        if search:
            queryset = queryset.filter(Q(name__icontains=search) | Q(sku__icontains=search))

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
            },
        )

    @extend_schema(
        tags=["Products"],
        summary="Create a new product",
        description="Creates a new product in the specified store. Store must be active.",
        request=ProductCreateSerializer,
        responses={
            201: ProductSerializer,
            400: PRODUCT_ERROR_SCHEMA,
            403: PRODUCT_ERROR_SCHEMA,
            404: PRODUCT_ERROR_SCHEMA,
        },
    )
    def post(self, request, pk):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if store.is_archived:
            return error_response(
                message="Cannot add products to an archived store.",
                code="STORE_ARCHIVED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if store.status == StoreStatus.SUSPENDED:
            return error_response(
                message="Cannot add products to a suspended store.",
                code="STORE_SUSPENDED",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        if not store.is_business_verified:
            return error_response(
                message="Store business verification must be completed before adding products.",
                code="BUSINESS_VERIFICATION_REQUIRED",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        serializer = ProductCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            product = ProductService.create_product(
                store=store,
                user=request.user,
                **serializer.validated_data,
            )
        except ValidationError as e:
            msg = e.messages[0] if hasattr(e, "messages") and e.messages else str(e)
            return error_response(
                message=msg,
                code="PRODUCT_CREATE_FAILED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        return success_response(
            message="Product created successfully.",
            data=ProductSerializer(product).data,
            status_code=status.HTTP_201_CREATED,
        )


class ProductDetailView(ProductMethodThrottleMixin, APIView):
    throttle_scope = "store_read"
    permission_classes = [IsAuthenticated, IsSeller]

    def _get_product(self, user, store_pk, product_pk):
        store, membership = get_store_membership(user, store_pk)
        if not store:
            return None, None, None
        product = Product.objects.filter(store=store, pk=product_pk).select_related("store").first()
        return store, membership, product

    @extend_schema(
        tags=["Products"],
        summary="Get product details",
        description="Returns details of a specific product. User must be a member of the store.",
        responses={200: ProductSerializer, 404: PRODUCT_ERROR_SCHEMA},
    )
    def get(self, request, pk, product_pk):
        store, _, product = self._get_product(request.user, pk, product_pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        if not product:
            return error_response(
                message="Product not found.",
                code="PRODUCT_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        return success_response(
            message="Product details fetched successfully.",
            data=ProductSerializer(product).data,
        )

    @extend_schema(
        tags=["Products"],
        summary="Update product details",
        description="Updates details of a product. User must be a member of the store.",
        request=ProductUpdateSerializer,
        responses={
            200: ProductSerializer,
            400: PRODUCT_ERROR_SCHEMA,
            404: PRODUCT_ERROR_SCHEMA,
        },
    )
    def patch(self, request, pk, product_pk):
        store, _, product = self._get_product(request.user, pk, product_pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        if not product:
            return error_response(
                message="Product not found.",
                code="PRODUCT_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if store.is_archived:
            return error_response(
                message="Cannot modify products in an archived store.",
                code="STORE_ARCHIVED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if store.status == StoreStatus.SUSPENDED:
            return error_response(
                message="Cannot modify products in a suspended store.",
                code="STORE_SUSPENDED",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        serializer = ProductUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        updated_product = ProductService.update_product(product, **serializer.validated_data)
        return success_response(
            message="Product updated successfully.",
            data=ProductSerializer(updated_product).data,
        )

    @extend_schema(
        tags=["Products"],
        summary="Delete a product",
        description="Permanently deletes a product. User must be a member of the store.",
        responses={200: PRODUCT_ERROR_SCHEMA, 400: PRODUCT_ERROR_SCHEMA, 403: PRODUCT_ERROR_SCHEMA, 404: PRODUCT_ERROR_SCHEMA},
    )
    def delete(self, request, pk, product_pk):
        store, _, product = self._get_product(request.user, pk, product_pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        if not product:
            return error_response(
                message="Product not found.",
                code="PRODUCT_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if store.is_archived:
            return error_response(
                message="Cannot delete products from an archived store.",
                code="STORE_ARCHIVED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if store.status == StoreStatus.SUSPENDED:
            return error_response(
                message="Cannot delete products from a suspended store.",
                code="STORE_SUSPENDED",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        ProductService.delete_product(product)
        return success_response(
            message="Product deleted successfully.",
            data=None,
        )


class ProductImageView(APIView):
    throttle_scope = "store_write"
    permission_classes = [IsAuthenticated, IsSeller]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        tags=["Products"],
        summary="Upload product image",
        description="Uploads or replaces the product image (JPEG, PNG, WEBP; max 5MB).",
        request=inline_serializer(
            name="ProductImageUpload",
            fields={"file": serializers.ImageField()},
        ),
        responses={200: ProductSerializer, 400: PRODUCT_ERROR_SCHEMA, 403: PRODUCT_ERROR_SCHEMA, 404: PRODUCT_ERROR_SCHEMA},
    )
    def put(self, request, pk, product_pk):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if store.is_archived:
            return error_response(
                message="Cannot modify products in an archived store.",
                code="STORE_ARCHIVED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if store.status == StoreStatus.SUSPENDED:
            return error_response(
                message="Cannot modify products in a suspended store.",
                code="STORE_SUSPENDED",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        product = Product.objects.filter(store=store, pk=product_pk).first()
        if not product:
            return error_response(
                message="Product not found.",
                code="PRODUCT_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        image_file = request.FILES.get("file")
        if not image_file:
            return error_response(
                message="Image file is required.",
                code="VALIDATION_ERROR",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        try:
            validate_store_image(image_file, MAX_PRODUCT_IMAGE_SIZE, "product image")
        except ValidationError as e:
            msg = e.messages[0] if hasattr(e, "messages") and e.messages else str(e)
            return error_response(
                message=msg,
                code="INVALID_IMAGE",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        updated = ProductService.set_product_image(product, image_file)
        return success_response(
            message="Product image uploaded successfully.",
            data=ProductSerializer(updated).data,
        )

    @extend_schema(
        tags=["Products"],
        summary="Remove product image",
        description="Removes the product image.",
        responses={200: PRODUCT_ERROR_SCHEMA, 400: PRODUCT_ERROR_SCHEMA, 403: PRODUCT_ERROR_SCHEMA, 404: PRODUCT_ERROR_SCHEMA},
    )
    def delete(self, request, pk, product_pk):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if store.is_archived:
            return error_response(
                message="Cannot modify products in an archived store.",
                code="STORE_ARCHIVED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if store.status == StoreStatus.SUSPENDED:
            return error_response(
                message="Cannot modify products in a suspended store.",
                code="STORE_SUSPENDED",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        product = Product.objects.filter(store=store, pk=product_pk).first()
        if not product:
            return error_response(
                message="Product not found.",
                code="PRODUCT_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        ProductService.remove_product_image(product)
        return success_response(
            message="Product image removed successfully.",
            data=None,
        )


class ProductListingStatusView(APIView):
    throttle_scope = "store_read"
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        tags=["Products"],
        summary="Get catalog listing status overview",
        description="Returns breakdown of product counts by status (ACTIVE, DRAFT, INACTIVE) and stock health.",
        responses={200: ProductListingStatusSerializer, 404: PRODUCT_ERROR_SCHEMA},
    )
    def get(self, request, pk):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        products = Product.objects.for_store(store)
        total = products.count()
        active = products.filter(status=ProductStatus.ACTIVE).count()
        draft = products.filter(status=ProductStatus.DRAFT).count()
        inactive = products.filter(status=ProductStatus.INACTIVE).count()
        in_stock = products.filter(stock__gt=0).count()
        out_of_stock = products.filter(stock=0).count()

        return success_response(
            message="Listing status fetched successfully.",
            data={
                "total": total,
                "active": active,
                "draft": draft,
                "inactive": inactive,
                "in_stock": in_stock,
                "out_of_stock": out_of_stock,
            },
        )


class ProductBulkUpdateView(APIView):
    throttle_scope = "store_write"
    permission_classes = [IsAuthenticated, IsSeller, IsBusinessVerified]

    @extend_schema(
        tags=["Products"],
        summary="Bulk update products status and price",
        request=BulkProductUpdateSerializer,
        responses={200: inline_serializer(name="BulkProductUpdateResponse", fields={"updated_count": serializers.IntegerField()}), 400: PRODUCT_ERROR_SCHEMA},
    )
    def patch(self, request, pk):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if not store.is_business_verified:
            return error_response(
                message="Store business verification must be completed before modifying products.",
                code="BUSINESS_VERIFICATION_REQUIRED",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        if store.is_archived:
            return error_response(
                message="Cannot modify products in an archived store.",
                code="STORE_ARCHIVED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if store.status == StoreStatus.SUSPENDED:
            return error_response(
                message="Cannot modify products in a suspended store.",
                code="STORE_SUSPENDED",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        serializer = BulkProductUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        items = serializer.validated_data["items"]

        updated_ids = []
        errors = []
        for item in items:
            product = Product.objects.filter(store=store, pk=item["product_id"]).first()
            if not product:
                errors.append({"product_id": item["product_id"], "error": "Product not found"})
                continue
            update_data = {}
            if "status" in item:
                update_data["status"] = item["status"]
            if "price" in item:
                update_data["price"] = item["price"]
            if "compare_at_price" in item:
                update_data["compare_at_price"] = item["compare_at_price"]
            if update_data:
                ProductService.update_product(product, **update_data)
                updated_ids.append(product.id)

        return success_response(
            message=f"Updated {len(updated_ids)} product(s).",
            data={
                "updated_ids": updated_ids,
                "errors": errors,
            },
        )


class PublicProductListView(APIView):
    """
    Public API: Fetch all available products across all active stores.
    Excludes products from suspended, archived, or inactive stores.
    Supports pagination, lettering (alphabetical ordering / initial letter filtering),
    and in-stock / price filters.
    """

    throttle_scope = "store_read"
    permission_classes = [AllowAny]
    pagination_class = ProductPagination

    @extend_schema(
        tags=["Public Products"],
        summary="Browse all available products",
        description=(
            "Returns all available products across active stores. "
            "Automatically excludes products from suspended or archived stores, "
            "as well as non-active products. Supports pagination and lettering/alphabetical sorting."
        ),
        parameters=[
            OpenApiParameter(
                name="letter",
                description="Filter products starting with this letter (case-insensitive)",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="ordering",
                description="Sort order: name (A-Z), -name (Z-A), price, -price, created_at, -created_at",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="in_stock",
                description="If true, only return products with stock > 0",
                required=False,
                type=bool,
            ),
            OpenApiParameter(
                name="search",
                description="Basic text search on product name and description",
                required=False,
                type=str,
            ),
            OpenApiParameter(name="page", type=int, required=False),
            OpenApiParameter(name="page_size", type=int, required=False),
        ],
        responses={200: PublicProductSerializer(many=True)},
    )
    def get(self, request):
        queryset = Product.objects.filter(
            status=ProductStatus.ACTIVE,
            store__status=StoreStatus.ACTIVE,
            store__archived_at__isnull=True,
        ).select_related("store")

        # Lettering filter (e.g. ?letter=A)
        letter = request.query_params.get("letter", "").strip()
        if letter:
            queryset = queryset.filter(name__istartswith=letter[0])

        # Optional search
        search_query = request.query_params.get("search") or request.query_params.get("q")
        if search_query:
            q_str = search_query.strip()
            queryset = queryset.filter(
                Q(name__icontains=q_str) | Q(description__icontains=q_str)
            )

        # In-stock filter
        in_stock = request.query_params.get("in_stock")
        if in_stock is not None and str(in_stock).lower() in ("true", "1", "yes"):
            queryset = queryset.filter(stock__gt=0)

        # Ordering / Lettering (defaults to alphabetical name ascending)
        ordering = request.query_params.get("ordering", "name").strip().lower()
        valid_orderings = {
            "name": "name",
            "-name": "-name",
            "a-z": "name",
            "z-a": "-name",
            "price": "price",
            "-price": "-price",
            "created_at": "created_at",
            "-created_at": "-created_at",
            "newest": "-created_at",
            "oldest": "created_at",
        }
        order_field = valid_orderings.get(ordering, "name")
        queryset = queryset.order_by(order_field)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset.distinct(), request)
        serializer = PublicProductSerializer(page, many=True)

        return success_response(
            message="Products fetched successfully.",
            data={
                "count": paginator.page.paginator.count,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "results": serializer.data,
            },
        )


class ProductSearchView(APIView):
    """
    Public API: Search products by title (name) and description using a database scan.
    Excludes products from suspended, archived, or inactive stores.
    Supports pagination and lettering/alphabetical sorting.
    """

    throttle_scope = "store_read"
    permission_classes = [AllowAny]
    pagination_class = ProductPagination

    @extend_schema(
        tags=["Public Products"],
        summary="Search products (basic DB scan)",
        description=(
            "Performs a case-insensitive database scan across product title (`name`) "
            "and `description`. Excludes products from suspended or archived stores. "
            "Supports pagination and ordering."
        ),
        parameters=[
            OpenApiParameter(
                name="q",
                description="Search query string (scanned against name and description)",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="letter",
                description="Optional starting letter filter",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="ordering",
                description="Sort order (name, -name, price, -price, created_at, -created_at)",
                required=False,
                type=str,
            ),
            OpenApiParameter(name="page", type=int, required=False),
            OpenApiParameter(name="page_size", type=int, required=False),
        ],
        responses={200: PublicProductSerializer(many=True)},
    )
    def get(self, request):
        query = (
            request.query_params.get("q")
            or request.query_params.get("query")
            or request.query_params.get("search", "")
        ).strip()

        queryset = Product.objects.filter(
            status=ProductStatus.ACTIVE,
            store__status=StoreStatus.ACTIVE,
            store__archived_at__isnull=True,
        ).select_related("store")

        if query:
            queryset = queryset.filter(
                Q(name__icontains=query) | Q(description__icontains=query)
            )

        # Letter filter
        letter = request.query_params.get("letter", "").strip()
        if letter:
            queryset = queryset.filter(name__istartswith=letter[0])

        # Ordering
        ordering = request.query_params.get("ordering", "name").strip().lower()
        valid_orderings = {
            "name": "name",
            "-name": "-name",
            "a-z": "name",
            "z-a": "-name",
            "price": "price",
            "-price": "-price",
            "created_at": "created_at",
            "-created_at": "-created_at",
        }
        order_field = valid_orderings.get(ordering, "name")
        queryset = queryset.order_by(order_field)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset.distinct(), request)
        serializer = PublicProductSerializer(page, many=True)

        return success_response(
            message=f"Search results for '{query}'" if query else "Products fetched successfully.",
            data={
                "query": query,
                "count": paginator.page.paginator.count,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "results": serializer.data,
            },
        )


class PublicProductDetailView(APIView):
    """
    Public API: View details of a single product.
    Excludes products from suspended, archived, or inactive stores.
    """

    throttle_scope = "store_read"
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Public Products"],
        summary="Get public product detail",
        description="Returns details for an active product belonging to an active store.",
        responses={200: PublicProductSerializer, 404: PRODUCT_ERROR_SCHEMA},
    )
    def get(self, request, product_pk):
        product = (
            Product.objects.filter(
                pk=product_pk,
                status=ProductStatus.ACTIVE,
                store__status=StoreStatus.ACTIVE,
                store__archived_at__isnull=True,
            )
            .select_related("store")
            .first()
        )
        if not product:
            return error_response(
                message="Product not found or unavailable.",
                code="PRODUCT_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        return success_response(
            message="Product details fetched successfully.",
            data=PublicProductSerializer(product).data,
        )

