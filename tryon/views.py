from django.db.models import Prefetch
from django.http import HttpResponseRedirect
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from accounts.responses import error_response, success_response
from products.models import Product
from products.views import ProductPagination
from stores.permissions import IsSeller, get_store_membership
from .models import (
    ARStatus,
    Combo,
    ComboItem,
    TRYON_SUPPORTED_CATEGORIES,
    TRYON_UNSUPPORTED_CATEGORIES,
    TryOnAngle,
    TryOnRequest,
    TryOnRequestStatus,
)
from .serializers import (
    ComboAddItemSerializer,
    ComboCreateSerializer,
    ComboSerializer,
    ComboUpdateSerializer,
    SellerProductARStatusSerializer,
    SharedComboSerializer,
    TryOnGenerateRequestSerializer,
    TryOnRequestDetailSerializer,
)
from .services import ComboService
from .tasks import generate_tryon_task

from KiseKaeDjango.schema.helpers import api_response, paginated_response, ERROR_SCHEMA

COMBO_RESPONSE_SCHEMA = api_response(ComboSerializer, "ComboResponseEnvelope")
COMBO_LIST_RESPONSE_SCHEMA = api_response(ComboSerializer, "ComboListResponseEnvelope", many=True)
SHARED_COMBO_RESPONSE_SCHEMA = api_response(SharedComboSerializer, "SharedComboResponseEnvelope")
COMBO_ERROR_SCHEMA = ERROR_SCHEMA

SELLER_AR_STATUS_LIST_SCHEMA = paginated_response(SellerProductARStatusSerializer, "SellerARStatusPaginatedData")
SELLER_AR_STATUS_DETAIL_SCHEMA = api_response(SellerProductARStatusSerializer, "SellerARStatusDetailEnvelope")
TRYON_GENERATE_RESPONSE_SCHEMA = api_response(
    inline_serializer(name="TryOnGenerateResponseData", fields={"request_id": serializers.UUIDField(), "status": serializers.CharField()}),
    "TryOnGenerateResponseEnvelope",
)
TRYON_DETAIL_RESPONSE_SCHEMA = api_response(TryOnRequestDetailSerializer, "TryOnDetailResponseEnvelope")
TRYON_LIST_RESPONSE_SCHEMA = api_response(TryOnRequestDetailSerializer, "TryOnListResponseEnvelope", many=True)



class BaseComboView(APIView):
    permission_classes = [IsAuthenticated]

    def get_combo(self, user, pk: int):
        return (
            Combo.objects.filter(pk=pk, user=user)
            .prefetch_related(
                Prefetch(
                    "items",
                    queryset=ComboItem.objects.select_related("product__store"),
                )
            )
            .first()
        )


class ComboListCreateView(BaseComboView):
    """
    List user's outfit combos or create a new combo with strict category and layering validation.
    """

    @extend_schema(
        operation_id="user_combo_list",
        tags=["Profile - Saved Looks"],
        summary="List user's combos",
        description="Returns all outfit combos created by the authenticated user with full product card items, deep links, and share URLs.",
        responses={200: COMBO_LIST_RESPONSE_SCHEMA},
    )
    def get(self, request):
        combos = (
            Combo.objects.filter(user=request.user)
            .prefetch_related(
                Prefetch(
                    "items",
                    queryset=ComboItem.objects.select_related("product__store"),
                )
            )
            .order_by("-created_at")
        )
        serializer = ComboSerializer(combos, many=True)
        return success_response(
            message="Combos fetched successfully.",
            data=serializer.data,
        )

    @extend_schema(
        operation_id="user_combo_create",
        tags=["Profile - Saved Looks"],
        summary="Create a new combo",
        description=(
            "Creates a new outfit combo. Strictly enforces category rules: "
            "Only Top, Bottom, One-Piece, and Outerwear are allowed. "
            "One-Piece cannot combine with Top or Bottom. "
            "At most one item per category. Max 3 items total. "
            "Generates deep link (kisekae://combos/<share_code>) and share URL."
        ),
        request=ComboCreateSerializer,
        responses={201: COMBO_RESPONSE_SCHEMA, 400: COMBO_ERROR_SCHEMA},
    )
    def post(self, request):
        serializer = ComboCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        combo = ComboService.create_combo(
            user=request.user,
            name=serializer.validated_data.get("name"),
            product_ids=serializer.validated_data.get("product_ids"),
        )

        combo_detail = self.get_combo(request.user, combo.pk)
        return success_response(
            message="Combo created successfully.",
            data=ComboSerializer(combo_detail).data,
            status_code=status.HTTP_201_CREATED,
        )


