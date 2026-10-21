from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase

from accounts.models import UserSettings
from accounts.tasks import send_otp_email_task
from inventory.models import StockAlert
from inventory.services import InventoryService
from notifications.email_service import NotificationEmailService
from notifications.tasks import (
    send_order_lifecycle_email_task,
    send_seller_low_stock_alert_task,
    send_seller_new_order_alert_task,
)
from orders.models import Order, OrderItem, OrderStatus
from products.models import Product, ProductCategory, ProductStatus
from stores.models import (
    BusinessVerification,
    Store,
    StoreMembership,
    StoreRole,
    StoreStatus,
    VerificationStatus,
)
from stores.tasks import send_store_invitation_email_task

User = get_user_model()


class NotificationSystemTestCase(TestCase):
    def setUp(self):
        mail.outbox.clear()

        # Create Seller Owner
        self.owner = User.objects.create_user(
            email="owner@boutique.com",
            name="Store Owner",
            password="StrongPassword123!",
            is_email_verified=True,
            is_seller=True,
            is_active=True,
        )
        UserSettings.objects.create(user=self.owner, email_notifications=True, order_alerts=True, stock_alerts=True)

        # Create Seller Manager
        self.manager = User.objects.create_user(
            email="manager@boutique.com",
            name="Store Manager",
            password="StrongPassword123!",
            is_email_verified=True,
            is_seller=True,
            is_active=True,
        )
        UserSettings.objects.create(user=self.manager, email_notifications=True, order_alerts=True, stock_alerts=True)

        # Create Customer
        self.customer = User.objects.create_user(
            email="customer@example.com",
            name="Alice Customer",
            password="StrongPassword123!",
            is_email_verified=True,
            is_active=True,
        )
        self.customer_settings = UserSettings.objects.create(
            user=self.customer, email_notifications=True, order_alerts=True
        )

        # Create Store
        self.store = Store.objects.create(
            name="Maison Kisekae",
            slug="maison-kisekae",
            status=StoreStatus.ACTIVE,
            created_by=self.owner,
        )
        StoreMembership.objects.create(store=self.store, user=self.owner, role=StoreRole.OWNER)
        StoreMembership.objects.create(store=self.store, user=self.manager, role=StoreRole.MANAGER)

        BusinessVerification.objects.create(
            store=self.store,
            pan_number="ABCDE1234F",
            gst_number="22AAAAA0000A1Z5",
            status=VerificationStatus.APPROVED,
        )

        self.product = Product.objects.create(
            store=self.store,
            name="Burgundy Silk Kimono",
            price=Decimal("4999.00"),
            category=ProductCategory.ONE_PIECE,
            status=ProductStatus.ACTIVE,
            stock=10,
            created_by=self.owner,
        )

        self.order = Order.objects.create(
            user=self.customer,
            store=self.store,
            customer_name="Alice Customer",
            customer_email="customer@example.com",
            customer_phone="9876543210",
            shipping_address="742 Evergreen Terrace, Springfield",
            status=OrderStatus.CONFIRMED,
            total_amount=Decimal("4999.00"),
            tracking_number="TRACK-9876",
            tracking_url="https://tracker.carrier.com/TRACK-9876",
        )
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_name=self.product.name,
            size="M",
            unit_price=self.product.price,
            quantity=1,
            total_price=self.product.price,
        )

    def test_order_confirmed_email_rendered_and_sent(self):
        send_order_lifecycle_email_task(self.order.id, "CONFIRMED")

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ["customer@example.com"])
        self.assertIn(f"Order Confirmed: #{self.order.order_number}", sent.subject)
        # Check that HTML alternative exists and has the brand burgundy color
        self.assertTrue(any(alt[1] == "text/html" for alt in sent.alternatives))
        html_body = next(alt[0] for alt in sent.alternatives if alt[1] == "text/html")
        self.assertIn("#802B3E", html_body)
        self.assertIn("Burgundy Silk Kimono", html_body)
        self.assertIn("Maison Kisekae", html_body)

    def test_order_shipped_email_contains_tracking_link(self):
        send_order_lifecycle_email_task(self.order.id, "SHIPPED")

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertIn("Has Shipped", sent.subject)
        html_body = next(alt[0] for alt in sent.alternatives if alt[1] == "text/html")
        self.assertIn("TRACK-9876", html_body)
        self.assertIn("https://tracker.carrier.com/TRACK-9876", html_body)

    def test_order_delivered_email_sent(self):
        send_order_lifecycle_email_task(self.order.id, "DELIVERED")

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertIn("Delivered", sent.subject)
        html_body = next(alt[0] for alt in sent.alternatives if alt[1] == "text/html")
        self.assertIn("Your Order Has Arrived", html_body)

    def test_seller_new_order_alert_sent_to_owners_and_managers(self):
        send_seller_new_order_alert_task(self.order.id)

        # Should send 2 emails: 1 to owner, 1 to manager
        self.assertEqual(len(mail.outbox), 2)
        recipients = {m.to[0] for m in mail.outbox}
        self.assertEqual(recipients, {"owner@boutique.com", "manager@boutique.com"})
        for m in mail.outbox:
            self.assertIn("New Order Received", m.subject)
            html_body = next(alt[0] for alt in m.alternatives if alt[1] == "text/html")
            self.assertIn(self.order.order_number, html_body)

    def test_seller_low_stock_alert_sent(self):
        send_seller_low_stock_alert_task(
            product_id=self.product.id,
            size="M",
            current_stock=2,
            threshold=5,
        )

        self.assertEqual(len(mail.outbox), 2)
        for m in mail.outbox:
            self.assertIn("Low Stock Alert", m.subject)
            html_body = next(alt[0] for alt in m.alternatives if alt[1] == "text/html")
            self.assertIn("Burgundy Silk Kimono", html_body)
            self.assertIn("Current Stock Remaining: 2", html_body)

    def test_customer_notification_preferences_respected(self):
        # Disable notifications for customer
        self.customer_settings.email_notifications = False
        self.customer_settings.save(update_fields=["email_notifications"])

        send_order_lifecycle_email_task(self.order.id, "CONFIRMED")
        self.assertEqual(len(mail.outbox), 0)

    def test_seller_manager_alert_preferences_respected(self):
        # Manager turns off order alerts
        manager_settings = self.manager.settings
        manager_settings.order_alerts = False
        manager_settings.save(update_fields=["order_alerts"])

        send_seller_new_order_alert_task(self.order.id)
        # Only owner should receive
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["owner@boutique.com"])

    def test_upgraded_otp_email_renders_html_with_brand_colors(self):
        send_otp_email_task("customer@example.com", "847291", purpose="login")

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertIn("847291", sent.body)
        html_body = next(alt[0] for alt in sent.alternatives if alt[1] == "text/html")
        self.assertIn("847291", html_body)
        self.assertIn("#802B3E", html_body)
        self.assertIn("KISEKAE", html_body)

    def test_upgraded_store_invitation_email_renders_html(self):
        send_store_invitation_email_task(
            target_email="newstaff@example.com",
            store_name="Maison Kisekae",
            inviter_name="Store Owner",
            raw_token="INVITE-TOKEN-12345",
        )

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertIn("INVITE-TOKEN-12345", sent.body)
        html_body = next(alt[0] for alt in sent.alternatives if alt[1] == "text/html")
        self.assertIn("INVITE-TOKEN-12345", html_body)
        self.assertIn("Maison Kisekae", html_body)
        self.assertIn("#802B3E", html_body)
