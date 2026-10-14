from rest_framework import serializers

from .models import Product, ProductStatus


class ProductSerializer(serializers.ModelSerializer):
    image_url = serializers.CharField(read_only=True, allow_null=True)
    store_name = serializers.CharField(source="store.name", read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "store",
            "store_name",
            "name",
            "slug",
            "description",
            "image_url",
            "price",
            "compare_at_price",
            "currency",
            "sku",
            "stock",
            "status",
            "created_at",
            "updated_at",
        ]


class PublicProductSerializer(serializers.ModelSerializer):
    image_url = serializers.CharField(read_only=True, allow_null=True)
    store_name = serializers.CharField(source="store.name", read_only=True)
    store_slug = serializers.CharField(source="store.slug", read_only=True)

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
            "image_url",
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
    description = serializers.CharField(required=False, allow_blank=True, default="")
    price = serializers.DecimalField(max_digits=10, decimal_places=2)
    compare_at_price = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, allow_null=True, default=None,
    )
    currency = serializers.CharField(max_length=3, required=False, default="INR")
    sku = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    stock = serializers.IntegerField(required=False, default=0, min_value=0)
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


class ProductUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    price = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)
    compare_at_price = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, allow_null=True,
    )
    currency = serializers.CharField(max_length=3, required=False)
    sku = serializers.CharField(max_length=100, required=False, allow_blank=True)
    stock = serializers.IntegerField(required=False, min_value=0)
    status = serializers.ChoiceField(
        choices=[ProductStatus.DRAFT, ProductStatus.ACTIVE, ProductStatus.INACTIVE],
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


class BulkProductItemUpdateSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(min_value=1)
    status = serializers.ChoiceField(
        choices=[ProductStatus.DRAFT, ProductStatus.ACTIVE, ProductStatus.INACTIVE],
        required=False,
    )
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

