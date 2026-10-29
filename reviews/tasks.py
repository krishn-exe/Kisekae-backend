import logging
from celery import shared_task
from django.db import transaction
from products.models import Product, ProductStatus
from .ai_service import ReviewAIService
from .models import ProductReviewSummary, Review

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def generate_product_review_summary_task(self, product_id: int):
    """
    Generates an AI summary for a single active product with customer reviews.
    """
    try:
        product = Product.objects.filter(pk=product_id, status=ProductStatus.ACTIVE).first()
        if not product:
            logger.info(f"Product #{product_id} is not active or does not exist.")
            return False

        reviews_qs = Review.objects.filter(product=product).values(
            "rating", "title", "description", "is_verified_buyer"
        )
        reviews_list = [
            {
                "rating": r["rating"],
                "title": r["title"],
                "description": r["description"],
                "is_verified_buyer": r["is_verified_buyer"],
            }
            for r in reviews_qs
        ]

        if not reviews_list:
            logger.info(f"Product #{product_id} has no customer reviews; skipping summary.")
            return False

        summary_data = ReviewAIService.generate_review_summary(product.name, reviews_list)

        with transaction.atomic():
            ProductReviewSummary.objects.update_or_create(
                product=product,
                defaults={
                    "summary_text": summary_data.get("summary_text", ""),
                    "pros": summary_data.get("pros", []),
                    "cons": summary_data.get("cons", []),
                    "sizing_verdict": summary_data.get("sizing_verdict", ""),
                    "sentiment_distribution": summary_data.get("sentiment_distribution", {}),
                    "review_count_at_summary": len(reviews_list),
                },
            )
        logger.info(f"Successfully generated review summary for Product #{product_id} ({product.name}).")
        return True
    except Exception as exc:
        logger.error(f"Failed generating review summary for Product #{product_id}: {exc}")
        raise self.retry(exc=exc)


@shared_task
def refresh_all_product_review_summaries_task():
    """
    Periodic Celery task scheduled once every 24 hours.
    Processes only active products that have customer reviews.
    """
    products_with_reviews = (
        Product.objects.filter(status=ProductStatus.ACTIVE, review_count__gt=0)
        .values_list("id", flat=True)
    )

    dispatched = 0
    for product_id in products_with_reviews:
        generate_product_review_summary_task.delay(product_id)
        dispatched += 1

    logger.info(f"Dispatched 24-hour review summary generation for {dispatched} products.")
    return dispatched
