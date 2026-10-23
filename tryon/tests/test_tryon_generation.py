import io
import uuid
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image
from rest_framework import status
from rest_framework.test import APIClient

from products.models import Product, ProductCategory, ProductImage, ProductStatus
from stores.models import BusinessVerification, Store, StoreMembership, StoreRole, StoreStatus, VerificationStatus
from tryon.models import (
    ARStatus,
    Combo,
    ComboItem,
    TryOnAngle,
    TryOnProductAsset,
    TryOnRequest,
    TryOnRequestStatus,
)
from tryon.tasks import generate_tryon_task

User = get_user_model()


def make_test_image_file(name="image.jpg", fmt="JPEG") -> SimpleUploadedFile:
    buf = io.BytesIO()
    img = Image.new("RGB", (100, 100), color=(120, 180, 240))
    img.save(buf, format=fmt)
    content_type = "image/jpeg" if fmt == "JPEG" else "image/png"
    return SimpleUploadedFile(name, buf.getvalue(), content_type=content_type)


class BaseTryOnTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="shopper@example.com",
            name="Alice Customer",
            password="StrongPassword123!",
            is_email_verified=True,
            is_active=True,
        )
        self.other_user = User.objects.create_user(
            email="other@example.com",
            name="Bob Stranger",
            password="StrongPassword123!",
            is_email_verified=True,
            is_active=True,
        )
        self.client.force_authenticate(user=self.user)

        # Store & Seller
        self.seller = User.objects.create_user(
            email="merchant@example.com",
            name="Store Owner",
            password="StrongPassword123!",
            is_email_verified=True,
            is_seller=True,
            is_active=True,
        )
        self.store = Store.objects.create(
            name="Fashion Hub",
            slug="fashion-hub",
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

        # Verified Top product with AR AVAILABLE
        self.top_product = Product.objects.create(
            store=self.store,
            name="Classic White Tee",
            category=ProductCategory.TOP,
            price=Decimal("499.00"),
            status=ProductStatus.ACTIVE,
        )
        self.top_img = ProductImage.objects.create(
            product=self.top_product,
            image=make_test_image_file("tee_front.jpg"),
        )
        self.top_asset = TryOnProductAsset.objects.create(
            product=self.top_product,
            front_image=self.top_img,
            candidate_image=self.top_img,
            status=ARStatus.AVAILABLE,
            detected_category=ProductCategory.TOP,
        )

        # Verified Bottom product with AR AVAILABLE
        self.bottom_product = Product.objects.create(
            store=self.store,
            name="Navy Chinos",
            category=ProductCategory.BOTTOM,
            price=Decimal("1299.00"),
            status=ProductStatus.ACTIVE,
        )
        self.bottom_img = ProductImage.objects.create(
            product=self.bottom_product,
            image=make_test_image_file("chinos_front.jpg"),
        )
        self.bottom_asset = TryOnProductAsset.objects.create(
            product=self.bottom_product,
            front_image=self.bottom_img,
            candidate_image=self.bottom_img,
            status=ARStatus.AVAILABLE,
            detected_category=ProductCategory.BOTTOM,
        )

        # Unverified product with AR REJECTED
        self.unverified_product = Product.objects.create(
            store=self.store,
            name="Draft Hoodie",
            category=ProductCategory.TOP,
            price=Decimal("899.00"),
            status=ProductStatus.ACTIVE,
        )
        self.unverified_asset = TryOnProductAsset.objects.create(
            product=self.unverified_product,
            status=ARStatus.REJECTED,
            rejection_reason="Only on-model photos detected.",
        )

        # Unsupported Footwear product
        self.shoes_product = Product.objects.create(
            store=self.store,
            name="Running Sneakers",
            category=ProductCategory.FOOTWEAR,
            price=Decimal("1999.00"),
            status=ProductStatus.ACTIVE,
        )

        # Combo with valid items
        self.combo = Combo.objects.create(user=self.user, name="Weekend Casual")
        ComboItem.objects.create(combo=self.combo, product=self.top_product, category=ProductCategory.TOP)
        ComboItem.objects.create(combo=self.combo, product=self.bottom_product, category=ProductCategory.BOTTOM)


class TestTryOnGenerateAPI(BaseTryOnTestCase):
    @patch("tryon.views.generate_tryon_task.delay")
    def test_queue_single_product_tryon_success(self, mock_delay):
        url = reverse("tryon-generate")
        selfie = make_test_image_file("selfie.jpg")
        payload = {
            "person_image": selfie,
            "product_id": self.top_product.id,
        }
        res = self.client.post(url, payload, format="multipart")
        self.assertEqual(res.status_code, status.HTTP_202_ACCEPTED)
        self.assertTrue(res.data["success"])
        req_id = res.data["data"]["request_id"]
        self.assertEqual(res.data["data"]["status"], "PENDING")

        mock_delay.assert_called_once_with(req_id)
        # Verify in DB
        tryon_req = TryOnRequest.objects.get(id=req_id)
        self.assertEqual(tryon_req.user, self.user)
        self.assertEqual(tryon_req.product, self.top_product)
        self.assertEqual(tryon_req.angle, TryOnAngle.AUTO)
        self.assertEqual(tryon_req.status, TryOnRequestStatus.PENDING)

    @patch("tryon.views.generate_tryon_task.delay")
    def test_queue_combo_tryon_success(self, mock_delay):
        url = reverse("tryon-generate")
        selfie = make_test_image_file("selfie.jpg")
        payload = {
            "person_image": selfie,
            "combo_id": self.combo.id,
        }
        res = self.client.post(url, payload, format="multipart")
        self.assertEqual(res.status_code, status.HTTP_202_ACCEPTED)
        self.assertTrue(res.data["success"])
        req_id = res.data["data"]["request_id"]
        mock_delay.assert_called_once_with(req_id)

        tryon_req = TryOnRequest.objects.get(id=req_id)
        self.assertEqual(tryon_req.combo, self.combo)
        self.assertIsNone(tryon_req.product)
        self.assertEqual(tryon_req.angle, TryOnAngle.AUTO)

    def test_rejects_missing_both_product_and_combo(self):
        url = reverse("tryon-generate")
        selfie = make_test_image_file("selfie.jpg")
        res = self.client.post(url, {"person_image": selfie}, format="multipart")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Either 'product_id' or 'combo_id' must be provided", str(res.data))

    def test_rejects_providing_both_product_and_combo(self):
        url = reverse("tryon-generate")
        selfie = make_test_image_file("selfie.jpg")
        payload = {
            "person_image": selfie,
            "product_id": self.top_product.id,
            "combo_id": self.combo.id,
        }
        res = self.client.post(url, payload, format="multipart")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Cannot provide both", str(res.data))

    def test_rejects_unsupported_footwear_category(self):
        url = reverse("tryon-generate")
        selfie = make_test_image_file("selfie.jpg")
        payload = {
            "person_image": selfie,
            "product_id": self.shoes_product.id,
        }
        res = self.client.post(url, payload, format="multipart")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Footwear", res.data["message"])

    def test_rejects_product_not_ar_ready(self):
        url = reverse("tryon-generate")
        selfie = make_test_image_file("selfie.jpg")
        payload = {
            "person_image": selfie,
            "product_id": self.unverified_product.id,
        }
        res = self.client.post(url, payload, format="multipart")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Product is not AR ready", res.data["message"])

    def test_rejects_non_existent_product(self):
        url = reverse("tryon-generate")
        selfie = make_test_image_file("selfie.jpg")
        payload = {
            "person_image": selfie,
            "product_id": 999999,
        }
        res = self.client.post(url, payload, format="multipart")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_unauthenticated_request_rejected(self):
        self.client.logout()
        url = reverse("tryon-generate")
        selfie = make_test_image_file("selfie.jpg")
        res = self.client.post(url, {"person_image": selfie, "product_id": self.top_product.id}, format="multipart")
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)


