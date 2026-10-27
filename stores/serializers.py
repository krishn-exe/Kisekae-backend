from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import (
    BusinessVerification,
    Store,
    StoreInvitation,
    StoreMembership,
    StoreStatus,
)
from .validators import (
    MAX_BANNER_SIZE,
    MAX_LOGO_SIZE,
    gst_validator,
    pan_validator,
    phone_validator,
    validate_store_image,
)

User = get_user_model()


class StoreMemberUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "name", "email"]


class StoreMemberSerializer(serializers.ModelSerializer):
    user = StoreMemberUserSerializer(read_only=True)

    class Meta:
        model = StoreMembership
        fields = ["id", "user", "role", "created_at"]


class AddMemberSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        email = value.strip().lower()
        user = User.objects.filter(email__iexact=email).first()
        if not user:
            raise serializers.ValidationError("No user found with this email.")
        if not user.is_active:
            raise serializers.ValidationError("This user account is inactive.")
        if not user.is_seller:
            raise serializers.ValidationError("User is not registered as a seller.")
        self.context["target_user"] = user
        return email


class StoreSerializer(serializers.ModelSerializer):
    my_role = serializers.SerializerMethodField()
    member_count = serializers.SerializerMethodField()
    is_business_verified = serializers.BooleanField(read_only=True)
    logo_url = serializers.CharField(read_only=True, allow_null=True)
    banner_url = serializers.CharField(read_only=True, allow_null=True)

    class Meta:
        model = Store
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "logo_url",
            "banner_url",
            "website_link",
            "instagram_link",
            "contact_email",
            "contact_phone",
            "status",
            "is_business_verified",
            "my_role",
            "member_count",
            "created_at",
            "updated_at",
            "archived_at",
        ]

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_my_role(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None
        if hasattr(obj, "my_membership_role"):
            return obj.my_membership_role
        if hasattr(obj, "_prefetched_objects_cache") and "memberships" in obj._prefetched_objects_cache:
            for m in obj.memberships.all():
                if m.user_id == request.user.id:
                    return m.role
            return None
        membership = obj.memberships.filter(user=request.user).first()
        return membership.role if membership else None

    @extend_schema_field(serializers.IntegerField())
    def get_member_count(self, obj):
        if hasattr(obj, "member_count"):
            return obj.member_count
        if hasattr(obj, "annotated_member_count"):
            return obj.annotated_member_count
        if hasattr(obj, "_prefetched_objects_cache") and "memberships" in obj._prefetched_objects_cache:
            return len(obj.memberships.all())
        return obj.memberships.count()


class StoreCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150, help_text="Unique display name of the store")
    description = serializers.CharField(min_length=5, help_text="Store description detailing offerings and brand story")
    logo = serializers.ImageField(required=True, help_text="Store logo image (JPEG/PNG/WEBP, max 2MB)")
    banner = serializers.ImageField(required=True, help_text="Store banner header image (JPEG/PNG/WEBP, max 5MB)")
    website_link = serializers.URLField(required=False, allow_blank=True, default="", help_text="Official website URL")
    instagram_link = serializers.CharField(max_length=150, required=False, allow_blank=True, default="", help_text="Store Instagram handle or URL")
    contact_email = serializers.EmailField(required=False, allow_blank=True, default="", help_text="Customer support email")
    contact_phone = serializers.CharField(max_length=20, required=False, allow_blank=True, default="", help_text="Customer support contact number")

    def validate_name(self, value):
        val = value.strip()
        if len(val) < 2:
            raise serializers.ValidationError("Store name must be at least 2 characters.")
        return val

    def validate_description(self, value):
        val = value.strip()
        if len(val) < 5:
            raise serializers.ValidationError("Store description must be at least 5 characters.")
        return val

    def validate_logo(self, value):
        if value:
            try:
                validate_store_image(value, MAX_LOGO_SIZE, "logo")
            except DjangoValidationError as e:
                raise serializers.ValidationError(list(e.messages) if hasattr(e, "messages") else str(e))
        return value

    def validate_banner(self, value):
        if value:
            try:
                validate_store_image(value, MAX_BANNER_SIZE, "banner")
            except DjangoValidationError as e:
                raise serializers.ValidationError(list(e.messages) if hasattr(e, "messages") else str(e))
        return value


class StoreUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150, required=False, help_text="Store display name")
    description = serializers.CharField(required=False, allow_blank=True, help_text="Store description")
    logo = serializers.ImageField(required=False, help_text="New logo image file (JPEG/PNG/WEBP, max 2MB)")
    banner = serializers.ImageField(required=False, help_text="New banner image file (JPEG/PNG/WEBP, max 5MB)")
    website_link = serializers.URLField(required=False, allow_blank=True, help_text="Official website URL")
    instagram_link = serializers.CharField(max_length=150, required=False, allow_blank=True, help_text="Store Instagram handle or URL")
    contact_email = serializers.EmailField(required=False, allow_blank=True, help_text="Support email")
    contact_phone = serializers.CharField(max_length=20, required=False, allow_blank=True, help_text="Support phone")
    status = serializers.ChoiceField(
        choices=[StoreStatus.ACTIVE, StoreStatus.INACTIVE],
        required=False,
        help_text="Store active status (ACTIVE or INACTIVE)",
    )

    def validate_name(self, value):
        val = value.strip()
        if len(val) < 2:
            raise serializers.ValidationError("Store name must be at least 2 characters.")
        return val

    def validate_logo(self, value):
        if value:
            try:
                validate_store_image(value, MAX_LOGO_SIZE, "logo")
            except DjangoValidationError as e:
                raise serializers.ValidationError(list(e.messages) if hasattr(e, "messages") else str(e))
        return value

    def validate_banner(self, value):
        if value:
            try:
                validate_store_image(value, MAX_BANNER_SIZE, "banner")
            except DjangoValidationError as e:
                raise serializers.ValidationError(list(e.messages) if hasattr(e, "messages") else str(e))
        return value


class TransferOwnershipSerializer(serializers.Serializer):
    member_id = serializers.IntegerField(
        help_text="The ID of the store membership to transfer ownership to."
    )


class StoreInvitationStoreSerializer(serializers.ModelSerializer):
    logo_url = serializers.CharField(read_only=True, allow_null=True)

    class Meta:
        model = Store
        fields = ["id", "name", "slug", "logo_url"]


class StoreInvitationSerializer(serializers.ModelSerializer):
    store = StoreInvitationStoreSerializer(read_only=True)
    invited_by = StoreMemberUserSerializer(read_only=True)

    class Meta:
        model = StoreInvitation
        fields = [
            "id",
            "store",
            "email",
            "status",
            "invited_by",
            "expires_at",
            "created_at",
            "responded_at",
        ]


class CreateInvitationSerializer(serializers.Serializer):
    email = serializers.EmailField(help_text="Email address to invite to manage this store.")

    def validate_email(self, value):
        return value.strip().lower()


class AcceptTokenSerializer(serializers.Serializer):
    token = serializers.CharField(
        help_text="The invitation token received in the invitation email or deep link."
    )


class ImageUploadSerializer(serializers.Serializer):
    file = serializers.ImageField(help_text="Image file (JPEG, PNG, WEBP).")


class BusinessVerificationSerializer(serializers.ModelSerializer):
    store_id = serializers.IntegerField(source="store.id", read_only=True)
    store_name = serializers.CharField(source="store.name", read_only=True)

    class Meta:
        model = BusinessVerification
        fields = [
            "id",
            "store_id",
            "store_name",
            "pan_number",
            "gst_number",
            "business_address",
            "business_phone",
            "business_email",
            "status",
            "rejection_reason",
            "submitted_at",
            "updated_at",
        ]


class BusinessVerificationSubmitSerializer(serializers.ModelSerializer):
    class Meta:
        model = BusinessVerification
        fields = [
            "pan_number",
            "gst_number",
            "business_address",
            "business_phone",
            "business_email",
        ]
        extra_kwargs = {
            "pan_number": {"max_length": 10, "validators": [pan_validator]},
            "gst_number": {"max_length": 15, "validators": [gst_validator]},
            "business_address": {"min_length": 5, "trim_whitespace": True},
            "business_phone": {"max_length": 20, "validators": [phone_validator], "trim_whitespace": True},
            "business_email": {"trim_whitespace": True},
        }

    def validate_pan_number(self, value):
        val = value.strip().upper()
        store = self.context.get("store")
        qs = BusinessVerification.objects.filter(pan_number__iexact=val)
        if store:
            qs = qs.exclude(store=store)
        if qs.exists():
            raise serializers.ValidationError("This PAN number is already registered with another store.")
        return val

    def validate_gst_number(self, value):
        val = value.strip().upper()
        store = self.context.get("store")
        qs = BusinessVerification.objects.filter(gst_number__iexact=val)
        if store:
            qs = qs.exclude(store=store)
        if qs.exists():
            raise serializers.ValidationError("This GSTIN is already registered with another store.")
        return val

    def validate_business_email(self, value):
        return value.strip().lower()
