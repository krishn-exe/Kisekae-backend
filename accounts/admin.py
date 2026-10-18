from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Address, User, WishlistItem


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("email", "name", "is_staff", "is_active", "is_email_verified", "is_seller")
    list_filter = ("is_staff", "is_superuser", "is_active", "is_email_verified", "is_seller")
    search_fields = ("email", "name")
    ordering = ("email",)
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("name",)}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Verification & Roles", {"fields": ("is_email_verified", "is_seller")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "name", "password1", "password2"),
        }),
    )
    readonly_fields = ("date_joined", "last_login")


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "address_line_1", "city", "state", "postal_code", "is_default", "created_at")
    list_filter = ("is_default", "state", "country")
    search_fields = ("user__email", "title", "address_line_1", "postal_code", "recipient_name")


@admin.register(WishlistItem)
class WishlistItemAdmin(admin.ModelAdmin):
    list_display = ("user", "product", "note", "created_at")
    search_fields = ("user__email", "product__name", "note")
    list_filter = ("created_at",)

