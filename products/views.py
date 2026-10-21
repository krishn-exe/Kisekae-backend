from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models import Case, Count, FloatField, Prefetch, Q, Value, When
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from pgvector.django import CosineDistance
from rest_framework import serializers, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from accounts.responses import error_response, success_response
from stores.models import StoreStatus
from stores.pagination import StorePagination
from stores.permissions import IsBusinessVerified, IsSeller, get_store_membership
from stores.validators import validate_store_image, MAX_LOGO_SIZE

from .models import (
    Product,
    ProductCategory,
    ProductImage,
    ProductSize,
    ProductStatus,
    ProductView,
    SearchHistory,
    UserTasteProfile,
)
from .serializers import (
    BulkProductUpdateSerializer,
    CategoryItemSerializer,
    ProductCardSerializer,
    ProductCreateSerializer,
    ProductImageSerializer,
    ProductListingStatusSerializer,
    ProductSerializer,
    ProductUpdateSerializer,
    PublicProductDetailSerializer,
    PublicProductSerializer,
    SearchHistorySerializer,
)
from .embedding_service import EmbeddingService
from .recommendation_service import RecommendationService
from .services import MAX_IMAGES_PER_PRODUCT, ProductService
from .tasks import record_product_view_task, record_search_query_task

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
    """Returns base public product queryset with store and related images/sizes prefetched to prevent N+1 queries."""
    return (
        Product.objects.filter(
            status=ProductStatus.ACTIVE,
            store__status=StoreStatus.ACTIVE,
            store__archived_at__isnull=True,
        )
        .select_related("store")
        .prefetch_related(
            Prefetch("images", queryset=ProductImage.objects.order_by("order", "id")),
            "sizes",
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
    category_param = request.query_params.get("category") or request.query_params.get("type")
    if category_param:
        cat_code = normalize_category_param(category_param)
        if cat_code:
            queryset = queryset.filter(category=cat_code)

    size_param = request.query_params.get("size")
    if size_param:
        s_clean = str(size_param).strip().upper()
        queryset = queryset.filter(sizes__size__iexact=s_clean, sizes__stock__gt=0)

    color_param = request.query_params.get("color")
    if color_param:
        queryset = queryset.filter(color__icontains=str(color_param).strip())

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
        description="Returns list of all available fashion categories (One-Piece, Top, Bottom, Footwear, Headwear, Eyewear, Outerwear, Accessories) with counts of active products.",
        responses={200: CategoryItemSerializer(many=True)},
    )
    def get(self, request):
        active_counts = dict(
            Product.objects.filter(
                status=ProductStatus.ACTIVE,
                store__status=StoreStatus.ACTIVE,
                store__archived_at__isnull=True,
            )
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
            data=categories_data,
        )


# ============================================================================
# SELLER PRODUCT CATALOG VIEWS
# ============================================================================

class ProductListCreateView(ProductMethodThrottleMixin, APIView):
    throttle_scope = "store_read"
    permission_classes = [IsAuthenticated, IsSeller]
    pagination_class = ProductPagination

    @extend_schema(
        tags=["Products"],
        summary="List products for a store",
        description="Returns all products for the specified store. Filterable by status and category.",
        parameters=[
            OpenApiParameter(
                name="status",
                description="Filter by product status (DRAFT, ACTIVE, INACTIVE)",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="category",
                description="Filter by category (e.g. TOP, BOTTOM, FOOTWEAR)",
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

        queryset = (
            Product.objects.for_store(store)
            .select_related("store")
            .prefetch_related(
                Prefetch("images", queryset=ProductImage.objects.order_by("order", "id")),
                "sizes",
            )
        )

        status_filter = request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter.upper())

        category_param = request.query_params.get("category")
        if category_param:
            cat_code = normalize_category_param(category_param)
            if cat_code:
                queryset = queryset.filter(category=cat_code)

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
        description="Creates a new product with category, color, and size breakdown (e.g. S/M/L/XL for clothing, 6-10 for shoes).",
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
        product = (
            Product.objects.filter(store=store, pk=product_pk)
            .select_related("store")
            .prefetch_related(
                Prefetch("images", queryset=ProductImage.objects.order_by("order", "id")),
                "sizes",
            )
            .first()
        )
        return store, membership, product

    @extend_schema(
        tags=["Products"],
        summary="Get product details",
        description="Returns details of a specific product with its images and size stocks.",
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
        description="Updates details of a product, including category, color, and size stocks.",
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

        try:
            updated_product = ProductService.update_product(product, **serializer.validated_data)
        except ValidationError as e:
            msg = e.messages[0] if hasattr(e, "messages") and e.messages else str(e)
            return error_response(
                message=msg,
                code="PRODUCT_UPDATE_FAILED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        return success_response(
            message="Product updated successfully.",
            data=ProductSerializer(updated_product).data,
        )

    @extend_schema(
        tags=["Products"],
        summary="Delete a product",
        description="Permanently deletes a product and cleans up gallery images.",
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


# ============================================================================
# MULTIPLE IMAGES MANAGEMENT (UP TO 10 IMAGES)
# ============================================================================

class ProductMultipleImageView(APIView):
    """
    Manage gallery images for a product (max 10 images).
    """
    throttle_scope = "store_write"
    permission_classes = [IsAuthenticated, IsSeller]
    parser_classes = [MultiPartParser, FormParser]

    def _get_product(self, user, pk, product_pk):
        store, _ = get_store_membership(user, pk)
        if not store:
            return None, error_response("Store not found.", "STORE_NOT_FOUND", status_code=status.HTTP_404_NOT_FOUND)
        if store.is_archived:
            return None, error_response("Cannot modify products in an archived store.", "STORE_ARCHIVED", status_code=status.HTTP_400_BAD_REQUEST)
        if store.status == StoreStatus.SUSPENDED:
            return None, error_response("Cannot modify products in a suspended store.", "STORE_SUSPENDED", status_code=status.HTTP_403_FORBIDDEN)

        product = Product.objects.filter(store=store, pk=product_pk).first()
        if not product:
            return None, error_response("Product not found.", "PRODUCT_NOT_FOUND", status_code=status.HTTP_404_NOT_FOUND)
        return product, None

    @extend_schema(
        tags=["Products"],
        summary="List all product images",
        description="Returns list of all images for this product (up to 10 images).",
        responses={200: ProductImageSerializer(many=True), 404: PRODUCT_ERROR_SCHEMA},
    )
    def get(self, request, pk, product_pk):
        product, err = self._get_product(request.user, pk, product_pk)
        if err:
            return err
        images = product.images.all()
        return success_response(
            message="Product images fetched successfully.",
            data=ProductImageSerializer(images, many=True).data,
        )

    @extend_schema(
        tags=["Products"],
        summary="Upload product images (max 10 total)",
        description="Uploads one or multiple images to the product image gallery (JPEG, PNG, WEBP; max 5MB each). Maximum 10 images per product.",
        request=inline_serializer(
            name="ProductMultiImageUpload",
            fields={"images": serializers.ListField(child=serializers.ImageField(), required=False), "file": serializers.ImageField(required=False)},
        ),
        responses={200: ProductImageSerializer(many=True), 400: PRODUCT_ERROR_SCHEMA, 404: PRODUCT_ERROR_SCHEMA},
    )
    def post(self, request, pk, product_pk):
        product, err = self._get_product(request.user, pk, product_pk)
        if err:
            return err

        uploaded_files = request.FILES.getlist("images")
        if not uploaded_files:
            single = request.FILES.get("file")
            if single:
                uploaded_files = [single]

        if not uploaded_files:
            return error_response("At least one image file is required.", "VALIDATION_ERROR", status_code=status.HTTP_400_BAD_REQUEST)

        current_count = product.images.count()
        if current_count + len(uploaded_files) > MAX_IMAGES_PER_PRODUCT:
            return error_response(
                f"Cannot upload {len(uploaded_files)} image(s). Product currently has {current_count}/{MAX_IMAGES_PER_PRODUCT} images.",
                "MAX_IMAGES_EXCEEDED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        for f in uploaded_files:
            try:
                validate_store_image(f, MAX_PRODUCT_IMAGE_SIZE, "product image")
            except ValidationError as e:
                msg = e.messages[0] if hasattr(e, "messages") and e.messages else str(e)
                return error_response(msg, "INVALID_IMAGE", status_code=status.HTTP_400_BAD_REQUEST)

        added = []
        for f in uploaded_files:
            img_obj = ProductService.add_gallery_image(product, f)
            added.append(img_obj)

        return success_response(
            message=f"{len(added)} image(s) added successfully.",
            data=ProductImageSerializer(product.images.all(), many=True).data,
            status_code=status.HTTP_200_OK,
        )


class ProductImageDetailView(APIView):
    """Delete a specific image from product gallery."""
    throttle_scope = "store_write"
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        tags=["Products"],
        summary="Delete a specific product image",
        description="Deletes a specific gallery image by ID. If thumbnail was deleted, promotes the next available image.",
        responses={200: inline_serializer(name="ProductImageDeleteSuccess", fields={"success": serializers.BooleanField()}), 404: PRODUCT_ERROR_SCHEMA},
    )
    def delete(self, request, pk, product_pk, image_id):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response("Store not found.", "STORE_NOT_FOUND", status_code=status.HTTP_404_NOT_FOUND)
        product = Product.objects.filter(store=store, pk=product_pk).first()
        if not product:
            return error_response("Product not found.", "PRODUCT_NOT_FOUND", status_code=status.HTTP_404_NOT_FOUND)

        try:
            ProductService.remove_gallery_image(product, image_id)
        except ValidationError as e:
            msg = e.messages[0] if hasattr(e, "messages") and e.messages else str(e)
            return error_response(msg, "IMAGE_NOT_FOUND", status_code=status.HTTP_404_NOT_FOUND)

        return success_response(message="Product image removed successfully.")


class ProductImageView(APIView):
    """Maintained for backward compatibility: uploads or removes primary thumbnail."""
    throttle_scope = "store_write"
    permission_classes = [IsAuthenticated, IsSeller]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        tags=["Products"],
        summary="Upload primary product image",
        description="Uploads or replaces the primary product thumbnail image (JPEG, PNG, WEBP; max 5MB).",
        request=inline_serializer(
            name="ProductImageUpload",
            fields={"file": serializers.ImageField()},
        ),
        responses={200: ProductSerializer, 400: PRODUCT_ERROR_SCHEMA, 403: PRODUCT_ERROR_SCHEMA, 404: PRODUCT_ERROR_SCHEMA},
    )
    def put(self, request, pk, product_pk):
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

        image_file = request.FILES.get("file")
        if not image_file:
            return error_response("Image file is required.", "VALIDATION_ERROR", status_code=status.HTTP_400_BAD_REQUEST)

        try:
            validate_store_image(image_file, MAX_PRODUCT_IMAGE_SIZE, "product image")
        except ValidationError as e:
            msg = e.messages[0] if hasattr(e, "messages") and e.messages else str(e)
            return error_response(msg, "INVALID_IMAGE", status_code=status.HTTP_400_BAD_REQUEST)

        updated = ProductService.set_product_image(product, image_file)
        return success_response(
            message="Product image uploaded successfully.",
            data=ProductSerializer(updated).data,
        )

    @extend_schema(
        tags=["Products"],
        summary="Remove primary product image",
        description="Removes the primary product image.",
        responses={200: PRODUCT_ERROR_SCHEMA, 400: PRODUCT_ERROR_SCHEMA, 403: PRODUCT_ERROR_SCHEMA, 404: PRODUCT_ERROR_SCHEMA},
    )
    def delete(self, request, pk, product_pk):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response("Store not found.", "STORE_NOT_FOUND", status_code=status.HTTP_404_NOT_FOUND)
        product = Product.objects.filter(store=store, pk=product_pk).first()
        if not product:
            return error_response("Product not found.", "PRODUCT_NOT_FOUND", status_code=status.HTTP_404_NOT_FOUND)

        ProductService.remove_product_image(product)
        return success_response(message="Product image removed successfully.")


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
            return error_response("Store not found.", "STORE_NOT_FOUND", status_code=status.HTTP_404_NOT_FOUND)

        # Single aggregation query to prevent 6 separate round-trips
        counts = Product.objects.for_store(store).aggregate(
            total=Count("id"),
            active=Count("id", filter=Q(status=ProductStatus.ACTIVE)),
            draft=Count("id", filter=Q(status=ProductStatus.DRAFT)),
            inactive=Count("id", filter=Q(status=ProductStatus.INACTIVE)),
            in_stock=Count("id", filter=Q(stock__gt=0)),
            out_of_stock=Count("id", filter=Q(stock=0)),
        )

        return success_response(
            message="Listing status fetched successfully.",
            data=counts,
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
        for item in items:
            product = Product.objects.filter(store=store, pk=item["product_id"]).first()
            if not product:
                errors.append({"product_id": item["product_id"], "error": "Product not found"})
                continue
            update_data = {}
            for field in ["status", "price", "compare_at_price", "category", "color"]:
                if field in item:
                    update_data[field] = item[field]
            if update_data:
                ProductService.update_product(product, **update_data)
                updated_ids.append(product.id)

        return success_response(
            message=f"Bulk update completed: {len(updated_ids)} updated, {len(errors)} errors.",
            data={"updated_count": len(updated_ids), "updated_ids": updated_ids, "errors": errors},
        )


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
            OpenApiParameter(name="ordering", description="Sort order: 'price', '-price', 'newest', 'oldest', 'name', '-name'", required=False, type=str),
            OpenApiParameter(name="page", type=int, required=False),
            OpenApiParameter(name="page_size", type=int, required=False),
        ],
        responses={200: ProductCardSerializer(many=True)},
    )
    def get(self, request):
        queryset = get_optimized_product_queryset()

        # Search query if present
        search_query = request.query_params.get("search") or request.query_params.get("q")
        if search_query:
            q_str = search_query.strip()
            queryset = queryset.filter(Q(name__icontains=q_str) | Q(description__icontains=q_str))
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
            },
        )


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
            OpenApiParameter(name="similarity_threshold", description="Cosine distance threshold for semantic matching (default: 0.82)", required=False, type=float),
            OpenApiParameter(name="ordering", description="Sort order: 'relevance' (default), 'price', '-price', 'newest', 'oldest', 'name', '-name'", required=False, type=str),
            OpenApiParameter(name="page", type=int, required=False),
            OpenApiParameter(name="page_size", type=int, required=False),
        ],
        responses={200: ProductCardSerializer(many=True)},
    )
    def get(self, request):
        query = (
            request.query_params.get("q")
            or request.query_params.get("query")
            or request.query_params.get("search", "")
        ).strip()

        raw_threshold = request.query_params.get("similarity_threshold")
        try:
            threshold = float(raw_threshold) if raw_threshold is not None else 0.82
        except (ValueError, TypeError):
            threshold = 0.82

        queryset = get_optimized_product_queryset()

        has_vector_search = False
        supports_pgvector = (connection.vendor == "postgresql")

        if query:
            if request.user and request.user.is_authenticated:
                record_search_query_task.delay(request.user.id, query)
            if not supports_pgvector:
                queryset = queryset.filter(
                    Q(name__icontains=query)
                    | Q(description__icontains=query)
                    | Q(color__icontains=query)
                )
            else:
                try:
                    embedding_service = EmbeddingService.get_instance()
                    query_vector = embedding_service.embed_query_cached(query)
                except Exception:
                    query_vector = None

                if query_vector:
                    try:
                        queryset = queryset.annotate(
                            distance=CosineDistance("embedding_record__embedding", query_vector)
                        ).filter(
                            Q(name__icontains=query)
                            | Q(description__icontains=query)
                            | Q(color__icontains=query)
                            | (Q(embedding_record__isnull=False) & Q(distance__lte=threshold))
                        )
                        has_vector_search = True
                    except Exception:
                        queryset = queryset.filter(
                            Q(name__icontains=query)
                            | Q(description__icontains=query)
                            | Q(color__icontains=query)
                        )
                else:
                    queryset = queryset.filter(
                        Q(name__icontains=query)
                        | Q(description__icontains=query)
                        | Q(color__icontains=query)
                    )

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

        if ordering == "relevance" and has_vector_search:
            queryset = queryset.order_by(
                Case(
                    When(name__iexact=query, then=Value(0.0)),
                    When(name__istartswith=query, then=Value(0.1)),
                    When(name__icontains=query, then=Value(0.2)),
                    default=Value(1.0),
                    output_field=FloatField(),
                ),
                "distance",
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
            },
        )


