import re
from django.core.validators import RegexValidator

NAME_REGEX = re.compile(r"^[A-Za-z\s\.\'-]{2,150}$")
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")

name_validator = RegexValidator(
    regex=NAME_REGEX,
    message="Name must be 2-150 characters and can only contain letters, spaces, hyphens, periods, and apostrophes.",
)

email_validator = RegexValidator(
    regex=EMAIL_REGEX,
    message="Enter a valid email address.",
)

OTP_CODE_REGEX = re.compile(r"^[0-9]{6}$")

otp_code_validator = RegexValidator(
    regex=OTP_CODE_REGEX,
    message="Enter a valid 6-digit code.",
)