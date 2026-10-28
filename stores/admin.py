from django.contrib import admin

from .models import (
    BusinessVerification,
    Store,
    StoreInvitation,
    StoreMembership,
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
    inlines = [BusinessVerificationInline, StoreMembershipInline, StoreInvitationInline]
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
