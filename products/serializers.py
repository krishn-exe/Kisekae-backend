import re
from rest_framework import serializers
from drf_spectacular.utils import extend_schema_field

from .models import (
    CLOTHING_CATEGORIES,
    CLOTHING_SIZES,
    FOOTWEAR_CATEGORIES,
    FOOTWEAR_SIZES,
    NO_SIZE_CATEGORIES,
    HEX_COLOR_REGEX,
    Product,
    ProductCategory,
    ProductImage,
    ProductSize,
    ProductStatus,
    ProductType,
    ProductVariant,
    SearchHistory,
)


class ProductVariantSizeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductSize
        fields = ["id", "size", "stock"]


class ProductVariantImageSerializer(serializers.ModelSerializer):
    image_url = serializers.CharField(read_only=True)

    class Meta:
        model = ProductImage
        fields = ["id", "image_url", "order", "created_at"]


class ProductVariantSerializer(serializers.ModelSerializer):
    sizes = ProductVariantSizeSerializer(many=True, read_only=True)
    images = ProductVariantImageSerializer(many=True, read_only=True)
    total_stock = serializers.IntegerField(read_only=True)
    thumbnail = serializers.CharField(read_only=True, allow_null=True)

    class Meta:
        model = ProductVariant
        fields = [
            "id",
            "color_name",
            "color_hex",
            "sku",
            "is_active",
            "total_stock",
            "thumbnail",
            "sizes",
            "images",
            "created_at",
            "updated_at",
        ]


class VariantSizeInputSerializer(serializers.Serializer):
    size = serializers.CharField(
        max_length=20,
        required=False,
        allow_blank=True,
        default="",
        help_text="Size label (e.g. XS-XL for clothing; 6-12 for footwear; leave empty for eyewear/accessories).",
    )
    stock = serializers.IntegerField(
        min_value=0,
        default=0,
        help_text="Available inventory count for this size.",
    )

    def validate_size(self, value):
        return value.strip().upper() if value else ""


class VariantInputSerializer(serializers.Serializer):
    color_name = serializers.CharField(
        max_length=50,
        help_text="Color label for this variant (e.g. 'Navy Blue', 'Crimson Red', 'Pitch Black').",
    )
    color_hex = serializers.RegexField(
        regex=HEX_COLOR_REGEX,
        help_text="Hex color code in #RRGGBB format (e.g. '#000080', '#FF0000', '#000000').",
    )
    sku = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
        default="",
        help_text="Variant-specific Stock Keeping Unit identifier.",
    )
    is_active = serializers.BooleanField(
        required=False,
        default=True,
        help_text="Whether this variant is active and available for customer ordering.",
    )
    sizes = serializers.ListField(
        child=VariantSizeInputSerializer(),
        required=False,
        default=list,
        help_text="List of sizes and their stock quantities for this color variant.",
    )
    stock = serializers.IntegerField(
        required=False,
        default=0,
        min_value=0,
        help_text="Direct stock count when product category has no sizes (e.g. Eyewear, Accessories).",
    )


class VariantImageUploadSerializer(serializers.Serializer):
    image = serializers.ImageField(help_text="Variant photo file (JPEG/PNG/WEBP, max 5MB)")
    order = serializers.IntegerField(
        required=False,
        default=0,
        min_value=0,
        max_value=9,
        help_text="Display order index (0 is thumbnail/primary photo, max 9 for 10 total images).",
    )


# Backward-compatible aliases
ProductSizeSerializer = ProductVariantSizeSerializer
ProductImageSerializer = ProductVariantImageSerializer


class ProductCardSerializer(serializers.ModelSerializer):
    """
    Compact product card returned in search results, public browsing, and recommendations.
    Returns thumbnail (first image of first variant), pricing, category, and available sizes.
    """
    thumbnail = serializers.CharField(read_only=True, allow_null=True)
    image_url = serializers.CharField(read_only=True, allow_null=True)
    store_name = serializers.CharField(source="store.name", read_only=True)
    store_slug = serializers.CharField(source="store.slug", read_only=True)
    category_display = serializers.CharField(source="get_category_display", read_only=True)
    product_type_display = serializers.CharField(source="get_product_type_display", read_only=True)
    is_in_stock = serializers.BooleanField(read_only=True)
    available_sizes = serializers.ListField(child=serializers.CharField(), read_only=True)
    average_rating = serializers.FloatField(read_only=True)
    review_count = serializers.IntegerField(read_only=True)
    total_stock = serializers.IntegerField(read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "store",
            "store_name",
            "store_slug",
            "name",
            "slug",
            "price",
            "compare_at_price",
            "currency",
            "thumbnail",
            "image_url",
            "product_type",
            "product_type_display",
            "category",
            "category_display",
            "color",
            "stock",
            "total_stock",
            "is_in_stock",
            "available_sizes",
            "average_rating",
            "review_count",
            "created_at",
        ]


