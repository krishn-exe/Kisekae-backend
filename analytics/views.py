from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.responses import error_response, success_response
from stores.models import StoreStatus
from stores.permissions import IsBusinessVerified, IsSeller, get_store_membership

from .serializers import (
    AnalyticsDateFilterSerializer,
    DashboardMetricsResponseSerializer,
    OrderMetricsResponseSerializer,
    ProductPerformanceQuerySerializer,
    ProductPerformanceResponseSerializer,
    ReportDownloadQuerySerializer,
    RevenueAnalyticsQuerySerializer,
    RevenueAnalyticsResponseSerializer,
)
from .services import AnalyticsService

ANALYTICS_ERROR_SCHEMA = inline_serializer(
    name="AnalyticsErrorEnvelope",
    fields={
        "success": serializers.BooleanField(default=False),
        "message": serializers.CharField(),
        "error": inline_serializer(
            name="AnalyticsErrorDetail",
            fields={
                "code": serializers.CharField(),
                "details": serializers.JSONField(allow_null=True),
            },
        ),
    },
)


class BaseAnalyticsView(APIView):
    throttle_scope = "store_read"
    permission_classes = [IsAuthenticated, IsSeller, IsBusinessVerified]

    def _get_store_and_check(self, user, store_pk, require_active=True):
        store, membership = get_store_membership(user, store_pk)
        if not store:
            return None, None, error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if not store.is_business_verified:
            return None, None, error_response(
                message="Store business verification must be approved before accessing analytics.",
                code="BUSINESS_VERIFICATION_REQUIRED",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        if require_active and store.status != StoreStatus.ACTIVE:
            return None, None, error_response(
                message=f"Store is currently {store.status.lower()} and cannot be accessed.",
                code="STORE_NOT_ACTIVE",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        return store, membership, None


class AnalyticsDashboardView(BaseAnalyticsView):
    """Retrieve key performance indicators and high-level dashboard metrics for a store."""

    @extend_schema(
        tags=["Store Analytics"],
        summary="Store dashboard KPI metrics",
        description=(
            "Calculates high-level key performance metrics for a store, including total revenue, "
            "order breakdown by lifecycle state (delivered, pending, shipped, cancelled), "
            "total units sold, average order value, cancellation rate, and low stock count."
        ),
        parameters=[
            OpenApiParameter(name="from_date", description="Filter from date (YYYY-MM-DD)", required=False, type=str),
            OpenApiParameter(name="to_date", description="Filter to date (YYYY-MM-DD)", required=False, type=str),
        ],
        responses={
            200: DashboardMetricsResponseSerializer,
            400: ANALYTICS_ERROR_SCHEMA,
            403: ANALYTICS_ERROR_SCHEMA,
            404: ANALYTICS_ERROR_SCHEMA,
        },
        examples=[
            OpenApiExample(
                name="DashboardMetricsSuccessExample",
                summary="Complete Store Dashboard Metrics (200 OK)",
                description="Lists all 11 KPI metric fields calculated from non-cancelled and total orders.",
                value={
                    "success": True,
                    "message": "Dashboard metrics retrieved successfully.",
                    "data": {
                        "total_revenue": 145250.75,
                        "currency": "INR",
                        "total_orders": 350,
                        "delivered_orders": 295,
                        "pending_orders": 28,
                        "shipped_orders": 15,
                        "cancelled_orders": 8,
                        "total_units_sold": 540,
                        "average_order_value": 424.71,
                        "cancellation_rate": 2.29,
                        "low_stock_products_count": 5,
                    },
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                name="DashboardMetricsErrorExample",
                summary="Validation Error (400 Bad Request)",
                value={
                    "success": False,
                    "message": "Invalid date filters.",
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "details": {"from_date": ["from_date cannot be later than to_date."]},
                    },
                },
                response_only=True,
                status_codes=["400"],
            ),
        ],
    )
    def get(self, request, pk):
        store, _, err = self._get_store_and_check(request.user, pk)
        if err:
            return err

        query_serializer = AnalyticsDateFilterSerializer(data=request.query_params)
        if not query_serializer.is_valid():
            return error_response(
                message="Invalid date filters.",
                code="VALIDATION_ERROR",
                status_code=status.HTTP_400_BAD_REQUEST,
                details=query_serializer.errors,
            )

        validated = query_serializer.validated_data
        metrics = AnalyticsService.get_dashboard_metrics(
            store,
            from_date=validated.get("from_date"),
            to_date=validated.get("to_date"),
        )
        return success_response(data=metrics, message="Dashboard metrics retrieved successfully.")


class RevenueAnalyticsView(BaseAnalyticsView):
    """Retrieve revenue time series and summary for a store."""

    @extend_schema(
        tags=["Store Analytics"],
        summary="Store revenue analytics over time",
        description=(
            "Aggregates revenue and order counts into chronological time series buckets (daily, weekly, or monthly). "
            "Returns total period revenue, order volume, average revenue per order, and array of time-series points."
        ),
        parameters=[
            OpenApiParameter(name="period", description="Aggregation period (daily, weekly, monthly)", required=False, type=str),
            OpenApiParameter(name="from_date", description="Filter from date (YYYY-MM-DD)", required=False, type=str),
            OpenApiParameter(name="to_date", description="Filter to date (YYYY-MM-DD)", required=False, type=str),
        ],
        responses={
            200: RevenueAnalyticsResponseSerializer,
            400: ANALYTICS_ERROR_SCHEMA,
            403: ANALYTICS_ERROR_SCHEMA,
            404: ANALYTICS_ERROR_SCHEMA,
        },
        examples=[
            OpenApiExample(
                name="RevenueAnalyticsSuccessExample",
                summary="Complete Store Revenue Analytics (200 OK)",
                description="Lists all summary fields (period, currency, total_revenue, total_orders, average_revenue_per_order) and time_series items (date, revenue, orders_count).",
                value={
                    "success": True,
                    "message": "Revenue analytics retrieved successfully.",
                    "data": {
                        "period": "daily",
                        "currency": "INR",
                        "total_revenue": 45890.00,
                        "total_orders": 120,
                        "average_revenue_per_order": 382.42,
                        "time_series": [
                            {
                                "date": "2026-10-14",
                                "revenue": 14200.00,
                                "orders_count": 38,
                            },
                            {
                                "date": "2026-10-15",
                                "revenue": 16800.00,
                                "orders_count": 44,
                            },
                            {
                                "date": "2026-10-16",
                                "revenue": 14890.00,
                                "orders_count": 38,
                            },
                        ],
                    },
                },
                response_only=True,
                status_codes=["200"],
            ),
        ],
    )
    def get(self, request, pk):
        store, _, err = self._get_store_and_check(request.user, pk)
        if err:
            return err

        query_serializer = RevenueAnalyticsQuerySerializer(data=request.query_params)
        if not query_serializer.is_valid():
            return error_response(
                message="Invalid query parameters.",
                code="VALIDATION_ERROR",
                status_code=status.HTTP_400_BAD_REQUEST,
                details=query_serializer.errors,
            )

        validated = query_serializer.validated_data
        revenue_data = AnalyticsService.get_revenue_analytics(
            store,
            period=validated.get("period", "daily"),
            from_date=validated.get("from_date"),
            to_date=validated.get("to_date"),
        )
        return success_response(data=revenue_data, message="Revenue analytics retrieved successfully.")


class ProductPerformanceView(BaseAnalyticsView):
    """Retrieve product sales rankings and performance analytics."""

    @extend_schema(
        tags=["Store Analytics"],
        summary="Store product sales performance ranking",
        description=(
            "Calculates ranked product performance based on gross revenue or units sold. "
            "Returns product_id, product_name, sku, units_sold, and gross_revenue."
        ),
        parameters=[
            OpenApiParameter(name="sort_by", description="Sort products by: revenue, units_sold (default: revenue)", required=False, type=str),
            OpenApiParameter(name="limit", description="Max number of products to return (1-100)", required=False, type=int),
            OpenApiParameter(name="from_date", description="Filter from date (YYYY-MM-DD)", required=False, type=str),
            OpenApiParameter(name="to_date", description="Filter to date (YYYY-MM-DD)", required=False, type=str),
        ],
        responses={
            200: ProductPerformanceResponseSerializer,
            400: ANALYTICS_ERROR_SCHEMA,
            403: ANALYTICS_ERROR_SCHEMA,
            404: ANALYTICS_ERROR_SCHEMA,
        },
        examples=[
            OpenApiExample(
                name="ProductPerformanceSuccessExample",
                summary="Complete Product Performance Ranking (200 OK)",
                description="Lists performance metrics for each ranked product: product_id, product_name, sku, units_sold, gross_revenue.",
                value={
                    "success": True,
                    "message": "Product performance retrieved successfully.",
                    "data": {
                        "sort_by": "revenue",
                        "total_products_sold": 2,
                        "products": [
                            {
                                "product_id": 101,
                                "product_name": "Premium Cotton Oversized T-Shirt",
                                "sku": "TSH-OVR-001",
                                "units_sold": 220,
                                "gross_revenue": 109780.00,
                            },
                            {
                                "product_id": 102,
                                "product_name": "Minimalist Linen Trousers",
                                "sku": "TRS-LIN-002",
                                "units_sold": 85,
                                "gross_revenue": 67915.00,
                            },
                        ],
                    },
                },
                response_only=True,
                status_codes=["200"],
            ),
        ],
    )
    def get(self, request, pk):
        store, _, err = self._get_store_and_check(request.user, pk)
        if err:
            return err

        query_serializer = ProductPerformanceQuerySerializer(data=request.query_params)
        if not query_serializer.is_valid():
            return error_response(
                message="Invalid query parameters.",
                code="VALIDATION_ERROR",
                status_code=status.HTTP_400_BAD_REQUEST,
                details=query_serializer.errors,
            )

        validated = query_serializer.validated_data
        perf_data = AnalyticsService.get_product_performance(
            store,
            sort_by=validated.get("sort_by", "revenue"),
            limit=validated.get("limit", 20),
            from_date=validated.get("from_date"),
            to_date=validated.get("to_date"),
        )
        return success_response(data=perf_data, message="Product performance retrieved successfully.")


class OrderMetricsView(BaseAnalyticsView):
    """Retrieve order status breakdown, cancellation rate, and fulfillment velocity."""

    @extend_schema(
        tags=["Store Analytics"],
        summary="Store order metrics & fulfillment velocity",
        description=(
            "Aggregates order counts across all 6 lifecycle statuses (PENDING, CONFIRMED, PROCESSING, SHIPPED, "
            "DELIVERED, CANCELLED), calculates cancellation percentage, "
            "and measures average fulfillment duration in hours for delivered orders."
        ),
        parameters=[
            OpenApiParameter(name="from_date", description="Filter from date (YYYY-MM-DD)", required=False, type=str),
            OpenApiParameter(name="to_date", description="Filter to date (YYYY-MM-DD)", required=False, type=str),
        ],
        responses={
            200: OrderMetricsResponseSerializer,
            400: ANALYTICS_ERROR_SCHEMA,
            403: ANALYTICS_ERROR_SCHEMA,
            404: ANALYTICS_ERROR_SCHEMA,
        },
        examples=[
            OpenApiExample(
                name="OrderMetricsSuccessExample",
                summary="Complete Order Metrics Breakdown (200 OK)",
                description="Lists all status_breakdown keys (PENDING, CONFIRMED, PROCESSING, SHIPPED, DELIVERED, CANCELLED), cancellation_rate, and average_fulfillment_hours.",
                value={
                    "success": True,
                    "message": "Order metrics retrieved successfully.",
                    "data": {
                        "total_orders": 350,
                        "status_breakdown": {
                            "PENDING": 12,
                            "CONFIRMED": 10,
                            "PROCESSING": 6,
                            "SHIPPED": 15,
                            "DELIVERED": 295,
                            "CANCELLED": 8,
                        },
                        "cancellation_rate": 2.29,
                        "average_fulfillment_hours": 21.4,
                    },
                },
                response_only=True,
                status_codes=["200"],
            ),
        ],
    )
    def get(self, request, pk):
        store, _, err = self._get_store_and_check(request.user, pk)
        if err:
            return err

        query_serializer = AnalyticsDateFilterSerializer(data=request.query_params)
        if not query_serializer.is_valid():
            return error_response(
                message="Invalid date filters.",
                code="VALIDATION_ERROR",
                status_code=status.HTTP_400_BAD_REQUEST,
                details=query_serializer.errors,
            )

        validated = query_serializer.validated_data
        metrics = AnalyticsService.get_order_metrics(
            store,
            from_date=validated.get("from_date"),
            to_date=validated.get("to_date"),
        )
        return success_response(data=metrics, message="Order metrics retrieved successfully.")



class ReportDownloadView(BaseAnalyticsView):
    """Generate and download a CSV report (sales, inventory, orders)."""

    @extend_schema(
        tags=["Store Analytics"],
        summary="Download CSV reports",
        description="Generates and streams a CSV report for sales, inventory, or orders within the optional date filter range.",
        parameters=[
            OpenApiParameter(name="report_type", description="Type of report: 'sales', 'inventory', or 'orders' (default: 'sales')", required=False, type=str),
            OpenApiParameter(name="from_date", description="Filter from date (YYYY-MM-DD)", required=False, type=str),
            OpenApiParameter(name="to_date", description="Filter to date (YYYY-MM-DD)", required=False, type=str),
        ],
        responses={
            (200, "text/csv"): OpenApiResponse(
                description="CSV file spreadsheet download.",
                response=OpenApiTypes.BINARY,
            ),
            400: ANALYTICS_ERROR_SCHEMA,
            403: ANALYTICS_ERROR_SCHEMA,
            404: ANALYTICS_ERROR_SCHEMA,
        },
    )
    def get(self, request, pk):
        store, _, err = self._get_store_and_check(request.user, pk)
        if err:
            return err

        query_serializer = ReportDownloadQuerySerializer(data=request.query_params)
        if not query_serializer.is_valid():
            return error_response(
                message="Invalid report query parameters.",
                code="VALIDATION_ERROR",
                status_code=status.HTTP_400_BAD_REQUEST,
                details=query_serializer.errors,
            )

        validated = query_serializer.validated_data
        report_type = validated.get("report_type", "sales")
        csv_data = AnalyticsService.generate_csv_report(
            store,
            report_type=report_type,
            from_date=validated.get("from_date"),
            to_date=validated.get("to_date"),
        )

        now_str = timezone.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{report_type}_report_store_{store.id}_{now_str}.csv"

        response = HttpResponse(csv_data, content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
