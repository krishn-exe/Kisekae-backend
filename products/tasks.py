import logging

from celery import shared_task

from .embedding_service import EmbeddingService
from .models import Product, ProductEmbedding

logger = logging.getLogger(__name__)


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
