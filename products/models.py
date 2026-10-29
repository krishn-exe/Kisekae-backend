import re
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import IntegrityError, models, transaction
from django.utils.text import slugify
from pgvector.django import HnswIndex, VectorField


def product_image_upload_path(instance, filename):
    ext = filename.split(".")[-1].lower() if "." in filename else "jpg"
    return f"products/{instance.store_id}/{uuid.uuid4().hex}.{ext}"


def product_gallery_image_upload_path(instance, filename):
    ext = filename.split(".")[-1].lower() if "." in filename else "jpg"
    store_id = instance.product.store_id if instance.product_id else "common"
    return f"products/{store_id}/gallery/{uuid.uuid4().hex}.{ext}"


class ProductStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    ACTIVE = "ACTIVE", "Active"
    INACTIVE = "INACTIVE", "Inactive"


class ProductType(models.TextChoices):
    MEN = "MEN", "Men"
    WOMEN = "WOMEN", "Women"
    KIDS = "KIDS", "Kids"
    ETHNIC = "ETHNIC", "Ethnic"
    TRADITIONAL = "TRADITIONAL", "Traditional"
    FORMAL = "FORMAL", "Formal"
    SPORTS = "SPORTS", "Sports"


class ProductCategory(models.TextChoices):
    ONE_PIECE = "ONE_PIECE", "One-Piece"
    TOP = "TOP", "Top"
    BOTTOM = "BOTTOM", "Bottom"
    FOOTWEAR = "FOOTWEAR", "Footwear"
    HEADWEAR = "HEADWEAR", "Headwear"
    EYEWEAR = "EYEWEAR", "Eyewear"
    OUTERWEAR = "OUTERWEAR", "Outerwear"
    ACCESSORIES = "ACCESSORIES", "Accessories"


def normalize_category_param(val):
    """Safely normalizes input category string to ProductCategory enum code."""
    if not val:
        return None
    val_clean = str(val).strip().replace(" ", "_").replace("-", "_").upper()
    for choice in ProductCategory:
        if val_clean == choice.value or val_clean == choice.label.replace(" ", "_").replace("-", "_").upper():
            return choice.value
    return None


CLOTHING_CATEGORIES = {
    ProductCategory.TOP,
    ProductCategory.BOTTOM,
    ProductCategory.ONE_PIECE,
    ProductCategory.OUTERWEAR,
}
FOOTWEAR_CATEGORIES = {
    ProductCategory.FOOTWEAR,
}
NO_SIZE_CATEGORIES = {
    ProductCategory.HEADWEAR,
    ProductCategory.EYEWEAR,
    ProductCategory.ACCESSORIES,
}

CLOTHING_SIZES = ["XS", "S", "M", "L", "XL"]
FOOTWEAR_SIZES = ["6", "7", "8", "9", "10", "11", "12"]

HEX_COLOR_REGEX = re.compile(r"^#([A-Fa-f0-9]{6})$")
hex_color_validator = RegexValidator(
    regex=HEX_COLOR_REGEX,
    message="Enter a valid 6-character hex color code starting with '#' (e.g. #000080 or #FFFFFF).",
)


class ProductQuerySet(models.QuerySet):
    def active(self):
        return self.filter(status=ProductStatus.ACTIVE)

    def for_store(self, store):
        return self.filter(store=store)


