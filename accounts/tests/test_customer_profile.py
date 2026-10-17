from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Address, WishlistItem
from products.models import Product, ProductStatus
from stores.models import Store, StoreMembership, StoreRole, StoreStatus

User = get_user_model()


class CustomerProfileTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="customer@example.com",
            name="John Customer",
            password="StrongPassword123!",
            is_email_verified=True,
            is_active=True,
        )
        self.other_user = User.objects.create_user(
            email="other@example.com",
            name="Other Person",
            password="StrongPassword123!",
            is_email_verified=True,
            is_active=True,
        )
        self.client.force_authenticate(user=self.user)

        # Store & Product for wishlist tests
        self.seller = User.objects.create_user(
            email="seller@example.com",
            name="Store Owner",
            password="StrongPassword123!",
            is_email_verified=True,
            is_seller=True,
        )
        self.store = Store.objects.create(
            name="Trendy Boutique",
            slug="trendy-boutique",
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
            name="Japanese Silk Robe",
            slug="japanese-silk-robe",
            price=Decimal("2999.00"),
            stock=15,
            status=ProductStatus.ACTIVE,
            created_by=self.seller,
        )

    # ------------------------------------------------------------------------
    # 1. PROFILE TESTS
    # ------------------------------------------------------------------------
    def test_get_customer_profile(self):
        response = self.client.get("/accounts/me/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["data"]["name"], "John Customer")
        self.assertEqual(response.data["data"]["email"], "customer@example.com")
        self.assertEqual(response.data["data"]["addresses_count"], 0)
        self.assertEqual(response.data["data"]["wishlist_items_count"], 0)

        # Also verify /accounts/profile/ alias works
        resp_alias = self.client.get("/accounts/profile/")
        self.assertEqual(resp_alias.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_alias.data["data"]["name"], "John Customer")

    def test_update_customer_name(self):
        # PATCH update
        response = self.client.patch("/accounts/profile/", {"name": "Johnny Updated"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["data"]["name"], "Johnny Updated")

        self.user.refresh_from_db()
        self.assertEqual(self.user.name, "Johnny Updated")

        # PUT update
        response_put = self.client.put("/accounts/me/", {"name": "John NewName"})
        self.assertEqual(response_put.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.name, "John NewName")

    # ------------------------------------------------------------------------
    # 2. ADDRESS CRUD TESTS
    # ------------------------------------------------------------------------
    def test_address_crud_and_default_handling(self):
        # 1. List addresses (initially empty)
        resp = self.client.get("/accounts/addresses/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data["data"]), 0)

        # 2. Create first address (should auto-become default)
        addr1_data = {
            "title": "Home",
            "recipient_name": "John Customer",
            "phone": "+919876543210",
            "address_line_1": "Flat 101, Lakeview Residency",
            "address_line_2": "Near Central Park",
            "city": "Bengaluru",
            "state": "Karnataka",
            "pin_code": "560001",
            "latitude": 12.971598,
            "longitude": 77.594566,
        }
        resp1 = self.client.post("/accounts/addresses/", addr1_data)
        self.assertEqual(resp1.status_code, status.HTTP_201_CREATED)
        addr1_id = resp1.data["data"]["id"]
        self.assertTrue(resp1.data["data"]["is_default"])
        self.assertEqual(resp1.data["data"]["postal_code"], "560001")
        self.assertEqual(resp1.data["data"]["pin_code"], "560001")

        # 3. Create second address marked as default
        addr2_data = {
            "title": "Office",
            "address_line_1": "Level 4, IT Hub",
            "state": "Karnataka",
            "postal_code": "560103",
            "is_default": True,
        }
        resp2 = self.client.post("/accounts/addresses/", addr2_data)
        self.assertEqual(resp2.status_code, status.HTTP_201_CREATED)
        addr2_id = resp2.data["data"]["id"]
        self.assertTrue(resp2.data["data"]["is_default"])

        # Check that addr1 was unset as default
        addr1_obj = Address.objects.get(pk=addr1_id)
        self.assertFalse(addr1_obj.is_default)

        # 4. Retrieve single address
        get_resp = self.client.get(f"/accounts/addresses/{addr1_id}/")
        self.assertEqual(get_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(get_resp.data["data"]["title"], "Home")

        # 5. Other user cannot access this address
        self.client.force_authenticate(user=self.other_user)
        other_resp = self.client.get(f"/accounts/addresses/{addr1_id}/")
        self.assertEqual(other_resp.status_code, status.HTTP_404_NOT_FOUND)

        # Re-authenticate as owner
        self.client.force_authenticate(user=self.user)

        # 6. Update address (PATCH)
        patch_resp = self.client.patch(f"/accounts/addresses/{addr1_id}/", {"title": "Beach Villa"})
        self.assertEqual(patch_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(patch_resp.data["data"]["title"], "Beach Villa")

        # 7. Set default address via explicit endpoint
        set_def_resp = self.client.post(f"/accounts/addresses/{addr1_id}/default/")
        self.assertEqual(set_def_resp.status_code, status.HTTP_200_OK)
        self.assertTrue(set_def_resp.data["data"]["is_default"])
        addr2_obj = Address.objects.get(pk=addr2_id)
        self.assertFalse(addr2_obj.is_default)

        # 8. Check profile shows addresses_count = 2
        prof_resp = self.client.get("/accounts/profile/")
        self.assertEqual(prof_resp.data["data"]["addresses_count"], 2)

        # 9. Delete default address -> addr2 becomes promoted to default
        del_resp = self.client.delete(f"/accounts/addresses/{addr1_id}/")
        self.assertEqual(del_resp.status_code, status.HTTP_200_OK)
        self.assertFalse(Address.objects.filter(pk=addr1_id).exists())
        addr2_obj.refresh_from_db()
        self.assertTrue(addr2_obj.is_default)

    def test_address_validation_lat_long(self):
        invalid_data = {
            "title": "Home",
            "address_line_1": "123 Main St",
            "state": "Delhi",
            "pin_code": "110001",
            "latitude": 95.0,  # Invalid: > 90
            "longitude": 200.0,  # Invalid: > 180
        }
        resp = self.client.post("/accounts/addresses/", invalid_data)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    # ------------------------------------------------------------------------
    # 3. WISHLIST CRUD TESTS
    # ------------------------------------------------------------------------
    def test_wishlist_crud_and_move_to_cart(self):
        # 1. Wishlist starts empty
        resp = self.client.get("/accounts/wishlist/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data["data"]), 0)

        # 2. Add product to wishlist
        add_resp = self.client.post(
            "/accounts/wishlist/",
            {"product_id": self.product.id, "note": "Birthday wishlist"},
        )
        self.assertEqual(add_resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(add_resp.data["data"]["product"]["name"], "Japanese Silk Robe")
        self.assertEqual(add_resp.data["data"]["note"], "Birthday wishlist")

        # 3. Add same product again -> idempotent 200 OK
        add_again = self.client.post(
            "/accounts/wishlist/",
            {"product_id": self.product.id, "note": "Updated Birthday note"},
        )
        self.assertEqual(add_again.status_code, status.HTTP_200_OK)
        self.assertEqual(add_again.data["data"]["note"], "Updated Birthday note")

        # 4. Check if product is wishlisted
        check_resp = self.client.get(f"/accounts/wishlist/{self.product.id}/")
        self.assertEqual(check_resp.status_code, status.HTTP_200_OK)
        self.assertTrue(check_resp.data["data"]["is_in_wishlist"])

        # 5. Check non-wishlisted product
        non_wl_resp = self.client.get("/accounts/wishlist/99999/")
        self.assertEqual(non_wl_resp.status_code, status.HTTP_200_OK)
        self.assertFalse(non_wl_resp.data["data"]["is_in_wishlist"])

        # 6. Update wishlist item note
        patch_note_resp = self.client.patch(
            f"/accounts/wishlist/{self.product.id}/",
            {"note": "Wait for weekend sale"},
        )
        self.assertEqual(patch_note_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(patch_note_resp.data["data"]["note"], "Wait for weekend sale")

        # 7. Move product from wishlist to cart
        move_resp = self.client.post(f"/accounts/wishlist/{self.product.id}/move-to-cart/")
        self.assertEqual(move_resp.status_code, status.HTTP_200_OK)
        self.assertTrue(move_resp.data["success"])

        # Check it is removed from wishlist
        self.assertFalse(WishlistItem.objects.filter(user=self.user, product=self.product).exists())

        # Check it is now in user's cart
        cart_resp = self.client.get("/cart/")
        self.assertEqual(cart_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(cart_resp.data["data"]["total_items"], 1)

        # 8. Re-add and delete single item
        self.client.post("/accounts/wishlist/", {"product_id": self.product.id})
        del_item_resp = self.client.delete(f"/accounts/wishlist/{self.product.id}/")
        self.assertEqual(del_item_resp.status_code, status.HTTP_200_OK)
        self.assertFalse(WishlistItem.objects.filter(user=self.user, product=self.product).exists())

        # 9. Clear wishlist
        self.client.post("/accounts/wishlist/", {"product_id": self.product.id})
        clear_resp = self.client.delete("/accounts/wishlist/")
        self.assertEqual(clear_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(WishlistItem.objects.filter(user=self.user).count(), 0)

    def test_inactive_and_unverified_user_rejected(self):
        # Unverified email
        unverified_user = User.objects.create_user(
            email="unverified@example.com",
            name="Unverified User",
            password="StrongPassword123!",
            is_email_verified=False,
            is_active=True,
        )
        self.client.force_authenticate(user=unverified_user)
        resp = self.client.get("/accounts/me/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(resp.data["error"]["code"], "EMAIL_NOT_VERIFIED")

        # Inactive user
        inactive_user = User.objects.create_user(
            email="inactive@example.com",
            name="Inactive User",
            password="StrongPassword123!",
            is_email_verified=True,
            is_active=False,
        )
        self.client.force_authenticate(user=inactive_user)
        resp = self.client.get("/accounts/me/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(resp.data["error"]["code"], "ACCOUNT_INACTIVE")

    def test_wishlist_inactive_product(self):
        inactive_product = Product.objects.create(
            store=self.store,
            name="Draft Silk Scarf",
            slug="draft-silk-scarf",
            price=Decimal("999.00"),
            stock=10,
            status=ProductStatus.DRAFT,
            created_by=self.seller,
        )
        resp = self.client.post("/accounts/wishlist/", {"product_id": inactive_product.id})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