class PublicProductDetailSerializer(serializers.ModelSerializer):
    """
    Full public product detail view including description, variants,
    sizes, rating stats, and tryon readiness.
    """
    thumbnail = serializers.CharField(read_only=True, allow_null=True)
    image_url = serializers.CharField(read_only=True, allow_null=True)
    store_name = serializers.CharField(source="store.name", read_only=True)
    store_slug = serializers.CharField(source="store.slug", read_only=True)
    category_display = serializers.CharField(source="get_category_display", read_only=True)
    product_type_display = serializers.CharField(source="get_product_type_display", read_only=True)
    variants = ProductVariantSerializer(many=True, read_only=True)
    available_sizes = serializers.ListField(child=serializers.CharField(), read_only=True)
    is_in_stock = serializers.BooleanField(read_only=True)
    total_stock = serializers.IntegerField(read_only=True)
    average_rating = serializers.FloatField(read_only=True)
    review_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "store",
            "store_name",
            "store_slug",
            "name",
            "slug",
            "description",
            "product_type",
            "product_type_display",
            "category",
            "category_display",
            "color",
            "price",
            "compare_at_price",
            "currency",
            "sku",
            "stock",
            "total_stock",
            "is_in_stock",
            "average_rating",
            "review_count",
            "thumbnail",
            "image_url",
            "variants",
            "available_sizes",
            "created_at",
            "updated_at",
        ]


class ProductSerializer(serializers.ModelSerializer):
    """
    Seller-facing product detail serializer including product_type, category,
    variants breakdown, computed total stock, embedded AR status, and review stats.
    """
    thumbnail = serializers.CharField(read_only=True, allow_null=True)
    image_url = serializers.CharField(read_only=True, allow_null=True)
    store_name = serializers.CharField(source="store.name", read_only=True)
    category_display = serializers.CharField(source="get_category_display", read_only=True)
    product_type_display = serializers.CharField(source="get_product_type_display", read_only=True)
    variants = ProductVariantSerializer(many=True, read_only=True)
    available_sizes = serializers.ListField(child=serializers.CharField(), read_only=True)
    total_stock = serializers.IntegerField(read_only=True)
    ar_status = serializers.JSONField(read_only=True)
    average_rating = serializers.FloatField(read_only=True)
    review_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "store",
            "store_name",
            "name",
            "slug",
            "product_type",
            "product_type_display",
            "category",
            "category_display",
            "description",
            "price",
            "compare_at_price",
            "currency",
            "sku",
            "stock",
            "total_stock",
            "thumbnail",
            "image_url",
            "variants",
            "available_sizes",
            "ar_status",
            "average_rating",
            "review_count",
            "status",
            "created_at",
            "updated_at",
        ]


