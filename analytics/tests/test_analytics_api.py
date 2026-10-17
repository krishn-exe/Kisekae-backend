from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from orders.models import Order, OrderItem, OrderStatus
from products.models import Product, ProductStatus
from stores.models import (
    BusinessVerification,
    Store,
    StoreMembership,
    StoreRole,
    StoreStatus,
    VerificationStatus,
)

User = get_user_model()


class AnalyticsEndpointsTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.seller = User.objects.create_user(
            email="seller@example.com",
            name="Store Owner",
            password="StrongPassword123!",
            is_email_verified=True,
            is_seller=True,
            is_active=True,
        )
        self.client.force_authenticate(user=self.seller)

        self.store = Store.objects.create(
            name="Analytics Boutique",
            slug="analytics-boutique",
            status=StoreStatus.ACTIVE,
            created_by=self.seller,
        )
        StoreMembership.objects.create(
            store=self.store,
            user=self.seller,
            role=StoreRole.OWNER,
        )
        BusinessVerification.objects.create(
            store=self.store,
            pan_number="ABCDE1234F",
            gst_number="22AAAAA0000A1Z5",
            status=VerificationStatus.APPROVED,
        )

        self.product = Product.objects.create(
            store=self.store,
            name="Silk Kimono",
            slug="silk-kimono",
            price=Decimal("1500.00"),
            stock=20,
            status=ProductStatus.ACTIVE,
            created_by=self.seller,
        )

        self.order = Order.objects.create(
            store=self.store,
            customer_name="Alice Smith",
            customer_email="alice@example.com",
            customer_phone="+919876543210",
            shipping_address="123 Street, City, State 560001",
            status=OrderStatus.DELIVERED,
            total_amount=Decimal("1500.00"),
        )
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_name="Silk Kimono",
            product_sku="SK-001",
            unit_price=Decimal("1500.00"),
            quantity=1,
            total_price=Decimal("1500.00"),
        )

    def test_analytics_dashboard_fields(self):
        url = f"/stores/{self.store.id}/analytics/dashboard/"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["success"])

        data = resp.data["data"]
        expected_fields = [
            "total_revenue",
            "currency",
            "total_orders",
            "delivered_orders",
            "pending_orders",
            "shipped_orders",
            "cancelled_orders",
            "returned_orders",
            "total_units_sold",
            "average_order_value",
            "return_rate",
            "cancellation_rate",
            "low_stock_products_count",
        ]
        for f in expected_fields:
            self.assertIn(f, data, f"Field '{f}' missing from dashboard analytics data")

        self.assertEqual(data["total_orders"], 1)
        self.assertEqual(data["delivered_orders"], 1)
        self.assertEqual(data["total_revenue"], 1500.00)

    def test_analytics_revenue_fields(self):
        url = f"/stores/{self.store.id}/analytics/revenue/?period=daily"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["success"])

        data = resp.data["data"]
        for f in ["period", "currency", "total_revenue", "total_orders", "average_revenue_per_order", "time_series"]:
            self.assertIn(f, data, f"Field '{f}' missing from revenue analytics data")

        self.assertIsInstance(data["time_series"], list)
        if len(data["time_series"]) > 0:
            point = data["time_series"][0]
            for pf in ["date", "revenue", "orders_count"]:
                self.assertIn(pf, point, f"Time series point missing '{pf}'")

    def test_analytics_product_performance_fields(self):
        url = f"/stores/{self.store.id}/analytics/products/?sort_by=revenue"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["success"])

        data = resp.data["data"]
        for f in ["sort_by", "total_products_sold", "products"]:
            self.assertIn(f, data, f"Field '{f}' missing from product performance data")

        self.assertIsInstance(data["products"], list)
        self.assertEqual(len(data["products"]), 1)
        p_item = data["products"][0]
        for pf in ["product_id", "product_name", "sku", "units_sold", "gross_revenue", "return_count", "return_rate"]:
            self.assertIn(pf, p_item, f"Product performance item missing '{pf}'")

    def test_analytics_order_metrics_fields(self):
        url = f"/stores/{self.store.id}/analytics/orders/"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["success"])

        data = resp.data["data"]
        for f in ["total_orders", "status_breakdown", "cancellation_rate", "return_rate", "average_fulfillment_hours"]:
            self.assertIn(f, data, f"Field '{f}' missing from order metrics data")

        breakdown = data["status_breakdown"]
        for s in ["PENDING", "CONFIRMED", "PROCESSING", "SHIPPED", "DELIVERED", "CANCELLED", "RETURN_REQUESTED", "RETURNED"]:
            self.assertIn(s, breakdown, f"Status '{s}' missing from status_breakdown")
