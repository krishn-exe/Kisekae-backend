import uuid

from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.utils.text import slugify


def product_image_upload_path(instance, filename):
    ext = filename.split(".")[-1].lower() if "." in filename else "jpg"
    return f"products/{instance.store_id}/{uuid.uuid4().hex}.{ext}"


class ProductStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    ACTIVE = "ACTIVE", "Active"
    INACTIVE = "INACTIVE", "Inactive"


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
    description = models.TextField(blank=True, default="")
    image = models.ImageField(
        upload_to=product_image_upload_path,
        null=True,
        blank=True,
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
        help_text="Available inventory count.",
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
    def image_url(self):
        if self.image and hasattr(self.image, "url"):
            return self.image.url
        return None

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
