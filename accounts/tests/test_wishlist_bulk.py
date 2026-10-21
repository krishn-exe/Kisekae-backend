from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import WishlistItem
from cart.models import Cart
from cart.services import CartService
from products.models import Product, ProductCategory, ProductStatus
from stores.models import Store, StoreMembership, StoreRole, StoreStatus

User = get_user_model()


class WishlistBulkMoveTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="shopper@example.com",
            name="Alice Shopper",
            password="StrongPassword123!",
            is_email_verified=True,
            is_active=True,
        )
        self.seller = User.objects.create_user(
            email="seller@example.com",
            name="Seller Bob",
            password="StrongPassword123!",
            is_email_verified=True,
            is_seller=True,
            is_active=True,
        )
        self.client.force_authenticate(user=self.user)

        self.store = Store.objects.create(
            name="Urban Trends",
            slug="urban-trends",
            status=StoreStatus.ACTIVE,
            created_by=self.seller,
        )
        StoreMembership.objects.create(
            store=self.store,
            user=self.seller,
            role=StoreRole.OWNER,
        )

        self.p1 = Product.objects.create(
            store=self.store,
            name="Denim Jacket",
            price=Decimal("1999.00"),
            category=ProductCategory.OUTERWEAR,
            status=ProductStatus.ACTIVE,
            stock=10,
            created_by=self.seller,
        )
        self.p2 = Product.objects.create(
            store=self.store,
            name="Casual Sneakers",
            price=Decimal("2999.00"),
            category=ProductCategory.FOOTWEAR,
            status=ProductStatus.ACTIVE,
            stock=5,
            created_by=self.seller,
        )

        WishlistItem.objects.create(user=self.user, product=self.p1)
        WishlistItem.objects.create(user=self.user, product=self.p2)

    def test_bulk_move_all_wishlist_items_to_cart(self):
        self.assertEqual(self.user.wishlist_items.count(), 2)

        url = reverse("wishlist-bulk-move-to-cart")
        response = self.client.post(url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["data"]["moved_count"], 2)

        # Wishlist should now be empty
        self.assertEqual(self.user.wishlist_items.count(), 0)

        # Cart should now have 2 items
        cart = CartService.get_or_create_cart(user=self.user)
        self.assertEqual(cart.items.count(), 2)

    def test_bulk_move_selective_product_ids(self):
        url = reverse("wishlist-bulk-move-to-cart")
        response = self.client.post(url, {"product_ids": [self.p1.id]}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["moved_count"], 1)

        # Only p2 remains in wishlist
        self.assertEqual(self.user.wishlist_items.count(), 1)
        self.assertEqual(self.user.wishlist_items.first().product, self.p2)
