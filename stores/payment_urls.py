from django.urls import path

from .payment_views import (
    StoreBankAccountView,
    StoreDirectSettleView,
    StorePaymentsDashboardView,
    StorePaymentSettingsView,
    StoreSettlementListView,
)

urlpatterns = [
    # Payments Dashboard / Monthly Summary
    path("dashboard/", StorePaymentsDashboardView.as_view(), name="store-payments-dashboard"),

    # Bank Account Management (Owner-only for modifications)
    path("bank-account/", StoreBankAccountView.as_view(), name="store-payments-bank-account"),

    # Payment Settings (Settlement date & Advertising budget)
    path("settings/", StorePaymentSettingsView.as_view(), name="store-payments-settings"),

    # Direct / On-Demand Payout Settlement
    path("settle/", StoreDirectSettleView.as_view(), name="store-payments-settle"),

    # Historical Settlements
    path("settlements/", StoreSettlementListView.as_view(), name="store-payments-settlements"),
]
