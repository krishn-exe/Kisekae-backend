import logging
from datetime import timedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from products.models import Product, ProductCategory, ProductStatus
from stores.models import StoreStatus

from .models import (
    Combo,
    ComboItem,
    TRYON_SUPPORTED_CATEGORIES,
    TRYON_UNSUPPORTED_CATEGORIES,
    TryOnRequest,
)

logger = logging.getLogger(__name__)


class ComboValidationService:
    """
    Centralized validation rules for Combos and Virtual Try-On outfit composition.
    Enforces category constraints, compatibility rules, and product status checks.
    """

    @classmethod
    def validate_product_eligibility(cls, product: Product) -> None:
        """Validates that a single product is active and in an eligible try-on category."""
        if not product:
            raise ValidationError("Product not found.", code="PRODUCT_NOT_FOUND")

        if product.status != ProductStatus.ACTIVE:
            raise ValidationError(
                f"Product '{product.name}' is not active and cannot be added to a combo.",
                code="PRODUCT_INACTIVE",
            )

        if product.store.status != StoreStatus.ACTIVE or product.store.archived_at is not None:
            raise ValidationError(
                f"Store for product '{product.name}' is not active.",
                code="STORE_INACTIVE",
            )

        if product.category in TRYON_UNSUPPORTED_CATEGORIES or product.category not in TRYON_SUPPORTED_CATEGORIES:
            display_name = product.get_category_display() if hasattr(product, "get_category_display") else product.category
            raise ValidationError(
                f"Category '{display_name}' is not supported for Combos and Virtual Try-On. "
                f"Allowed categories: Top, Bottom, One-Piece, Outerwear.",
                code="UNSUPPORTED_CATEGORY",
            )

    @classmethod
    def validate_category_composition(cls, categories: list[str]) -> None:
        """
        Validates outfit category compatibility rules:
        1. Only eligible categories (TOP, BOTTOM, ONE_PIECE, OUTERWEAR).
        2. At most one product per category.
        3. If ONE_PIECE is present, no TOP or BOTTOM allowed (and vice versa).
        4. OUTERWEAR can combine with anything.
        5. Max 3 items per combo.
        """
        if not categories:
            return

        cat_counts: dict[str, int] = {}
        for cat in categories:
            if cat in TRYON_UNSUPPORTED_CATEGORIES or cat not in TRYON_SUPPORTED_CATEGORIES:
                raise ValidationError(
                    f"Category '{cat}' is not supported for Combos. "
                    f"Allowed categories: {', '.join(sorted(TRYON_SUPPORTED_CATEGORIES))}.",
                    code="UNSUPPORTED_CATEGORY",
                )
            cat_counts[cat] = cat_counts.get(cat, 0) + 1

        # Rule 1: Max one per category
        for cat, count in cat_counts.items():
            if count > 1:
                raise ValidationError(
                    f"A combo cannot contain multiple items of category '{cat}'. Found {count}.",
                    code="DUPLICATE_CATEGORY",
                )

        has_one_piece = ProductCategory.ONE_PIECE in cat_counts
        has_top = ProductCategory.TOP in cat_counts
        has_bottom = ProductCategory.BOTTOM in cat_counts

        # Rule 2: ONE_PIECE vs TOP/BOTTOM mutual exclusivity
        if has_one_piece and (has_top or has_bottom):
            conflicts = []
            if has_top:
                conflicts.append("Top")
            if has_bottom:
                conflicts.append("Bottom")
            raise ValidationError(
                f"A One-Piece cannot be combined with {' or '.join(conflicts)}. "
                "Choose either a One-Piece OR separate Top/Bottom garments.",
                code="ONE_PIECE_CONFLICT",
            )

        # Rule 3: Max outfit size
        if len(categories) > 3:
            raise ValidationError(
                "A combo cannot exceed 3 items (Top + Bottom + Outerwear, or One-Piece + Outerwear).",
                code="MAX_ITEMS_EXCEEDED",
            )

    @classmethod
    def validate_products(cls, product_ids: list[int]) -> list[Product]:
        """
        Validates a list of product IDs:
        - Resolves objects from DB in a single query
        - Validates uniqueness
        - Validates each product's eligibility
        - Validates overall outfit composition
        """
        if not product_ids:
            return []

        # Check for duplicate IDs in payload
        if len(product_ids) != len(set(product_ids)):
            raise ValidationError("Duplicate product IDs provided.", code="DUPLICATE_PRODUCT")

        products = list(
            Product.objects.filter(id__in=product_ids).select_related("store")
        )

        found_ids = {p.id for p in products}
        missing_ids = set(product_ids) - found_ids
        if missing_ids:
            raise ValidationError(
                f"Products with IDs {sorted(missing_ids)} do not exist.",
                code="PRODUCT_NOT_FOUND",
            )

        for product in products:
            cls.validate_product_eligibility(product)

        categories = [p.category for p in products]
        cls.validate_category_composition(categories)

        return products


