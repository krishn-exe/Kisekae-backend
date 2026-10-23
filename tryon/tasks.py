import logging
from celery import shared_task
from django.db import transaction

from products.models import Product, ProductCategory
from .gemini_service import GeminiVisionService
from .models import (
    ARStatus,
    TRYON_SUPPORTED_CATEGORIES,
    TRYON_UNSUPPORTED_CATEGORIES,
    TryOnProductAsset,
)

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=10, rate_limit="12/m")
def preprocess_product_image_task(self, product_id: int) -> bool:
    """
    Asynchronously inspects a product's images using Gemini Flash Vision.
    Validates category consistency and detects required flat-lay angles (Front, Back, Side).
    Sets the product's ARStatus to AVAILABLE, PROCESSING, or REJECTED with human-readable reasons.
    """
    try:
        product = (
            Product.objects.filter(pk=product_id)
            .select_related("store")
            .prefetch_related("images")
            .first()
        )
        if not product:
            logger.warning(f"Product #{product_id} not found for AR preprocessing.")
            return False

        # 1. Immediate rejection for unsupported categories
        if (
            product.category in TRYON_UNSUPPORTED_CATEGORIES
            or product.category not in TRYON_SUPPORTED_CATEGORIES
        ):
            display_name = (
                product.get_category_display()
                if hasattr(product, "get_category_display")
                else product.category
            )
            TryOnProductAsset.objects.update_or_create(
                product=product,
                defaults={
                    "status": ARStatus.REJECTED,
                    "rejection_reason": f"{display_name} is not supported for Virtual Try-On.",
                    "detected_category": product.category,
                },
            )
            logger.info(
                f"Product #{product_id} ({product.name}) rejected: unsupported category '{product.category}'."
            )
            return True

        asset, _ = TryOnProductAsset.objects.get_or_create(product=product)

        # 2. Incremental check: if already AVAILABLE and all 3 angle images still exist, no re-scan needed
        if asset.status == ARStatus.AVAILABLE and asset.has_all_angles:
            existing_image_ids = set(product.images.values_list("id", flat=True))
            required_ids = {asset.front_image_id, asset.back_image_id, asset.side_image_id}
            if required_ids.issubset(existing_image_ids):
                logger.info(
                    f"Product #{product_id} already has verified angle images. Skipping scan."
                )
                return True

        # 3. Gather all product images
        gallery_images = list(product.images.all())
        if not gallery_images and not product.image:
            asset.status = ARStatus.REJECTED
            asset.rejection_reason = (
                "No product images found. Please upload at least 3 flat-lay images (Front, Back, Side)."
            )
            asset.save(update_fields=["status", "rejection_reason", "updated_at"])
            return True

        # Mark as PROCESSING while inspecting
        asset.status = ARStatus.PROCESSING
        asset.rejection_reason = "Analyzing product images..."
        asset.save(update_fields=["status", "rejection_reason", "updated_at"])

        # 4. Gemini Inspection Triage
        found_angles: dict[str, Any] = {}
        has_model_photos = False
        has_category_mismatch = False
        mismatched_category = ""
        has_valid_clothing = False

        # Inspect gallery images
        for img in gallery_images:
            try:
                if not img.image:
                    continue
                img.image.open("rb")
                image_bytes = img.image.read()
                img.image.close()
            except Exception as read_err:
                logger.warning(f"Could not read image #{img.id} for product #{product_id}: {read_err}")
                continue

            inspection = GeminiVisionService.inspect_clothing_image(
                image_bytes=image_bytes,
                filename=img.image.name,
            )

            if not inspection.get("is_valid_clothing"):
                continue

            has_valid_clothing = True
            detected_cat = inspection.get("detected_category", "UNKNOWN")

            if detected_cat != product.category and detected_cat != "OTHER":
                has_category_mismatch = True
                mismatched_category = detected_cat
                continue

            pres_type = inspection.get("presentation_type", "IRRELEVANT")
            if pres_type == "ON_MODEL":
                has_model_photos = True
                continue

            if pres_type in ["FLAT_LAY", "MANNEQUIN"]:
                angle = inspection.get("detected_view", "UNKNOWN")
                if angle in ["FRONT", "BACK", "SIDE"] and angle not in found_angles:
                    found_angles[angle] = img
                elif "FRONT" not in found_angles:
                    found_angles["FRONT"] = img

        # If primary legacy image exists and we still need angles, inspect it as well
        if len(found_angles) < 3 and product.image:
            try:
                product.image.open("rb")
                image_bytes = product.image.read()
                product.image.close()
                inspection = GeminiVisionService.inspect_clothing_image(
                    image_bytes=image_bytes,
                    filename=product.image.name,
                )
                if inspection.get("is_valid_clothing") and inspection.get("presentation_type") in ["FLAT_LAY", "MANNEQUIN"]:
                    angle = inspection.get("detected_view", "FRONT")
                    if angle in ["FRONT", "BACK", "SIDE"] and angle not in found_angles:
                        found_angles[angle] = product.image
            except Exception as e:
                logger.warning(f"Could not inspect legacy thumbnail for product #{product_id}: {e}")

        # 5. Resolve ARStatus & Rejection Reason
        if "FRONT" in found_angles and "BACK" in found_angles and "SIDE" in found_angles:
            asset.front_image = found_angles["FRONT"] if hasattr(found_angles["FRONT"], "id") else None
            asset.back_image = found_angles["BACK"] if hasattr(found_angles["BACK"], "id") else None
            asset.side_image = found_angles["SIDE"] if hasattr(found_angles["SIDE"], "id") else None
            asset.candidate_image = asset.front_image or asset.back_image or asset.side_image
            asset.status = ARStatus.AVAILABLE
            asset.rejection_reason = ""
            asset.detected_category = product.category
            logger.info(f"Product #{product_id} ({product.name}) marked AVAILABLE for AR.")
        elif has_category_mismatch:
            asset.status = ARStatus.REJECTED
            asset.rejection_reason = (
                f"Category mismatch: product is listed as {product.get_category_display()}, "
                f"but photo appears to be a {mismatched_category.capitalize()}."
            )
        elif not has_valid_clothing and not found_angles:
            asset.status = ARStatus.REJECTED
            asset.rejection_reason = "Uploaded photo does not contain a recognizable fashion item."
        else:
            missing = [ang for ang in ["Front", "Back", "Side"] if ang.upper() not in found_angles]
            found_str = ", ".join(ang.capitalize() for ang in found_angles.keys()) if found_angles else "None"
            if has_model_photos and not found_angles:
                asset.rejection_reason = (
                    "Only on-model photos detected. Please upload at least 3 flat-lay or mannequin photos (Front, Back, Side)."
                )
            else:
                asset.rejection_reason = (
                    f"Requires at least 3 flat-lay views (Front, Back, Side). "
                    f"Found: {found_str}. Missing: {', '.join(missing)}."
                )
            asset.status = ARStatus.REJECTED

            # Save any partial angles found
            if "FRONT" in found_angles and hasattr(found_angles["FRONT"], "id"):
                asset.front_image = found_angles["FRONT"]
                asset.candidate_image = asset.front_image
            if "BACK" in found_angles and hasattr(found_angles["BACK"], "id"):
                asset.back_image = found_angles["BACK"]
            if "SIDE" in found_angles and hasattr(found_angles["SIDE"], "id"):
                asset.side_image = found_angles["SIDE"]

        asset.save()
        return True

    except Exception as exc:
        logger.error(f"Error in preprocess_product_image_task for product #{product_id}: {exc}")
        raise self.retry(exc=exc)
