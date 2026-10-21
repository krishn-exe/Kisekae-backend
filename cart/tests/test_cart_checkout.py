from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Address
from cart.models import Cart
from cart.services import CartService
from orders.models import Order, OrderStatus
from products.models import Product, ProductCategory, ProductSize, ProductStatus
from stores.models import (
    BusinessVerification,
    Store,
    StoreMembership,
    StoreRole,
    StoreStatus,
    VerificationStatus,
)

User = get_user_model()


class CartCheckoutTestCase(TestCase):
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
        self.client.force_authenticate(user=self.customer)

        self.store = Store.objects.create(
            name="Fashion Hub",
            slug="fashion-hub",
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
            name="Silk Evening Dress",
            price=Decimal("2499.00"),
            category=ProductCategory.ONE_PIECE,
            status=ProductStatus.ACTIVE,
            stock=15,
            created_by=self.seller,
        )
        self.size_m = ProductSize.objects.create(
            product=self.product,
            size="M",
            stock=10,
        )
        self.size_l = ProductSize.objects.create(
            product=self.product,
            size="L",
            stock=5,
        )
        self.product.sync_total_stock()

        self.address = Address.objects.create(
            user=self.customer,
            recipient_name="Alice Shopper",
            phone="9876543210",
            address_line_1="Flat 301, Sunset Villa",
            city="Mumbai",
            state="Maharashtra",
            postal_code="400001",
            is_default=True,
        )

    def test_checkout_cart_successfully_with_saved_address(self):
        cart = CartService.get_or_create_cart(user=self.customer)
        CartService.add_item(cart=cart, product_id=self.product.id, quantity=2, size="M")

        url = reverse("cart-checkout")
        response = self.client.post(url, {"address_id": self.address.id}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["success"])
        orders = response.data["data"]["orders"]
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["status"], OrderStatus.CONFIRMED)
        self.assertEqual(Decimal(str(orders[0]["total_amount"])), Decimal("4998.00"))

        # Verify stock decrement
        self.size_m.refresh_from_db()
        self.assertEqual(self.size_m.stock, 8)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 13)

        # Verify OrderItem size
        order = Order.objects.get(pk=orders[0]["order_id"])
        self.assertEqual(order.user, self.customer)
        order_item = order.items.first()
        self.assertEqual(order_item.size, "M")
        self.assertEqual(order_item.quantity, 2)

        # Verify cart is empty
        cart.refresh_from_db()
        self.assertEqual(cart.items.count(), 0)

    def test_checkout_cart_with_inline_address(self):
        cart = CartService.get_or_create_cart(user=self.customer)
        CartService.add_item(cart=cart, product_id=self.product.id, quantity=1, size="L")

        url = reverse("cart-checkout")
        payload = {
            "customer_name": "Alice Custom",
            "customer_phone": "9998887776",
            "shipping_address": "456 Marine Drive, Mumbai, 400020",
        }
        response = self.client.post(url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        order_id = response.data["data"]["orders"][0]["order_id"]
        order = Order.objects.get(pk=order_id)
        self.assertEqual(order.customer_name, "Alice Custom")
        self.assertEqual(order.shipping_address, "456 Marine Drive, Mumbai, 400020")
        self.assertEqual(order.status, OrderStatus.CONFIRMED)

    def test_checkout_empty_cart_fails(self):
        url = reverse("cart-checkout")
        response = self.client.post(url, {"address_id": self.address.id}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])