class Product(models.Model):
    store = models.ForeignKey(
        "stores.Store",
        on_delete=models.CASCADE,
        related_name="products",
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=300, db_index=True)
    product_type = models.CharField(
        max_length=20,
        choices=ProductType.choices,
        default=ProductType.MEN,
        db_index=True,
        help_text="Product target demographic / category type.",
    )
    category = models.CharField(
        max_length=30,
        choices=ProductCategory.choices,
        default=ProductCategory.TOP,
        db_index=True,
        help_text="Product clothing category / apparel structure.",
    )
    color = models.CharField(
        max_length=50,
        blank=True,
        default="",
        db_index=True,
        help_text="Primary color descriptor (derived from variants).",
    )
    description = models.TextField(blank=True, default="")
    image = models.ImageField(
        upload_to=product_image_upload_path,
        null=True,
        blank=True,
        help_text="Primary / legacy product thumbnail image.",
    )
    price = models.DecimalField(max_digits=10, decimal_places=2)
    compare_at_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Original price before discount, shown as strikethrough.",
    )
    currency = models.CharField(max_length=3, default="INR")
    sku = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Stock Keeping Unit identifier.",
    )
    stock = models.PositiveIntegerField(
        default=0,
        help_text="Available inventory count (sum across all variant sizes).",
    )
    average_rating = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=Decimal("0.00"),
        db_index=True,
        help_text="Average review rating from 1.00 to 5.00.",
    )
    review_count = models.PositiveIntegerField(
        default=0,
        db_index=True,
        help_text="Total number of customer reviews.",
    )
    status = models.CharField(
        max_length=20,
        choices=ProductStatus.choices,
        default=ProductStatus.DRAFT,
        db_index=True,
    )
    is_refundable = models.BooleanField(
        default=True,
        help_text="Seller option: whether this product is eligible for return and refund post-delivery.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_products",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ProductQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["store", "slug"],
                name="unique_product_slug_per_store",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.store.name})"

    @property
    def is_active(self):
        return self.status == ProductStatus.ACTIVE

    @property
    def is_in_stock(self):
        return self.total_stock > 0

    @property
    def total_stock(self):
        """Sum of all variant size stocks."""
        if hasattr(self, "_prefetched_objects_cache") and "variants" in self._prefetched_objects_cache:
            return sum(v.total_stock for v in self.variants.all())
        total = self.sizes.aggregate(total=models.Sum("stock"))["total"]
        if total is not None:
            return total
        return self.stock or 0

    @property
    def thumbnail(self):
        """Returns the URL of the first image of the product (first variant order 0 or gallery image 0)."""
        if hasattr(self, "_prefetched_objects_cache") and "variants" in self._prefetched_objects_cache:
            for variant in self.variants.all():
                thumb = variant.thumbnail
                if thumb:
                    return thumb
        else:
            first_variant = self.variants.first()
            if first_variant:
                thumb = first_variant.thumbnail
                if thumb:
                    return thumb

        if hasattr(self, "_prefetched_objects_cache") and "images" in self._prefetched_objects_cache:
            images = list(self.images.all())
            if images and images[0].image and hasattr(images[0].image, "url"):
                return images[0].image.url
        else:
            first_img = self.images.order_by("order", "id").first()
            if first_img and first_img.image and hasattr(first_img.image, "url"):
                return first_img.image.url
        if self.image and hasattr(self.image, "url"):
            return self.image.url
        return None

    @property
    def image_url(self):
        return self.thumbnail

    @property
    def available_sizes(self):
        """Returns list of sizes currently in stock across all variants."""
        if hasattr(self, "_prefetched_objects_cache") and "sizes" in self._prefetched_objects_cache:
            return sorted(list({s.size for s in self.sizes.all() if s.stock > 0 and s.size}))
        return list(self.sizes.filter(stock__gt=0).exclude(size="").values_list("size", flat=True).distinct())

    @property
    def ar_status(self):
        """Returns AR readiness status data for virtual try-on."""
        if hasattr(self, "tryon_asset") and self.tryon_asset:
            asset = self.tryon_asset
            return {
                "status": asset.status,
                "is_ready_for_tryon": asset.status == "AVAILABLE",
                "rejection_reason": asset.rejection_reason,
                "has_front_image": bool(asset.front_image),
                "has_back_image": bool(asset.back_image),
                "has_side_image": bool(asset.side_image),
            }
        return {
            "status": "NOT_PROCESSED",
            "is_ready_for_tryon": False,
            "rejection_reason": None,
            "has_front_image": False,
            "has_back_image": False,
            "has_side_image": False,
        }

    def sync_total_stock(self):
        """Updates product.stock to match the sum of its size stocks."""
        total = self.sizes.aggregate(total=models.Sum("stock"))["total"] or 0
        self.stock = total
        self.save(update_fields=["stock", "updated_at"])

    def update_rating_stats(self):
        """Updates product.average_rating and product.review_count from related reviews."""
        stats = self.reviews.aggregate(
            avg_rating=models.Avg("rating"),
            total_reviews=models.Count("id"),
        )
        avg = stats["avg_rating"]
        self.average_rating = round(Decimal(str(avg)), 2) if avg is not None else Decimal("0.00")
        self.review_count = stats["total_reviews"] or 0
        self.save(update_fields=["average_rating", "review_count", "updated_at"])

    def _generate_candidate_slug(self, start_counter=0):
        base_slug = slugify(self.name) or "product"
        if start_counter == 0 and not Product.objects.filter(store=self.store, slug=base_slug).exclude(pk=self.pk).exists():
            return base_slug

        counter = max(1, start_counter)
        while Product.objects.filter(store=self.store, slug=f"{base_slug}-{counter}").exclude(pk=self.pk).exists():
            counter += 1
        return f"{base_slug}-{counter}"

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name) or "product"
            counter = 0
            max_retries = 5
            for _ in range(max_retries):
                self.slug = self._generate_candidate_slug(start_counter=counter)
                try:
                    with transaction.atomic():
                        return super().save(*args, **kwargs)
                except IntegrityError as exc:
                    err_str = str(exc).lower()
                    if any(kw in err_str for kw in ["slug", "unique_product_slug", "unique constraint"]):
                        counter += 1
                        continue
                    raise
            self.slug = f"{base_slug}-{uuid.uuid4().hex[:6]}"
            return super().save(*args, **kwargs)

        super().save(*args, **kwargs)


