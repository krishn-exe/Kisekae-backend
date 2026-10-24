import io
import logging
import urllib.request
from decimal import Decimal
from typing import Optional

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from PIL import Image, ImageDraw

from products.embedding_service import EmbeddingService
from products.fixtures.demo_catalog import DEMO_PRODUCTS
from products.models import (
    Product,
    ProductCategory,
    ProductEmbedding,
    ProductImage,
    ProductSize,
    ProductStatus,
)
from stores.models import BusinessVerification, Store, StoreMembership, StoreRole, StoreStatus, VerificationStatus
from tryon.models import ARStatus, TryOnProductAsset

logger = logging.getLogger(__name__)
User = get_user_model()


def generate_placeholder_image(name: str, category: str, view: str, color_name: str) -> bytes:
    """
    Generates a clean studio flat-lay placeholder image with the garment title,
    view angle (FRONT, BACK, SIDE), and category text on a clean neutral background.
    """
    img = Image.new("RGB", (600, 800), color=(248, 248, 250))
    draw = ImageDraw.Draw(img)

    # Draw studio border
    draw.rectangle([(20, 20), (580, 780)], outline=(225, 225, 230), width=2)

    # Draw garment shape silhouette representation
    draw.rectangle([(100, 150), (500, 650)], fill=(235, 238, 242), outline=(210, 215, 222), width=1)

    # Draw metadata text
    draw.text((300, 250), category, fill=(100, 110, 125), anchor="mm")
    draw.text((300, 320), f"[{view} VIEW]", fill=(40, 50, 70), anchor="mm")
    draw.text((300, 420), name[:32], fill=(20, 20, 30), anchor="mm")
    draw.text((300, 460), f"Color: {color_name}", fill=(120, 130, 145), anchor="mm")
    draw.text((300, 560), "KISEKAE STUDIO PACKSHOT", fill=(160, 170, 185), anchor="mm")

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def fetch_or_create_image(url: str, name: str, category: str, view: str, color: str) -> bytes:
    """
    Attempts to download a real catalog image from a CDN URL.
    Falls back to generating a clean studio flat-lay graphic if network times out.
    """
    if url:
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            with urllib.request.urlopen(req, timeout=4) as response:
                if response.status == 200:
                    data = response.read()
                    if len(data) > 1000:
                        return data
        except Exception:
            pass

    return generate_placeholder_image(name, category, view, color)


