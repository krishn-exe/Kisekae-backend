from django.urls import path

from .views import (
    AnalyticsDashboardView,
    OrderMetricsView,
    ProductPerformanceView,
    ReportDownloadView,
    RevenueAnalyticsView,
)

urlpatterns = [
    path("analytics/dashboard/", AnalyticsDashboardView.as_view(), name="store-analytics-dashboard"),
    path("analytics/revenue/", RevenueAnalyticsView.as_view(), name="store-analytics-revenue"),
    path("analytics/products/", ProductPerformanceView.as_view(), name="store-analytics-products"),
    path("analytics/orders/", OrderMetricsView.as_view(), name="store-analytics-orders"),
    path("analytics/reports/download/", ReportDownloadView.as_view(), name="store-analytics-reports-download"),
]
