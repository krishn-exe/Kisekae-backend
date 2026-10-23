import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from products.models import ProductCategory

TRYON_SUPPORTED_CATEGORIES = {
    ProductCategory.TOP,
    ProductCategory.BOTTOM,
    ProductCategory.ONE_PIECE,
    ProductCategory.OUTERWEAR,
}

TRYON_UNSUPPORTED_CATEGORIES = {
    ProductCategory.HEADWEAR,
    ProductCategory.EYEWEAR,
    ProductCategory.FOOTWEAR,
    ProductCategory.ACCESSORIES,
}


class ARStatus(models.TextChoices):
    AVAILABLE = "AVAILABLE", "Available"
    PROCESSING = "PROCESSING", "Processing"
    REJECTED = "REJECTED", "Rejected"


def generate_share_code() -> str:
    return uuid.uuid4().hex[:12]


class Combo(models.Model):
    """
    A user-curated outfit / combo of products.
    Enforces strict category constraints for Virtual Try-On eligibility.
    Supports public sharing via Flutter deep link (kisekae://combos/<share_code>)
    and web links (https://live.kisekae.app/combos/<share_code>).
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tryon_combos",
    )
    name = models.CharField(max_length=120, blank=True, default="My Combo")
    share_code = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        null=True,
        blank=True,
        help_text="Unique public share code for deep linking.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.user.email})"

    @property
    def items_count(self):
        return self.items.count()

    @property
    def deep_link(self) -> str:
        """Custom scheme deep link opened directly by the Flutter app."""
        return f"kisekae://combos/{self.share_code}" if self.share_code else ""

    @property
    def share_url(self) -> str:
        """Universal / web link representation."""
        return f"https://live.kisekae.app/combos/{self.share_code}" if self.share_code else ""

    def save(self, *args, **kwargs):
        if not self.share_code:
            self.share_code = generate_share_code()
        super().save(*args, **kwargs)


class ComboItem(models.Model):
    """
    An individual product within a combo.
    Enforces that each combo has at most one item per category.
    """
    combo = models.ForeignKey(
        Combo,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(
        "products.Product",
        on_delete=models.CASCADE,
        related_name="combo_items",
    )
    category = models.CharField(
        max_length=30,
        choices=ProductCategory.choices,
        help_text="Product category denormalized from product for DB-level uniqueness constraint.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["combo", "category"],
                name="unique_category_per_combo",
            ),
            models.UniqueConstraint(
                fields=["combo", "product"],
                name="unique_product_per_combo",
            ),
        ]

    def __str__(self):
        return f"{self.product.name} ({self.category}) in {self.combo.name}"

    def clean(self):
        super().clean()
        if self.product_id and not self.category:
            self.category = self.product.category


class TryOnProductAsset(models.Model):
    """
    Tracks Virtual Try-On enability and multi-angle candidate images for a store product.
    Requires at least 3 angles (Front, Back, Side) in flat-lay/mannequin style for AVAILABLE status.
    """
    product = models.OneToOneField(
        "products.Product",
        on_delete=models.CASCADE,
        related_name="tryon_asset",
    )
    candidate_image = models.ForeignKey(
        "products.ProductImage",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Primary try-on image candidate (defaults to front_image).",
    )
    front_image = models.ForeignKey(
        "products.ProductImage",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Verified Front view flat-lay/mannequin image.",
    )
    back_image = models.ForeignKey(
        "products.ProductImage",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Verified Back view flat-lay/mannequin image.",
    )
    side_image = models.ForeignKey(
        "products.ProductImage",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Verified Side view flat-lay/mannequin image.",
    )
    status = models.CharField(
        max_length=20,
        choices=ARStatus.choices,
        default=ARStatus.PROCESSING,
        db_index=True,
    )
    rejection_reason = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Human-readable reason if status is REJECTED.",
    )
    detected_category = models.CharField(max_length=30, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.product.name} - AR: {self.status}"

    @property
    def has_all_angles(self):
        return bool(self.front_image and self.back_image and self.side_image)

    @property
    def angles_count(self):
        return sum(1 for img in [self.front_image, self.back_image, self.side_image] if img is not None)


class TryOnRequestStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    PROCESSING = "PROCESSING", "Processing"
    READY = "READY", "Ready"
    FAILED = "FAILED", "Failed"


class TryOnAngle(models.TextChoices):
    AUTO = "AUTO", "Auto Detect"
    FRONT = "FRONT", "Front"
    BACK = "BACK", "Back"
    SIDE = "SIDE", "Side"


class TryOnRequest(models.Model):
    """
    Tracks an asynchronous Virtual Try-On generation request submitted by a customer.
    Supports single-garment prediction or sequential combo chaining with Vertex AI.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tryon_requests",
    )
    product = models.ForeignKey(
        "products.Product",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="tryon_requests",
    )
    combo = models.ForeignKey(
        Combo,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="tryon_requests",
    )
    angle = models.CharField(
        max_length=10,
        choices=TryOnAngle.choices,
        default=TryOnAngle.AUTO,
        help_text="Requested perspective for try-on (AUTO, FRONT, BACK, SIDE). Defaults to AUTO.",
    )
    person_image = models.ImageField(upload_to="tryon/user_inputs/")
    result_image = models.ImageField(upload_to="tryon/results/", null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=TryOnRequestStatus.choices,
        default=TryOnRequestStatus.PENDING,
        db_index=True,
    )
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        target = self.product.name if self.product else (self.combo.name if self.combo else "None")
        return f"TryOn {self.id} ({self.status}) for {target}"

    def clean(self):
        super().clean()
        if not self.product and not self.combo:
            raise ValidationError("Either a product or a combo must be provided for Virtual Try-On.")
        if self.product and self.combo:
            raise ValidationError("Cannot specify both a single product and a combo in the same request.")
        if self.product and self.product.category not in TRYON_SUPPORTED_CATEGORIES:
            raise ValidationError(
                f"Category '{self.product.get_category_display()}' is not supported for Virtual Try-On."
            )

