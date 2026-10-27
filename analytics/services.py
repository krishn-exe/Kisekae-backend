import csv
import io
from datetime import datetime
from decimal import Decimal

from django.db.models import Count, F, Sum
from django.db.models.functions import TruncDate, TruncMonth, TruncWeek

from inventory.services import InventoryService
from orders.models import Order, OrderItem, OrderStatus
from products.models import Product


class AnalyticsService:
    """Service providing analytical aggregations and CSV reporting for stores."""

    @staticmethod
    def parse_date(date_str):
        """Safely parses YYYY-MM-DD string into a date object."""
        if not date_str:
            return None
        if isinstance(date_str, datetime):
            return date_str.date()
        try:
            return datetime.strptime(str(date_str).strip(), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    @classmethod
    def get_dashboard_metrics(cls, store, from_date=None, to_date=None):
        """Calculates key performance metrics for a store."""
        orders = Order.objects.filter(store=store)

        from_d = cls.parse_date(from_date)
        to_d = cls.parse_date(to_date)
        if from_d:
            orders = orders.filter(created_at__date__gte=from_d)
        if to_d:
            orders = orders.filter(created_at__date__lte=to_d)

        total_orders = orders.count()
        delivered_orders = orders.filter(status=OrderStatus.DELIVERED).count()
        pending_orders = orders.filter(
            status__in=[OrderStatus.PENDING, OrderStatus.CONFIRMED, OrderStatus.PROCESSING]
        ).count()
        shipped_orders = orders.filter(status=OrderStatus.SHIPPED).count()
        cancelled_orders = orders.filter(status=OrderStatus.CANCELLED).count()

        non_cancelled = orders.exclude(status=OrderStatus.CANCELLED)
        revenue_agg = non_cancelled.aggregate(total=Sum("total_amount"))["total"]
        total_revenue = Decimal(str(revenue_agg or "0.00"))

        valid_orders_count = non_cancelled.count()
        average_order_value = (
            round(float(total_revenue) / valid_orders_count, 2)
            if valid_orders_count > 0
            else 0.0
        )

        units_agg = OrderItem.objects.filter(order__in=non_cancelled).aggregate(
            total=Sum("quantity")
        )["total"]
        total_units_sold = units_agg or 0

        cancellation_rate = (
            round((cancelled_orders / total_orders) * 100, 2)
            if total_orders > 0
            else 0.0
        )

        low_stock_count = len(InventoryService.get_low_stock_products(store))

        return {
            "total_revenue": float(total_revenue),
            "currency": "INR",
            "total_orders": total_orders,
            "delivered_orders": delivered_orders,
            "pending_orders": pending_orders,
            "shipped_orders": shipped_orders,
            "cancelled_orders": cancelled_orders,
            "total_units_sold": total_units_sold,
            "average_order_value": average_order_value,
            "cancellation_rate": cancellation_rate,
            "low_stock_products_count": low_stock_count,
        }

    @classmethod
    def get_revenue_analytics(cls, store, period="daily", from_date=None, to_date=None):
        """Calculates revenue time-series data aggregated daily, weekly, or monthly."""
        orders = Order.objects.filter(store=store).exclude(status=OrderStatus.CANCELLED)

        from_d = cls.parse_date(from_date)
        to_d = cls.parse_date(to_date)
        if from_d:
            orders = orders.filter(created_at__date__gte=from_d)
        if to_d:
            orders = orders.filter(created_at__date__lte=to_d)

        period = (period or "daily").lower()
        if period == "monthly":
            trunc_func = TruncMonth("created_at")
            date_format = "%Y-%m"
        elif period == "weekly":
            trunc_func = TruncWeek("created_at")
            date_format = "%Y-%W"
        else:
            trunc_func = TruncDate("created_at")
            date_format = "%Y-%m-%d"

        rows = (
            orders.annotate(period_date=trunc_func)
            .values("period_date")
            .annotate(
                revenue=Sum("total_amount"),
                orders_count=Count("id"),
            )
            .order_by("period_date")
        )

        series = []
        total_revenue = Decimal("0.00")
        total_orders = 0

        for row in rows:
            p_date = row["period_date"]
            if hasattr(p_date, "strftime"):
                formatted_date = p_date.strftime(date_format)
            else:
                formatted_date = str(p_date)

            rev = Decimal(str(row["revenue"] or "0.00"))
            cnt = row["orders_count"]
            total_revenue += rev
            total_orders += cnt

            series.append({
                "date": formatted_date,
                "revenue": float(rev),
                "orders_count": cnt,
            })

        avg_rev = (
            round(float(total_revenue) / total_orders, 2)
            if total_orders > 0
            else 0.0
        )

        return {
            "period": period,
            "currency": "INR",
            "total_revenue": float(total_revenue),
            "total_orders": total_orders,
            "average_revenue_per_order": avg_rev,
            "time_series": series,
        }

    @classmethod
    def get_product_performance(cls, store, sort_by="revenue", limit=20, from_date=None, to_date=None):
        """Calculates sales and returns performance ranking for store products."""
        items = OrderItem.objects.filter(
            order__store=store
        ).exclude(order__status=OrderStatus.CANCELLED)

        from_d = cls.parse_date(from_date)
        to_d = cls.parse_date(to_date)
        if from_d:
            items = items.filter(order__created_at__date__gte=from_d)
        if to_d:
            items = items.filter(order__created_at__date__lte=to_d)

        items_agg = (
            items.values("product_id", "product_name", "product_sku")
            .annotate(
                units_sold=Sum("quantity"),
                gross_revenue=Sum("total_price"),
            )
        )

        results = []
        for row in items_agg:
            pid = row["product_id"]
            sold = row["units_sold"] or 0
            rev = Decimal(str(row["gross_revenue"] or "0.00"))

            results.append({
                "product_id": pid,
                "product_name": row["product_name"],
                "sku": row["product_sku"] or "",
                "units_sold": sold,
                "gross_revenue": float(rev),
            })

        # Sorting
        sort_by = (sort_by or "revenue").lower()
        if sort_by == "units_sold":
            results.sort(key=lambda x: x["units_sold"], reverse=True)
        else:
            results.sort(key=lambda x: x["gross_revenue"], reverse=True)

        try:
            lim = int(limit)
            if lim > 0:
                results = results[:lim]
        except (ValueError, TypeError):
            results = results[:20]

        return {
            "sort_by": sort_by,
            "total_products_sold": len(results),
            "products": results,
        }

    @classmethod
    def get_order_metrics(cls, store, from_date=None, to_date=None):
        """Calculates breakdown of order statuses, fulfillment velocity, and cancellation rates."""
        orders = Order.objects.filter(store=store)

        from_d = cls.parse_date(from_date)
        to_d = cls.parse_date(to_date)
        if from_d:
            orders = orders.filter(created_at__date__gte=from_d)
        if to_d:
            orders = orders.filter(created_at__date__lte=to_d)

        total_orders = orders.count()

        # Status counts breakdown
        status_counts = {choice.value: 0 for choice in OrderStatus}
        counts_agg = orders.values("status").annotate(cnt=Count("id"))
        for row in counts_agg:
            if row["status"] in status_counts:
                status_counts[row["status"]] = row["cnt"]

        cancelled = status_counts.get(OrderStatus.CANCELLED, 0)
        delivered = status_counts.get(OrderStatus.DELIVERED, 0)

        cancellation_rate = (
            round((cancelled / total_orders) * 100, 2)
            if total_orders > 0
            else 0.0
        )

        # Average fulfillment time in hours for DELIVERED orders
        delivered_orders = orders.filter(status=OrderStatus.DELIVERED).prefetch_related("status_history")
        total_fulfillment_hours = 0.0
        delivered_count = delivered_orders.count()

        if delivered_count > 0:
            for o in delivered_orders:
                history_entry = (
                    o.status_history.filter(to_status=OrderStatus.DELIVERED)
                    .order_by("created_at")
                    .first()
                )
                delivered_at = history_entry.created_at if history_entry else o.updated_at
                diff_hours = (delivered_at - o.created_at).total_seconds() / 3600.0
                total_fulfillment_hours += max(diff_hours, 0.0)

            avg_fulfillment_hours = round(total_fulfillment_hours / delivered_count, 1)
        else:
            avg_fulfillment_hours = None

        return {
            "total_orders": total_orders,
            "status_breakdown": status_counts,
            "cancellation_rate": cancellation_rate,
            "average_fulfillment_hours": avg_fulfillment_hours,
        }

    @classmethod
    def generate_csv_report(cls, store, report_type="sales", from_date=None, to_date=None):
        """Generates a CSV report string based on report_type."""
        output = io.StringIO()
        writer = csv.writer(output)

        report_type = (report_type or "sales").lower()
        from_d = cls.parse_date(from_date)
        to_d = cls.parse_date(to_date)

        if report_type == "inventory":
            writer.writerow([
                "Product Name",
                "SKU",
                "Current Stock",
                "Price (INR)",
                "Compare At Price (INR)",
                "Status",
                "Low Stock Threshold",
                "Is Low Stock",
            ])
            products = Product.objects.filter(store=store).select_related("stock_alert")
            for p in products:
                threshold = getattr(p, "stock_alert", None)
                thresh_val = threshold.threshold if threshold else 5
                is_low = p.stock <= thresh_val
                writer.writerow([
                    p.name,
                    p.sku or "",
                    p.stock,
                    str(p.price),
                    str(p.compare_at_price or ""),
                    p.status,
                    thresh_val,
                    "YES" if is_low else "NO",
                ])

        elif report_type == "orders":
            writer.writerow([
                "Order Number",
                "Date",
                "Status",
                "Customer Name",
                "Customer Phone",
                "Customer Email",
                "Total Amount (INR)",
                "Tracking Number",
                "Shipping Address",
            ])
            orders = Order.objects.filter(store=store)
            if from_d:
                orders = orders.filter(created_at__date__gte=from_d)
            if to_d:
                orders = orders.filter(created_at__date__lte=to_d)
            for o in orders:
                writer.writerow([
                    o.order_number,
                    o.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                    o.status,
                    o.customer_name,
                    o.customer_phone,
                    o.customer_email,
                    str(o.total_amount),
                    o.tracking_number or "",
                    o.shipping_address.replace("\n", ", "),
                ])

        else:
            # Default: sales report
            writer.writerow([
                "Order Number",
                "Date",
                "Customer Name",
                "Customer Email",
                "Total Amount (INR)",
                "Status",
                "Items Count",
            ])
            orders = Order.objects.filter(store=store).exclude(
                status=OrderStatus.CANCELLED
            ).prefetch_related("items")
            if from_d:
                orders = orders.filter(created_at__date__gte=from_d)
            if to_d:
                orders = orders.filter(created_at__date__lte=to_d)
            for o in orders:
                items_count = sum(item.quantity for item in o.items.all())
                writer.writerow([
                    o.order_number,
                    o.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                    o.customer_name,
                    o.customer_email,
                    str(o.total_amount),
                    o.status,
                    items_count,
                ])

        return output.getvalue()
