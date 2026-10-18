from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from products.embedding_service import EmbeddingService
from products.models import Product, ProductEmbedding, ProductStatus
from products.tasks import generate_product_embedding_task
from stores.models import Store, StoreStatus

User = get_user_model()


class SemanticSearchTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

        self.seller = User.objects.create_user(
            email="seller@kisekae.test",
            name="Test Boutique",
            password="SecurePassword123!",
            is_email_verified=True,
            is_seller=True,
        )

        self.store = Store.objects.create(
            name="Tokyo Threads",
            slug="tokyo-threads",
            status=StoreStatus.ACTIVE,
            created_by=self.seller,
        )

        self.product1 = Product.objects.create(
            store=self.store,
            name="Japanese Silk Kimono",
            slug="japanese-silk-kimono",
            description="Traditional lightweight floral print silk kimono robe for summer.",
            price=Decimal("4999.00"),
            stock=10,
            status=ProductStatus.ACTIVE,
            created_by=self.seller,
        )

        self.product2 = Product.objects.create(
            store=self.store,
            name="Oversized Vintage Hoodie",
            slug="oversized-vintage-hoodie",
            description="Cozy fleece streetwear sweatshirt with drop shoulders.",
            price=Decimal("2499.00"),
            stock=25,
            status=ProductStatus.ACTIVE,
            created_by=self.seller,
        )

    def test_embedding_service_text_generation(self):
        service = EmbeddingService.get_instance()
        vec = service.embed_text("vintage denim jacket")
        self.assertEqual(len(vec), 384)
        self.assertIsInstance(vec[0], float)

    def test_embedding_service_query_caching(self):
        service = EmbeddingService.get_instance()
        query = "lightweight robe"

        vec1 = service.embed_query_cached(query)
        self.assertEqual(len(vec1), 384)

        with patch.object(service, "embed_text") as mock_embed:
            vec2 = service.embed_query_cached(query)
            mock_embed.assert_not_called()
            self.assertEqual(vec1, vec2)

    def test_generate_product_embedding_task(self):
        ProductEmbedding.objects.filter(product=self.product1).delete()

        success = generate_product_embedding_task(self.product1.id)
        self.assertTrue(success)

        embedding_rec = ProductEmbedding.objects.filter(product=self.product1).first()
        self.assertIsNotNone(embedding_rec)
        self.assertEqual(len(embedding_rec.embedding), 384)

    def test_search_api_query_match(self):
        url = reverse("public-product-search") + "?q=kimono"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["data"]["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], self.product1.id)

    def test_search_api_query_second_product(self):
        url = reverse("public-product-search") + "?q=hoodie"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["data"]["results"]
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0]["id"], self.product2.id)

    def test_search_api_ordering_and_filters(self):
        url = reverse("public-product-search") + "?ordering=-price"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["data"]["results"]
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["id"], self.product1.id)
        self.assertEqual(results[1]["id"], self.product2.id)

    def test_backfill_embeddings_command(self):
        ProductEmbedding.objects.all().delete()
        self.assertEqual(ProductEmbedding.objects.count(), 0)

        call_command("backfill_embeddings", "--sync", "--force")
        self.assertEqual(ProductEmbedding.objects.count(), 2)
