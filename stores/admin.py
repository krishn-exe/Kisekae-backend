from django.contrib import admin

from .models import Store, StoreMembership


class StoreMembershipInline(admin.TabularInline):
    model = StoreMembership
    extra = 1
    autocomplete_fields = ["user"]
    fields = ("user", "role", "created_at")
    readonly_fields = ("created_at",)


@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "status", "created_by", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("name", "slug", "contact_email")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at", "archived_at")
    inlines = [StoreMembershipInline]


@admin.register(StoreMembership)
class StoreMembershipAdmin(admin.ModelAdmin):
    list_display = ("store", "user", "role", "created_at")
    list_filter = ("role", "created_at")
    search_fields = ("store__name", "user__email")
    readonly_fields = ("created_at", "updated_at")
