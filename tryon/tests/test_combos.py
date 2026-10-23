from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from products.models import Product, ProductCategory, ProductStatus
from stores.models import BusinessVerification, Store, StoreMembership, StoreRole, StoreStatus, VerificationStatus
from tryon.models import Combo, ComboItem
from tryon.services import ComboService, ComboValidationService

User = get_user_model()


class BaseComboTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="shopper@example.com",
            name="Alice",
            password="StrongPassword123!",
            is_email_verified=True,
            is_active=True,
        )
        self.other_user = User.objects.create_user(
            email="other@example.com",
            name="Bob",
            password="StrongPassword123!",
            is_email_verified=True,
            is_active=True,
        )
        self.client.force_authenticate(user=self.user)

        # Store setup
        self.seller = User.objects.create_user(
            email="seller@example.com",
            name="Store Owner",
            password="StrongPassword123!",
            is_email_verified=True,
            is_seller=True,
            is_active=True,
        )
        self.store = Store.objects.create(
            name="Trendy Store",
            slug="trendy-store",
            status=StoreStatus.ACTIVE,
            created_by=self.seller,
        )
        StoreMembership.objects.create(store=self.store, user=self.seller, role=StoreRole.OWNER)
        BusinessVerification.objects.create(
            store=self.store,
            pan_number="ABCDE1234F",
            gst_number="22AAAAA0000A1Z5",
            status=VerificationStatus.APPROVED,
        )

        # Create products in various categories
        self.top_product = Product.objects.create(
            store=self.store,
            name="Cotton White T-Shirt",
            category=ProductCategory.TOP,
            price=Decimal("499.00"),
            status=ProductStatus.ACTIVE,
        )
        self.bottom_product = Product.objects.create(
            store=self.store,
            name="Slim Fit Jeans",
            category=ProductCategory.BOTTOM,
            price=Decimal("1299.00"),
            status=ProductStatus.ACTIVE,
        )
        self.one_piece_product = Product.objects.create(
            store=self.store,
            name="Floral Summer Dress",
            category=ProductCategory.ONE_PIECE,
            price=Decimal("1499.00"),
            status=ProductStatus.ACTIVE,
        )
        self.outerwear_product = Product.objects.create(
            store=self.store,
            name="Denim Jacket",
            category=ProductCategory.OUTERWEAR,
            price=Decimal("2199.00"),
            status=ProductStatus.ACTIVE,
        )
        # Unsupported categories
        self.shoe_product = Product.objects.create(
            store=self.store,
            name="Running Sneakers",
            category=ProductCategory.FOOTWEAR,
            price=Decimal("1999.00"),
            status=ProductStatus.ACTIVE,
        )
        self.hat_product = Product.objects.create(
            store=self.store,
            name="Fedora Hat",
            category=ProductCategory.HEADWEAR,
            price=Decimal("399.00"),
            status=ProductStatus.ACTIVE,
        )
        self.glasses_product = Product.objects.create(
            store=self.store,
            name="Aviator Sunglasses",
            category=ProductCategory.EYEWEAR,
            price=Decimal("899.00"),
            status=ProductStatus.ACTIVE,
        )
        self.accessory_product = Product.objects.create(
            store=self.store,
            name="Leather Belt",
            category=ProductCategory.ACCESSORIES,
            price=Decimal("299.00"),
            status=ProductStatus.ACTIVE,
        )
        # Inactive product
        self.inactive_product = Product.objects.create(
            store=self.store,
            name="Draft Hoodie",
            category=ProductCategory.TOP,
            price=Decimal("799.00"),
            status=ProductStatus.DRAFT,
        )


