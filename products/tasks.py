import logging

from celery import shared_task
from django.contrib.auth import get_user_model
from django.db.models import F

from .embedding_service import EmbeddingService
from .models import Product, ProductEmbedding, ProductView, SearchHistory

logger = logging.getLogger(__name__)
User = get_user_model()


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def generate_product_embedding_task(self, product_id: int):
    try:
        product = Product.objects.select_related("store").filter(pk=product_id).first()
        if not product:
            logger.warning(f"Product #{product_id} not found for embedding generation.")
            return False

        service = EmbeddingService.get_instance()
        vector = service.embed_product(product)
        if not vector:
            logger.warning(f"Empty vector generated for Product #{product_id}.")
            return False

        ProductEmbedding.objects.update_or_create(
            product=product,
            defaults={"embedding": vector},
        )
        logger.info(f"Generated semantic embedding for Product #{product_id} ({product.name}).")
        return True
    except Exception as exc:
        logger.error(f"Failed generating embedding for Product #{product_id}: {exc}")
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=2, default_retry_delay=5)
def record_search_query_task(self, user_id: int, query: str):
    """
    Asynchronously records a user's search query, generates its 384-dim embedding,
    and updates the user's taste profile.
    """
    try:
        clean_query = query.strip()
        if not clean_query:
            return False

        user = User.objects.filter(pk=user_id).first()
        if not user:
            return False

        service = EmbeddingService.get_instance()
        vector = service.embed_query_cached(clean_query)

        search_record, created = SearchHistory.objects.get_or_create(
            user=user,
            query=clean_query,
            defaults={"embedding": vector, "search_count": 1},
        )
        if not created:
            SearchHistory.objects.filter(pk=search_record.pk).update(
                search_count=F("search_count") + 1,
                embedding=vector or search_record.embedding,
            )

        logger.info(f"Recorded search query '{clean_query}' for user #{user_id}.")
        return True
    except Exception as exc:
        logger.error(f"Failed recording search query for user #{user_id}: {exc}")
        return False


@shared_task(bind=True, max_retries=2, default_retry_delay=5)
def record_product_view_task(self, user_id: int, product_id: int):
    """
    Asynchronously records or increments a user's product view count.
    """
    try:
        user = User.objects.filter(pk=user_id).first()
        product = Product.objects.filter(pk=product_id).first()
        if not user or not product:
            return False

        view_record, created = ProductView.objects.get_or_create(
            user=user,
            product=product,
            defaults={"view_count": 1},
        )
        if not created:
            ProductView.objects.filter(pk=view_record.pk).update(
                view_count=F("view_count") + 1
            )

        logger.info(f"Recorded product view on product #{product_id} by user #{user_id}.")
        return True
    except Exception as exc:
        logger.error(f"Failed recording product view for user #{user_id}: {exc}")
        return False


@shared_task(bind=True, max_retries=2, default_retry_delay=10)
def recompute_user_taste_task(self, user_id: int):
    """
    Recomputes the user's taste profile vector from recent interactions.
    """
    try:
        from .recommendation_service import RecommendationService

        user = User.objects.filter(pk=user_id).first()
        if not user:
            return False

        RecommendationService.update_user_taste_profile(user)
        logger.info(f"Recomputed taste profile for user #{user_id}.")
        return True
    except Exception as exc:
        logger.error(f"Failed recomputing taste profile for user #{user_id}: {exc}")
        return False


@shared_task
def refresh_all_taste_profiles_task():
    """
    Periodic task (nightly) that refreshes taste profiles for active users.
    """
    from .recommendation_service import RecommendationService

    active_user_ids = set(
        SearchHistory.objects.values_list("user_id", flat=True)
    ) | set(
        ProductView.objects.values_list("user_id", flat=True)
    )

    refreshed_count = 0
    for uid in active_user_ids:
        user = User.objects.filter(pk=uid).first()
        if user:
            RecommendationService.update_user_taste_profile(user)
            refreshed_count += 1

    logger.info(f"Nightly refresh complete: updated {refreshed_count} user taste profiles.")
    return refreshed_count
