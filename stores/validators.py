from django.core.exceptions import ValidationError
from PIL import Image

ALLOWED_IMAGE_TYPES = ["JPEG", "PNG", "WEBP"]
MAX_LOGO_SIZE = 2 * 1024 * 1024       # 2MB
MAX_BANNER_SIZE = 5 * 1024 * 1024     # 5MB
MAX_DIMENSION = 4096


def validate_store_image(file_obj, max_size, field_name="image"):
    """
    Validates file size, integrity, format, and dimensions of uploaded images.
    """
    if not file_obj:
        raise ValidationError(f"{field_name.capitalize()} file is required.")

    if file_obj.size > max_size:
        max_mb = max_size // (1024 * 1024)
        raise ValidationError(f"{field_name.capitalize()} file size cannot exceed {max_mb}MB.")

    try:
        image = Image.open(file_obj)
        image.verify()
    except Exception:
        raise ValidationError(f"Invalid {field_name} file. Please upload a valid image (JPEG, PNG, WEBP).")

    if image.format not in ALLOWED_IMAGE_TYPES:
        raise ValidationError(f"Unsupported image format: {image.format}. Allowed formats: JPEG, PNG, WEBP.")

    file_obj.seek(0)
    try:
        img = Image.open(file_obj)
        width, height = img.size
        if width > MAX_DIMENSION or height > MAX_DIMENSION:
            raise ValidationError(
                f"{field_name.capitalize()} dimensions cannot exceed {MAX_DIMENSION}x{MAX_DIMENSION} pixels."
            )
    except ValidationError:
        raise
    except Exception:
        raise ValidationError(f"Could not read dimensions of {field_name}.")
    finally:
        file_obj.seek(0)

    return True
