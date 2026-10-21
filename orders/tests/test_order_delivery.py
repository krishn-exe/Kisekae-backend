from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from orders.models import Order, OrderItem, OrderStatus, OrderStatusHistory
from orders.services import OrderService
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


class OrderDeliveryTestCase(TestCase):
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
            email="customer@example.com",
            name="Customer Alice",
            password="StrongPassword123!",
            is_email_verified=True,
            is_active=True,
        )
        self.store = Store.objects.create(
            name="Boutique Fashion",
            slug="boutique-fashion",
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

        self.order = Order.objects.create(
            user=self.customer,
            store=self.store,
            customer_name="Customer Alice",
            customer_email="customer@example.com",
            customer_phone="9876543210",
            shipping_address="123 High Street, Mumbai",
            status=OrderStatus.SHIPPED,
            total_amount=Decimal("1500.00"),
        )

    def test_service_mark_order_delivered(self):
        self.assertIsNone(self.order.delivered_at)
        updated = OrderService.mark_order_delivered(
            order=self.order,
            user=self.seller,
            notes="Delivered to front door",
        )
        self.assertEqual(updated.status, OrderStatus.DELIVERED)
        self.assertIsNotNone(updated.delivered_at)

        # Check status history was created
        history = OrderStatusHistory.objects.filter(order=updated).first()
        self.assertIsNotNone(history)
        self.assertEqual(history.to_status, OrderStatus.DELIVERED)
        self.assertEqual(history.changed_by, self.seller)

    def test_service_mark_order_delivered_from_processing(self):
        self.order.status = OrderStatus.PROCESSING
        self.order.save(update_fields=["status"])

        updated = OrderService.mark_order_delivered(order=self.order, user=self.seller)
        self.assertEqual(updated.status, OrderStatus.DELIVERED)
        self.assertIsNotNone(updated.delivered_at)

    def test_service_cannot_deliver_cancelled_order(self):
        self.order.status = OrderStatus.CANCELLED
        self.order.save(update_fields=["status"])

        with self.assertRaises(ValidationError):
            OrderService.mark_order_delivered(order=self.order, user=self.seller)

    def test_api_deliver_order_as_seller(self):
        self.client.force_authenticate(user=self.seller)
        url = reverse("order-deliver", kwargs={"pk": self.store.pk, "order_id": self.order.pk})

        response = self.client.post(url, {"notes": "Handed to customer"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["data"]["status"], OrderStatus.DELIVERED)
        self.assertIsNotNone(response.data["data"]["delivered_at"])

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.DELIVERED)
        self.assertIsNotNone(self.order.delivered_at)

    def test_api_deliver_order_unauthorized_user(self):
        other_user = User.objects.create_user(
            email="other@example.com",
            password="StrongPassword123!",
            is_email_verified=True,
            is_seller=True,
        )
        self.client.force_authenticate(user=other_user)
        url = reverse("order-deliver", kwargs={"pk": self.store.pk, "order_id": self.order.pk})

        response = self.client.post(url, {"notes": "Attempt"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_api_deliver_order_non_existent(self):
        self.client.force_authenticate(user=self.seller)
        url = reverse("order-deliver", kwargs={"pk": self.store.pk, "order_id": 999999})

        response = self.client.post(url, {"notes": "Non existent"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
