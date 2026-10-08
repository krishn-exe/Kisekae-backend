import io
from decimal import Decimal
from PIL import Image

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from products.models import Product, ProductStatus
from products.services import ProductService
from stores.models import Store, StoreMembership, StoreRole, StoreStatus

User = get_user_model()


class ProductModelTests(TestCase):
    def setUp(self):
        self.seller = User.objects.create_user(
            email="seller@example.com",
            name="Seller One",
            password="Password123!",
            is_seller=True,
            is_email_verified=True,
        )
        self.store = Store.objects.create(name="Sakura Apparel", created_by=self.seller)
        StoreMembership.objects.create(
            store=self.store,
            user=self.seller,
            role=StoreRole.OWNER,
        )

    def test_product_creation_and_auto_slug(self):
        product = Product.objects.create(
            store=self.store,
            name="Classic Kimono",
            price=Decimal("2999.00"),
            stock=10,
            created_by=self.seller,
        )
        self.assertEqual(product.slug, "classic-kimono")
        self.assertEqual(product.status, ProductStatus.DRAFT)
        self.assertFalse(product.is_active)
        self.assertTrue(product.is_in_stock)
        self.assertEqual(str(product), "Classic Kimono (Sakura Apparel)")

    def test_slug_collision_resolution_per_store(self):
        p1 = Product.objects.create(
            store=self.store,
            name="Silk Scarf",
            price=Decimal("499.00"),
        )
        p2 = Product.objects.create(
            store=self.store,
            name="Silk Scarf",
            price=Decimal("599.00"),
        )
        self.assertEqual(p1.slug, "silk-scarf")
        self.assertEqual(p2.slug, "silk-scarf-1")

    def test_product_queryset_methods(self):
        p_active = Product.objects.create(
            store=self.store,
            name="Active Scarf",
            price=Decimal("100.00"),
            status=ProductStatus.ACTIVE,
        )
        p_draft = Product.objects.create(
            store=self.store,
            name="Draft Scarf",
            price=Decimal("200.00"),
            status=ProductStatus.DRAFT,
        )
        p_inactive = Product.objects.create(
            store=self.store,
            name="Inactive Scarf",
            price=Decimal("300.00"),
            status=ProductStatus.INACTIVE,
        )

        active_qs = Product.objects.active()
        self.assertIn(p_active, active_qs)
        self.assertNotIn(p_draft, active_qs)
        self.assertNotIn(p_inactive, active_qs)

        store_qs = Product.objects.for_store(self.store)
        self.assertIn(p_active, store_qs)
        self.assertIn(p_draft, store_qs)
        self.assertIn(p_inactive, store_qs)


