import logging
from typing import Any
from celery import shared_task
from django.core.files.base import ContentFile
from django.utils import timezone

from products.models import Product, ProductCategory
from .gemini_service import GeminiVisionService
from .models import (
    ARStatus,
    TRYON_SUPPORTED_CATEGORIES,
    TRYON_UNSUPPORTED_CATEGORIES,
    TryOnAngle,
    TryOnProductAsset,
    TryOnRequest,
    TryOnRequestStatus,
)
from .vertex_service import VertexAIVirtualTryOnClient

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


CATEGORY_TRYON_ORDER = {
    ProductCategory.BOTTOM: 1,
    ProductCategory.ONE_PIECE: 2,
    ProductCategory.TOP: 2,
    ProductCategory.OUTERWEAR: 3,
}


def get_garment_image_bytes(product: Product, angle: str = "FRONT") -> bytes:
    """
    Extracts raw image bytes for a garment flat-lay from its TryOnProductAsset or gallery.
    Selects the requested perspective angle (Front, Back, Side) if available.
    """
    asset = getattr(product, "tryon_asset", None)
    if not asset:
        asset = TryOnProductAsset.objects.filter(product=product).first()

    target_img = None
    if asset:
        if angle == TryOnAngle.BACK and asset.back_image:
            target_img = asset.back_image
        elif angle == TryOnAngle.SIDE and asset.side_image:
            target_img = asset.side_image
        else:
            target_img = asset.front_image or asset.candidate_image

    if not target_img and asset and asset.candidate_image:
        target_img = asset.candidate_image

    if target_img and target_img.image:
        target_img.image.open("rb")
        data = target_img.image.read()
        target_img.image.close()
        return data

    if product.image:
        product.image.open("rb")
        data = product.image.read()
        product.image.close()
        return data

    raise ValueError(f"No usable flat-lay image found for product #{product.id} ({product.name}).")


@shared_task(bind=True, max_retries=1, default_retry_delay=5)
def generate_tryon_task(self, request_id: str) -> bool:
    """
    Processes a customer Virtual Try-On request using Google Vertex AI (virtual-try-on-001).
    - If single product: calls Vertex AI once with the person photo and garment flat-lay image.
    - If combo: chains predictions sequentially (BOTTOM -> ONE_PIECE/TOP -> OUTERWEAR),
      piping each generated intermediate result into the next layer.
    """
    tryon_req = TryOnRequest.objects.filter(pk=request_id).first()
    if not tryon_req:
        logger.error(f"TryOnRequest #{request_id} not found.")
        return False

    tryon_req.status = TryOnRequestStatus.PROCESSING
    tryon_req.save(update_fields=["status"])

    try:
        if not tryon_req.person_image:
            raise ValueError("Customer selfie / person image is missing.")

        tryon_req.person_image.open("rb")
        person_bytes = tryon_req.person_image.read()
        tryon_req.person_image.close()

        angle = tryon_req.angle or TryOnAngle.AUTO

        # Auto-detect customer pose perspective using Gemini if AUTO
        if angle == TryOnAngle.AUTO:
            pose_info = GeminiVisionService.detect_person_pose(
                image_bytes=person_bytes,
                filename=tryon_req.person_image.name,
            )
            if not pose_info.get("is_valid_person", True):
                raise ValueError(
                    "No recognizable person was detected in the uploaded photo. "
                    "Please upload a clear photograph of yourself."
                )
            detected_angle = pose_info.get("detected_pose", TryOnAngle.FRONT)
            angle = detected_angle if detected_angle in [TryOnAngle.FRONT, TryOnAngle.BACK, TryOnAngle.SIDE] else TryOnAngle.FRONT
            tryon_req.angle = angle
            tryon_req.save(update_fields=["angle"])
            logger.info(f"Gemini auto-detected pose for TryOn #{tryon_req.id}: {angle}")

        if tryon_req.product:
            # Single Garment Prediction
            garment_bytes = get_garment_image_bytes(tryon_req.product, angle=angle)
            result_bytes = VertexAIVirtualTryOnClient.predict(
                person_image_bytes=person_bytes,
                garment_image_bytes=garment_bytes,
            )
        elif tryon_req.combo:
            # Sequential Combo Chaining
            combo = (
                tryon_req.combo.__class__.objects.filter(pk=tryon_req.combo_id)
                .prefetch_related("items__product__tryon_asset")
                .first()
            )
            if not combo or not combo.items.exists():
                raise ValueError("Combo contains no items for Virtual Try-On.")

            items = list(combo.items.all())
            # Sort items by layering hierarchy: BOTTOM (1) -> TOP/ONE_PIECE (2) -> OUTERWEAR (3)
            sorted_items = sorted(
                items,
                key=lambda it: CATEGORY_TRYON_ORDER.get(it.category, 99),
            )

            current_canvas_bytes = person_bytes
            for item in sorted_items:
                garment_bytes = get_garment_image_bytes(item.product, angle=angle)
                current_canvas_bytes = VertexAIVirtualTryOnClient.predict(
                    person_image_bytes=current_canvas_bytes,
                    garment_image_bytes=garment_bytes,
                )
            result_bytes = current_canvas_bytes
        else:
            raise ValueError("TryOnRequest has neither a product nor a combo specified.")

        # Save result image
        filename = f"tryon_result_{tryon_req.id}.png"
        tryon_req.result_image.save(filename, ContentFile(result_bytes), save=False)
        tryon_req.status = TryOnRequestStatus.READY
        tryon_req.completed_at = timezone.now()
        tryon_req.error_message = ""
        tryon_req.save(update_fields=["result_image", "status", "completed_at", "error_message"])

        try:
            from notifications.tasks import send_customer_tryon_push_notification_task
            send_customer_tryon_push_notification_task.delay(tryon_req.id)
        except Exception as e:
            logger.warning("Failed to enqueue tryon push notification: %s", e)

        logger.info(f"TryOnRequest #{tryon_req.id} completed successfully.")
        return True

    except Exception as exc:
        logger.error(f"TryOnRequest #{tryon_req.id} failed: {exc}", exc_info=True)
        tryon_req.status = TryOnRequestStatus.FAILED
        tryon_req.error_message = str(exc)
        tryon_req.save(update_fields=["status", "error_message"])

        try:
            from notifications.tasks import send_customer_tryon_push_notification_task
            send_customer_tryon_push_notification_task.delay(tryon_req.id)
        except Exception as e:
            logger.warning("Failed to enqueue tryon push notification: %s", e)

        return False

