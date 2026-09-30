from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from .utils import (
    email_validator,
    name_validator,
    otp_code_validator,
    password_validator,
)

User = get_user_model()


class RegisterSerializer(serializers.ModelSerializer):
    name = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=150,
        validators=[name_validator],
    )
    email = serializers.EmailField(
        required=True,
        allow_blank=False,
        allow_null=False,
        validators=[email_validator],
    )
    password = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=False,
        validators=[password_validator],
    )

    class Meta:
        model = User
        fields = ["name", "email", "password"]

    def validate_name(self, value):
        if not value:
            return ""
        cleaned = value.strip()
        if not cleaned:
            return ""
        name_validator(cleaned)
        return cleaned

    def validate_email(self, value):
        cleaned = value.strip().lower()
        email_validator(cleaned)
        return cleaned

    def validate(self, attrs):
        name = attrs.get("name")
        email = attrs.get("email")

        if not email:
            raise serializers.ValidationError({"email": "Email address is required for registration."})

        if name:
            attrs["name"] = name.strip()

        email = email.strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError({"email": "An account with this email already exists."})
        attrs["email"] = email

        password = attrs.get("password")
        if password:
            validate_password(password)

        return attrs

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


class LoginPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField(validators=[email_validator])
    password = serializers.CharField(write_only=True)

    def validate_email(self, value):
        cleaned = value.strip().lower()
        email_validator(cleaned)
        return cleaned

    def validate(self, attrs):
        email = attrs.get("email", "").strip().lower()
        user = User.objects.filter(email__iexact=email).first()
        if not user:
            raise serializers.ValidationError("No account found with that email address.")

        if not user.is_active:
            raise serializers.ValidationError("This account is inactive.")

        if not user.is_email_verified:
            raise serializers.ValidationError("Email is not verified. Please verify your email before logging in.")

        if not user.has_usable_password():
            raise serializers.ValidationError(
                "This account doesn't have a password set. Use 'Send me a code' to sign in instead."
            )

        if not user.check_password(attrs["password"]):
            raise serializers.ValidationError("Incorrect password.")

        attrs["user"] = user
        return attrs


class OTPRequestSerializer(serializers.Serializer):
    email = serializers.EmailField(validators=[email_validator])
    purpose = serializers.ChoiceField(
        choices=["login", "password_reset", "verify_email"],
        default="login",
        required=False,
        help_text="Purpose of the OTP. Options: 'login', 'password_reset', 'verify_email'.",
    )

    def validate_email(self, value):
        cleaned = value.strip().lower()
        email_validator(cleaned)
        return cleaned


class OTPVerifySerializer(serializers.Serializer):
    email = serializers.EmailField(validators=[email_validator])
    code = serializers.CharField(max_length=6, min_length=6, validators=[otp_code_validator])
    purpose = serializers.ChoiceField(
        choices=["login", "verify_email"],
        required=False,
        help_text="Purpose of the OTP. Options: 'login' or 'verify_email'. Defaults to 'login'.",
    )

    def validate_email(self, value):
        cleaned = value.strip().lower()
        email_validator(cleaned)
        return cleaned

    def validate_code(self, value):
        cleaned = value.strip()
        otp_code_validator(cleaned)
        return cleaned


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(
        help_text="The refresh token to be blacklisted."
    )

    def validate_refresh(self, value):
        cleaned = value.strip()
        if not cleaned:
            raise serializers.ValidationError("Refresh token is required.")
        try:
            token = RefreshToken(cleaned)
        except TokenError as e:
            raise serializers.ValidationError(f"Invalid or expired refresh token: {str(e)}.")
        return token

    def save(self, **kwargs):
        token = self.validated_data["refresh"]
        token.blacklist()


class ChangePasswordSerializer(serializers.Serializer):

    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, validators=[password_validator])

    def validate_old_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Old password is incorrect.")
        return value

    def validate_new_password(self, value):
        validate_password(value)
        return value

    def validate(self, attrs):
        if attrs["old_password"] == attrs["new_password"]:
            raise serializers.ValidationError(
                {"new_password": "New password must be different from the old password."}
            )
        return attrs

    def save(self, **kwargs):
        user = self.context["request"].user
        user.set_password(self.validated_data["new_password"])
        user.save(update_fields=["password"])

        from rest_framework_simplejwt.token_blacklist.models import (
            BlacklistedToken,
            OutstandingToken,
        )

        outstanding_tokens = OutstandingToken.objects.filter(user=user)
        existing_ids = set(
            BlacklistedToken.objects.filter(token__in=outstanding_tokens).values_list(
                "token_id", flat=True
            )
        )
        to_create = [
            BlacklistedToken(token=t)
            for t in outstanding_tokens
            if t.id not in existing_ids
        ]
        if to_create:
            BlacklistedToken.objects.bulk_create(to_create, ignore_conflicts=True)


class ResetPasswordSerializer(serializers.Serializer):

    email = serializers.EmailField(validators=[email_validator])
    code = serializers.CharField(max_length=6, min_length=6, validators=[otp_code_validator])
    new_password = serializers.CharField(write_only=True, validators=[password_validator])

    def validate_email(self, value):
        cleaned = value.strip().lower()
        email_validator(cleaned)
        return cleaned

    def validate_code(self, value):
        cleaned = value.strip()
        otp_code_validator(cleaned)
        return cleaned

    def validate_new_password(self, value):
        validate_password(value)
        return value


class GoogleAuthSerializer(serializers.Serializer):
    id_token = serializers.CharField(
        required=True,
        allow_blank=False,
        help_text="Google ID token (JWT) from Google Identity Services or One Tap.",
    )

    def validate_id_token(self, value):
        return value.strip()