class ProductVariant(models.Model):
    """
    Product color variant. Shares title and description with parent product,
    but possesses distinct color hex, photos gallery, and variant-specific sizes/stock.
    """
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="variants",
    )
    color_name = models.CharField(
        max_length=50,
        help_text="Display title for variant color (e.g. Navy Blue, Jet Black, Crimson).",
    )
    color_hex = models.CharField(
        max_length=7,
        validators=[hex_color_validator],
        help_text="Hex color code in #RRGGBB format (e.g. #000080, #FFFFFF).",
    )
    sku = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Variant-specific Stock Keeping Unit identifier.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "color_name"],
                name="unique_product_variant_color",
            ),
        ]

    def __str__(self):
        return f"{self.product.name} - {self.color_name} ({self.color_hex})"

    @property
    def total_stock(self):
        if hasattr(self, "_prefetched_objects_cache") and "sizes" in self._prefetched_objects_cache:
            return sum(s.stock for s in self.sizes.all())
        return self.sizes.aggregate(total=models.Sum("stock"))["total"] or 0

    @property
    def thumbnail(self):
        if hasattr(self, "_prefetched_objects_cache") and "images" in self._prefetched_objects_cache:
            imgs = list(self.images.all())
            if imgs and imgs[0].image and hasattr(imgs[0].image, "url"):
                return imgs[0].image.url
        first_img = self.images.order_by("order", "id").first()
        if first_img and first_img.image and hasattr(first_img.image, "url"):
            return first_img.image.url
        return None


class ProductSize(models.Model):
    """
    Inventory stock for a specific size of a product variant.
    For Clothing: XS, S, M, L, XL
    For Footwear: 6, 7, 8, 9, 10, 11, 12
    For Accessories/Eyewear: Empty or ONE_SIZE
    """
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="sizes",
    )
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="sizes",
    )
    size = models.CharField(
        max_length=20,
        blank=True,
        default="",
        db_index=True,
        help_text="Size designation (e.g. XS-XL for clothing; 6-12 for shoes).",
    )
    stock = models.PositiveIntegerField(
        default=0,
        help_text="Available inventory count for this specific size.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["variant", "size"],
                name="unique_variant_size",
                condition=models.Q(variant__isnull=False),
            ),
        ]

    def __str__(self):
        return f"{self.product.name} - Size {self.size or 'N/A'} ({self.stock} in stock)"

    def clean(self):
        super().clean()
        if self.size:
            self.size = str(self.size).strip().upper()
        prod = None
        if hasattr(self, "variant") and self.variant and self.variant.product:
            prod = self.variant.product
        elif hasattr(self, "product") and self.product:
            prod = self.product

        if prod and prod.category:
            cat = prod.category
            if cat in CLOTHING_CATEGORIES:
                allowed = ["XS", "S", "M", "L", "XL"]
                if self.size not in allowed:
                    raise ValidationError(
                        {"size": f"Invalid clothing size '{self.size}'. Allowed sizes: {', '.join(allowed)}."}
                    )
            elif cat in FOOTWEAR_CATEGORIES:
                allowed = ["6", "7", "8", "9", "10", "11", "12"]
                if self.size not in allowed:
                    raise ValidationError(
                        {"size": f"Invalid shoe size '{self.size}'. Allowed sizes: {', '.join(allowed)}."}
                    )
            elif cat in NO_SIZE_CATEGORIES:
                if self.size and self.size not in ["", "ONE_SIZE", "NO_SIZE"]:
                    raise ValidationError(
                        {"size": f"Category '{cat}' does not have sizes. Leave size blank."}
                    )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


