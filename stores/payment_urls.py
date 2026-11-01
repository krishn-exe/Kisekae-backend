from django.urls import path

from .payment_views import (
    StoreBankAccountView,
    StoreDirectSettleView,
    StorePaymentsDashboardView,
    StorePaymentSettingsView,
    StoreSettlementListView,
)

urlpatterns = [
    path("dashboard/", StorePaymentsDashboardView.as_view(), name="store-payments-dashboard"),

    path("bank-account/", StoreBankAccountView.as_view(), name="store-payments-bank-account"),

    path("settings/", StorePaymentSettingsView.as_view(), name="store-payments-settings"),

    path("settle/", StoreDirectSettleView.as_view(), name="store-payments-settle"),

    path("settlements/", StoreSettlementListView.as_view(), name="store-payments-settlements"),
]
