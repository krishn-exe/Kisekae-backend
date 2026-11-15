from django.core.exceptions import ValidationError
from PIL import Image

ALLOWED_REVIEW_IMAGE_TYPES = ["JPEG", "PNG", "WEBP"]
MAX_REVIEW_IMAGE_SIZE = 1 * 1024 * 1024  # 1 MB
MAX_REVIEW_IMAGES_COUNT = 5
MAX_REVIEW_IMAGE_DIMENSION = 4096


def validate_review_image(file_obj, max_size=MAX_REVIEW_IMAGE_SIZE):
    """
    Validates review photo:
    - Must be provided
    - File size <= 1MB (default)
    - Valid image format (JPEG, PNG, WEBP)
    - Dimensions <= 4096 x 4096 pixels
    """
    if not file_obj:
        raise ValidationError("Review image file is required.")

    if file_obj.size > max_size:
        max_mb = max_size / (1024 * 1024)
        actual_mb = file_obj.size / (1024 * 1024)
        raise ValidationError(
            f"Review image file size cannot exceed {max_mb:.0f}MB. Got {actual_mb:.2f}MB."
        )

    try:
        image = Image.open(file_obj)
        image.verify()
    except Exception:
        raise ValidationError("Invalid image file. Please upload a valid image (JPEG, PNG, WEBP).")

    if image.format not in ALLOWED_REVIEW_IMAGE_TYPES:
        raise ValidationError(
            f"Unsupported image format: {image.format}. Allowed formats: {', '.join(ALLOWED_REVIEW_IMAGE_TYPES)}."
        )

    file_obj.seek(0)
    try:
        img = Image.open(file_obj)
        width, height = img.size
        if width > MAX_REVIEW_IMAGE_DIMENSION or height > MAX_REVIEW_IMAGE_DIMENSION:
            raise ValidationError(
                f"Review image dimensions cannot exceed {MAX_REVIEW_IMAGE_DIMENSION}x{MAX_REVIEW_IMAGE_DIMENSION} pixels."
            )
    except ValidationError:
        raise
    except Exception:
        raise ValidationError("Could not read image dimensions.")
    finally:
        file_obj.seek(0)

    return True
