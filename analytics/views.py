from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.responses import error_response, success_response
from stores.models import StoreStatus
from stores.permissions import IsBusinessVerified, IsSeller, get_store_membership

from .serializers import (
    AnalyticsDateFilterSerializer,
    ProductPerformanceQuerySerializer,
    ReportDownloadQuerySerializer,
    RevenueAnalyticsQuerySerializer,
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
        parameters=[
            OpenApiParameter(name="from_date", description="Filter from date (YYYY-MM-DD)", required=False, type=str),
            OpenApiParameter(name="to_date", description="Filter to date (YYYY-MM-DD)", required=False, type=str),
        ],
        responses={200: inline_serializer(name="DashboardMetricsEnvelope", fields={"success": serializers.BooleanField(), "data": serializers.JSONField()})},
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
        parameters=[
            OpenApiParameter(name="period", description="Aggregation period (daily, weekly, monthly)", required=False, type=str),
            OpenApiParameter(name="from_date", description="Filter from date (YYYY-MM-DD)", required=False, type=str),
            OpenApiParameter(name="to_date", description="Filter to date (YYYY-MM-DD)", required=False, type=str),
        ],
        responses={200: inline_serializer(name="RevenueAnalyticsEnvelope", fields={"success": serializers.BooleanField(), "data": serializers.JSONField()})},
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
    """Retrieve product sales rankings and return rate analytics."""

    @extend_schema(
        tags=["Store Analytics"],
        summary="Store product sales and return performance",
        parameters=[
            OpenApiParameter(name="sort_by", description="Sort products by: revenue, units_sold, returns", required=False, type=str),
            OpenApiParameter(name="limit", description="Max number of products to return", required=False, type=int),
            OpenApiParameter(name="from_date", description="Filter from date (YYYY-MM-DD)", required=False, type=str),
            OpenApiParameter(name="to_date", description="Filter to date (YYYY-MM-DD)", required=False, type=str),
        ],
        responses={200: inline_serializer(name="ProductPerformanceEnvelope", fields={"success": serializers.BooleanField(), "data": serializers.JSONField()})},
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
        parameters=[
            OpenApiParameter(name="from_date", description="Filter from date (YYYY-MM-DD)", required=False, type=str),
            OpenApiParameter(name="to_date", description="Filter to date (YYYY-MM-DD)", required=False, type=str),
        ],
        responses={200: inline_serializer(name="OrderMetricsEnvelope", fields={"success": serializers.BooleanField(), "data": serializers.JSONField()})},
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
        parameters=[
            OpenApiParameter(name="report_type", description="Type of report (sales, inventory, orders)", required=False, type=str),
            OpenApiParameter(name="from_date", description="Filter from date (YYYY-MM-DD)", required=False, type=str),
            OpenApiParameter(name="to_date", description="Filter to date (YYYY-MM-DD)", required=False, type=str),
        ],
        responses={200: inline_serializer(name="CSVFileDownload", fields={})},
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
