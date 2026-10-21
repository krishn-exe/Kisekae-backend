from rest_framework import serializers

from .models import (
    CLOTHING_CATEGORIES,
    CLOTHING_SIZES,
    FOOTWEAR_CATEGORIES,
    FOOTWEAR_SIZES,
    Product,
    ProductCategory,
    ProductImage,
    ProductSize,
    ProductStatus,
    SearchHistory,
)


class ProductSizeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductSize
        fields = ["id", "size", "stock"]


class ProductSizeInputSerializer(serializers.Serializer):
    size = serializers.CharField(max_length=20, help_text="Size (e.g. S, M, L, XL or 6-10)")
    stock = serializers.IntegerField(min_value=0, default=0, help_text="Stock quantity for this size")

    def validate_size(self, value):
        return value.strip().upper()


class ProductImageSerializer(serializers.ModelSerializer):
    image_url = serializers.CharField(read_only=True)

    class Meta:
        model = ProductImage
        fields = ["id", "image_url", "order", "created_at"]


class ProductCardSerializer(serializers.ModelSerializer):
    """
    Compact product card returned in search results, public browsing, and recommendations.
    Returns thumbnail (first image), pricing, category, and available sizes.
    """
    thumbnail = serializers.CharField(read_only=True, allow_null=True)
    image_url = serializers.CharField(read_only=True, allow_null=True)
    store_name = serializers.CharField(source="store.name", read_only=True)
    store_slug = serializers.CharField(source="store.slug", read_only=True)
    category_display = serializers.CharField(source="get_category_display", read_only=True)
    is_in_stock = serializers.BooleanField(read_only=True)
    available_sizes = serializers.ListField(child=serializers.CharField(), read_only=True)

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
            "category",
            "category_display",
            "color",
            "stock",
            "is_in_stock",
            "available_sizes",
            "created_at",
        ]


class PublicProductSerializer(ProductCardSerializer):
    """Maintained for backward compatibility."""
    pass


class PublicProductDetailSerializer(serializers.ModelSerializer):
    """
    Full public product detail view including description, multiple gallery images,
    category, color, and size-specific stock breakdowns.
    """
    thumbnail = serializers.CharField(read_only=True, allow_null=True)
    image_url = serializers.CharField(read_only=True, allow_null=True)
    images = ProductImageSerializer(many=True, read_only=True)
    sizes = ProductSizeSerializer(many=True, read_only=True)
    available_sizes = serializers.ListField(child=serializers.CharField(), read_only=True)
    store_name = serializers.CharField(source="store.name", read_only=True)
    store_slug = serializers.CharField(source="store.slug", read_only=True)
    category_display = serializers.CharField(source="get_category_display", read_only=True)
    is_in_stock = serializers.BooleanField(read_only=True)

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
            "category",
            "category_display",
            "color",
            "price",
            "compare_at_price",
            "currency",
            "sku",
            "stock",
            "is_in_stock",
            "thumbnail",
            "image_url",
            "images",
            "sizes",
            "available_sizes",
            "created_at",
            "updated_at",
        ]


class ProductSerializer(serializers.ModelSerializer):
    """
    Seller-facing product detail serializer including status, images, and sizes.
    """
    thumbnail = serializers.CharField(read_only=True, allow_null=True)
    image_url = serializers.CharField(read_only=True, allow_null=True)
    store_name = serializers.CharField(source="store.name", read_only=True)
    category_display = serializers.CharField(source="get_category_display", read_only=True)
    images = ProductImageSerializer(many=True, read_only=True)
    sizes = ProductSizeSerializer(many=True, read_only=True)
    available_sizes = serializers.ListField(child=serializers.CharField(), read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "store",
            "store_name",
            "name",
            "slug",
            "category",
            "category_display",
            "color",
            "description",
            "thumbnail",
            "image_url",
            "images",
            "sizes",
            "available_sizes",
            "price",
            "compare_at_price",
            "currency",
            "sku",
            "stock",
            "status",
            "created_at",
            "updated_at",
        ]


class ProductCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    category = serializers.ChoiceField(
        choices=ProductCategory.choices,
        default=ProductCategory.TOP,
        help_text="Clothing category / type",
    )
    color = serializers.CharField(
        max_length=50,
        required=False,
        allow_blank=True,
        default="",
        help_text="Primary color (e.g. Black, White, Navy)",
    )
    description = serializers.CharField(required=False, allow_blank=True, default="")
    price = serializers.DecimalField(max_digits=10, decimal_places=2)
    compare_at_price = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, allow_null=True, default=None,
    )
    currency = serializers.CharField(max_length=3, required=False, default="INR")
    sku = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    stock = serializers.IntegerField(
        required=False,
        default=0,
        min_value=0,
        help_text="Overall stock (auto-computed from sizes if sizes list is provided)",
    )
    sizes = serializers.ListField(
        child=ProductSizeInputSerializer(),
        required=False,
        default=list,
        help_text="List of sizes and their stock counts: S/M/L/XL for clothing, 6-10 for shoes.",
    )
    status = serializers.ChoiceField(
        choices=[ProductStatus.DRAFT, ProductStatus.ACTIVE],
        required=False,
        default=ProductStatus.DRAFT,
    )

    def validate_name(self, value):
        val = value.strip()
        if len(val) < 2:
            raise serializers.ValidationError("Product name must be at least 2 characters.")
        return val

    def validate_price(self, value):
        if value < 0:
            raise serializers.ValidationError("Price cannot be negative.")
        return value

    def validate(self, attrs):
        category = attrs.get("category", ProductCategory.TOP)
        sizes = attrs.get("sizes", [])
        if sizes:
            seen_sizes = set()
            for item in sizes:
                s_name = item["size"].upper()
                if s_name in seen_sizes:
                    raise serializers.ValidationError({"sizes": f"Duplicate size '{s_name}' provided."})
                seen_sizes.add(s_name)

                if category in CLOTHING_CATEGORIES:
                    allowed = CLOTHING_SIZES + ["XS", "XXL", "FREE_SIZE", "ONE_SIZE"]
                    if s_name not in allowed:
                        raise serializers.ValidationError(
                            {"sizes": f"Size '{s_name}' is invalid for category '{category}'. Allowed: {', '.join(CLOTHING_SIZES)}."}
                        )
                elif category in FOOTWEAR_CATEGORIES:
                    if s_name not in FOOTWEAR_SIZES:
                        raise serializers.ValidationError(
                            {"sizes": f"Shoe size '{s_name}' is invalid for Footwear. Allowed: {', '.join(FOOTWEAR_SIZES)}."}
                        )
        return attrs


class ProductUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    category = serializers.ChoiceField(choices=ProductCategory.choices, required=False)
    color = serializers.CharField(max_length=50, required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)
    price = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)
    compare_at_price = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, allow_null=True,
    )
    currency = serializers.CharField(max_length=3, required=False)
    sku = serializers.CharField(max_length=100, required=False, allow_blank=True)
    stock = serializers.IntegerField(required=False, min_value=0)
    sizes = serializers.ListField(
        child=ProductSizeInputSerializer(),
        required=False,
        help_text="Updated list of sizes and stock counts.",
    )
    status = serializers.ChoiceField(
        choices=ProductStatus.choices,
        required=False,
    )

    def validate_name(self, value):
        val = value.strip()
        if len(val) < 2:
            raise serializers.ValidationError("Product name must be at least 2 characters.")
        return val

    def validate_price(self, value):
        if value < 0:
            raise serializers.ValidationError("Price cannot be negative.")
        return value

    def validate(self, attrs):
        sizes = attrs.get("sizes")
        if sizes is not None:
            seen_sizes = set()
            for item in sizes:
                s_name = item["size"].upper()
                if s_name in seen_sizes:
                    raise serializers.ValidationError({"sizes": f"Duplicate size '{s_name}' provided."})
                seen_sizes.add(s_name)
        return attrs


class BulkProductItemUpdateSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(min_value=1)
    status = serializers.ChoiceField(
        choices=ProductStatus.choices,
        required=False,
    )
    category = serializers.ChoiceField(choices=ProductCategory.choices, required=False)
    color = serializers.CharField(max_length=50, required=False, allow_blank=True)
    price = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)
    compare_at_price = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, allow_null=True
    )


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
