import io
from decimal import Decimal
from PIL import Image

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from cart.models import Cart, CartItem
from cart.services import CartService
from products.models import (
    Product,
    ProductCategory,
    ProductImage,
    ProductSize,
    ProductStatus,
)
from stores.models import Store, StoreMembership, StoreRole, StoreStatus

User = get_user_model()


def generate_test_image(name="test.jpg", color="blue"):
    file_obj = io.BytesIO()
    image = Image.new("RGB", (100, 100), color=color)
    image.save(file_obj, format="JPEG")
    file_obj.seek(0)
    return SimpleUploadedFile(name, file_obj.read(), content_type="image/jpeg")


class CatalogAndSizesTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

        self.seller = User.objects.create_user(
            email="seller@kisekae.test",
            name="Tokyo Store Owner",
            password="SecurePassword123!",
            is_email_verified=True,
            is_seller=True,
        )

        self.customer = User.objects.create_user(
            email="shopper@kisekae.test",
            name="Fashion Shopper",
            password="SecurePassword123!",
            is_email_verified=True,
            is_seller=False,
        )

        self.store = Store.objects.create(
            name="Harajuku Styles",
            slug="harajuku-styles",
            status=StoreStatus.ACTIVE,
            created_by=self.seller,
        )
        StoreMembership.objects.create(
            store=self.store,
            user=self.seller,
            role=StoreRole.OWNER,
        )

        # 1. Top Clothing Product
        self.shirt = Product.objects.create(
            store=self.store,
            name="Harajuku Graphic T-Shirt",
            slug="harajuku-graphic-tshirt",
            description="Cotton graphic tee from Tokyo.",
            price=Decimal("1500.00"),
            category=ProductCategory.TOP,
            color="Black",
            status=ProductStatus.ACTIVE,
            created_by=self.seller,
        )
        self.shirt_size_m = ProductSize.objects.create(
            product=self.shirt,
            size="M",
            stock=10,
        )
        self.shirt_size_l = ProductSize.objects.create(
            product=self.shirt,
            size="L",
            stock=5,
        )
        self.shirt.sync_total_stock()

        # 2. Footwear Product
        self.shoes = Product.objects.create(
            store=self.store,
            name="Cyberpunk Chunky Sneakers",
            slug="cyberpunk-chunky-sneakers",
            description="Futuristic sneakers with chunky rubber soles.",
            price=Decimal("4500.00"),
            category=ProductCategory.FOOTWEAR,
            color="White",
            status=ProductStatus.ACTIVE,
            created_by=self.seller,
        )
        self.shoe_size_8 = ProductSize.objects.create(
            product=self.shoes,
            size="8",
            stock=4,
        )
        self.shoe_size_9 = ProductSize.objects.create(
            product=self.shoes,
            size="9",
            stock=0,  # Out of stock size
        )
        self.shoes.sync_total_stock()

        # 3. Outerwear Product
        self.jacket = Product.objects.create(
            store=self.store,
            name="Shibuya Denim Jacket",
            slug="shibuya-denim-jacket",
            description="Vintage washed oversized denim jacket.",
            price=Decimal("3200.00"),
            category=ProductCategory.OUTERWEAR,
            color="Blue",
            status=ProductStatus.ACTIVE,
            created_by=self.seller,
        )
        self.jacket_size_xl = ProductSize.objects.create(
            product=self.jacket,
            size="XL",
            stock=3,
        )
        self.jacket.sync_total_stock()

    def test_category_choices_and_listing_endpoint(self):
        """Categories listing returns all 8 categories with metadata."""
        url = reverse("public-category-list")
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        categories = response.data.get("data", [])
        self.assertEqual(len(categories), 8)

        codes = [c["code"] for c in categories]
        expected_codes = [
            "ONE_PIECE",
            "TOP",
            "BOTTOM",
            "FOOTWEAR",
            "HEADWEAR",
            "EYEWEAR",
            "OUTERWEAR",
            "ACCESSORIES",
        ]
        self.assertEqual(sorted(codes), sorted(expected_codes))

        # Check Top item product count
        top_item = next(c for c in categories if c["code"] == "TOP")
        self.assertEqual(top_item["products_count"], 1)

    def test_size_validation_enforcement(self):
        """Validate size constraints based on category (clothing vs shoes)."""
        # Clothing item cannot take shoe size
        invalid_size = ProductSize(product=self.shirt, size="9", stock=5)
        with self.assertRaises(ValidationError) as ctx:
            invalid_size.clean()
        self.assertIn("Invalid clothing size '9'", str(ctx.exception))

        # Footwear cannot take clothing size
        invalid_shoe_size = ProductSize(product=self.shoes, size="L", stock=5)
        with self.assertRaises(ValidationError) as ctx:
            invalid_shoe_size.clean()
        self.assertIn("Invalid shoe size 'L'", str(ctx.exception))

        # Valid sizes succeed clean()
        valid_clothing = ProductSize(product=self.shirt, size="S", stock=3)
        valid_clothing.clean()  # should not raise

        valid_shoe = ProductSize(product=self.shoes, size="10", stock=2)
        valid_shoe.clean()  # should not raise

    def test_product_total_stock_synchronization(self):
        """Product total stock is the sum of all size stocks."""
        self.shirt.refresh_from_db()
        # M=10, L=5 => total=15
        self.assertEqual(self.shirt.stock, 15)

        # Update M stock
        self.shirt_size_m.stock = 12
        self.shirt_size_m.save()
        self.shirt.sync_total_stock()
        self.shirt.refresh_from_db()
        self.assertEqual(self.shirt.stock, 17)

    def test_public_product_list_category_filter(self):
        """Test filtering by category enum and type alias."""
        url = reverse("public-product-list")

        # Filter by ?category=TOP
        res_top = self.client.get(url, {"category": "TOP"})
        self.assertEqual(res_top.status_code, status.HTTP_200_OK)
        results = res_top.data.get("data", {}).get("results", [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], self.shirt.id)

        # Filter by ?type=Footwear (case-insensitive display name)
        res_shoes = self.client.get(url, {"type": "Footwear"})
        self.assertEqual(res_shoes.status_code, status.HTTP_200_OK)
        results = res_shoes.data.get("data", {}).get("results", [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], self.shoes.id)

    def test_public_product_search_and_filters(self):
        """Test search filters for size, color, and price range."""
        url = reverse("public-product-search")

        # 1. Filter by size (available stock > 0)
        res_size_m = self.client.get(url, {"size": "M"})
        self.assertEqual(res_size_m.status_code, status.HTTP_200_OK)
        results_m = res_size_m.data.get("data", {}).get("results", [])
        ids = [item["id"] for item in results_m]
        self.assertIn(self.shirt.id, ids)
        self.assertNotIn(self.shoes.id, ids)

        # Size 9 is out of stock (stock=0), should not be returned when filtered by size
        res_size_9 = self.client.get(url, {"size": "9"})
        self.assertEqual(res_size_9.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_size_9.data.get("data", {}).get("results", [])), 0)

        # 2. Filter by color
        res_color = self.client.get(url, {"color": "White"})
        self.assertEqual(res_color.status_code, status.HTTP_200_OK)
        results = res_color.data.get("data", {}).get("results", [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], self.shoes.id)

        # 3. Filter by price range
        res_price = self.client.get(url, {"min_price": "2000", "max_price": "4000"})
        self.assertEqual(res_price.status_code, status.HTTP_200_OK)
        results = res_price.data.get("data", {}).get("results", [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], self.jacket.id)

    def test_product_card_serializer_structure(self):
        """Ensure search and public listing return product card format."""
        url = reverse("public-product-list")
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.data.get("data", {}).get("results", [])
        self.assertGreater(len(results), 0)
        card = results[0]

        # Verify card fields
        expected_keys = {
            "id",
            "name",
            "price",
            "compare_at_price",
            "thumbnail",
            "category",
            "category_display",
            "color",
            "available_sizes",
            "is_in_stock",
            "store",
        }
        self.assertTrue(expected_keys.issubset(set(card.keys())))
        # Verify no variant / nested stock models exposed in card
        self.assertNotIn("variants", card)

    def test_multiple_images_limit_max_10(self):
        """Test uploading gallery images up to max 10 images."""
        self.client.force_authenticate(user=self.seller)
        url = reverse(
            "product-gallery-images",
            kwargs={"pk": self.store.id, "product_pk": self.shirt.id},
        )

        # Add 10 images
        for i in range(10):
            img_file = generate_test_image(f"image_{i}.jpg")
            res = self.client.post(url, {"file": img_file}, format="multipart")
            self.assertEqual(res.status_code, status.HTTP_200_OK)

        self.assertEqual(self.shirt.images.count(), 10)

        # 11th image must fail with 400 Bad Request
        extra_img = generate_test_image("image_11.jpg")
        res_overflow = self.client.post(url, {"file": extra_img}, format="multipart")
        self.assertEqual(res_overflow.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res_overflow.data.get("error", {}).get("code"), "MAX_IMAGES_EXCEEDED")

        # Check thumbnail property returns the first image
        self.shirt.refresh_from_db()
        first_img = self.shirt.images.order_by("order", "id").first()
        self.assertEqual(self.shirt.thumbnail, first_img.image.url)

    def test_cart_size_selection_and_validation(self):
        """Test adding sized products to cart, size-specific stock checks."""
        self.client.force_authenticate(user=self.customer)

        add_url = reverse("cart-add-item")

        # 1. Add valid size M with qty 2
        res = self.client.post(
            add_url,
            {"product_id": self.shirt.id, "quantity": 2, "size": "M"},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # 2. Add invalid size for this product (size doesn't exist on product)
        res_invalid_size = self.client.post(
            add_url,
            {"product_id": self.shirt.id, "quantity": 1, "size": "XS"},
            format="json",
        )
        self.assertEqual(res_invalid_size.status_code, status.HTTP_400_BAD_REQUEST)

        # 3. Add quantity exceeding size-specific stock (shirt M has 10, already 2 in cart => trying 9 more fails)
        res_excess = self.client.post(
            add_url,
            {"product_id": self.shirt.id, "quantity": 9, "size": "M"},
            format="json",
        )
        self.assertEqual(res_excess.status_code, status.HTTP_400_BAD_REQUEST)

        # 4. Add same product with different size (size L) => should create a separate cart item
        res_size_l = self.client.post(
            add_url,
            {"product_id": self.shirt.id, "quantity": 1, "size": "L"},
            format="json",
        )
        self.assertEqual(res_size_l.status_code, status.HTTP_200_OK)

        # Verify cart now has 2 distinct items for the same product
        cart = Cart.objects.get(user=self.customer)
        self.assertEqual(cart.items.count(), 2)

        items = cart.items.order_by("size")
        self.assertEqual(items[0].size, "L")
        self.assertEqual(items[0].quantity, 1)
        self.assertEqual(items[1].size, "M")
        self.assertEqual(items[1].quantity, 2)
