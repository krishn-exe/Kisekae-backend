import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
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


class ProductCategory(models.TextChoices):
    ONE_PIECE = "ONE_PIECE", "One-Piece"
    TOP = "TOP", "Top"
    BOTTOM = "BOTTOM", "Bottom"
    FOOTWEAR = "FOOTWEAR", "Footwear"
    HEADWEAR = "HEADWEAR", "Headwear"
    EYEWEAR = "EYEWEAR", "Eyewear"
    OUTERWEAR = "OUTERWEAR", "Outerwear"
    ACCESSORIES = "ACCESSORIES", "Accessories"


CLOTHING_CATEGORIES = {
    ProductCategory.TOP,
    ProductCategory.BOTTOM,
    ProductCategory.ONE_PIECE,
    ProductCategory.OUTERWEAR,
}
FOOTWEAR_CATEGORIES = {
    ProductCategory.FOOTWEAR,
}

CLOTHING_SIZES = ["S", "M", "L", "XL"]
FOOTWEAR_SIZES = ["6", "7", "8", "9", "10"]


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
    category = models.CharField(
        max_length=30,
        choices=ProductCategory.choices,
        default=ProductCategory.TOP,
        db_index=True,
        help_text="Product clothing category / type.",
    )
    color = models.CharField(
        max_length=50,
        blank=True,
        default="",
        db_index=True,
        help_text="Primary color of the product (e.g. Black, White, Navy Blue).",
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
        help_text="Available inventory count (sum across all sizes when sizes are configured).",
    )
    status = models.CharField(
        max_length=20,
        choices=ProductStatus.choices,
        default=ProductStatus.DRAFT,
        db_index=True,
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
        return self.stock > 0

    @property
    def thumbnail(self):
        """Returns the URL of the first image of the product (thumbnail)."""
        if hasattr(self, "_prefetched_objects_cache") and "images" in self._prefetched_objects_cache:
            images = list(self.images.all())
            if images and images[0].image and hasattr(images[0].image, "url"):
                return images[0].image.url
        else:
            first_img = self.images.first()
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
        """Returns list of sizes currently in stock."""
        if hasattr(self, "_prefetched_objects_cache") and "sizes" in self._prefetched_objects_cache:
            return [s.size for s in self.sizes.all() if s.stock > 0]
        return list(self.sizes.filter(stock__gt=0).values_list("size", flat=True))

    def sync_total_stock(self):
        """Updates product.stock to match the sum of its size stocks if sizes exist."""
        if self.sizes.exists():
            total = self.sizes.aggregate(total=models.Sum("stock"))["total"] or 0
            self.stock = total
            self.save(update_fields=["stock", "updated_at"])

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
            # Fallback if multiple concurrent retries still collided
            self.slug = f"{base_slug}-{uuid.uuid4().hex[:6]}"
            return super().save(*args, **kwargs)

        super().save(*args, **kwargs)


class ProductSize(models.Model):
    """
    Inventory stock for a specific size of a product.
    For Clothing: S, M, L, XL
    For Footwear: 6, 7, 8, 9, 10
    """
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="sizes",
    )
    size = models.CharField(
        max_length=20,
        db_index=True,
        help_text="Size designation (e.g. S, M, L, XL for clothing; 6-10 for shoes).",
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
                fields=["product", "size"],
                name="unique_product_size",
            ),
        ]

    def __str__(self):
        return f"{self.product.name} - Size {self.size} ({self.stock} in stock)"

    def clean(self):
        super().clean()
        if self.size:
            self.size = str(self.size).strip().upper()
        if hasattr(self, "product") and self.product and self.product.category:
            cat = self.product.category
            if cat in CLOTHING_CATEGORIES:
                allowed = CLOTHING_SIZES + ["XS", "XXL", "FREE_SIZE", "ONE_SIZE"]
                if self.size not in allowed:
                    raise ValidationError(
                        {"size": f"Invalid clothing size '{self.size}'. Allowed: {', '.join(CLOTHING_SIZES)}."}
                    )
            elif cat in FOOTWEAR_CATEGORIES:
                if self.size not in FOOTWEAR_SIZES:
                    raise ValidationError(
                        {"size": f"Invalid shoe size '{self.size}'. Allowed: {', '.join(FOOTWEAR_SIZES)}."}
                    )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class ProductImage(models.Model):
    """
    Multiple image support for product listings (up to 10 images per product).
    """
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
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
