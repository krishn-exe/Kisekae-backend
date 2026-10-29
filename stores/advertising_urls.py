from django.urls import path

from .advertising_views import StoreAdvertisingBudgetView

urlpatterns = [
    path("budget/", StoreAdvertisingBudgetView.as_view(), name="store-advertising-budget"),
]