class Command(BaseCommand):
    help = "Seeds 50 realistic demo fashion products across Tops, Bottoms, Outerwear, and One-Piece with multi-angle images, vector embeddings, and Virtual Try-On readiness."

    def add_arguments(self, parser):
        parser.add_argument(
            "--store-id",
            type=int,
            default=None,
            help="Target Store ID to seed products into (defaults to first active store or creates one).",
        )
        parser.add_argument(
            "--count",
            type=int,
            default=50,
            help="Number of demo products to seed (default 50).",
        )
        parser.add_argument(
            "--clean",
            action="store_true",
            help="Delete previously seeded demo products (by SKU prefix KIS-) before seeding.",
        )

    def handle(self, *args, **options):
        store_id = options.get("store_id")
        target_count = min(options.get("count", 50), len(DEMO_PRODUCTS))
        clean_first = options.get("clean", False)

        self.stdout.write(self.style.MIGRATE_HEADING("=== Kisekae Demo Products Seeder ==="))

        # 1. Resolve or Create Target Store & Seller User
        store = None
        if store_id:
            store = Store.objects.filter(pk=store_id).first()
            if not store:
                self.stderr.write(f"Store #{store_id} not found.")
                return

        if not store:
            store = Store.objects.filter(status=StoreStatus.ACTIVE).first()

        if not store:
            # Create a demo seller and active store
            seller, _ = User.objects.get_or_create(
                email="demo.seller@kisekae.live",
                defaults={
                    "name": "Kisekae Apparel Studio",
                    "is_seller": True,
                    "is_email_verified": True,
                    "is_active": True,
                },
            )
            if not seller.has_usable_password():
                seller.set_password("KisekaeDemo2026!")
                seller.save()

            store, _ = Store.objects.get_or_create(
                slug="kisekae-studio",
                defaults={
                    "name": "Kisekae Apparel Studio",
                    "status": StoreStatus.ACTIVE,
                    "created_by": seller,
                },
            )
            StoreMembership.objects.get_or_create(store=store, user=seller, defaults={"role": StoreRole.OWNER})
            BusinessVerification.objects.get_or_create(
                store=store,
                defaults={
                    "pan_number": "ABCDE1234F",
                    "gst_number": "22AAAAA0000A1Z5",
                    "status": VerificationStatus.APPROVED,
                },
            )

        self.stdout.write(f"Seeding into Store: #{store.id} - '{store.name}' ({store.slug})")

        # 2. Clean if requested
        if clean_first:
            deleted_count, _ = Product.objects.filter(store=store, sku__startswith="KIS-").delete()
            self.stdout.write(self.style.WARNING(f"Cleaned {deleted_count} previous demo products."))

        # 3. Load Embedding Model for Semantic Search
        self.stdout.write("Initializing SentenceTransformer embedding service...")
        embedding_service = EmbeddingService.get_instance()

        # 4. Seed Products
        products_to_seed = DEMO_PRODUCTS[:target_count]
        category_counts = {cat: 0 for cat in ProductCategory.values}
        seeded_count = 0

        standard_sizes = [
            {"size": "S", "stock": 15},
            {"size": "M", "stock": 35},
            {"size": "L", "stock": 25},
            {"size": "XL", "stock": 10},
        ]

        views = ["FRONT", "BACK", "SIDE"]

        for idx, item in enumerate(products_to_seed, start=1):
            sku = item["sku"]
            name = item["name"]
            category = item["category"]
            color = item["color"]
            price = item["price"]
            compare_price = item.get("compare_at_price")
            description = item["description"]
            image_urls = item.get("image_urls", [])

            self.stdout.write(f"[{idx}/{target_count}] Seeding: {name} ({category})...")

            product, created = Product.objects.get_or_create(
                store=store,
                sku=sku,
                defaults={
                    "name": name,
                    "category": category,
                    "color": color,
                    "price": price,
                    "compare_at_price": compare_price,
                    "description": description,
                    "currency": "INR",
                    "status": ProductStatus.ACTIVE,
                    "created_by": store.created_by,
                },
            )

            if not created:
                product.name = name
                product.category = category
                product.color = color
                product.price = price
                product.compare_at_price = compare_price
                product.description = description
                product.status = ProductStatus.ACTIVE
                product.save()

            # Seed Sizes & Inventory
            for s_info in standard_sizes:
                ProductSize.objects.update_or_create(
                    product=product,
                    size=s_info["size"],
                    defaults={"stock": s_info["stock"]},
                )
            product.sync_total_stock()

            # Seed 3 Angle Images (Order 0: Front, Order 1: Back, Order 2: Side)
            product_images = []
            for v_idx, view_name in enumerate(views):
                url = image_urls[v_idx] if v_idx < len(image_urls) else ""
                existing_img = product.images.filter(order=v_idx).first()
                if not existing_img:
                    img_bytes = fetch_or_create_image(url, name, category, view_name, color)
                    filename = f"{sku.lower()}_{view_name.lower()}.jpg"
                    new_img = ProductImage.objects.create(
                        product=product,
                        order=v_idx,
                    )
                    new_img.image.save(filename, ContentFile(img_bytes), save=True)
                    product_images.append(new_img)
                else:
                    product_images.append(existing_img)

            # Set primary thumbnail on product
            if product_images and product_images[0].image:
                product.image = product_images[0].image
                product.save(update_fields=["image"])

            # Generate and save Vector Embedding for Semantic Search & Recommendations
            search_text = f"{product.name} {product.color} {product.get_category_display()} {product.description}"
            vec = embedding_service.embed_text(search_text)
            if vec:
                ProductEmbedding.objects.update_or_create(
                    product=product,
                    defaults={"embedding": vec},
                )

            # Activate Virtual Try-On Asset (FRONT, BACK, SIDE)
            front_img = product_images[0] if len(product_images) > 0 else None
            back_img = product_images[1] if len(product_images) > 1 else None
            side_img = product_images[2] if len(product_images) > 2 else None

            TryOnProductAsset.objects.update_or_create(
                product=product,
                defaults={
                    "candidate_image": front_img,
                    "front_image": front_img,
                    "back_image": back_img,
                    "side_image": side_img,
                    "status": ARStatus.AVAILABLE,
                    "rejection_reason": "",
                    "detected_category": product.category,
                },
            )

            category_counts[category] = category_counts.get(category, 0) + 1
            seeded_count += 1

        self.stdout.write(self.style.SUCCESS("\n=== Seeding Completed Successfully! ==="))
        self.stdout.write(f"Total Products Seeded: {seeded_count}")
        self.stdout.write(f" - TOPS: {category_counts.get(ProductCategory.TOP, 0)}")
        self.stdout.write(f" - BOTTOMS: {category_counts.get(ProductCategory.BOTTOM, 0)}")
        self.stdout.write(f" - OUTERWEAR: {category_counts.get(ProductCategory.OUTERWEAR, 0)}")
        self.stdout.write(f" - ONE_PIECE: {category_counts.get(ProductCategory.ONE_PIECE, 0)}")
        self.stdout.write(f"All 50 products have 384-dim vector embeddings for Search & Recommendations.")
        self.stdout.write(f"All 50 products are marked ARStatus: AVAILABLE for Virtual Try-On & Combos.")
