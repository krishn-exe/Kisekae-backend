from django.core.exceptions import ValidationError
from django.db.models import Prefetch
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.responses import error_response, success_response
from .models import Combo, ComboItem
from .serializers import (
    ComboAddItemSerializer,
    ComboCreateSerializer,
    ComboItemSerializer,
    ComboSerializer,
    ComboUpdateSerializer,
)
from .services import ComboService

COMBO_RESPONSE_SCHEMA = inline_serializer(
    name="ComboResponseEnvelope",
    fields={
        "success": serializers.BooleanField(default=True),
        "message": serializers.CharField(),
        "data": ComboSerializer(),
    },
)

COMBO_LIST_RESPONSE_SCHEMA = inline_serializer(
    name="ComboListResponseEnvelope",
    fields={
        "success": serializers.BooleanField(default=True),
        "message": serializers.CharField(),
        "data": ComboSerializer(many=True),
    },
)

COMBO_ERROR_SCHEMA = inline_serializer(
    name="ComboErrorEnvelope",
    fields={
        "success": serializers.BooleanField(default=False),
        "message": serializers.CharField(),
        "error": inline_serializer(
            name="ComboErrorDetail",
            fields={
                "code": serializers.CharField(),
                "details": serializers.JSONField(allow_null=True),
            },
        ),
    },
)


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
        tags=["Combos"],
        summary="List user's combos",
        description="Returns all outfit combos created by the authenticated user with full product card items.",
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
        tags=["Combos"],
        summary="Create a new combo",
        description=(
            "Creates a new outfit combo. Strictly enforces category rules: "
            "Only Top, Bottom, One-Piece, and Outerwear are allowed. "
            "One-Piece cannot combine with Top or Bottom. "
            "At most one item per category. Max 3 items total."
        ),
        request=ComboCreateSerializer,
        responses={201: COMBO_RESPONSE_SCHEMA, 400: COMBO_ERROR_SCHEMA},
    )
    def post(self, request):
        serializer = ComboCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            combo = ComboService.create_combo(
                user=request.user,
                name=serializer.validated_data.get("name"),
                product_ids=serializer.validated_data.get("product_ids"),
            )
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc)
            code = getattr(exc, "code", "VALIDATION_ERROR") or "VALIDATION_ERROR"
            return error_response(
                message=msg,
                code=str(code).upper(),
                status_code=status.HTTP_400_BAD_REQUEST,
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
        tags=["Combos"],
        summary="Get combo details",
        description="Returns details and items for a specific outfit combo.",
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
        tags=["Combos"],
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

        try:
            updated_combo = ComboService.update_combo(
                combo=combo,
                name=serializer.validated_data.get("name"),
                product_ids=serializer.validated_data.get("product_ids"),
            )
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc)
            code = getattr(exc, "code", "VALIDATION_ERROR") or "VALIDATION_ERROR"
            return error_response(
                message=msg,
                code=str(code).upper(),
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        combo_detail = self.get_combo(request.user, updated_combo.pk)
        return success_response(
            message="Combo updated successfully.",
            data=ComboSerializer(combo_detail).data,
        )

    @extend_schema(
        tags=["Combos"],
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
        tags=["Combos"],
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

        try:
            ComboService.add_item_to_combo(
                combo=combo,
                product_id=serializer.validated_data["product_id"],
            )
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc)
            code = getattr(exc, "code", "VALIDATION_ERROR") or "VALIDATION_ERROR"
            return error_response(
                message=msg,
                code=str(code).upper(),
                status_code=status.HTTP_400_BAD_REQUEST,
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
        tags=["Combos"],
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

        try:
            ComboService.remove_item_from_combo(combo=combo, item_id=item_id)
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc)
            code = getattr(exc, "code", "ITEM_NOT_FOUND") or "ITEM_NOT_FOUND"
            return error_response(
                message=msg,
                code=str(code).upper(),
                status_code=status.HTTP_404_NOT_FOUND,
            )

        return success_response(message="Item removed from combo successfully.")
