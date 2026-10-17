from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Address, AddressTitle, UserSettings, WishlistItem
from products.models import Product, ProductStatus
from stores.models import StoreStatus
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
    is_seller = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Designates whether this user registers as a seller. Defaults to false.",
    )

    class Meta:
        model = User
        fields = ["name", "email", "password", "is_seller"]

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
        choices=["login", "verify_email", "password_reset"],
        required=False,
        help_text="Purpose of the OTP. Options: 'login', 'verify_email', 'password_reset'. Defaults to 'login'.",
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

    token = serializers.CharField(
        required=False,
        help_text="Password reset token obtained from OTP verification.",
    )
    reset_token = serializers.CharField(
        required=False,
        help_text="Alias for token.",
    )
    new_password = serializers.CharField(
        write_only=True,
        validators=[password_validator],
        help_text="New password conforming to password requirements.",
    )

    def validate_new_password(self, value):
        validate_password(value)
        return value

    def validate(self, attrs):
        token = attrs.get("token") or attrs.get("reset_token")
        if not token or not str(token).strip():
            raise serializers.ValidationError({"token": "Password reset token is required."})
        attrs["token"] = str(token).strip()
        return attrs


class OAuthLoginSerializer(serializers.Serializer):
    code = serializers.CharField(
        required=False,
        allow_blank=False,
        help_text="Authorization code returned by the OAuth provider.",
    )
    callback_url = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        help_text="Redirect URI registered with the OAuth provider (e.g. kisekae://auth/callback).",
    )
    access_token = serializers.CharField(
        required=False,
        allow_blank=False,
        help_text="Optional direct OAuth access token if already obtained by client.",
    )

    def validate(self, attrs):
        code = attrs.get("code")
        access_token = attrs.get("access_token")
        if not code and not access_token:
            raise serializers.ValidationError({"code": "Authorization code or access token is required."})
        return attrs


class GoogleOAuthSerializer(OAuthLoginSerializer):
    code_verifier = serializers.CharField(
        required=False,
        allow_blank=False,
        min_length=43,
        max_length=128,
        help_text="PKCE code verifier (RFC 7636) used for mobile/public clients (Android & iOS).",
    )
    client_id = serializers.CharField(
        required=False,
        allow_blank=False,
        help_text="OAuth client ID (Web, Android, or iOS). Must be in the allowed client IDs list.",
    )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        code = attrs.get("code")
        code_verifier = attrs.get("code_verifier")
        client_id = attrs.get("client_id")
        callback_url = attrs.get("callback_url")

        if code_verifier and not code:
            raise serializers.ValidationError(
                {"code": "Authorization code is required when code_verifier is provided."}
            )

        allowed_client_ids = getattr(settings, "GOOGLE_ALLOWED_CLIENT_IDS", [])
        if client_id:
            if allowed_client_ids and client_id not in allowed_client_ids:
                raise serializers.ValidationError(
                    {"client_id": f"Client ID '{client_id}' is not in the allowed client IDs list."}
                )
        elif code_verifier and allowed_client_ids:
            if len(allowed_client_ids) == 1:
                attrs["client_id"] = allowed_client_ids[0]
            elif getattr(settings, "GOOGLE_ANDROID_CLIENT_ID", None) and not getattr(settings, "GOOGLE_IOS_CLIENT_ID", None):
                attrs["client_id"] = settings.GOOGLE_ANDROID_CLIENT_ID
            elif getattr(settings, "GOOGLE_IOS_CLIENT_ID", None) and not getattr(settings, "GOOGLE_ANDROID_CLIENT_ID", None):
                attrs["client_id"] = settings.GOOGLE_IOS_CLIENT_ID

        # Fill default callback URL if PKCE and omitted
        if code_verifier and not callback_url:
            effective_client_id = attrs.get("client_id")
            if effective_client_id and effective_client_id == getattr(settings, "GOOGLE_ANDROID_CLIENT_ID", None):
                attrs["callback_url"] = getattr(settings, "GOOGLE_ANDROID_CALLBACK_URL", "kisekae://auth/google/callback")
            elif effective_client_id and effective_client_id == getattr(settings, "GOOGLE_IOS_CLIENT_ID", None):
                attrs["callback_url"] = getattr(settings, "GOOGLE_IOS_CALLBACK_URL", "live.kisekae.app:/oauth2redirect")
            elif getattr(settings, "GOOGLE_ANDROID_CALLBACK_URL", None):
                attrs["callback_url"] = settings.GOOGLE_ANDROID_CALLBACK_URL

        allowed_redirect_uris = getattr(settings, "GOOGLE_ALLOWED_REDIRECT_URIS", [])
        callback_url = attrs.get("callback_url")
        if callback_url and allowed_redirect_uris:
            normalized_callback = callback_url.rstrip("/")
            normalized_allowed = [uri.rstrip("/") for uri in allowed_redirect_uris]
            if normalized_callback not in normalized_allowed:
                raise serializers.ValidationError(
                    {"callback_url": f"Callback URL '{callback_url}' is not in the allowed redirect URIs list."}
                )

        return attrs


class GitHubOAuthSerializer(OAuthLoginSerializer):
    pass


class UserUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(
        max_length=150,
        required=True,
        validators=[name_validator],
    )

    def validate_name(self, value):
        val = value.strip()
        name_validator(val)
        return val


class UserSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserSettings
        fields = [
            "email_notifications",
            "order_alerts",
            "stock_alerts",
            "language",
            "timezone",
            "updated_at",
        ]


class UserProfileSerializer(serializers.ModelSerializer):
    addresses_count = serializers.SerializerMethodField()
    wishlist_items_count = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "name",
            "email",
            "is_email_verified",
            "is_seller",
            "date_joined",
            "addresses_count",
            "wishlist_items_count",
        ]
        read_only_fields = fields

    def get_addresses_count(self, obj):
        return obj.addresses.count() if hasattr(obj, "addresses") else 0

    def get_wishlist_items_count(self, obj):
        return obj.wishlist_items.count() if hasattr(obj, "wishlist_items") else 0


class AddressSerializer(serializers.ModelSerializer):
    pin_code = serializers.CharField(
        write_only=True,
        required=False,
        help_text="Alias for postal_code.",
    )
    pincode = serializers.CharField(
        write_only=True,
        required=False,
        help_text="Alias for postal_code.",
    )

    class Meta:
        model = Address
        fields = [
            "id",
            "title",
            "recipient_name",
            "phone",
            "address_line_1",
            "address_line_2",
            "city",
            "state",
            "postal_code",
            "pin_code",
            "pincode",
            "country",
            "latitude",
            "longitude",
            "is_default",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
        extra_kwargs = {
            "postal_code": {"required": False},
            "address_line_1": {"required": True},
            "state": {"required": True},
        }

    def to_internal_value(self, data):
        data = data.copy() if hasattr(data, "copy") else dict(data)
        pin = data.get("pin_code") or data.get("pincode")
        if pin and not data.get("postal_code"):
            data["postal_code"] = pin
        return super().to_internal_value(data)

    def validate_postal_code(self, value):
        val = str(value).strip()
        if not val:
            raise serializers.ValidationError("Postal code / PIN code is required.")
        if len(val) < 3 or len(val) > 20:
            raise serializers.ValidationError("Postal code must be between 3 and 20 characters.")
        return val

    def validate_latitude(self, value):
        if value is not None and (value < -90 or value > 90):
            raise serializers.ValidationError("Latitude must be between -90.0 and 90.0 degrees.")
        return value

    def validate_longitude(self, value):
        if value is not None and (value < -180 or value > 180):
            raise serializers.ValidationError("Longitude must be between -180.0 and 180.0 degrees.")
        return value

    def validate_address_line_1(self, value):
        val = str(value).strip()
        if not val:
            raise serializers.ValidationError("Address line 1 is required.")
        return val

    def validate_state(self, value):
        val = str(value).strip()
        if not val:
            raise serializers.ValidationError("State is required.")
        return val

    def validate(self, attrs):
        if not attrs.get("postal_code") and not self.instance:
            raise serializers.ValidationError({"postal_code": "Postal code / PIN code is required."})
        return attrs

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        ret["pin_code"] = instance.postal_code
        return ret

    def create(self, validated_data):
        user = self.context["request"].user
        validated_data.pop("pin_code", None)
        validated_data.pop("pincode", None)
        return Address.objects.create(user=user, **validated_data)

    def update(self, instance, validated_data):
        validated_data.pop("pin_code", None)
        validated_data.pop("pincode", None)
        return super().update(instance, validated_data)


class WishlistStoreSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    slug = serializers.CharField()


class WishlistProductSerializer(serializers.ModelSerializer):
    store = WishlistStoreSummarySerializer(read_only=True)
    is_in_stock = serializers.BooleanField(read_only=True)
    image_url = serializers.CharField(read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "slug",
            "price",
            "compare_at_price",
            "currency",
            "image_url",
            "sku",
            "stock",
            "is_in_stock",
            "status",
            "store",
        ]


class WishlistItemSerializer(serializers.ModelSerializer):
    product = WishlistProductSerializer(read_only=True)

    class Meta:
        model = WishlistItem
        fields = [
            "id",
            "product",
            "note",
            "created_at",
        ]
        read_only_fields = ["id", "product", "created_at"]


class AddWishlistSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(
        required=True,
        help_text="ID of the product to add to wishlist.",
    )
    note = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=255,
        help_text="Optional note for this wishlisted product.",
    )

    def validate_product_id(self, value):
        product = Product.objects.select_related("store").filter(pk=value).first()
        if not product:
            raise serializers.ValidationError("Product not found.")
        if product.status != ProductStatus.ACTIVE:
            raise serializers.ValidationError("This product is inactive and cannot be added to wishlist.")
        if product.store.status != StoreStatus.ACTIVE or product.store.archived_at is not None:
            raise serializers.ValidationError("The store offering this product is not active.")
        return value


class UpdateWishlistNoteSerializer(serializers.Serializer):
    note = serializers.CharField(
        required=True,
        allow_blank=True,
        max_length=255,
        help_text="Updated note or remark for this wishlisted item.",
    )