class ComboService:
    """Business operations for creating, updating, managing, and sharing user Combos."""

    @staticmethod
    @transaction.atomic
    def create_combo(user, name: str = "", product_ids: list[int] = None) -> Combo:
        product_ids = product_ids or []
        products = ComboValidationService.validate_products(product_ids)

        clean_name = (name or "").strip() or "My Combo"
        combo = Combo.objects.create(user=user, name=clean_name)

        if products:
            items = [
                ComboItem(combo=combo, product=p, category=p.category)
                for p in products
            ]
            ComboItem.objects.bulk_create(items)

        return combo

    @staticmethod
    @transaction.atomic
    def update_combo(combo: Combo, name: str = None, product_ids: list[int] = None) -> Combo:
        if name is not None:
            combo.name = name.strip() or "My Combo"
            combo.save(update_fields=["name", "updated_at"])

        if product_ids is not None:
            products = ComboValidationService.validate_products(product_ids)
            combo.items.all().delete()
            if products:
                items = [
                    ComboItem(combo=combo, product=p, category=p.category)
                    for p in products
                ]
                ComboItem.objects.bulk_create(items)
            combo.save(update_fields=["updated_at"])

        return combo

    @staticmethod
    @transaction.atomic
    def add_item_to_combo(combo: Combo, product_id: int) -> ComboItem:
        product = Product.objects.filter(id=product_id).select_related("store").first()
        if not product:
            raise ValidationError("Product not found.", code="PRODUCT_NOT_FOUND")

        ComboValidationService.validate_product_eligibility(product)

        if combo.items.filter(product=product).exists():
            raise ValidationError("This product is already in the combo.", code="PRODUCT_ALREADY_IN_COMBO")

        current_categories = list(combo.items.values_list("category", flat=True))
        projected_categories = current_categories + [product.category]

        ComboValidationService.validate_category_composition(projected_categories)

        return ComboItem.objects.create(
            combo=combo,
            product=product,
            category=product.category,
        )

    @staticmethod
    @transaction.atomic
    def remove_item_from_combo(combo: Combo, item_id: int) -> bool:
        deleted_count, _ = combo.items.filter(id=item_id).delete()
        if deleted_count == 0:
            raise ValidationError("Combo item not found.", code="ITEM_NOT_FOUND")
        combo.save(update_fields=["updated_at"])
        return True

    @staticmethod
    @transaction.atomic
    def delete_combo(combo: Combo) -> bool:
        combo.delete()
        return True

    @staticmethod
    def get_shared_combo(share_code: str):
        return (
            Combo.objects.filter(share_code=share_code)
            .select_related("user")
            .prefetch_related("items__product__store")
            .first()
        )

    @staticmethod
    @transaction.atomic
    def clone_shared_combo(user, share_code: str) -> Combo:
        original = ComboService.get_shared_combo(share_code)
        if not original:
            raise ValidationError("Shared combo not found.", code="COMBO_NOT_FOUND")

        new_name = f"{original.name} (Saved)"
        product_ids = list(original.items.values_list("product_id", flat=True))
        return ComboService.create_combo(user=user, name=new_name, product_ids=product_ids)