class ComboDetailView(BaseComboView):
    """
    Retrieve, update (rename or replace items), or delete a specific combo.
    """

    @extend_schema(
        operation_id="user_combo_detail",
        tags=["Profile - Saved Looks"],
        summary="Get combo details",
        description="Returns details and items for a specific outfit combo including share links.",
        responses={200: COMBO_RESPONSE_SCHEMA, 404: COMBO_ERROR_SCHEMA},
    )
    def get(self, request, pk):
        combo = self.get_combo(request.user, pk)
        if not combo:
            return error_response(
                message="Combo not found.",
                code="COMBO_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return success_response(
            message="Combo details fetched successfully.",
            data=ComboSerializer(combo).data,
        )

    @extend_schema(
        operation_id="user_combo_update",
        tags=["Profile - Saved Looks"],
        summary="Update combo",
        description="Updates a combo's name or replaces its product items with category validation.",
        request=ComboUpdateSerializer,
        responses={200: COMBO_RESPONSE_SCHEMA, 400: COMBO_ERROR_SCHEMA, 404: COMBO_ERROR_SCHEMA},
    )
    def patch(self, request, pk):
        combo = self.get_combo(request.user, pk)
        if not combo:
            return error_response(
                message="Combo not found.",
                code="COMBO_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        serializer = ComboUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        updated_combo = ComboService.update_combo(
            combo=combo,
            name=serializer.validated_data.get("name"),
            product_ids=serializer.validated_data.get("product_ids"),
        )

        combo_detail = self.get_combo(request.user, updated_combo.pk)
        return success_response(
            message="Combo updated successfully.",
            data=ComboSerializer(combo_detail).data,
        )

    @extend_schema(
        operation_id="user_combo_delete",
        tags=["Profile - Saved Looks"],
        summary="Delete combo",
        description="Permanently deletes an outfit combo and its item associations.",
        responses={
            200: inline_serializer(
                name="ComboDeleteSuccess",
                fields={"success": serializers.BooleanField(), "message": serializers.CharField()},
            ),
            404: COMBO_ERROR_SCHEMA,
        },
    )
    def delete(self, request, pk):
        combo = self.get_combo(request.user, pk)
        if not combo:
            return error_response(
                message="Combo not found.",
                code="COMBO_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        ComboService.delete_combo(combo)
        return success_response(message="Combo deleted successfully.")


class ComboItemAddView(BaseComboView):
    """
    Add an individual item into an existing combo.
    """

    @extend_schema(
        operation_id="user_combo_item_add",
        tags=["Profile - Saved Looks"],
        summary="Add item to combo",
        description="Adds a product to the combo. Validates category compatibility against existing items in the combo.",
        request=ComboAddItemSerializer,
        responses={201: COMBO_RESPONSE_SCHEMA, 400: COMBO_ERROR_SCHEMA, 404: COMBO_ERROR_SCHEMA},
    )
    def post(self, request, pk):
        combo = self.get_combo(request.user, pk)
        if not combo:
            return error_response(
                message="Combo not found.",
                code="COMBO_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        serializer = ComboAddItemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        ComboService.add_item_to_combo(
            combo=combo,
            product_id=serializer.validated_data["product_id"],
        )

        combo_detail = self.get_combo(request.user, combo.pk)
        return success_response(
            message="Item added to combo successfully.",
            data=ComboSerializer(combo_detail).data,
            status_code=status.HTTP_201_CREATED,
        )


class ComboItemDeleteView(BaseComboView):
    """
    Remove an individual item from an existing combo.
    """

    @extend_schema(
        operation_id="user_combo_item_delete",
        tags=["Profile - Saved Looks"],
        summary="Remove item from combo",
        description="Removes a specific product item from the combo.",
        responses={
            200: inline_serializer(
                name="ComboItemDeleteSuccess",
                fields={"success": serializers.BooleanField(), "message": serializers.CharField()},
            ),
            404: COMBO_ERROR_SCHEMA,
        },
    )
    def delete(self, request, pk, item_id):
        combo = self.get_combo(request.user, pk)
        if not combo:
            return error_response(
                message="Combo not found.",
                code="COMBO_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        ComboService.remove_item_from_combo(combo=combo, item_id=item_id)

        return success_response(message="Item removed from combo successfully.")


class SharedComboDetailView(APIView):
    """
    Public API: Fetch details of a shared outfit combo via its unique share code.
    Accessible without authentication for deep-linking in the Flutter mobile app
    and web previews.
    """
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="shared_combo_detail",
        tags=["Profile - Saved Looks"],
        summary="Get shared combo details",
        description=(
            "Public endpoint to retrieve an outfit combo using its unique share code. "
            "Does not require authentication. Powers Flutter deep-linking (kisekae://combos/<share_code>) "
            "and web landing previews."
        ),
        responses={200: SHARED_COMBO_RESPONSE_SCHEMA, 404: COMBO_ERROR_SCHEMA},
    )
    def get(self, request, share_code: str):
        combo = ComboService.get_shared_combo(share_code.strip())
        if not combo:
            return error_response(
                message="Shared combo not found or no longer available.",
                code="COMBO_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        serializer = SharedComboSerializer(combo)
        return success_response(
            message="Shared combo retrieved successfully.",
            data=serializer.data,
        )


class MobileDeepLinkRedirect(HttpResponseRedirect):
    allowed_schemes = ["http", "https", "kisekae"]


class SharedComboRedirectView(APIView):
    """
    Public API: Web-to-App deep link redirect.
    When a user clicks a web link (e.g. from WhatsApp, SMS, or mobile browser),
    this endpoint issues an HTTP 302 redirect directly into the Flutter app custom URI scheme.
    """
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="shared_combo_redirect_to_app",
        tags=["Profile - Saved Looks"],
        summary="Redirect to Flutter app",
        description="Redirects HTTP requests directly to the Flutter app custom URI scheme 'kisekae://combos/<share_code>'.",
        responses={302: None, 404: COMBO_ERROR_SCHEMA},
    )
    def get(self, request, share_code: str):
        combo = ComboService.get_shared_combo(share_code.strip())
        if not combo:
            return error_response(
                message="Shared combo not found.",
                code="COMBO_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return MobileDeepLinkRedirect(redirect_to=combo.deep_link)


class SharedComboSaveView(APIView):
    """
    Customer API: Save / Clone a shared combo into the authenticated user's own collection.
    Allows customers who discover a shared outfit to save it and customize it.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="shared_combo_clone",
        tags=["Profile - Saved Looks"],
        summary="Save shared combo to my combos",
        description="Clones an outfit discovered via a shared link into the current authenticated user's personal combos collection.",
        request=None,
        responses={201: COMBO_RESPONSE_SCHEMA, 400: COMBO_ERROR_SCHEMA, 404: COMBO_ERROR_SCHEMA},
    )
    def post(self, request, share_code: str):
        cloned_combo = ComboService.clone_shared_combo(
            user=request.user,
            share_code=share_code.strip(),
        )

        combo_detail = (
            Combo.objects.filter(pk=cloned_combo.pk, user=request.user)
            .prefetch_related(
                Prefetch(
                    "items",
                    queryset=ComboItem.objects.select_related("product__store"),
                )
            )
            .first()
        )
        return success_response(
            message="Combo saved to your collection successfully.",
            data=ComboSerializer(combo_detail).data,
            status_code=status.HTTP_201_CREATED,
        )


class StoreProductsARStatusView(APIView):
    """
    Seller API: Browse store products with their AR Try-On readiness,
    rejection reason, and detected angle views.
    """
    permission_classes = [IsAuthenticated, IsSeller]
    pagination_class = ProductPagination

    @extend_schema(
        operation_id="seller_store_products_ar_status",
        tags=["Store - AR Status"],
        summary="List store products AR Try-On status",
        description=(
            "Returns products for a store accompanied by their AR status flag "
            "('AVAILABLE', 'PROCESSING', 'REJECTED'), exact rejection reasons, "
            "and angle detection metrics (Front, Back, Side)."
        ),
        parameters=[
            OpenApiParameter(
                name="status",
                description="Filter by AR status (AVAILABLE, PROCESSING, REJECTED)",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="page",
                description="Page number",
                required=False,
                type=int,
            ),
        ],
        responses={200: SELLER_AR_STATUS_LIST_SCHEMA, 404: COMBO_ERROR_SCHEMA},
    )
    def get(self, request, store_pk):
        store, _ = get_store_membership(request.user, store_pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        queryset = (
            Product.objects.filter(store=store)
            .select_related("tryon_asset")
            .prefetch_related("images")
            .order_by("-created_at")
        )

        status_filter = request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(tryon_asset__status=status_filter.upper())

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request)
        serializer = SellerProductARStatusSerializer(page, many=True)

        return success_response(
            message="Store products AR status fetched successfully.",
            data={
                "count": paginator.page.paginator.count,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "results": serializer.data,
            },
        )


class StoreProductARStatusDetailView(APIView):
    """
    Seller API: Get single product AR Try-On status and angle metrics.
    """
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        operation_id="seller_store_product_ar_status_detail",
        tags=["Store - AR Status"],
        summary="Get single product AR Try-On status",
        description="Returns AR Try-On readiness flag, rejection reason, and verified angles for a single product.",
        responses={200: SELLER_AR_STATUS_DETAIL_SCHEMA, 404: COMBO_ERROR_SCHEMA},
    )
    def get(self, request, store_pk, product_pk):
        store, _ = get_store_membership(request.user, store_pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        product = (
            Product.objects.filter(store=store, pk=product_pk)
            .select_related("tryon_asset")
            .prefetch_related("images")
            .first()
        )
        if not product:
            return error_response(
                message="Product not found.",
                code="PRODUCT_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        serializer = SellerProductARStatusSerializer(product)
        return success_response(
            message="Product AR status fetched successfully.",
            data=serializer.data,
        )


class TryOnGenerateView(APIView):
    """
    Customer API: Trigger asynchronous Virtual Try-On generation using Vertex AI.
    Accepts customer selfie / body photo and either a single product_id or a combo_id.
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        operation_id="tryon_generate",
        tags=["Virtual Try-On"],
        summary="Queue Virtual Try-On generation",
        description=(
            "Submits customer selfie/photo along with either a product_id (single garment) "
            "or a combo_id (outfit). Validates garment AR availability and queues asynchronous "
            "Google Vertex AI prediction."
        ),
        request=TryOnGenerateRequestSerializer,
        responses={202: TRYON_GENERATE_RESPONSE_SCHEMA, 400: COMBO_ERROR_SCHEMA, 404: COMBO_ERROR_SCHEMA},
    )
    def post(self, request):
        serializer = TryOnGenerateRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        product_id = data.get("product_id")
        combo_id = data.get("combo_id")
        person_image = data["person_image"]

        target_product = None
        target_combo = None

        if product_id:
            target_product = (
                Product.objects.filter(pk=product_id)
                .select_related("tryon_asset")
                .first()
            )
            if not target_product:
                return error_response(
                    message=f"Product #{product_id} not found.",
                    code="PRODUCT_NOT_FOUND",
                    status_code=status.HTTP_404_NOT_FOUND,
                )

            # Category constraint check
            if target_product.category in TRYON_UNSUPPORTED_CATEGORIES or target_product.category not in TRYON_SUPPORTED_CATEGORIES:
                display_name = target_product.get_category_display() if hasattr(target_product, "get_category_display") else target_product.category
                return error_response(
                    message=f"Category '{display_name}' is not supported for Virtual Try-On.",
                    code="UNSUPPORTED_CATEGORY",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            # AR Availability check
            asset = getattr(target_product, "tryon_asset", None)
            if not asset or asset.status != ARStatus.AVAILABLE:
                reason = asset.rejection_reason if asset else "Product images have not been processed for AR."
                return error_response(
                    message=f"Product is not AR ready. Status: {asset.status if asset else 'UNPROCESSED'}. {reason}",
                    code="AR_NOT_AVAILABLE",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

        elif combo_id:
            target_combo = (
                Combo.objects.filter(pk=combo_id)
                .prefetch_related("items__product__tryon_asset")
                .first()
            )
            if not target_combo:
                return error_response(
                    message=f"Combo #{combo_id} not found.",
                    code="COMBO_NOT_FOUND",
                    status_code=status.HTTP_404_NOT_FOUND,
                )

            if not target_combo.items.exists():
                return error_response(
                    message="Combo contains no items.",
                    code="EMPTY_COMBO",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            # Verify each item in combo
            for item in target_combo.items.all():
                p = item.product
                if p.category in TRYON_UNSUPPORTED_CATEGORIES or p.category not in TRYON_SUPPORTED_CATEGORIES:
                    return error_response(
                        message=f"Item '{p.name}' belongs to unsupported category '{p.category}'.",
                        code="UNSUPPORTED_CATEGORY",
                        status_code=status.HTTP_400_BAD_REQUEST,
                    )
                asset = getattr(p, "tryon_asset", None)
                if not asset or asset.status != ARStatus.AVAILABLE:
                    return error_response(
                        message=f"Item '{p.name}' is not AR ready. Status: {asset.status if asset else 'UNPROCESSED'}.",
                        code="AR_NOT_AVAILABLE",
                        status_code=status.HTTP_400_BAD_REQUEST,
                    )

        # Create TryOnRequest record
        tryon_req = TryOnRequest.objects.create(
            user=request.user,
            product=target_product,
            combo=target_combo,
            angle=TryOnAngle.AUTO,
            person_image=person_image,
            status=TryOnRequestStatus.PENDING,
        )

        # Queue async task
        generate_tryon_task.delay(str(tryon_req.id))

        return success_response(
            message="Try-on generation queued successfully.",
            data={
                "request_id": str(tryon_req.id),
                "status": TryOnRequestStatus.PENDING,
            },
            status_code=status.HTTP_202_ACCEPTED,
        )


class TryOnRequestDetailView(APIView):
    """
    Customer API: Check status or retrieve result of an asynchronous Try-On job.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="tryon_status",
        tags=["Virtual Try-On"],
        summary="Get Try-On request status",
        description="Polls the status and returns the rendered result image URL once READY.",
        responses={200: TRYON_DETAIL_RESPONSE_SCHEMA, 404: COMBO_ERROR_SCHEMA},
    )
    def get(self, request, request_id):
        tryon_req = (
            TryOnRequest.objects.filter(pk=request_id, user=request.user)
            .select_related("product", "combo")
            .first()
        )
        if not tryon_req:
            return error_response(
                message="Try-On request not found.",
                code="REQUEST_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        serializer = TryOnRequestDetailSerializer(tryon_req)
        return success_response(
            message="Try-On status retrieved successfully.",
            data=serializer.data,
        )


class TryOnHistoryListView(APIView):
    """
    Customer API: Retrieve previous Virtual Try-On history for the authenticated user.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="tryon_history_list",
        tags=["Virtual Try-On"],
        summary="List customer Try-On history",
        description="Returns customer's past virtual try-on requests and generated images.",
        responses={200: TRYON_LIST_RESPONSE_SCHEMA},
    )
    def get(self, request):
        requests_qs = (
            TryOnRequest.objects.filter(user=request.user)
            .select_related("product", "combo")
            .order_by("-created_at")[:50]
        )
        serializer = TryOnRequestDetailSerializer(requests_qs, many=True)
        return success_response(
            message="Try-on history retrieved successfully.",
            data=serializer.data,
        )

