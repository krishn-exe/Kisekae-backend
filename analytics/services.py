import csv
import io
from datetime import datetime
from decimal import Decimal

from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import TruncDate, TruncMonth, TruncWeek

from inventory.services import InventoryService
from orders.models import Order, OrderItem, OrderStatus
from products.models import Product


class AnalyticsService:
    """Service providing high-performance analytical aggregations and CSV reporting for stores."""

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
        """
        Calculates key performance metrics for a store in a single conditional SQL roundtrip.
        """
        orders = Order.objects.filter(store=store)

        from_d = cls.parse_date(from_date)
        to_d = cls.parse_date(to_date)
        if from_d:
            orders = orders.filter(created_at__date__gte=from_d)
        if to_d:
            orders = orders.filter(created_at__date__lte=to_d)

        # Single conditional aggregation (PERF-04 fix)
        agg_data = orders.aggregate(
            total_orders=Count("id"),
            delivered_orders=Count("id", filter=Q(status=OrderStatus.DELIVERED)),
            pending_orders=Count(
                "id",
                filter=Q(status__in=[OrderStatus.PENDING, OrderStatus.CONFIRMED]),
            ),
            shipped_orders=Count("id", filter=Q(status=OrderStatus.SHIPPED)),
            out_for_delivery_orders=Count("id", filter=Q(status=OrderStatus.OUT_FOR_DELIVERY)),
            cancelled_orders=Count("id", filter=Q(status=OrderStatus.CANCELLED)),
            refunded_orders=Count("id", filter=Q(status=OrderStatus.REFUNDED)),
            revenue=Sum(
                "total_amount",
                filter=Q(status__in=[OrderStatus.CONFIRMED, OrderStatus.SHIPPED, OrderStatus.OUT_FOR_DELIVERY, OrderStatus.DELIVERED]),
            ),
            paid_orders=Count(
                "id",
                filter=Q(status__in=[OrderStatus.CONFIRMED, OrderStatus.SHIPPED, OrderStatus.OUT_FOR_DELIVERY, OrderStatus.DELIVERED]),
            ),
        )

        total_orders = agg_data["total_orders"] or 0
        delivered_orders = agg_data["delivered_orders"] or 0
        pending_orders = agg_data["pending_orders"] or 0
        shipped_orders = agg_data["shipped_orders"] or 0
        out_for_delivery_orders = agg_data["out_for_delivery_orders"] or 0
        cancelled_orders = agg_data["cancelled_orders"] or 0
        total_revenue = Decimal(str(agg_data["revenue"] or "0.00"))
        paid_orders_count = agg_data["paid_orders"] or 0

        average_order_value = (
            Decimal(str(round(total_revenue / paid_orders_count, 2)))
            if paid_orders_count > 0
            else Decimal("0.00")
        )

        # Total units sold from paid/confirmed orders
        paid_orders_qs = orders.filter(
            status__in=[OrderStatus.CONFIRMED, OrderStatus.SHIPPED, OrderStatus.OUT_FOR_DELIVERY, OrderStatus.DELIVERED]
        )
        units_agg = OrderItem.objects.filter(order__in=paid_orders_qs).aggregate(total=Sum("quantity"))["total"]
        total_units_sold = units_agg or 0

        cancellation_rate = (
            round((cancelled_orders / total_orders) * 100, 2)
            if total_orders > 0
            else 0.0
        )

        # Scalable SQL low stock count
        low_stock_count = InventoryService.get_low_stock_products_queryset(store).count()

        return {
            "total_revenue": float(total_revenue),
            "currency": "INR",
            "total_orders": total_orders,
            "delivered_orders": delivered_orders,
            "pending_orders": pending_orders,
            "shipped_orders": shipped_orders,
            "out_for_delivery_orders": out_for_delivery_orders,
            "cancelled_orders": cancelled_orders,
            "total_units_sold": total_units_sold,
            "average_order_value": float(average_order_value),
            "cancellation_rate": cancellation_rate,
            "low_stock_products_count": low_stock_count,
        }

    @classmethod
    def get_revenue_analytics(cls, store, period="daily", from_date=None, to_date=None):
        """Calculates revenue time-series data aggregated daily, weekly, or monthly."""
        orders = Order.objects.filter(
            store=store,
            status__in=[OrderStatus.CONFIRMED, OrderStatus.SHIPPED, OrderStatus.OUT_FOR_DELIVERY, OrderStatus.DELIVERED]
        )

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
            Decimal(str(round(total_revenue / total_orders, 2)))
            if total_orders > 0
            else Decimal("0.00")
        )

        return {
            "period": period,
            "currency": "INR",
            "total_revenue": float(total_revenue),
            "total_orders": total_orders,
            "average_revenue_per_order": float(avg_rev),
            "time_series": series,
        }

    @classmethod
    def get_product_performance(cls, store, sort_by="revenue", limit=20, from_date=None, to_date=None):
        """
        Calculates sales leaderboard grouped strictly by product_id and ordered in SQL.
        """
        items = OrderItem.objects.filter(
            order__store=store,
            order__status__in=[OrderStatus.CONFIRMED, OrderStatus.SHIPPED, OrderStatus.OUT_FOR_DELIVERY, OrderStatus.DELIVERED]
        )

        from_d = cls.parse_date(from_date)
        to_d = cls.parse_date(to_date)
        if from_d:
            items = items.filter(order__created_at__date__gte=from_d)
        if to_d:
            items = items.filter(order__created_at__date__lte=to_d)

        sort_by = (sort_by or "revenue").lower()
        order_field = "-gross_revenue" if sort_by != "units_sold" else "-units_sold"

        try:
            lim = int(limit)
            lim = lim if lim > 0 else 20
        except (ValueError, TypeError):
            lim = 20

        # Group by product_id and product_sku
        items_agg = (
            items.values("product_id", "product_name", "product_sku")
            .annotate(
                units_sold=Sum("quantity"),
                gross_revenue=Sum("total_price"),
            )
            .order_by(order_field)[:lim]
        )

        results = [
            {
                "product_id": row["product_id"],
                "product_name": row["product_name"],
                "sku": row.get("product_sku") or "",
                "units_sold": row["units_sold"] or 0,
                "gross_revenue": float(Decimal(str(row["gross_revenue"] or "0.00"))),
            }
            for row in items_agg
        ]

        return {
            "sort_by": sort_by,
            "total_products_sold": len(results),
            "products": results,
        }

    @classmethod
    def get_order_metrics(cls, store, from_date=None, to_date=None):
        """
        Calculates breakdown of order statuses, fulfillment velocity, and cancellation rates
        using database expressions without Python N+1 loops (PERF-02 fix).
        """
        orders = Order.objects.filter(store=store)

        from_d = cls.parse_date(from_date)
        to_d = cls.parse_date(to_date)
        if from_d:
            orders = orders.filter(created_at__date__gte=from_d)
        if to_d:
            orders = orders.filter(created_at__date__lte=to_d)

        status_counts = {choice.value: 0 for choice in OrderStatus}
        counts_agg = orders.values("status").annotate(cnt=Count("id"))
        total_orders = 0
        for row in counts_agg:
            s_val = row["status"]
            cnt = row["cnt"]
            total_orders += cnt
            if s_val in status_counts:
                status_counts[s_val] = cnt

        cancelled = status_counts.get(OrderStatus.CANCELLED, 0)
        cancellation_rate = (
            round((cancelled / total_orders) * 100, 2)
            if total_orders > 0
            else 0.0
        )

        # Average fulfillment duration computed directly in database SQL (PERF-02 fix)
        delivered_agg = orders.filter(
            status=OrderStatus.DELIVERED,
            delivered_at__isnull=False,
        ).aggregate(
            avg_duration=Avg(
                ExpressionWrapper(F("delivered_at") - F("created_at"), output_field=DurationField())
            )
        )
        avg_dur = delivered_agg.get("avg_duration")
        avg_fulfillment_hours = (
            round(avg_dur.total_seconds() / 3600.0, 1)
            if avg_dur and avg_dur.total_seconds() > 0
            else None
        )

        return {
            "total_orders": total_orders,
            "status_breakdown": status_counts,
            "cancellation_rate": cancellation_rate,
            "average_fulfillment_hours": avg_fulfillment_hours,
        }

    @classmethod
    def generate_csv_report(cls, store, report_type="sales", from_date=None, to_date=None):
        """
        Generates comprehensive CSV report with variant, color, and size breakdown (BUG-21 fix).
        """
        output = io.StringIO()
        writer = csv.writer(output)

        report_type = (report_type or "sales").lower()
        from_d = cls.parse_date(from_date)
        to_d = cls.parse_date(to_date)

        if report_type == "inventory":
            writer.writerow([
                "Product ID",
                "Product Name",
                "SKU",
                "Category",
                "Color Variant",
                "Color Hex",
                "Size",
                "Variant Stock",
                "Total Product Stock",
                "Price (INR)",
                "Is Refundable",
                "Status",
            ])
            products = (
                Product.objects.filter(store=store)
                .prefetch_related("variants__sizes")
                .order_by("name")
            )
            for p in products:
                has_variants = False
                for v in p.variants.all():
                    has_variants = True
                    if v.sizes.exists():
                        for s in v.sizes.all():
                            writer.writerow([
                                p.id,
                                p.name,
                                v.sku or p.sku or "",
                                p.category,
                                v.color_name,
                                v.color_hex,
                                s.size or "N/A",
                                s.stock,
                                p.stock,
                                str(p.price),
                                "YES" if p.is_refundable else "NO",
                                p.status,
                            ])
                    else:
                        writer.writerow([
                            p.id,
                            p.name,
                            v.sku or p.sku or "",
                            p.category,
                            v.color_name,
                            v.color_hex,
                            "N/A",
                            v.total_stock,
                            p.stock,
                            str(p.price),
                            "YES" if p.is_refundable else "NO",
                            p.status,
                        ])
                if not has_variants:
                    writer.writerow([
                        p.id,
                        p.name,
                        p.sku or "",
                        p.category,
                        "Standard",
                        "#000000",
                        "N/A",
                        p.stock,
                        p.stock,
                        str(p.price),
                        "YES" if p.is_refundable else "NO",
                        p.status,
                    ])

        elif report_type == "orders":
            writer.writerow([
                "Order Number",
                "Date",
                "Status",
                "Payment Status",
                "Customer Name",
                "Customer Phone",
                "Customer Email",
                "Total Amount (INR)",
                "Shipping Address",
            ])
            orders = Order.objects.filter(store=store).order_by("-created_at")
            if from_d:
                orders = orders.filter(created_at__date__gte=from_d)
            if to_d:
                orders = orders.filter(created_at__date__lte=to_d)
            for o in orders:
                writer.writerow([
                    o.order_number,
                    o.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                    o.status,
                    o.payment_status,
                    o.customer_name,
                    o.customer_phone,
                    o.customer_email,
                    str(o.total_amount),
                    (o.shipping_address or "").replace("\n", ", "),
                ])

        else:
            # Sales report with line-item detail
            writer.writerow([
                "Order Number",
                "Date",
                "Customer Name",
                "Customer Email",
                "Product Name",
                "Color",
                "Size",
                "Quantity",
                "Unit Price (INR)",
                "Item Total (INR)",
                "Order Total (INR)",
                "Status",
            ])
            orders = (
                Order.objects.filter(
                    store=store,
                    status__in=[OrderStatus.CONFIRMED, OrderStatus.SHIPPED, OrderStatus.OUT_FOR_DELIVERY, OrderStatus.DELIVERED]
                )
                .prefetch_related("items")
                .order_by("-created_at")
            )
            if from_d:
                orders = orders.filter(created_at__date__gte=from_d)
            if to_d:
                orders = orders.filter(created_at__date__lte=to_d)
            for o in orders:
                for item in o.items.all():
                    writer.writerow([
                        o.order_number,
                        o.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                        o.customer_name,
                        o.customer_email,
                        item.product_name,
                        item.color_name or "Standard",
                        item.size or "N/A",
                        item.quantity,
                        str(item.unit_price),
                        str(item.total_price),
                        str(o.total_amount),
                        o.status,
                    ])

        return output.getvalue()