class ProductCreateSerializer(serializers.ModelSerializer):
    product_type = serializers.ChoiceField(
        choices=ProductType.choices,
        default=ProductType.MEN,
        help_text="Product target category: MEN, WOMEN, KIDS, ETHNIC, TRADITIONAL, FORMAL, SPORTS.",
    )
    category = serializers.ChoiceField(
        choices=ProductCategory.choices,
        default=ProductCategory.TOP,
        help_text=(
            "Apparel category: ONE_PIECE (One-Piece), TOP (Top), BOTTOM (Bottom), "
            "FOOTWEAR (Footwear), HEADWEAR (Headwear), EYEWEAR (Eyewear), "
            "OUTERWEAR (Outerwear), ACCESSORIES (Accessories)."
        ),
    )
    variants = serializers.ListField(
        child=VariantInputSerializer(),
        required=False,
        default=list,
        help_text="List of color variants, each with its own hex code (#RRGGBB) and size/stock configuration.",
    )
    # Backward compatibility: allow top-level sizes / color
    sizes = serializers.ListField(
        child=VariantSizeInputSerializer(),
        required=False,
        default=list,
        help_text="Legacy direct size array (automatically converted into a default color variant).",
    )

    class Meta:
        model = Product
        fields = [
            "name",
            "product_type",
            "category",
            "description",
            "price",
            "compare_at_price",
            "currency",
            "sku",
            "status",
            "variants",
            "sizes",
            "color",
        ]
        extra_kwargs = {
            "name": {"min_length": 2},
            "price": {"min_value": 0},
            "color": {"required": False, "allow_blank": True, "default": ""},
            "description": {"required": False, "allow_blank": True, "default": ""},
            "compare_at_price": {"required": False, "allow_null": True, "default": None},
            "currency": {"required": False, "default": "INR"},
            "sku": {"required": False, "allow_blank": True, "default": ""},
            "status": {"required": False, "default": ProductStatus.DRAFT},
        }

    def validate_name(self, value):
        return value.strip()

    def validate(self, attrs):
        category = attrs.get("category", ProductCategory.TOP)
        variants = attrs.get("variants", [])
        sizes = attrs.get("sizes", [])

        # Validate variant colors and nested sizes
        if variants:
            seen_colors = set()
            for v_idx, variant in enumerate(variants):
                c_name = variant.get("color_name", "").strip().lower()
                if c_name in seen_colors:
                    raise serializers.ValidationError({"variants": f"Duplicate color '{variant.get('color_name')}' provided."})
                seen_colors.add(c_name)

                # Validate sizes inside variant
                v_sizes = variant.get("sizes", [])
                seen_sizes = set()
                for s_item in v_sizes:
                    s_name = (s_item.get("size") or "").strip().upper()
                    if s_name in seen_sizes:
                        raise serializers.ValidationError({"variants": f"Duplicate size '{s_name}' for color '{variant.get('color_name')}'."})
                    seen_sizes.add(s_name)

                    if category in CLOTHING_CATEGORIES:
                        if s_name not in CLOTHING_SIZES:
                            raise serializers.ValidationError(
                                {"variants": f"Size '{s_name}' is invalid for clothing. Allowed: {', '.join(CLOTHING_SIZES)}."}
                            )
                    elif category in FOOTWEAR_CATEGORIES:
                        if s_name not in FOOTWEAR_SIZES:
                            raise serializers.ValidationError(
                                {"variants": f"Shoe size '{s_name}' is invalid for Footwear. Allowed: {', '.join(FOOTWEAR_SIZES)}."}
                            )
                    elif category in NO_SIZE_CATEGORIES:
                        if s_name and s_name not in ["ONE_SIZE", "NO_SIZE"]:
                            raise serializers.ValidationError(
                                {"variants": f"Category '{category}' has no sizes. Leave size blank."}
                            )

        # Validate legacy top-level sizes if passed
        elif sizes:
            seen_sizes = set()
            for s_item in sizes:
                s_name = (s_item.get("size") or "").strip().upper()
                if s_name in seen_sizes:
                    raise serializers.ValidationError({"sizes": f"Duplicate size '{s_name}' provided."})
                seen_sizes.add(s_name)

                if category in CLOTHING_CATEGORIES:
                    if s_name not in CLOTHING_SIZES:
                        raise serializers.ValidationError(
                            {"sizes": f"Size '{s_name}' is invalid for clothing. Allowed: {', '.join(CLOTHING_SIZES)}."}
                        )
                elif category in FOOTWEAR_CATEGORIES:
                    if s_name not in FOOTWEAR_SIZES:
                        raise serializers.ValidationError(
                            {"sizes": f"Shoe size '{s_name}' is invalid for Footwear. Allowed: {', '.join(FOOTWEAR_SIZES)}."}
                        )

        return attrs


class ProductUpdateSerializer(ProductCreateSerializer):
    status = serializers.ChoiceField(choices=ProductStatus.choices, required=False)

    class Meta(ProductCreateSerializer.Meta):
        extra_kwargs = {
            field: {"required": False}
            for field in ProductCreateSerializer.Meta.fields
            if field not in ("variants", "sizes")
        }


class BulkProductItemUpdateSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(min_value=1)
    status = serializers.ChoiceField(choices=ProductStatus.choices, required=False)
    category = serializers.ChoiceField(choices=ProductCategory.choices, required=False)
    product_type = serializers.ChoiceField(choices=ProductType.choices, required=False)
    color = serializers.CharField(max_length=50, required=False, allow_blank=True)
    price = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)
    compare_at_price = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)


class BulkProductUpdateSerializer(serializers.Serializer):
    items = serializers.ListField(
        child=BulkProductItemUpdateSerializer(),
        allow_empty=False,
    )


class ProductListingStatusSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    active = serializers.IntegerField()
    draft = serializers.IntegerField()
    inactive = serializers.IntegerField()
    in_stock = serializers.IntegerField()
    out_of_stock = serializers.IntegerField()


class CategoryItemSerializer(serializers.Serializer):
    code = serializers.ChoiceField(
        choices=ProductCategory.choices,
        help_text=(
            "Unique category enum code (e.g. 'ONE_PIECE', 'TOP', 'BOTTOM', 'FOOTWEAR', "
            "'HEADWEAR', 'EYEWEAR', 'OUTERWEAR', 'ACCESSORIES'). "
            "Pass this code to public product list endpoints via query param `?category=<code>` or `?type=<code>`."
        ),
    )
    name = serializers.CharField(
        help_text="Human-readable category display title (e.g. 'One-Piece', 'Top', 'Bottom', 'Footwear')."
    )
    products_count = serializers.IntegerField(
        help_text="Total number of active, non-archived products currently cataloged under this category."
    )


class SearchHistorySerializer(serializers.ModelSerializer):
    """Serializer for user search history records."""
    class Meta:
        model = SearchHistory
        fields = [
            "id",
            "query",
            "search_count",
            "last_searched_at",
            "created_at",
        ]
