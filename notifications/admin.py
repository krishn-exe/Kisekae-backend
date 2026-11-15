from django.contrib import admin
from .models import CustomerDevice


@admin.register(CustomerDevice)
class CustomerDeviceAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "device_name",
        "device_type",
        "is_active",
        "registration_token_masked",
        "created_at",
        "updated_at",
    )
    list_filter = ("device_type", "is_active", "created_at")
    search_fields = ("user__email", "user__name", "device_name", "registration_token")
    autocomplete_fields = ["user"]
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="FCM Token")
    def registration_token_masked(self, obj):
        token = obj.registration_token or ""
        if len(token) > 16:
            return f"{token[:8]}...{token[-8:]}"
        return token
