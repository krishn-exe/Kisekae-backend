import logging
from django.core.management.base import BaseCommand
from django.db import transaction

from products.models import Product, ProductImage, ProductEmbedding, ProductSize
from tryon.models import TryOnProductAsset, ComboItem, TryOnRequest

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Purges all seeded demo products (by SKU prefix 'KIS-') along with their gallery images, embeddings, try-on assets, and associated files."

    def add_arguments(self, parser):
        parser.add_argument(
            "--store-id",
            type=int,
            default=None,
            help="Filter purge to a specific Store ID (e.g. --store-id 21). If omitted, purges across all stores.",
        )
        parser.add_argument(
            "--prefix",
            type=str,
            default="KIS-",
            help="SKU prefix identifying demo products (default: 'KIS-').",
        )
        parser.add_argument(
            "--delete-files",
            action="store_true",
            default=True,
            help="Also delete underlying image files from storage/S3 (default: True).",
        )

    def handle(self, *args, **options):
        store_id = options.get("store_id")
        prefix = options.get("prefix", "KIS-")
        delete_files = options.get("delete_files", True)

        self.stdout.write(self.style.MIGRATE_HEADING("=== Kisekae Demo Products Purge ==="))

        products_qs = Product.objects.filter(sku__startswith=prefix)
        if store_id:
            products_qs = products_qs.filter(store_id=store_id)
            self.stdout.write(f"Targeting Store #{store_id} with SKU prefix '{prefix}'...")
        else:
            self.stdout.write(f"Targeting ALL stores with SKU prefix '{prefix}'...")

        total_products = products_qs.count()
        if total_products == 0:
            self.stdout.write(self.style.WARNING(f"No products found matching SKU prefix '{prefix}'."))
            return

        self.stdout.write(f"Found {total_products} demo products to purge.")

        product_ids = list(products_qs.values_list("id", flat=True))

        # 1. Clean associated files from storage if requested
        files_deleted = 0
        if delete_files:
            self.stdout.write("Deleting image files from storage...")
            # Gallery images
            gallery_images = ProductImage.objects.filter(product_id__in=product_ids)
            for p_img in gallery_images:
                if p_img.image:
                    try:
                        p_img.image.delete(save=False)
                        files_deleted += 1
                    except Exception as e:
                        logger.warning(f"Failed to delete gallery image file: {e}")

            # Primary thumbnails
            for p in products_qs:
                if p.image:
                    try:
                        p.image.delete(save=False)
                        files_deleted += 1
                    except Exception as e:
                        logger.warning(f"Failed to delete product thumbnail file: {e}")

        # 2. Database cascading cleanup
        with transaction.atomic():
            # Delete TryOnRequests targeting these products
            tryon_reqs_count, _ = TryOnRequest.objects.filter(product_id__in=product_ids).delete()

            # Delete ComboItems referencing these products
            combo_items_count, _ = ComboItem.objects.filter(product_id__in=product_ids).delete()

            # Delete TryOnProductAssets
            assets_count, _ = TryOnProductAsset.objects.filter(product_id__in=product_ids).delete()

            # Delete ProductEmbeddings
            embeddings_count, _ = ProductEmbedding.objects.filter(product_id__in=product_ids).delete()

            # Delete ProductSizes
            sizes_count, _ = ProductSize.objects.filter(product_id__in=product_ids).delete()

            # Delete ProductImages
            images_count, _ = ProductImage.objects.filter(product_id__in=product_ids).delete()

            # Delete Products
            products_count, _ = products_qs.delete()

        self.stdout.write(self.style.SUCCESS("\n=== Purge Completed Successfully! ==="))
        self.stdout.write(f" - Products deleted: {products_count}")
        self.stdout.write(f" - Gallery images deleted: {images_count}")
        self.stdout.write(f" - Sizes deleted: {sizes_count}")
        self.stdout.write(f" - Vector embeddings deleted: {embeddings_count}")
        self.stdout.write(f" - AR Try-On assets deleted: {assets_count}")
        self.stdout.write(f" - TryOn requests cleaned: {tryon_reqs_count}")
        self.stdout.write(f" - Combo items removed: {combo_items_count}")
        if delete_files:
            self.stdout.write(f" - Storage files deleted: {files_deleted}")
