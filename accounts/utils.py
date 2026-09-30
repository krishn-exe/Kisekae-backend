import re
from django.core.validators import RegexValidator

NAME_REGEX = re.compile(r"^[A-Za-z\s\.\'-]{2,150}$")
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
PASSWORD_REGEX = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)(?=.*[^A-Za-z0-9\s]).{8,128}$")
OTP_CODE_REGEX = re.compile(r"^[0-9]{6}$")

name_validator = RegexValidator(
    regex=NAME_REGEX,
    message="Name must be 2-150 characters and can only contain letters, spaces, hyphens, periods, and apostrophes.",
)

email_validator = RegexValidator(
    regex=EMAIL_REGEX,
    message="Enter a valid email address.",
)

otp_code_validator = RegexValidator(
    regex=OTP_CODE_REGEX,
    message="Enter a valid 6-digit code.",
)

password_validator = RegexValidator(
    regex=PASSWORD_REGEX,
    message="Password must be between 8 and 128 characters long and include at least one letter, one number, and one special character.",
)