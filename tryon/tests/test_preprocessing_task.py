from decimal import Decimal
from io import BytesIO
from unittest.mock import patch
from PIL import Image

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from products.models import Product, ProductCategory, ProductImage, ProductStatus
from stores.models import BusinessVerification, Store, StoreMembership, StoreRole, StoreStatus, VerificationStatus
from tryon.models import ARStatus, TryOnProductAsset
from tryon.tasks import preprocess_product_image_task

User = get_user_model()


def create_dummy_image():
    """Generates an in-memory 100x100 RGB JPEG image."""
    buffer = BytesIO()
    image = Image.new("RGB", (100, 100), color=(255, 255, 255))
    image.save(buffer, format="JPEG")
    buffer.seek(0)
    return SimpleUploadedFile("dummy.jpg", buffer.read(), content_type="image/jpeg")


class PreprocessingTaskTestCase(TestCase):
    def setUp(self):
        self.seller = User.objects.create_user(
            email="seller@example.com",
            name="Seller Sam",
            password="StrongPassword123!",
            is_email_verified=True,
            is_seller=True,
            is_active=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.seller)

        self.store = Store.objects.create(
            name="Active Fashion",
            slug="active-fashion",
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

        self.product = Product.objects.create(
            store=self.store,
            name="Linen Casual Shirt",
            category=ProductCategory.TOP,
            price=Decimal("999.00"),
            status=ProductStatus.ACTIVE,
        )

    def test_unsupported_category_rejected_immediately(self):
        shoe = Product.objects.create(
            store=self.store,
            name="Sneakers",
            category=ProductCategory.FOOTWEAR,
            price=Decimal("1999.00"),
            status=ProductStatus.ACTIVE,
        )
        with patch("tryon.tasks.GeminiVisionService.inspect_clothing_image") as mock_gemini:
            success = preprocess_product_image_task(shoe.id)
            self.assertTrue(success)
            mock_gemini.assert_not_called()

        asset = TryOnProductAsset.objects.get(product=shoe)
        self.assertEqual(asset.status, ARStatus.REJECTED)
        self.assertIn("Footwear is not supported", asset.rejection_reason)

    def test_no_images_rejected(self):
        success = preprocess_product_image_task(self.product.id)
        self.assertTrue(success)
        asset = TryOnProductAsset.objects.get(product=self.product)
        self.assertEqual(asset.status, ARStatus.REJECTED)
        self.assertIn("No product images found", asset.rejection_reason)

    @patch("tryon.tasks.GeminiVisionService.inspect_clothing_image")
    def test_garbage_non_clothing_image_rejected(self, mock_gemini):
        ProductImage.objects.create(product=self.product, image=create_dummy_image(), order=0)
        mock_gemini.return_value = {
            "is_valid_clothing": False,
            "detected_category": "UNKNOWN",
            "presentation_type": "IRRELEVANT",
            "detected_view": "UNKNOWN",
            "has_neutral_background": False,
            "confidence": 0.0,
        }

        preprocess_product_image_task(self.product.id)
        asset = TryOnProductAsset.objects.get(product=self.product)
        self.assertEqual(asset.status, ARStatus.REJECTED)
        self.assertIn("does not contain a recognizable fashion item", asset.rejection_reason)

    @patch("tryon.tasks.GeminiVisionService.inspect_clothing_image")
    def test_category_mismatch_rejected(self, mock_gemini):
        ProductImage.objects.create(product=self.product, image=create_dummy_image(), order=0)
        # Product is TOP, but image is BOTTOM
        mock_gemini.return_value = {
            "is_valid_clothing": True,
            "detected_category": "BOTTOM",
            "presentation_type": "FLAT_LAY",
            "detected_view": "FRONT",
            "has_neutral_background": True,
            "confidence": 0.95,
        }

        preprocess_product_image_task(self.product.id)
        asset = TryOnProductAsset.objects.get(product=self.product)
        self.assertEqual(asset.status, ARStatus.REJECTED)
        self.assertIn("Category mismatch", asset.rejection_reason)
        self.assertIn("Bottom", asset.rejection_reason)

    @patch("tryon.tasks.GeminiVisionService.inspect_clothing_image")
    def test_only_on_model_photos_rejected_with_guidance(self, mock_gemini):
        ProductImage.objects.create(product=self.product, image=create_dummy_image(), order=0)
        mock_gemini.return_value = {
            "is_valid_clothing": True,
            "detected_category": "TOP",
            "presentation_type": "ON_MODEL",
            "detected_view": "FRONT",
            "has_neutral_background": True,
            "confidence": 0.92,
        }

        preprocess_product_image_task(self.product.id)
        asset = TryOnProductAsset.objects.get(product=self.product)
        self.assertEqual(asset.status, ARStatus.REJECTED)
        self.assertIn("Only on-model photos detected", asset.rejection_reason)

    @patch("tryon.tasks.GeminiVisionService.inspect_clothing_image")
    def test_missing_angles_rejected_with_exact_missing_list(self, mock_gemini):
        ProductImage.objects.create(product=self.product, image=create_dummy_image(), order=0)
        # Only front view provided
        mock_gemini.return_value = {
            "is_valid_clothing": True,
            "detected_category": "TOP",
            "presentation_type": "FLAT_LAY",
            "detected_view": "FRONT",
            "has_neutral_background": True,
            "confidence": 0.98,
        }

        preprocess_product_image_task(self.product.id)
        asset = TryOnProductAsset.objects.get(product=self.product)
        self.assertEqual(asset.status, ARStatus.REJECTED)
        self.assertIn("Requires at least 3 flat-lay views", asset.rejection_reason)
        self.assertIn("Missing: Back, Side", asset.rejection_reason)
        self.assertIsNotNone(asset.front_image)

    @patch("tryon.tasks.GeminiVisionService.inspect_clothing_image")
    def test_all_three_angles_marks_available(self, mock_gemini):
        img_front = ProductImage.objects.create(product=self.product, image=create_dummy_image(), order=0)
        img_back = ProductImage.objects.create(product=self.product, image=create_dummy_image(), order=1)
        img_side = ProductImage.objects.create(product=self.product, image=create_dummy_image(), order=2)

        def side_effect(image_bytes, filename):
            if "dummy.jpg" in filename:
                pass
            # Simulate 3 different angles returned sequentially
            if not hasattr(side_effect, "call_count"):
                side_effect.call_count = 0
            side_effect.call_count += 1

            views = ["FRONT", "BACK", "SIDE"]
            view = views[(side_effect.call_count - 1) % 3]
            return {
                "is_valid_clothing": True,
                "detected_category": "TOP",
                "presentation_type": "FLAT_LAY",
                "detected_view": view,
                "has_neutral_background": True,
                "confidence": 0.95,
            }

        mock_gemini.side_effect = side_effect

        preprocess_product_image_task(self.product.id)
        asset = TryOnProductAsset.objects.get(product=self.product)
        self.assertEqual(asset.status, ARStatus.AVAILABLE)
        self.assertEqual(asset.rejection_reason, "")
        self.assertTrue(asset.has_all_angles)
        self.assertEqual(asset.angles_count, 3)
        self.assertEqual(asset.front_image, img_front)
        self.assertEqual(asset.back_image, img_back)
        self.assertEqual(asset.side_image, img_side)
        self.assertEqual(asset.candidate_image, img_front)

    @patch("tryon.tasks.GeminiVisionService.inspect_clothing_image")
    def test_incremental_skip_when_already_available(self, mock_gemini):
        # Pre-seed available asset
        img_front = ProductImage.objects.create(product=self.product, image=create_dummy_image(), order=0)
        img_back = ProductImage.objects.create(product=self.product, image=create_dummy_image(), order=1)
        img_side = ProductImage.objects.create(product=self.product, image=create_dummy_image(), order=2)
        asset = TryOnProductAsset.objects.create(
            product=self.product,
            status=ARStatus.AVAILABLE,
            front_image=img_front,
            back_image=img_back,
            side_image=img_side,
            candidate_image=img_front,
        )

        preprocess_product_image_task(self.product.id)
        # Should not call Gemini when images are already complete and valid
        mock_gemini.assert_not_called()


class SellerARStatusAPITestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.seller = User.objects.create_user(
            email="seller@example.com",
            name="Seller Sam",
            password="StrongPassword123!",
            is_email_verified=True,
            is_seller=True,
            is_active=True,
        )
        self.other_seller = User.objects.create_user(
            email="other_seller@example.com",
            name="Other Seller",
            password="StrongPassword123!",
            is_email_verified=True,
            is_seller=True,
            is_active=True,
        )
        self.client.force_authenticate(user=self.seller)

        self.store = Store.objects.create(
            name="Brand Store",
            slug="brand-store",
            status=StoreStatus.ACTIVE,
            created_by=self.seller,
        )
        StoreMembership.objects.create(store=self.store, user=self.seller, role=StoreRole.OWNER)

        self.prod_available = Product.objects.create(
            store=self.store,
            name="Cotton Tee",
            category=ProductCategory.TOP,
            price=Decimal("499.00"),
            status=ProductStatus.ACTIVE,
        )
        img_front = ProductImage.objects.create(product=self.prod_available, image=create_dummy_image(), order=0)
        img_back = ProductImage.objects.create(product=self.prod_available, image=create_dummy_image(), order=1)
        img_side = ProductImage.objects.create(product=self.prod_available, image=create_dummy_image(), order=2)
        TryOnProductAsset.objects.create(
            product=self.prod_available,
            status=ARStatus.AVAILABLE,
            front_image=img_front,
            back_image=img_back,
            side_image=img_side,
            candidate_image=img_front,
        )

        self.prod_rejected = Product.objects.create(
            store=self.store,
            name="Jeans Pants",
            category=ProductCategory.BOTTOM,
            price=Decimal("1499.00"),
            status=ProductStatus.ACTIVE,
        )
        TryOnProductAsset.objects.create(
            product=self.prod_rejected,
            status=ARStatus.REJECTED,
            rejection_reason="Only on-model photos detected. Please upload at least 3 flat-lay or mannequin photos.",
        )

    def test_get_store_products_ar_status_list(self):
        url = reverse("store-products-ar-status", kwargs={"store_pk": self.store.id})
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data["success"])
        results = res.data["data"]["results"]
        self.assertEqual(len(results), 2)

        available_item = next(item for item in results if item["id"] == self.prod_available.id)
        self.assertEqual(available_item["ar_status"], "AVAILABLE")
        self.assertEqual(available_item["rejection_reason"], "")
        self.assertEqual(available_item["angles_count"], 3)
        self.assertTrue(available_item["angles"]["front"])
        self.assertTrue(available_item["angles"]["back"])
        self.assertTrue(available_item["angles"]["side"])

        rejected_item = next(item for item in results if item["id"] == self.prod_rejected.id)
        self.assertEqual(rejected_item["ar_status"], "REJECTED")
        self.assertIn("Only on-model photos detected", rejected_item["rejection_reason"])
        self.assertEqual(rejected_item["angles_count"], 0)

    def test_filter_by_status(self):
        url = reverse("store-products-ar-status", kwargs={"store_pk": self.store.id})
        res = self.client.get(url, {"status": "AVAILABLE"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data["data"]["results"]), 1)
        self.assertEqual(res.data["data"]["results"][0]["id"], self.prod_available.id)

    def test_get_single_product_ar_status_detail(self):
        url = reverse(
            "store-product-ar-status-detail",
            kwargs={"store_pk": self.store.id, "product_pk": self.prod_available.id},
        )
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["data"]["id"], self.prod_available.id)
        self.assertEqual(res.data["data"]["ar_status"], "AVAILABLE")

    def test_unauthorized_store_access_forbidden(self):
        # Authenticate as a different seller who is not a member of self.store
        self.client.force_authenticate(user=self.other_seller)
        url = reverse("store-products-ar-status", kwargs={"store_pk": self.store.id})
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