class TryOnService:
    """
    Business operations for Virtual Try-On lifecycle management:
    - Purging expired try-on records and deleting their uploaded customer photos and generated S3 results.
    - Deleting single request with media.
    - Safety sweeping orphaned files in S3 and local storage.
    Results and customer selfie photos have a maximum lifetime of 1 hour.
    """
    TRYON_EXPIRATION_HOURS = 1

    @classmethod
    def get_expiration_cutoff(cls, hours: int = None):
        h = hours if hours is not None else cls.TRYON_EXPIRATION_HOURS
        return timezone.now() - timedelta(hours=h)

    @classmethod
    def delete_request_media(cls, tryon_req: TryOnRequest) -> int:
        """
        Deletes the uploaded customer selfie (person_image) and generated try-on
        result image (result_image) from storage (S3 bucket or local disk).
        Returns the number of files deleted.
        """
        deleted_count = 0
        if tryon_req.person_image:
            try:
                name = tryon_req.person_image.name
                if name:
                    tryon_req.person_image.delete(save=False)
                    deleted_count += 1
                    logger.debug("Deleted person_image '%s' for TryOnRequest %s", name, tryon_req.id)
            except Exception as e:
                logger.warning("Error deleting person_image for TryOnRequest %s: %s", tryon_req.id, e)

        if tryon_req.result_image:
            try:
                name = tryon_req.result_image.name
                if name:
                    tryon_req.result_image.delete(save=False)
                    deleted_count += 1
                    logger.debug("Deleted result_image '%s' for TryOnRequest %s", name, tryon_req.id)
            except Exception as e:
                logger.warning("Error deleting result_image for TryOnRequest %s: %s", tryon_req.id, e)

        return deleted_count

    @classmethod
    @transaction.atomic
    def delete_tryon_request(cls, tryon_req: TryOnRequest) -> bool:
        """
        Deletes a single TryOnRequest along with its associated S3 / disk images.
        """
        cls.delete_request_media(tryon_req)
        tryon_req.delete()
        return True

    @classmethod
    def purge_expired_requests(cls, hours: int = 1) -> dict:
        """
        Hourly maintenance job: purges TryOnRequest records older than `hours` (default: 1)
        along with uploaded user photos and generated try-on images.
        """
        cutoff = cls.get_expiration_cutoff(hours)
        expired_qs = TryOnRequest.objects.filter(created_at__lte=cutoff)

        total_records = 0
        total_images = 0

        for req in expired_qs.iterator(chunk_size=100):
            deleted_images = cls.delete_request_media(req)
            total_images += deleted_images
            req.delete()
            total_records += 1

        orphaned_s3 = cls.purge_orphaned_s3_images(cutoff)

        logger.info(
            "Purged %d expired TryOnRequest(s) and %d image(s) (orphaned S3 purged: %d). Cutoff: %s",
            total_records,
            total_images,
            orphaned_s3,
            cutoff.isoformat(),
        )

        return {
            "cleared_count": total_records,
            "deleted_images_count": total_images,
            "orphaned_s3_count": orphaned_s3,
            "cutoff": cutoff.isoformat(),
        }

    @classmethod
    def purge_orphaned_s3_images(cls, cutoff) -> int:
        """
        Safety sweeper: directly inspects the S3 bucket if configured to delete any
        stray files in tryon/user_inputs/ and tryon/results/ older than the cutoff.
        """
        bucket_name = getattr(settings, "AWS_STORAGE_BUCKET_NAME", "")
        if not bucket_name:
            return 0

        import sys
        if "test" in sys.argv and not getattr(settings, "_FORCE_TEST_S3", False):
            try:
                from unittest.mock import Mock
                import boto3
                if not isinstance(boto3.session.Session, Mock) and not hasattr(boto3.session.Session, "mock_calls"):
                    return 0
            except Exception:
                return 0

        try:
            import boto3
            session = boto3.session.Session()
            s3_client = session.client(
                "s3",
                aws_access_key_id=getattr(settings, "AWS_ACCESS_KEY_ID", None) or None,
                aws_secret_access_key=getattr(settings, "AWS_SECRET_ACCESS_KEY", None) or None,
                region_name=getattr(settings, "AWS_S3_REGION_NAME", "ap-south-1"),
            )

            deleted_count = 0
            for prefix in ["tryon/user_inputs/", "tryon/results/"]:
                paginator = s3_client.get_paginator("list_objects_v2")
                for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
                    objects_to_delete = []
                    for obj in page.get("Contents", []):
                        last_mod = obj.get("LastModified")
                        if last_mod and last_mod <= cutoff:
                            objects_to_delete.append({"Key": obj["Key"]})

                    if objects_to_delete:
                        s3_client.delete_objects(
                            Bucket=bucket_name,
                            Delete={"Objects": objects_to_delete, "Quiet": True},
                        )
                        deleted_count += len(objects_to_delete)

            if deleted_count:
                logger.info("Purged %d orphaned S3 image(s) from bucket %s", deleted_count, bucket_name)
            return deleted_count
        except Exception as exc:
            logger.warning("Failed to sweep orphaned S3 images: %s", exc)
            return 0