ProductVariantSize = ProductSize


class ProductImage(models.Model):
    """
    Multiple image support for product variants (up to 10 images per variant).
    Display order 0 is the primary thumbnail.
    """
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="images",
    )
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="images",
    )
    image = models.ImageField(upload_to=product_gallery_image_upload_path)
    order = models.PositiveSmallIntegerField(
        default=0,
        help_text="Display order sequence (0 is thumbnail/first).",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"Image #{self.id} for {self.product.name} (order {self.order})"

    @property
    def image_url(self):
        if self.image and hasattr(self.image, "url"):
            return self.image.url
        return None


ProductVariantImage = ProductImage


class CompatibleHnswIndex(HnswIndex):
    def create_sql(self, model, schema_editor, using="", **kwargs):
        if schema_editor.connection.vendor != "postgresql":
            from django.db.backends.ddl_references import Statement
            return Statement("SELECT 1")
        return super().create_sql(model, schema_editor, using=using, **kwargs)

    def remove_sql(self, model, schema_editor, **kwargs):
        if schema_editor.connection.vendor != "postgresql":
            from django.db.backends.ddl_references import Statement
            return Statement("SELECT 1")
        return super().remove_sql(model, schema_editor, **kwargs)


class ProductEmbedding(models.Model):
    product = models.OneToOneField(
        Product,
        on_delete=models.CASCADE,
        related_name="embedding_record",
    )
    embedding = VectorField(dimensions=384)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            CompatibleHnswIndex(
                name="product_vec_hnsw_idx",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]

    def __str__(self):
        return f"Embedding for Product #{self.product_id} ({self.product.name})"


class SearchHistory(models.Model):
    """
    Stores user search queries with semantic embeddings for search history and recommendation ML.
    Enables recommendations to reflect recent active discovery intent in the same 384-dim vector space.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="search_history",
    )
    query = models.CharField(max_length=255, db_index=True)
    embedding = VectorField(dimensions=384, null=True, blank=True)
    search_count = models.PositiveIntegerField(default=1)
    last_searched_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-last_searched_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "query"],
                name="unique_user_search_query",
            )
        ]
        indexes = [
            models.Index(fields=["user", "-last_searched_at"]),
        ]

    def __str__(self):
        return f"{self.user.email}: '{self.query}' (x{self.search_count})"


class ProductView(models.Model):
    """
    Tracks when a user views a product detail page.
    Supplies passive browsing interest signals to the recommendation ML engine.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="product_views",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="views",
    )
    view_count = models.PositiveIntegerField(default=1)
    first_viewed_at = models.DateTimeField(auto_now_add=True)
    last_viewed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_viewed_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "product"],
                name="unique_user_product_view",
            )
        ]
        indexes = [
            models.Index(fields=["user", "-last_viewed_at"]),
        ]

    def __str__(self):
        return f"{self.user.email} viewed {self.product.name} (x{self.view_count})"


class UserTasteProfile(models.Model):
    """
    Precomputed taste vector for personalized recommendations.
    Synthesizes purchase, cart, wishlist, search history, and view signals into a 384-dim vector.
    Indexed with HNSW for sub-10ms nearest neighbor queries against the product catalog.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="taste_profile",
    )
    embedding = VectorField(dimensions=384)
    interaction_count = models.PositiveIntegerField(
        default=0,
        help_text="Total weighted interactions used to compute this vector.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            CompatibleHnswIndex(
                name="user_taste_hnsw_idx",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]

    def __str__(self):
        return f"Taste Profile for User #{self.user_id} ({self.interaction_count} signals)"