class TestTryOnStatusAndHistoryAPIs(BaseTryOnTestCase):
    def setUp(self):
        super().setUp()
        self.tryon_req = TryOnRequest.objects.create(
            user=self.user,
            product=self.top_product,
            person_image=make_test_image_file("person.jpg"),
            status=TryOnRequestStatus.READY,
            angle=TryOnAngle.FRONT,
        )
        self.tryon_req.result_image.save("result_test.png", ContentFile(b"\x89PNG\r\n\x1a\n...test_bytes..."))

    def test_get_tryon_status_success(self):
        url = reverse("tryon-request-detail", kwargs={"request_id": self.tryon_req.id})
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data["success"])
        data = res.data["data"]
        self.assertEqual(data["id"], str(self.tryon_req.id))
        self.assertEqual(data["status"], "READY")
        self.assertEqual(data["product_name"], "Classic White Tee")
        self.assertIn("result_test", data["result_image_url"])

    def test_other_user_cannot_access_request(self):
        self.client.force_authenticate(user=self.other_user)
        url = reverse("tryon-request-detail", kwargs={"request_id": self.tryon_req.id})
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_non_existent_request_returns_404(self):
        url = reverse("tryon-request-detail", kwargs={"request_id": uuid.uuid4()})
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_get_tryon_history_list(self):
        url = reverse("tryon-history-list")
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data["success"])
        self.assertEqual(len(res.data["data"]), 1)
        self.assertEqual(res.data["data"][0]["id"], str(self.tryon_req.id))


