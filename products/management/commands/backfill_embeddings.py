from django.core.management.base import BaseCommand
from products.models import Product, ProductEmbedding
from products.embedding_service import EmbeddingService
from products.tasks import generate_product_embedding_task


class Command(BaseCommand):
    help = "Generate semantic embeddings for existing products in the catalog."

    def add_arguments(self, parser):
        parser.add_argument(
            "--sync",
            action="store_true",
            help="Generate embeddings synchronously in this process instead of enqueuing Celery tasks.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-generate embeddings for all products, even if an embedding record already exists.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=100,
            help="Batch size for processing products (default: 100).",
        )

    def handle(self, *args, **options):
        is_sync = options["sync"]
        force = options["force"]
        batch_size = options["batch_size"]

        queryset = Product.objects.all().select_related("store")
        if not force:
            queryset = queryset.filter(embedding_record__isnull=True)

        total_count = queryset.count()
        if total_count == 0:
            self.stdout.write(self.style.SUCCESS("No products need embeddings generated."))
            return

        self.stdout.write(
            f"Found {total_count} products needing embeddings. Processing (sync={is_sync}, batch_size={batch_size})..."
        )

        if is_sync:
            service = EmbeddingService.get_instance()
            processed = 0
            for product in queryset.iterator(chunk_size=batch_size):
                vector = service.embed_product(product)
                if vector:
                    ProductEmbedding.objects.update_or_create(
                        product=product,
                        defaults={"embedding": vector},
                    )
                processed += 1
                if processed % 50 == 0 or processed == total_count:
                    self.stdout.write(f"Processed {processed}/{total_count} products...")
            self.stdout.write(self.style.SUCCESS(f"Successfully generated embeddings for {processed} products."))
        else:
            enqueued = 0
            for product_id in queryset.values_list("id", flat=True).iterator(chunk_size=batch_size):
                generate_product_embedding_task.delay(product_id)
                enqueued += 1
                if enqueued % 100 == 0 or enqueued == total_count:
                    self.stdout.write(f"Enqueued {enqueued}/{total_count} products to Celery...")
            self.stdout.write(self.style.SUCCESS(f"Successfully enqueued {enqueued} embedding tasks to Celery."))
