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

from accounts.models import WishlistItem
from cart.models import Cart, CartItem
from cart.services import CartService
from orders.models import Order, OrderItem, OrderStatus
from orders.services import OrderService
from products.embedding_service import EmbeddingService
from products.models import (
    Product,
    ProductCategory,
    ProductEmbedding,
    ProductSize,
    ProductStatus,
    ProductView,
    SearchHistory,
    UserTasteProfile,
)
from products.recommendation_service import RecommendationService
from stores.models import BusinessVerification, Store, StoreMembership, StoreRole, StoreStatus, VerificationStatus

User = get_user_model()


def generate_test_image(name="test.jpg", color="blue"):
    file_obj = io.BytesIO()
    image = Image.new("RGB", (100, 100), color=color)
    image.save(file_obj, format="JPEG")
    file_obj.seek(0)
    return SimpleUploadedFile(name, file_obj.read(), content_type="image/jpeg")


class RecommendationAndSearchHistoryTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

        self.seller = User.objects.create_user(
            email="tokyo.boutique@kisekae.test",
            name="Tokyo Boutique",
            password="SecurePassword123!",
            is_email_verified=True,
            is_seller=True,
        )

        self.user = User.objects.create_user(
            email="shopper.yuki@kisekae.test",
            name="Yuki Tanaka",
            password="SecurePassword123!",
            is_email_verified=True,
            is_seller=False,
        )

        self.other_user = User.objects.create_user(
            email="other.shopper@kisekae.test",
            name="Kenji Sato",
            password="SecurePassword123!",
            is_email_verified=True,
            is_seller=False,
        )

        self.store = Store.objects.create(
            name="Shibuya Streetwear",
            slug="shibuya-streetwear",
            status=StoreStatus.ACTIVE,
            created_by=self.seller,
        )
        BusinessVerification.objects.create(
            store=self.store,
            pan_number="ABCDE1234F",
            status=VerificationStatus.APPROVED,
        )
        StoreMembership.objects.create(
            store=self.store,
            user=self.seller,
            role=StoreRole.OWNER,
        )

        # 1. Product: Oversized Streetwear Hoodie (TOP)
        self.hoodie = Product.objects.create(
            store=self.store,
            name="Oversized Streetwear Black Hoodie",
            slug="oversized-streetwear-black-hoodie",
            description="Heavyweight fleece hoodie in washed black cotton.",
            category=ProductCategory.TOP,
            color="Black",
            price=Decimal("3500.00"),
            stock=20,
            status=ProductStatus.ACTIVE,
            created_by=self.seller,
        )
        ProductSize.objects.create(product=self.hoodie, size="M", stock=10)
        ProductSize.objects.create(product=self.hoodie, size="L", stock=10)
        self.hoodie.sync_total_stock()

        # 2. Product: Cargo Baggy Pants (BOTTOM)
        self.pants = Product.objects.create(
            store=self.store,
            name="Wide Leg Tech Cargo Pants",
            slug="wide-leg-tech-cargo-pants",
            description="Black utilitarian techwear cargo pants with pockets.",
            category=ProductCategory.BOTTOM,
            color="Black",
            price=Decimal("4200.00"),
            stock=15,
            status=ProductStatus.ACTIVE,
            created_by=self.seller,
        )
        ProductSize.objects.create(product=self.pants, size="M", stock=10)
        ProductSize.objects.create(product=self.pants, size="L", stock=5)
        self.pants.sync_total_stock()

        # 3. Product: Chunky High-Top Sneakers (FOOTWEAR)
        self.shoes = Product.objects.create(
            store=self.store,
            name="Cyberpunk High-Top Platform Sneakers",
            slug="cyberpunk-high-top-platform-sneakers",
            description="Chunky white platform running shoes with futuristic accents.",
            category=ProductCategory.FOOTWEAR,
            color="White",
            price=Decimal("6500.00"),
            stock=8,
            status=ProductStatus.ACTIVE,
            created_by=self.seller,
        )
        ProductSize.objects.create(product=self.shoes, size="8", stock=4)
        ProductSize.objects.create(product=self.shoes, size="9", stock=4)
        self.shoes.sync_total_stock()

        # Generate embeddings for test products
        service = EmbeddingService.get_instance()
        for prod in [self.hoodie, self.pants, self.shoes]:
            vec = service.embed_product(prod)
            ProductEmbedding.objects.create(product=prod, embedding=vec)

    def test_search_history_apis(self):
        """Test listing, deleting, and clearing search history."""
        self.client.force_authenticate(user=self.user)

        # Create search history records
        s1 = SearchHistory.objects.create(user=self.user, query="oversized black hoodie", search_count=2)
        s2 = SearchHistory.objects.create(user=self.user, query="tech cargo pants", search_count=1)

        # 1. GET search history list
        list_url = reverse("public-search-history-list")
        res = self.client.get(list_url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        items = res.data.get("data", [])
        self.assertEqual(len(items), 2)
        queries = [item["query"] for item in items]
        self.assertIn("oversized black hoodie", queries)
        self.assertIn("tech cargo pants", queries)

        # 2. DELETE single search history entry
        detail_url = reverse("public-search-history-detail", kwargs={"history_id": s2.id})
        res_del = self.client.delete(detail_url)
        self.assertEqual(res_del.status_code, status.HTTP_200_OK)
        self.assertFalse(SearchHistory.objects.filter(pk=s2.id).exists())
        self.assertTrue(SearchHistory.objects.filter(pk=s1.id).exists())

        # 3. DELETE all search history
        res_clear = self.client.delete(list_url)
        self.assertEqual(res_clear.status_code, status.HTTP_200_OK)
        self.assertEqual(SearchHistory.objects.filter(user=self.user).count(), 0)

    def test_search_auto_recording(self):
        """Searching while authenticated auto-records search history."""
        self.client.force_authenticate(user=self.user)

        search_url = reverse("public-product-search")
        res = self.client.get(search_url, {"q": "black hoodie"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # Verify SearchHistory entry was created
        record = SearchHistory.objects.filter(user=self.user, query="black hoodie").first()
        self.assertIsNotNone(record)
        self.assertEqual(record.search_count, 1)

    def test_product_detail_view_recording(self):
        """Viewing product detail while authenticated records ProductView."""
        self.client.force_authenticate(user=self.user)

        detail_url = reverse("public-product-detail", kwargs={"product_pk": self.hoodie.id})
        res = self.client.get(detail_url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        view_record = ProductView.objects.filter(user=self.user, product=self.hoodie).first()
        self.assertIsNotNone(view_record)
        self.assertEqual(view_record.view_count, 1)

    def test_taste_vector_computation_with_search_history(self):
        """Taste vector computation includes search query embeddings and product signals."""
        # Add a search query
        service = EmbeddingService.get_instance()
        search_vec = service.embed_query_cached("black streetwear hoodie")
        SearchHistory.objects.create(
            user=self.user,
            query="black streetwear hoodie",
            embedding=search_vec,
            search_count=3,
        )

        # Add a product view
        ProductView.objects.create(user=self.user, product=self.hoodie, view_count=2)

        # Add a cart item
        cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(cart=cart, product=self.pants, quantity=1, size="M")

        # Compute taste vector
        vector, count = RecommendationService.compute_taste_vector(self.user)
        self.assertIsNotNone(vector)
        self.assertEqual(len(vector), 384)
        self.assertEqual(count, 3)

        # Update taste profile model
        profile = RecommendationService.update_user_taste_profile(self.user)
        self.assertIsNotNone(profile)
        self.assertEqual(profile.interaction_count, 3)
        self.assertEqual(len(profile.embedding), 384)

    def test_recommended_feed_cold_start(self):
        """Cold-start feed returns active products for anonymous / new users."""
        url = reverse("public-product-recommendations")

        # Anonymous request
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.data.get("data", {}).get("results", [])
        self.assertGreater(len(results), 0)

        # Product card structure verified
        card = results[0]
        self.assertIn("id", card)
        self.assertIn("name", card)
        self.assertIn("price", card)
        self.assertIn("thumbnail", card)
        self.assertIn("category", card)

    def test_recommended_feed_personalized(self):
        """Personalized feed recommends products matching user's taste vector."""
        self.client.force_authenticate(user=self.user)

        # User has high interest in footwear
        service = EmbeddingService.get_instance()
        search_vec = service.embed_query_cached("chunky white platform sneakers")
        SearchHistory.objects.create(
            user=self.user,
            query="chunky white platform sneakers",
            embedding=search_vec,
            search_count=3,
        )
        ProductView.objects.create(user=self.user, product=self.shoes, view_count=5)
        WishlistItem.objects.create(user=self.user, product=self.shoes)

        # Update taste profile
        RecommendationService.update_user_taste_profile(self.user)

        # Request recommended feed
        url = reverse("public-product-recommendations")
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.data.get("data", {}).get("results", [])
        self.assertGreater(len(results), 0)

        # Most similar product should be the shoes
        top_product_id = results[0]["id"]
        self.assertEqual(top_product_id, self.shoes.id)

    def test_recommended_feed_exclude_cart(self):
        """exclude_cart=true filters out products already in user's cart."""
        self.client.force_authenticate(user=self.user)

        # Add hoodie to cart
        cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(cart=cart, product=self.hoodie, quantity=1, size="M")

        # Provide signals so user has personalized recommendations
        ProductView.objects.create(user=self.user, product=self.pants, view_count=3)
        SearchHistory.objects.create(user=self.user, query="streetwear pants", search_count=2)
        WishlistItem.objects.create(user=self.user, product=self.shoes)
        RecommendationService.update_user_taste_profile(self.user)

        # Request feed with exclude_cart=true
        url = reverse("public-product-recommendations")
        res = self.client.get(url, {"exclude_cart": "true"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.data.get("data", {}).get("results", [])
        result_ids = [r["id"] for r in results]

        self.assertNotIn(self.hoodie.id, result_ids)

    def test_order_requires_non_null_user(self):
        """OrderService.create_order enforces non-null user foreign key."""
        customer_data = {
            "customer_name": "Yuki Tanaka",
            "customer_email": "shopper.yuki@kisekae.test",
            "customer_phone": "+819012345678",
            "shipping_address": "1-2-3 Shibuya, Tokyo",
            "notes": "Please leave at door",
        }
        items_data = [
            {"product_id": self.hoodie.id, "quantity": 1},
        ]

        # 1. Order without user raises ValidationError
        with self.assertRaises(ValidationError) as ctx:
            OrderService.create_order(
                store=self.store,
                customer_data=customer_data,
                items_data=items_data,
                user=None,
            )
        self.assertIn("A valid user account is required", str(ctx.exception))

        # 2. Order with user succeeds and links foreign key
        order = OrderService.create_order(
            store=self.store,
            customer_data=customer_data,
            items_data=items_data,
            user=self.user,
        )
        self.assertIsNotNone(order.id)
        self.assertEqual(order.user, self.user)
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(order.items.first().product, self.hoodie)

    def test_cold_start_popularity_order_and_cancellation_exclusion(self):
        """Cold-start recommendations exclude cancelled orders from popularity and preserve rank order."""
        cache.clear()

        # Pants: 1 delivered order, 1 cancelled order -> effective sales = 1
        order_pants_1 = Order.objects.create(
            user=self.user,
            store=self.store,
            customer_name="Customer",
            customer_email="c@test.com",
            customer_phone="1234567890",
            shipping_address="Tokyo",
            status=OrderStatus.DELIVERED,
            total_amount=Decimal("100.00"),
        )
        OrderItem.objects.create(
            order=order_pants_1,
            product=self.pants,
            product_name=self.pants.name,
            quantity=1,
            unit_price=self.pants.price,
            total_price=self.pants.price,
        )

        order_pants_2 = Order.objects.create(
            user=self.user,
            store=self.store,
            customer_name="Customer",
            customer_email="c@test.com",
            customer_phone="1234567890",
            shipping_address="Tokyo",
            status=OrderStatus.CANCELLED,
            total_amount=Decimal("100.00"),
        )
        OrderItem.objects.create(
            order=order_pants_2,
            product=self.pants,
            product_name=self.pants.name,
            quantity=1,
            unit_price=self.pants.price,
            total_price=self.pants.price,
        )

        # Shoes: 2 delivered orders -> effective sales = 2
        for _ in range(2):
            o = Order.objects.create(
                user=self.user,
                store=self.store,
                customer_name="Customer",
                customer_email="c@test.com",
                customer_phone="1234567890",
                shipping_address="Tokyo",
                status=OrderStatus.DELIVERED,
                total_amount=Decimal("100.00"),
            )
            OrderItem.objects.create(
                order=o,
                product=self.shoes,
                product_name=self.shoes.name,
                quantity=1,
                unit_price=self.shoes.price,
                total_price=self.shoes.price,
            )

        cold_start_products = list(RecommendationService.get_cold_start_recommendations(limit=10))
        cold_ids = [p.id for p in cold_start_products]

        # Shoes (2 sales) must come before Pants (1 sale)
        self.assertIn(self.shoes.id, cold_ids)
        self.assertIn(self.pants.id, cold_ids)
        self.assertLess(cold_ids.index(self.shoes.id), cold_ids.index(self.pants.id))