class TestGenerateTryOnTask(BaseTryOnTestCase):
    @patch("tryon.tasks.GeminiVisionService.detect_person_pose")
    @patch("tryon.tasks.VertexAIVirtualTryOnClient.predict")
    def test_generate_tryon_task_single_product(self, mock_predict, mock_pose):
        mock_pose.return_value = {"is_valid_person": True, "detected_pose": "FRONT", "confidence": 1.0}
        req = TryOnRequest.objects.create(
            user=self.user,
            product=self.top_product,
            person_image=make_test_image_file("person.jpg"),
            status=TryOnRequestStatus.PENDING,
        )

        mock_predict.return_value = b"\x89PNG\r\nsynthetic_result_bytes"
        success = generate_tryon_task(str(req.id))

        self.assertTrue(success)
        req.refresh_from_db()
        self.assertEqual(req.status, TryOnRequestStatus.READY)
        self.assertIsNotNone(req.completed_at)
        self.assertTrue(bool(req.result_image))
        mock_predict.assert_called_once()
        mock_pose.assert_called_once()

    @patch("tryon.tasks.GeminiVisionService.detect_person_pose")
    @patch("tryon.tasks.VertexAIVirtualTryOnClient.predict")
    def test_generate_tryon_task_combo_sequential_chaining(self, mock_predict, mock_pose):
        mock_pose.return_value = {"is_valid_person": True, "detected_pose": "FRONT", "confidence": 1.0}
        req = TryOnRequest.objects.create(
            user=self.user,
            combo=self.combo,
            person_image=make_test_image_file("person.jpg"),
            status=TryOnRequestStatus.PENDING,
        )

        mock_predict.side_effect = [
            b"\x89PNG\r\npass_1_bottom",
            b"\x89PNG\r\npass_2_top",
        ]
        success = generate_tryon_task(str(req.id))

        self.assertTrue(success)
        req.refresh_from_db()
        self.assertEqual(req.status, TryOnRequestStatus.READY)
        # Verify 2 passes were chained (BOTTOM first, then TOP)
        self.assertEqual(mock_predict.call_count, 2)

    @patch("tryon.tasks.GeminiVisionService.detect_person_pose")
    @patch("tryon.tasks.VertexAIVirtualTryOnClient.predict")
    def test_generate_tryon_task_auto_detects_pose(self, mock_predict, mock_detect_pose):
        mock_detect_pose.return_value = {
            "is_valid_person": True,
            "detected_pose": "BACK",
            "confidence": 0.95,
        }
        mock_predict.return_value = b"\x89PNG\r\nsynthetic_result_bytes"

        # Give product a back image as well
        back_img = ProductImage.objects.create(
            product=self.top_product,
            image=make_test_image_file("tee_back.jpg"),
        )
        self.top_asset.back_image = back_img
        self.top_asset.save()

        req = TryOnRequest.objects.create(
            user=self.user,
            product=self.top_product,
            person_image=make_test_image_file("person_back.jpg"),
            status=TryOnRequestStatus.PENDING,
            angle=TryOnAngle.AUTO,
        )

        success = generate_tryon_task(str(req.id))
        self.assertTrue(success)

        req.refresh_from_db()
        self.assertEqual(req.status, TryOnRequestStatus.READY)
        # Verify angle was automatically updated to BACK
        self.assertEqual(req.angle, TryOnAngle.BACK)
        mock_detect_pose.assert_called_once()
        mock_predict.assert_called_once()

    @patch("tryon.tasks.GeminiVisionService.detect_person_pose")
    def test_generate_tryon_task_rejects_non_person_photo(self, mock_detect_pose):
        mock_detect_pose.return_value = {
            "is_valid_person": False,
            "detected_pose": "FRONT",
            "confidence": 0.99,
        }

        req = TryOnRequest.objects.create(
            user=self.user,
            product=self.top_product,
            person_image=make_test_image_file("dog_or_car.jpg"),
            status=TryOnRequestStatus.PENDING,
            angle=TryOnAngle.AUTO,
        )

        success = generate_tryon_task(str(req.id))
        self.assertFalse(success)

        req.refresh_from_db()
        self.assertEqual(req.status, TryOnRequestStatus.FAILED)
        self.assertIn("No recognizable person", req.error_message)

