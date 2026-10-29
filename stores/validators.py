import re
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from PIL import Image

PAN_REGEX = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$", re.IGNORECASE)
GST_REGEX = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$", re.IGNORECASE)
PHONE_REGEX = re.compile(r"^\+?[0-9\s\-]{10,15}$")
BANK_ACCOUNT_REGEX = re.compile(r"^\d{9,18}$")
IFSC_REGEX = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
ACCOUNT_HOLDER_REGEX = re.compile(r"^[a-zA-Z\s\.\']{2,100}$")
SETTLEMENT_DAY_REGEX = re.compile(r"^(0?[1-9]|[12][0-9]|3[01])$")

pan_validator = RegexValidator(
    regex=PAN_REGEX,
    message="Enter a valid 10-character PAN number (e.g. ABCDE1234F).",
)

gst_validator = RegexValidator(
    regex=GST_REGEX,
    message="Enter a valid 15-character GSTIN (e.g. 22AAAAA0000A1Z5).",
)

phone_validator = RegexValidator(
    regex=PHONE_REGEX,
    message="Enter a valid phone number (10-15 digits).",
)

bank_account_validator = RegexValidator(
    regex=BANK_ACCOUNT_REGEX,
    message="Enter a valid bank account number (9 to 18 digits).",
)

ifsc_validator = RegexValidator(
    regex=IFSC_REGEX,
    message="Enter a valid 11-character IFSC code (e.g. HDFC0001234).",
)

account_holder_validator = RegexValidator(
    regex=ACCOUNT_HOLDER_REGEX,
    message="Enter a valid account holder name (2-100 alphabetic characters).",
)

settlement_day_validator = RegexValidator(
    regex=SETTLEMENT_DAY_REGEX,
    message="Settlement day must be between 1 and 31.",
)

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