class TestComboValidationConstraints(BaseComboTestCase):
    def test_unsupported_footwear_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            ComboValidationService.validate_products([self.shoe_product.id])
        self.assertIn("Category 'Footwear' is not supported", str(ctx.exception))

    def test_unsupported_headwear_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            ComboValidationService.validate_products([self.hat_product.id])
        self.assertIn("Category 'Headwear' is not supported", str(ctx.exception))

    def test_unsupported_eyewear_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            ComboValidationService.validate_products([self.glasses_product.id])
        self.assertIn("Category 'Eyewear' is not supported", str(ctx.exception))

    def test_unsupported_accessories_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            ComboValidationService.validate_products([self.accessory_product.id])
        self.assertIn("Category 'Accessories' is not supported", str(ctx.exception))

    def test_one_piece_blocks_top(self):
        with self.assertRaises(ValidationError) as ctx:
            ComboValidationService.validate_products([self.one_piece_product.id, self.top_product.id])
        self.assertIn("One-Piece cannot be combined with Top", str(ctx.exception))

    def test_one_piece_blocks_bottom(self):
        with self.assertRaises(ValidationError) as ctx:
            ComboValidationService.validate_products([self.one_piece_product.id, self.bottom_product.id])
        self.assertIn("One-Piece cannot be combined with Bottom", str(ctx.exception))

    def test_one_piece_blocks_both_top_and_bottom(self):
        with self.assertRaises(ValidationError) as ctx:
            ComboValidationService.validate_products([
                self.one_piece_product.id,
                self.top_product.id,
                self.bottom_product.id,
            ])
        self.assertIn("One-Piece cannot be combined with Top or Bottom", str(ctx.exception))

    def test_duplicate_category_rejected(self):
        another_top = Product.objects.create(
            store=self.store,
            name="Second Shirt",
            category=ProductCategory.TOP,
            price=Decimal("699.00"),
            status=ProductStatus.ACTIVE,
        )
        with self.assertRaises(ValidationError) as ctx:
            ComboValidationService.validate_products([self.top_product.id, another_top.id])
        self.assertIn("cannot contain multiple items of category 'TOP'", str(ctx.exception))

    def test_inactive_product_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            ComboValidationService.validate_products([self.inactive_product.id])
        self.assertIn("is not active", str(ctx.exception))

    def test_valid_separates_top_bottom_outerwear(self):
        products = ComboValidationService.validate_products([
            self.top_product.id,
            self.bottom_product.id,
            self.outerwear_product.id,
        ])
        self.assertEqual(len(products), 3)

    def test_valid_dress_and_outerwear(self):
        products = ComboValidationService.validate_products([
            self.one_piece_product.id,
            self.outerwear_product.id,
        ])
        self.assertEqual(len(products), 2)