class ProductAPITests(APITestCase):
    def setUp(self):
        self.seller = User.objects.create_user(
            email="seller@example.com",
            name="Seller Owner",
            password="Password123!",
            is_seller=True,
            is_email_verified=True,
        )
        self.other_seller = User.objects.create_user(
            email="other_seller@example.com",
            name="Other Seller",
            password="Password123!",
            is_seller=True,
            is_email_verified=True,
        )
        self.non_seller = User.objects.create_user(
            email="customer@example.com",
            name="Customer",
            password="Password123!",
            is_seller=False,
            is_email_verified=True,
        )
        self.store = Store.objects.create(name="Tokyo Streetwear", created_by=self.seller)
        StoreMembership.objects.create(
            store=self.store,
            user=self.seller,
            role=StoreRole.OWNER,
        )

    def _create_image_file(self, filename="product.png", fmt="PNG", size=(100, 100)):
        file_obj = io.BytesIO()
        image = Image.new("RGB", size, (255, 0, 0))
        image.save(file_obj, format=fmt)
        file_obj.seek(0)
        return SimpleUploadedFile(filename, file_obj.read(), content_type=f"image/{fmt.lower()}")

    def test_non_seller_access_denied(self):
        self.client.force_authenticate(user=self.non_seller)
        res = self.client.get(f"/stores/{self.store.id}/products/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(res.data["error"]["code"], "NOT_A_SELLER")

    def test_non_member_seller_access_denied(self):
        self.client.force_authenticate(user=self.other_seller)
        res = self.client.get(f"/stores/{self.store.id}/products/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_create_product_success(self):
        self.client.force_authenticate(user=self.seller)
        res = self.client.post(
            f"/stores/{self.store.id}/products/",
            {
                "name": "Graphic Hoodie",
                "description": "100% cotton Japanese streetwear hoodie",
                "price": "1899.00",
                "compare_at_price": "2499.00",
                "currency": "INR",
                "sku": "HOODIE-BLK-M",
                "stock": 25,
                "status": "ACTIVE",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(res.data["success"])
        data = res.data["data"]
        self.assertEqual(data["name"], "Graphic Hoodie")
        self.assertEqual(data["slug"], "graphic-hoodie")
        self.assertEqual(data["price"], "1899.00")
        self.assertEqual(data["compare_at_price"], "2499.00")
        self.assertEqual(data["stock"], 25)
        self.assertEqual(data["status"], "ACTIVE")

        # Verify DB
        product = Product.objects.get(id=data["id"])
        self.assertEqual(product.store, self.store)
        self.assertEqual(product.created_by, self.seller)

    def test_create_product_in_inactive_or_archived_store_fails(self):
        self.store.archive()
        self.client.force_authenticate(user=self.seller)
        res = self.client.post(
            f"/stores/{self.store.id}/products/",
            {
                "name": "Graphic Hoodie",
                "price": "1899.00",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data["error"]["code"], "STORE_ARCHIVED")

    def test_list_products_with_search_and_filter(self):
        p1 = Product.objects.create(
            store=self.store,
            name="Blue Denim Jacket",
            sku="DENIM-01",
            price=Decimal("3500.00"),
            status=ProductStatus.ACTIVE,
        )
        p2 = Product.objects.create(
            store=self.store,
            name="Black Leather Jacket",
            sku="LTHR-01",
            price=Decimal("7500.00"),
            status=ProductStatus.DRAFT,
        )
        p3 = Product.objects.create(
            store=self.store,
            name="Red Beanie",
            sku="HAT-01",
            price=Decimal("600.00"),
            status=ProductStatus.ACTIVE,
        )

        self.client.force_authenticate(user=self.seller)

        # List all
        res = self.client.get(f"/stores/{self.store.id}/products/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["data"]["count"], 3)

        # Filter by status
        res_filter = self.client.get(f"/stores/{self.store.id}/products/?status=ACTIVE")
        self.assertEqual(res_filter.status_code, status.HTTP_200_OK)
        self.assertEqual(res_filter.data["data"]["count"], 2)

        # Search by name
        res_search = self.client.get(f"/stores/{self.store.id}/products/?search=denim")
        self.assertEqual(res_search.status_code, status.HTTP_200_OK)
        self.assertEqual(res_search.data["data"]["count"], 1)
        self.assertEqual(res_search.data["data"]["results"][0]["sku"], "DENIM-01")

        # Search by SKU
        res_sku = self.client.get(f"/stores/{self.store.id}/products/?search=LTHR-01")
        self.assertEqual(res_sku.status_code, status.HTTP_200_OK)
        self.assertEqual(res_sku.data["data"]["count"], 1)
        self.assertEqual(res_sku.data["data"]["results"][0]["name"], "Black Leather Jacket")

    def test_get_product_detail(self):
        product = Product.objects.create(
            store=self.store,
            name="Tokyo Tee",
            price=Decimal("999.00"),
        )
        self.client.force_authenticate(user=self.seller)
        res = self.client.get(f"/stores/{self.store.id}/products/{product.id}/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["data"]["name"], "Tokyo Tee")

    def test_update_product(self):
        product = Product.objects.create(
            store=self.store,
            name="Old Product Name",
            price=Decimal("999.00"),
            stock=5,
        )
        self.client.force_authenticate(user=self.seller)
        res = self.client.patch(
            f"/stores/{self.store.id}/products/{product.id}/",
            {
                "name": "New Product Name",
                "price": "1299.00",
                "stock": 15,
                "status": "ACTIVE",
            },
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["data"]["name"], "New Product Name")
        self.assertEqual(res.data["data"]["price"], "1299.00")
        self.assertEqual(res.data["data"]["stock"], 15)
        self.assertEqual(res.data["data"]["status"], "ACTIVE")

        product.refresh_from_db()
        self.assertEqual(product.slug, "new-product-name")

    def test_delete_product(self):
        product = Product.objects.create(
            store=self.store,
            name="To Be Deleted",
            price=Decimal("500.00"),
        )
        self.client.force_authenticate(user=self.seller)
        res = self.client.delete(f"/stores/{self.store.id}/products/{product.id}/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(Product.objects.filter(id=product.id).exists())

    def test_product_image_upload_and_delete(self):
        product = Product.objects.create(
            store=self.store,
            name="Image Test Item",
            price=Decimal("500.00"),
        )
        self.client.force_authenticate(user=self.seller)
        img_file = self._create_image_file("item.png", "PNG")

        upload_res = self.client.put(
            f"/stores/{self.store.id}/products/{product.id}/image/",
            {"file": img_file},
            format="multipart",
        )
        self.assertEqual(upload_res.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(upload_res.data["data"]["image_url"])

        # Delete image
        del_res = self.client.delete(f"/stores/{self.store.id}/products/{product.id}/image/")
        self.assertEqual(del_res.status_code, status.HTTP_200_OK)

        product.refresh_from_db()
        self.assertIsNone(product.image_url)
