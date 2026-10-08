from django.core.exceptions import ValidationError
from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.responses import error_response, success_response
from stores.models import StoreStatus
from stores.pagination import StorePagination
from stores.permissions import IsSeller, get_store_membership
from stores.validators import validate_store_image, MAX_LOGO_SIZE

from .models import Product, ProductStatus
from .serializers import ProductCreateSerializer, ProductSerializer, ProductUpdateSerializer
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
        responses={200: PRODUCT_ERROR_SCHEMA, 404: PRODUCT_ERROR_SCHEMA},
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
        responses={200: ProductSerializer, 400: PRODUCT_ERROR_SCHEMA, 404: PRODUCT_ERROR_SCHEMA},
    )
    def put(self, request, pk, product_pk):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
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
        responses={200: PRODUCT_ERROR_SCHEMA, 404: PRODUCT_ERROR_SCHEMA},
    )
    def delete(self, request, pk, product_pk):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
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