class TestComboAPIs(BaseComboTestCase):
    def test_create_combo_success(self):
        url = reverse("combo-list-create")
        payload = {
            "name": "Summer Fit",
            "product_ids": [self.top_product.id, self.bottom_product.id],
        }
        res = self.client.post(url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(res.data["success"])
        self.assertEqual(res.data["data"]["name"], "Summer Fit")
        self.assertEqual(res.data["data"]["items_count"], 2)

        # Check DB
        combo = Combo.objects.get(id=res.data["data"]["id"])
        self.assertEqual(combo.user, self.user)
        self.assertEqual(combo.items.count(), 2)

    def test_create_combo_rejects_unsupported_footwear(self):
        url = reverse("combo-list-create")
        payload = {
            "name": "Shoe Outfit",
            "product_ids": [self.top_product.id, self.shoe_product.id],
        }
        res = self.client.post(url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(res.data["success"])
        self.assertIn("Footwear", res.data["message"])

    def test_create_combo_rejects_one_piece_top_conflict(self):
        url = reverse("combo-list-create")
        payload = {
            "name": "Invalid Combo",
            "product_ids": [self.one_piece_product.id, self.top_product.id],
        }
        res = self.client.post(url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("One-Piece cannot be combined with Top", res.data["message"])

    def test_list_combos_user_isolation(self):
        # Create combo for self
        ComboService.create_combo(user=self.user, name="Alice Combo", product_ids=[self.top_product.id])
        # Create combo for other user
        ComboService.create_combo(user=self.other_user, name="Bob Combo", product_ids=[self.bottom_product.id])

        url = reverse("combo-list-create")
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data["data"]), 1)
        self.assertEqual(res.data["data"][0]["name"], "Alice Combo")

    def test_get_combo_detail(self):
        combo = ComboService.create_combo(
            user=self.user,
            name="Alice Outfit",
            product_ids=[self.top_product.id, self.bottom_product.id],
        )
        url = reverse("combo-detail", kwargs={"pk": combo.id})
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["data"]["id"], combo.id)
        self.assertEqual(res.data["data"]["items_count"], 2)

    def test_patch_combo_rename_and_replace_items(self):
        combo = ComboService.create_combo(
            user=self.user,
            name="Old Name",
            product_ids=[self.top_product.id],
        )
        url = reverse("combo-detail", kwargs={"pk": combo.id})
        payload = {
            "name": "New Name",
            "product_ids": [self.one_piece_product.id, self.outerwear_product.id],
        }
        res = self.client.patch(url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["data"]["name"], "New Name")
        self.assertEqual(res.data["data"]["items_count"], 2)

        # Check DB
        combo.refresh_from_db()
        self.assertEqual(combo.name, "New Name")
        categories = list(combo.items.values_list("category", flat=True))
        self.assertIn(ProductCategory.ONE_PIECE, categories)
        self.assertIn(ProductCategory.OUTERWEAR, categories)

    def test_delete_combo(self):
        combo = ComboService.create_combo(
            user=self.user,
            name="To Delete",
            product_ids=[self.top_product.id],
        )
        url = reverse("combo-detail", kwargs={"pk": combo.id})
        res = self.client.delete(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(Combo.objects.filter(id=combo.id).exists())
        self.assertFalse(ComboItem.objects.filter(combo_id=combo.id).exists())

    def test_add_item_to_combo(self):
        combo = ComboService.create_combo(
            user=self.user,
            name="Base Outfit",
            product_ids=[self.top_product.id],
        )
        url = reverse("combo-item-add", kwargs={"pk": combo.id})
        res = self.client.post(url, {"product_id": self.bottom_product.id}, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["data"]["items_count"], 2)

    def test_add_conflicting_item_fails(self):
        combo = ComboService.create_combo(
            user=self.user,
            name="Base Outfit",
            product_ids=[self.top_product.id],
        )
        url = reverse("combo-item-add", kwargs={"pk": combo.id})
        # Try adding One-Piece to an outfit that already has Top
        res = self.client.post(url, {"product_id": self.one_piece_product.id}, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("One-Piece cannot be combined with Top", res.data["message"])

    def test_remove_item_from_combo(self):
        combo = ComboService.create_combo(
            user=self.user,
            name="Base Outfit",
            product_ids=[self.top_product.id, self.bottom_product.id],
        )
        item = combo.items.first()
        url = reverse("combo-item-delete", kwargs={"pk": combo.id, "item_id": item.id})
        res = self.client.delete(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        combo.refresh_from_db()
        self.assertEqual(combo.items.count(), 1)

    def test_unauthenticated_request_rejected(self):
        self.client.logout()
        url = reverse("combo-list-create")
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)


class TestComboSharing(BaseComboTestCase):
    def setUp(self):
        super().setUp()
        self.combo = ComboService.create_combo(
            user=self.user,
            name="Alice Party Look",
            product_ids=[self.top_product.id, self.bottom_product.id],
        )

    def test_combo_has_share_properties(self):
        self.combo.refresh_from_db()
        self.assertTrue(bool(self.combo.share_code))
        self.assertEqual(self.combo.deep_link, f"kisekae://combos/{self.combo.share_code}")
        self.assertEqual(self.combo.share_url, f"https://live.kisekae.app/combos/{self.combo.share_code}")

    def test_get_shared_combo_public(self):
        self.client.logout()
        url = reverse("shared-combo-detail", kwargs={"share_code": self.combo.share_code})
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data["success"])
        data = res.data["data"]
        self.assertEqual(data["name"], "Alice Party Look")
        self.assertEqual(data["share_code"], self.combo.share_code)
        self.assertEqual(data["deep_link"], f"kisekae://combos/{self.combo.share_code}")
        self.assertEqual(data["share_url"], f"https://live.kisekae.app/combos/{self.combo.share_code}")
        self.assertEqual(len(data["items"]), 2)

    def test_get_shared_combo_not_found(self):
        self.client.logout()
        url = reverse("shared-combo-detail", kwargs={"share_code": "non-existent-code"})
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(res.data["success"])

    def test_shared_combo_redirect_to_flutter(self):
        self.client.logout()
        url = reverse("shared-combo-redirect", kwargs={"share_code": self.combo.share_code})
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_302_FOUND)
        self.assertEqual(res["Location"], f"kisekae://combos/{self.combo.share_code}")

    def test_shared_combo_redirect_not_found(self):
        self.client.logout()
        url = reverse("shared-combo-redirect", kwargs={"share_code": "unknown-code"})
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_save_shared_combo_authenticated(self):
        # Authenticate as other_user (Bob)
        self.client.force_authenticate(user=self.other_user)
        url = reverse("shared-combo-save", kwargs={"share_code": self.combo.share_code})
        res = self.client.post(url)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(res.data["success"])
        cloned_id = res.data["data"]["id"]
        self.assertNotEqual(cloned_id, self.combo.id)

        # Verify cloned combo in database
        cloned = Combo.objects.get(id=cloned_id)
        self.assertEqual(cloned.user, self.other_user)
        self.assertEqual(cloned.name, f"{self.combo.name} (Saved)")
        self.assertEqual(cloned.items.count(), 2)

    def test_save_shared_combo_unauthenticated_fails(self):
        self.client.logout()
        url = reverse("shared-combo-save", kwargs={"share_code": self.combo.share_code})
        res = self.client.post(url)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

