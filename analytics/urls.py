from django.urls import path

from .views import (
    AnalyticsDashboardView,
    OrderMetricsView,
    ProductPerformanceView,
    ReportDownloadView,
    RevenueAnalyticsView,
)

urlpatterns = [
    path("dashboard/", AnalyticsDashboardView.as_view(), name="store-analytics-dashboard"),
    path("revenue/", RevenueAnalyticsView.as_view(), name="store-analytics-revenue"),
    path("products/", ProductPerformanceView.as_view(), name="store-analytics-products"),
    path("orders/", OrderMetricsView.as_view(), name="store-analytics-orders"),
    path("reports/download/", ReportDownloadView.as_view(), name="store-analytics-reports-download"),
]