class PublicProductDetailView(APIView):
    """
    Public API: View full details of a single product including description,
    images gallery, sizes with stock counts, category, and color.
    """
    throttle_scope = "store_read"
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Public Products"],
        summary="Get public product detail",
        description="Returns full details for an active product, including all gallery images (up to 10), category, color, and size breakdown.",
        responses={200: PublicProductDetailSerializer, 404: PRODUCT_ERROR_SCHEMA},
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
            .prefetch_related(
                Prefetch("images", queryset=ProductImage.objects.order_by("order", "id")),
                "sizes",
            )
            .first()
        )
        if not product:
            return error_response(
                message="Product not found or unavailable.",
                code="PRODUCT_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if request.user and request.user.is_authenticated:
            record_product_view_task.delay(request.user.id, product.id)

        return success_response(
            message="Product details fetched successfully.",
            data=PublicProductDetailSerializer(product).data,
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
        responses={200: SearchHistorySerializer(many=True)},
    )
    def get(self, request):
        history = SearchHistory.objects.filter(user=request.user).order_by("-last_searched_at")[:30]
        return success_response(
            message="Search history fetched successfully.",
            data=SearchHistorySerializer(history, many=True).data,
        )

    @extend_schema(
        operation_id="clear_all_search_history",
        tags=["Search History"],
        summary="Clear user's search history",
        description="Clears all stored search queries for the authenticated user and excludes them from future ML taste profile refreshes.",
        responses={200: inline_serializer(name="SearchHistoryClearResponse", fields={"success": serializers.BooleanField(), "message": serializers.CharField()})},
    )
    def delete(self, request):
        SearchHistory.objects.filter(user=request.user).delete()
        return success_response(
            message="Search history cleared successfully.",
        )


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
        responses={200: inline_serializer(name="SearchHistoryDeleteResponse", fields={"success": serializers.BooleanField(), "message": serializers.CharField()}), 404: PRODUCT_ERROR_SCHEMA},
    )
    def delete(self, request, history_id):
        deleted, _ = SearchHistory.objects.filter(user=request.user, pk=history_id).delete()
        if not deleted:
            return error_response(
                message="Search history entry not found.",
                code="NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return success_response(
            message="Search history entry removed successfully.",
        )


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
        responses={200: ProductCardSerializer(many=True)},
    )
    def get(self, request):
        exclude_cart_param = request.query_params.get("exclude_cart", "true").strip().lower()
        exclude_cart = exclude_cart_param in ("true", "1", "yes")
        category = request.query_params.get("category")

        user = request.user if request.user.is_authenticated else None
        queryset = RecommendationService.get_recommendations(
            user=user,
            limit=100,
            exclude_cart=exclude_cart,
            category=category,
        )

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
            },
        )
