import jwt
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import exceptions, serializers
from rest_framework_simplejwt.exceptions import (
    TokenBackendError,
    TokenBackendExpiredToken,
)
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.state import token_backend

from .models import RefreshToken
from .token_blacklist import (
    blacklist_access_token,
    is_access_token_blacklisted,
)
from .utils import (
    email_validator,
    name_validator,
    otp_code_validator,
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
    password = serializers.CharField(write_only=True, required=False, allow_blank=False)

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
        choices=["login", "password_reset"],
        default="login",
        required=False,
        help_text="Purpose of the OTP. Use 'password_reset' for forgot-password flow.",
    )

    def validate_email(self, value):
        cleaned = value.strip().lower()
        email_validator(cleaned)
        return cleaned


class OTPVerifySerializer(serializers.Serializer):
    email = serializers.EmailField(validators=[email_validator])
    code = serializers.CharField(max_length=6, min_length=6, validators=[otp_code_validator])

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
        help_text="The refresh token to be revoked."
    )

    def _validate_access_token(self, raw_access: str) -> dict:
        try:
            payload = token_backend.decode(raw_access, verify=True)
        except TokenBackendExpiredToken:
            # Access token is expired; verify cryptographic signature without rejecting for expiration
            try:
                payload = jwt.decode(
                    raw_access,
                    token_backend.get_verifying_key(raw_access),
                    algorithms=[token_backend.algorithm],
                    audience=token_backend.audience,
                    issuer=token_backend.issuer,
                    options={
                        "verify_signature": True,
                        "verify_exp": False,
                        "verify_aud": token_backend.audience is not None,
                    },
                )
            except jwt.PyJWTError as e:
                raise serializers.ValidationError({"access": "Invalid access token."}) from e
        except TokenBackendError as e:
            raise serializers.ValidationError({"access": "Invalid access token."}) from e

        token_type = payload.get("token_type")
        if token_type != "access":
            raise serializers.ValidationError(
                {"access": f"Invalid token type for access token: expected 'access', got '{token_type}'."}
            )

        jti = payload.get("jti")
        if not jti:
            raise serializers.ValidationError({"access": "Malformed access token: missing jti claim."})

        if is_access_token_blacklisted(jti):
            raise serializers.ValidationError({"access": "Access token has already been blacklisted."})

        return payload

    def _validate_refresh_token(self, raw_refresh: str) -> RefreshToken:
        token_obj = RefreshToken.resolve(raw_refresh)
        if not token_obj:
            raise serializers.ValidationError({"refresh": "Invalid refresh token."})

        if token_obj.is_revoked:
            raise serializers.ValidationError({"refresh": "Refresh token has already been revoked."})

        if token_obj.is_expired:
            raise serializers.ValidationError({"refresh": "Refresh token has expired."})

        return token_obj

    def validate(self, attrs):
        raw_access = self.context.get("header_token")
        if not raw_access:
            raise serializers.ValidationError(
                {"access": "Access token is required in the Authorization header ('Bearer <token>')."}
            )

        raw_refresh = attrs.get("refresh")
        if not raw_refresh:
            raise serializers.ValidationError({"refresh": "Refresh token is required."})

        access_payload = self._validate_access_token(raw_access)
        refresh_token_obj = self._validate_refresh_token(raw_refresh)

        access_user_id = access_payload.get(api_settings.USER_ID_CLAIM)
        refresh_user_id = refresh_token_obj.user_id

        if access_user_id is None or refresh_user_id is None or str(access_user_id) != str(refresh_user_id):
            raise serializers.ValidationError(
                {"detail": "Access token and refresh token belong to different users."}
            )

        attrs["access_payload"] = access_payload
        attrs["refresh_token_obj"] = refresh_token_obj
        return attrs

    def save(self, **kwargs):
        access_payload = self.validated_data["access_payload"]
        access_jti = access_payload["jti"]
        access_exp = access_payload["exp"]

        blacklist_access_token(access_jti, access_exp)

        refresh_token_obj = self.validated_data["refresh_token_obj"]
        refresh_token_obj.is_revoked = True
        refresh_token_obj.save(update_fields=["is_revoked"])


class TokenRefreshSerializer(serializers.Serializer):
    refresh = serializers.CharField(
        required=True,
        allow_blank=False,
        help_text="The opaque refresh token to rotate.",
    )

    def validate_refresh(self, value):
        cleaned = value.strip()
        if not cleaned:
            raise serializers.ValidationError("Refresh token cannot be blank.")
        return cleaned

    def validate(self, attrs):
        raw_refresh = attrs.get("refresh")
        token_obj = RefreshToken.resolve(raw_refresh)

        if not token_obj:
            raise exceptions.AuthenticationFailed({"detail": "Invalid or expired refresh token."})

        if token_obj.is_revoked:
            raise exceptions.AuthenticationFailed({"detail": "Refresh token has been revoked."})

        if token_obj.is_expired:
            raise exceptions.AuthenticationFailed({"detail": "Refresh token has expired."})

        if not token_obj.user.is_active:
            raise exceptions.PermissionDenied({"detail": "This account is inactive."})

        attrs["token_obj"] = token_obj
        return attrs


class ChangePasswordSerializer(serializers.Serializer):

    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)

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
        RefreshToken.revoke_all_for_user(user)


class ResetPasswordSerializer(serializers.Serializer):

    email = serializers.EmailField(validators=[email_validator])
    code = serializers.CharField(max_length=6, min_length=6, validators=[otp_code_validator])
    new_password = serializers.CharField(write_only=True)

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