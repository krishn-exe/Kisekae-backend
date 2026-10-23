from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import extend_schema_field, inline_serializer
from rest_framework import serializers

from products.models import Product
from .models import ARStatus, Combo, ComboItem, TryOnProductAsset
from .services import ComboValidationService


class ComboItemSerializer(serializers.ModelSerializer):
    product_id = serializers.IntegerField(source="product.id", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_slug = serializers.CharField(source="product.slug", read_only=True)
    category_display = serializers.CharField(source="product.get_category_display", read_only=True)
    price = serializers.DecimalField(source="product.price", max_digits=10, decimal_places=2, read_only=True)
    currency = serializers.CharField(source="product.currency", read_only=True)
    thumbnail = serializers.CharField(source="product.thumbnail", read_only=True, allow_null=True)

    class Meta:
        model = ComboItem
        fields = [
            "id",
            "product_id",
            "product_name",
            "product_slug",
            "category",
            "category_display",
            "price",
            "currency",
            "thumbnail",
            "created_at",
        ]


class ComboSerializer(serializers.ModelSerializer):
    items = ComboItemSerializer(many=True, read_only=True)
    items_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Combo
        fields = [
            "id",
            "name",
            "items_count",
            "items",
            "created_at",
            "updated_at",
        ]


class ComboCreateSerializer(serializers.Serializer):
    name = serializers.CharField(
        max_length=120,
        required=False,
        allow_blank=True,
        default="My Combo",
        help_text="Custom name for the combo (e.g. 'Summer Casual', 'Office Formal').",
    )
    product_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        default=list,
        help_text="List of product IDs composing this outfit (Top, Bottom, One-Piece, Outerwear).",
    )

    def validate_name(self, value):
        return (value or "").strip() or "My Combo"

    def validate_product_ids(self, value):
        if value:
            try:
                ComboValidationService.validate_products(value)
            except DjangoValidationError as exc:
                message = exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc)
                raise serializers.ValidationError(message)
        return value


class ComboUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(
        max_length=120,
        required=False,
        help_text="Updated name for the combo.",
    )
    product_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        help_text="Replacement list of product IDs for this combo.",
    )

    def validate_name(self, value):
        return (value or "").strip() or "My Combo"

    def validate_product_ids(self, value):
        if value is not None:
            try:
                ComboValidationService.validate_products(value)
            except DjangoValidationError as exc:
                message = exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc)
                raise serializers.ValidationError(message)
        return value


class ComboAddItemSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(
        min_value=1,
        help_text="Product ID to add to the combo.",
    )


class SellerProductARStatusSerializer(serializers.ModelSerializer):
    """
    Returns seller product card enhanced with AR Virtual Try-On enability status,
    rejection reason, and angle breakdown.
    """
    category_display = serializers.CharField(source="get_category_display", read_only=True)
    thumbnail = serializers.CharField(read_only=True, allow_null=True)
    ar_status = serializers.SerializerMethodField()
    rejection_reason = serializers.SerializerMethodField()
    angles_count = serializers.SerializerMethodField()
    angles = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "slug",
            "category",
            "category_display",
            "price",
            "currency",
            "thumbnail",
            "ar_status",
            "rejection_reason",
            "angles_count",
            "angles",
        ]

    @extend_schema_field(serializers.ChoiceField(choices=ARStatus.choices))
    def get_ar_status(self, obj) -> str:
        if hasattr(obj, "tryon_asset") and obj.tryon_asset:
            return obj.tryon_asset.status
        return "PROCESSING"

    @extend_schema_field(serializers.CharField(allow_blank=True))
    def get_rejection_reason(self, obj) -> str:
        if hasattr(obj, "tryon_asset") and obj.tryon_asset:
            return obj.tryon_asset.rejection_reason
        return "Image analysis in progress."

    @extend_schema_field(serializers.IntegerField())
    def get_angles_count(self, obj) -> int:
        if hasattr(obj, "tryon_asset") and obj.tryon_asset:
            return obj.tryon_asset.angles_count
        return 0

    @extend_schema_field(
        inline_serializer(
            name="ProductAnglesMap",
            fields={
                "front": serializers.BooleanField(),
                "back": serializers.BooleanField(),
                "side": serializers.BooleanField(),
            },
        )
    )
    def get_angles(self, obj) -> dict[str, bool]:
        if hasattr(obj, "tryon_asset") and obj.tryon_asset:
            asset = obj.tryon_asset
            return {
                "front": bool(asset.front_image_id),
                "back": bool(asset.back_image_id),
                "side": bool(asset.side_image_id),
            }
        return {"front": False, "back": False, "side": False}
