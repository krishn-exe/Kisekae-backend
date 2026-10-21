from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from orders.models import Order, OrderItem, OrderStatus
from products.models import Product, ProductCategory, ProductStatus
from stores.models import Store, StoreMembership, StoreRole, StoreStatus

User = get_user_model()


class CustomerOrderTestCase(TestCase):
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
        self.customer = User.objects.create_user(
            email="shopper@example.com",
            name="Alice Shopper",
            password="StrongPassword123!",
            is_email_verified=True,
            is_active=True,
        )
        self.other_user = User.objects.create_user(
            email="other@example.com",
            name="Bob User",
            password="StrongPassword123!",
            is_email_verified=True,
            is_active=True,
        )
        self.client.force_authenticate(user=self.customer)

        self.store = Store.objects.create(
            name="Fashion Street",
            slug="fashion-street",
            status=StoreStatus.ACTIVE,
            created_by=self.seller,
        )
        StoreMembership.objects.create(
            store=self.store,
            user=self.seller,
            role=StoreRole.OWNER,
        )

        self.product = Product.objects.create(
            store=self.store,
            name="Vintage Trench Coat",
            price=Decimal("4999.00"),
            category=ProductCategory.OUTERWEAR,
            status=ProductStatus.ACTIVE,
            stock=10,
            created_by=self.seller,
        )

        self.order = Order.objects.create(
            user=self.customer,
            store=self.store,
            customer_name="Alice Shopper",
            customer_email="shopper@example.com",
            customer_phone="9876543210",
            shipping_address="123 Park Avenue, Mumbai",
            status=OrderStatus.CONFIRMED,
            total_amount=Decimal("4999.00"),
            tracking_number="BLUEDART-12345",
            tracking_url="https://tracking.carrier.com/BLUEDART-12345",
        )
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_name=self.product.name,
            size="L",
            unit_price=self.product.price,
            quantity=1,
            total_price=self.product.price,
        )

        # Another order belonging to other_user
        self.other_order = Order.objects.create(
            user=self.other_user,
            store=self.store,
            customer_name="Bob User",
            customer_email="other@example.com",
            customer_phone="1122334455",
            shipping_address="789 Lane, Pune",
            status=OrderStatus.SHIPPED,
            total_amount=Decimal("1500.00"),
        )

    def test_customer_list_orders(self):
        url = reverse("customer-order-list")
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Should see only their own order
        data = response.data["results"] if "results" in response.data else response.data
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["order_number"], self.order.order_number)

    def test_customer_order_detail(self):
        url = reverse("customer-order-detail", kwargs={"order_id": self.order.id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["data"]["order_number"], self.order.order_number)
        self.assertEqual(len(response.data["data"]["items"]), 1)
        self.assertEqual(response.data["data"]["items"][0]["size"], "L")

    def test_customer_cannot_view_others_order(self):
        url = reverse("customer-order-detail", kwargs={"order_id": self.other_order.id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_customer_track_order_by_number(self):
        url = reverse("customer-order-track-number", kwargs={"order_number": self.order.order_number})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["data"]["tracking_number"], "BLUEDART-12345")
        self.assertEqual(response.data["data"]["tracking_url"], "https://tracking.carrier.com/BLUEDART-12345")
        self.assertEqual(response.data["data"]["status"], OrderStatus.CONFIRMED)

    def test_customer_cancel_order(self):
        url = reverse("customer-order-cancel", kwargs={"order_id": self.order.id})
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.CANCELLED)

        # Inventory should have been restored
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 11)
