from decimal import Decimal
from django.contrib.auth import get_user_model

from django.core.exceptions import ValidationError as DjangoValidationError

from drf_spectacular.utils import extend_schema_field

from rest_framework import serializers

from .models import (

    AccountType,

    BusinessVerification,

    Store,

    StoreBankAccount,

    StoreInvitation,

    StoreMembership,

    StorePaymentSettings,

    StoreSettlement,

    StoreStatus,

)

from .validators import (

    MAX_BANNER_SIZE,

    MAX_LOGO_SIZE,

    account_holder_validator,

    bank_account_validator,

    gst_validator,

    ifsc_validator,

    pan_validator,

    phone_validator,

    settlement_day_validator,

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

# ---------------------------------------------------------------------------

# Store Payments & Settlement Serializers

# ---------------------------------------------------------------------------

class StoreBankAccountSerializer(serializers.ModelSerializer):

    account_number_masked = serializers.CharField(read_only=True)

    class Meta:

        model = StoreBankAccount

        fields = [

            "id",

            "account_holder_name",

            "account_number",

            "account_number_masked",

            "ifsc_code",

            "bank_name",

            "branch_name",

            "account_type",

            "created_at",

            "updated_at",

        ]

        extra_kwargs = {

            "account_number": {"write_only": False},

        }

    def validate_account_holder_name(self, value):

        val = str(value).strip()

        account_holder_validator(val)

        return val

    def validate_account_number(self, value):

        val = str(value).strip()

        bank_account_validator(val)

        return val

    def validate_ifsc_code(self, value):

        val = str(value).strip().upper()

        ifsc_validator(val)

        return val

    def to_representation(self, instance):

        ret = super().to_representation(instance)

        # Mask account number for security in responses unless context asks for full

        request = self.context.get("request")

        user = getattr(request, "user", None)

        # Only full account number for owner when specifically allowed

        if not self.context.get("show_full_account", False):

            ret["account_number"] = instance.account_number_masked

        return ret

class StorePaymentSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = StorePaymentSettings
        fields = [
            "settlement_day",
            "auto_settlement_enabled",
            "created_at",
            "updated_at",
        ]

    def validate_settlement_day(self, value):
        raw_val = str(value).strip()
        settlement_day_validator(raw_val)
        val = int(raw_val)
        if val < 1 or val > 31:
            raise serializers.ValidationError("Settlement day must be between 1 and 31.")
        return val


class StoreAdvertisingBudgetSerializer(serializers.ModelSerializer):
    class Meta:
        model = StorePaymentSettings
        fields = [
            "monthly_advertising_budget",
            "updated_at",
        ]

    def validate_monthly_advertising_budget(self, value):
        if value < 0:
            raise serializers.ValidationError("Monthly advertising budget cannot be negative.")
        return value

class StoreSettlementSerializer(serializers.ModelSerializer):

    class Meta:

        model = StoreSettlement

        fields = [

            "id",

            "settlement_number",

            "amount",

            "status",

            "settlement_type",

            "account_holder_name_snapshot",

            "account_number_snapshot",

            "ifsc_code_snapshot",

            "bank_name_snapshot",

            "notes",

            "processed_at",

            "created_at",

        ]

class StoreDirectSettleSerializer(serializers.Serializer):

    amount = serializers.DecimalField(

        max_digits=12,

        decimal_places=2,

        required=False,

        allow_null=True,

        min_value=Decimal("0.01"),

        help_text="Optional custom amount to settle. If omitted, settles entire available balance.",

    )

    notes = serializers.CharField(

        required=False,

        allow_blank=True,

        default="",

        max_length=500,

        help_text="Optional note or reference for the payout.",

    )

class StoreMonthlySummaryPeriodSerializer(serializers.Serializer):

    year = serializers.IntegerField()

    month = serializers.IntegerField()

    days_in_month = serializers.IntegerField()

class StoreMonthlySummaryMetricsSerializer(serializers.Serializer):

    total_revenue = serializers.CharField()

    platform_cut_percentage = serializers.FloatField()

    gross_platform_cut = serializers.CharField()

    coupon_discount_absorbed = serializers.CharField()

    net_platform_cut = serializers.CharField()

    advertising_cost = serializers.CharField()

    final_gross_profit = serializers.CharField()

    orders_count = serializers.IntegerField()

class StoreMonthlySummaryAdvertisingSerializer(serializers.Serializer):

    monthly_budget = serializers.CharField()

    current_ad_spend = serializers.CharField()

class StoreMonthlySummarySettlementSerializer(serializers.Serializer):

    available_unsettled_balance = serializers.CharField()

    total_settled_this_month = serializers.CharField()

    total_settled_lifetime = serializers.CharField()

    lifetime_gross_profit = serializers.CharField()

    auto_settlement_enabled = serializers.BooleanField()

    settlement_day = serializers.IntegerField()

    next_settlement_date = serializers.CharField(allow_null=True)

class StoreMonthlySummaryBankAccountSerializer(serializers.Serializer):

    is_linked = serializers.BooleanField()

    account_holder_name = serializers.CharField(allow_null=True)

    account_number_masked = serializers.CharField(allow_null=True)

    ifsc_code = serializers.CharField(allow_null=True)

    bank_name = serializers.CharField(allow_null=True)

    branch_name = serializers.CharField(allow_null=True)

    account_type = serializers.CharField(allow_null=True)

    added_at = serializers.CharField(allow_null=True)

class StorePaymentDashboardSerializer(serializers.Serializer):

    period = StoreMonthlySummaryPeriodSerializer()

    currency = serializers.CharField()

    summary = StoreMonthlySummaryMetricsSerializer()

    advertising = StoreMonthlySummaryAdvertisingSerializer()

    settlement_overview = StoreMonthlySummarySettlementSerializer()

    bank_account = StoreMonthlySummaryBankAccountSerializer()


class PromotedProductItemSerializer(serializers.Serializer):
    promoted_product_id = serializers.IntegerField(source="id")
    product_id = serializers.IntegerField(source="product.id")
    product_name = serializers.CharField(source="product.name")
    product_price = serializers.DecimalField(source="product.price", max_digits=10, decimal_places=2)
    is_active = serializers.BooleanField()
    created_at = serializers.DateTimeField()


class PromoteProductsRequestSerializer(serializers.Serializer):
    product_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=False,
        help_text="List of product IDs to promote for this store.",
    )


class PromotedProductUpdateSerializer(serializers.Serializer):
    is_active = serializers.BooleanField(help_text="Enable or disable sponsored promotion for this product.")


class AdvertisingProductStatSerializer(serializers.Serializer):
    promoted_product_id = serializers.IntegerField()
    product_id = serializers.IntegerField()
    product_name = serializers.CharField()
    product_price = serializers.CharField()
    is_active = serializers.BooleanField()
    clicks = serializers.IntegerField()
    orders = serializers.IntegerField()
    units_sold = serializers.IntegerField()
    spend = serializers.CharField()
    created_at = serializers.CharField()


class StoreAdvertisingAnalyticsSerializer(serializers.Serializer):
    period = serializers.CharField()
    monthly_budget = serializers.CharField()
    current_spend = serializers.CharField()
    remaining_budget = serializers.CharField()
    total_clicks = serializers.IntegerField()
    total_orders = serializers.IntegerField()
    total_units_sold = serializers.IntegerField()
    conversion_rate = serializers.FloatField()
    cpc_rate = serializers.CharField()
    cpa_rate = serializers.CharField()
    promoted_products_count = serializers.IntegerField()
    products = AdvertisingProductStatSerializer(many=True)
