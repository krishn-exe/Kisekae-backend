from django.contrib import admin

from .models import (
    BusinessVerification,
    Store,
    StoreBankAccount,
    StoreInvitation,
    StoreMembership,
    StorePaymentSettings,
    StoreSettlement,
    VerificationStatus,
)


class BusinessVerificationInline(admin.StackedInline):
    model = BusinessVerification
    extra = 0
    can_delete = False
    fields = (
        "pan_number",
        "gst_number",
        "business_email",
        "business_phone",
        "business_address",
        "status",
        "rejection_reason",
        "submitted_by",
        "submitted_at",
        "updated_at",
    )
    readonly_fields = ("submitted_at", "updated_at")


class StoreBankAccountInline(admin.StackedInline):
    model = StoreBankAccount
    extra = 0
    can_delete = False
    fields = ("account_holder_name", "account_number", "ifsc_code", "bank_name", "branch_name", "account_type", "created_at")
    readonly_fields = ("created_at",)


class StorePaymentSettingsInline(admin.StackedInline):
    model = StorePaymentSettings
    extra = 0
    can_delete = False
    fields = ("settlement_day", "auto_settlement_enabled", "monthly_advertising_budget", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")

class StoreMembershipInline(admin.TabularInline):
    model = StoreMembership
    extra = 1
    autocomplete_fields = ["user"]
    fields = ("user", "role", "created_at")
    readonly_fields = ("created_at",)


class StoreInvitationInline(admin.TabularInline):
    model = StoreInvitation
    extra = 0
    fields = ("email", "status", "invited_by", "expires_at", "created_at")
    readonly_fields = ("created_at",)


@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "status", "created_by", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("name", "slug", "contact_email")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at", "archived_at")
    inlines = [BusinessVerificationInline, StoreBankAccountInline, StorePaymentSettingsInline, StoreMembershipInline, StoreInvitationInline]
    actions = ["suspend_stores", "archive_stores"]

    @admin.action(description="Suspend selected stores")
    def suspend_stores(self, request, queryset):
        for store in queryset:
            store.suspend()

    @admin.action(description="Archive selected stores")
    def archive_stores(self, request, queryset):
        for store in queryset:
            store.archive()


@admin.register(StoreMembership)
class StoreMembershipAdmin(admin.ModelAdmin):
    list_display = ("store", "user", "role", "created_at")
    list_filter = ("role", "created_at")
    search_fields = ("store__name", "user__email")
    readonly_fields = ("created_at", "updated_at")


@admin.register(StoreInvitation)
class StoreInvitationAdmin(admin.ModelAdmin):
    list_display = ("store", "email", "status", "invited_by", "expires_at", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("store__name", "email")
    readonly_fields = ("created_at", "token_hash")


@admin.register(BusinessVerification)
class BusinessVerificationAdmin(admin.ModelAdmin):
    list_display = (
        "store",
        "pan_number",
        "gst_number",
        "status",
        "submitted_by",
        "submitted_at",
        "updated_at",
    )
    list_filter = ("status", "submitted_at")
    search_fields = (
        "store__name",
        "pan_number",
        "gst_number",
        "business_email",
        "business_phone",
    )
    readonly_fields = ("submitted_at", "updated_at")
    actions = ["approve_verifications", "reject_verifications"]

    @admin.action(description="Approve selected business verifications")
    def approve_verifications(self, request, queryset):
        count = 0
        for obj in queryset:
            obj.status = VerificationStatus.APPROVED
            obj.rejection_reason = ""
            obj.save()
            count += 1
        self.message_user(request, f"{count} business verification(s) approved successfully.")

    @admin.action(description="Reject selected business verifications")
    def reject_verifications(self, request, queryset):
        count = 0
        for obj in queryset:
            obj.status = VerificationStatus.REJECTED
            obj.save()
            count += 1
        self.message_user(request, f"{count} business verification(s) rejected.")


@admin.register(StoreBankAccount)
class StoreBankAccountAdmin(admin.ModelAdmin):
    list_display = ("store", "account_holder_name", "account_number_masked", "ifsc_code", "bank_name", "created_at")
    search_fields = ("store__name", "account_holder_name", "ifsc_code", "bank_name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(StorePaymentSettings)
class StorePaymentSettingsAdmin(admin.ModelAdmin):
    list_display = ("store", "settlement_day", "auto_settlement_enabled", "monthly_advertising_budget", "updated_at")
    list_filter = ("auto_settlement_enabled", "settlement_day")
    search_fields = ("store__name",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(StoreSettlement)
class StoreSettlementAdmin(admin.ModelAdmin):
    list_display = ("settlement_number", "store", "amount", "status", "settlement_type", "processed_at")
    list_filter = ("status", "settlement_type", "processed_at")
    search_fields = ("settlement_number", "store__name", "account_holder_name_snapshot", "ifsc_code_snapshot")
    readonly_fields = ("created_at", "updated_at")
