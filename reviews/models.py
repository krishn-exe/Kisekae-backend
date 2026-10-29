from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Review(models.Model):
    """
    Customer review and 1-to-5 star rating for a product.
    Enforces one review per user per product.
    Automatically identifies verified buyers who purchased and received the product.
    """
    product = models.ForeignKey(
        "products.Product",
        on_delete=models.CASCADE,
        related_name="reviews",
        db_index=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reviews",
        db_index=True,
    )
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        db_index=True,
        help_text="Star rating from 1 to 5.",
    )
    title = models.CharField(
        max_length=255,
        help_text="Headline or title of the review.",
    )
    description = models.TextField(
        help_text="Detailed feedback and thoughts on the product.",
    )
    is_verified_buyer = models.BooleanField(
        default=False,
        db_index=True,
        help_text="True if customer ordered and had this item delivered.",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "user"],
                name="unique_product_user_review",
            )
        ]

    def __str__(self):
        return f"{self.user.name or self.user.email} - {self.product.name} ({self.rating}★)"

    def check_verified_buyer_status(self):
        """Checks if the user has an order containing this product in DELIVERED status."""
        from orders.models import OrderItem, OrderStatus
        return OrderItem.objects.filter(
            order__user=self.user,
            order__status=OrderStatus.DELIVERED,
            product=self.product,
        ).exists()

    def save(self, *args, **kwargs):
        if not self.is_verified_buyer and self.user and self.product:
            self.is_verified_buyer = self.check_verified_buyer_status()

        super().save(*args, **kwargs)
        if self.product:
            self.product.update_rating_stats()

    def delete(self, *args, **kwargs):
        product = self.product
        super().delete(*args, **kwargs)
        if product:
            product.update_rating_stats()


class ProductReviewSummary(models.Model):
    """
    Precomputed daily AI summary of customer reviews for an active product.
    Refreshed every 24 hours by Celery task for products with reviews.
    """
    product = models.OneToOneField(
        "products.Product",
        on_delete=models.CASCADE,
        related_name="review_summary",
        db_index=True,
    )
    summary_text = models.TextField(
        help_text="Concise 2-3 sentence overview of reviewer consensus.",
    )
    pros = models.JSONField(
        default=list,
        help_text="Key praises and positive highlights from reviewers.",
    )
    cons = models.JSONField(
        default=list,
        help_text="Common criticisms or concerns mentioned by reviewers.",
    )
    sizing_verdict = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="Consensus on fit (e.g. 'True to size', 'Runs small by 1 size').",
    )
    sentiment_distribution = models.JSONField(
        default=dict,
        help_text="Estimated sentiment percentages: {'positive': 80, 'neutral': 15, 'negative': 5}",
    )
    review_count_at_summary = models.PositiveIntegerField(
        default=0,
        help_text="Number of reviews evaluated when this summary was generated.",
    )
    generated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-generated_at"]

    def __str__(self):
        return f"AI Review Summary for {self.product.name} ({self.review_count_at_summary} reviews)"
