from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from orders.models import Order, OrderItem, OrderStatus
from products.models import Product, ProductCategory, ProductStatus
from reviews.models import Review
from stores.models import Store, StoreMembership, StoreRole, StoreStatus

User = get_user_model()


class ReviewSystemTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()

        # Seller
        self.seller = User.objects.create_user(
            email="seller@boutique.com",
            name="Store Owner",
            password="StrongPassword123!",
            is_email_verified=True,
            is_seller=True,
            is_active=True,
        )

        # Customers
        self.buyer = User.objects.create_user(
            email="alice@customer.com",
            name="Alice Buyer",
            password="StrongPassword123!",
            is_email_verified=True,
            is_active=True,
        )

        self.shopper = User.objects.create_user(
            email="bob@customer.com",
            name="Bob Shopper",
            password="StrongPassword123!",
            is_email_verified=True,
            is_active=True,
        )

        self.other_user = User.objects.create_user(
            email="carol@customer.com",
            name="Carol Visitor",
            password="StrongPassword123!",
            is_email_verified=True,
            is_active=True,
        )

        # Store
        self.store = Store.objects.create(
            name="Maison Chic",
            slug="maison-chic",
            status=StoreStatus.ACTIVE,
            created_by=self.seller,
        )
        StoreMembership.objects.create(store=self.store, user=self.seller, role=StoreRole.OWNER)

        # Products
        self.product = Product.objects.create(
            store=self.store,
            name="Cashmere Wool Blazer",
            price=Decimal("7999.00"),
            category=ProductCategory.OUTERWEAR,
            status=ProductStatus.ACTIVE,
            stock=10,
            created_by=self.seller,
        )

        self.product2 = Product.objects.create(
            store=self.store,
            name="Classic Linen Shirt",
            price=Decimal("2499.00"),
            category=ProductCategory.TOP,
            status=ProductStatus.ACTIVE,
            stock=20,
            created_by=self.seller,
        )

        # Alice has an order for product in DELIVERED status (verified buyer)
        order = Order.objects.create(
            user=self.buyer,
            store=self.store,
            customer_name="Alice Buyer",
            customer_email="alice@customer.com",
            customer_phone="9876543210",
            shipping_address="123 Park Lane",
            status=OrderStatus.DELIVERED,
            total_amount=Decimal("7999.00"),
        )
        OrderItem.objects.create(
            order=order,
            product=self.product,
            product_name=self.product.name,
            quantity=1,
            unit_price=self.product.price,
            total_price=self.product.price,
        )

    def test_post_review_success(self):
        self.client.force_authenticate(user=self.buyer)
        url = reverse("product-reviews-list-create", kwargs={"product_pk": self.product.id})

        payload = {
            "rating": 5,
            "title": "Exquisite fit and warmth",
            "description": "The wool feels luxurious and fits tailored to perfection.",
        }
        res = self.client.post(url, payload)

        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(res.data["success"])
        data = res.data["data"]
        self.assertEqual(data["rating"], 5)
        self.assertEqual(data["title"], "Exquisite fit and warmth")
        self.assertTrue(data["is_verified_buyer"])
        self.assertEqual(data["user_name"], "Alice Buyer")

        # Verify product rating fields updated
        self.product.refresh_from_db()
        self.assertEqual(float(self.product.average_rating), 5.0)
        self.assertEqual(self.product.review_count, 1)

    def test_post_review_unauthenticated_fails(self):
        url = reverse("product-reviews-list-create", kwargs={"product_pk": self.product.id})
        payload = {
            "rating": 5,
            "title": "Nice jacket",
            "description": "Great coat",
        }
        res = self.client.post(url, payload)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_post_review_invalid_rating(self):
        self.client.force_authenticate(user=self.shopper)
        url = reverse("product-reviews-list-create", kwargs={"product_pk": self.product.id})

        # Rating 0 (too low)
        res = self.client.post(url, {"rating": 0, "title": "Poor", "description": "Too small"})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # Rating 6 (too high)
        res = self.client.post(url, {"rating": 6, "title": "Too good", "description": "Amazing"})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_post_review_duplicate_prevented(self):
        self.client.force_authenticate(user=self.buyer)
        url = reverse("product-reviews-list-create", kwargs={"product_pk": self.product.id})

        # First review succeeds
        res1 = self.client.post(url, {"rating": 5, "title": "First review", "description": "Loved it"})
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)

        # Second review by same user on same product fails
        res2 = self.client.post(url, {"rating": 4, "title": "Second review", "description": "Still good"})
        self.assertEqual(res2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res2.data["error"]["code"], "DUPLICATE_REVIEW")
        self.assertIn("already reviewed", res2.data["message"])

    def test_verified_buyer_distinction(self):
        # Alice is a verified buyer (has DELIVERED order for this product)
        self.client.force_authenticate(user=self.buyer)
        url = reverse("product-reviews-list-create", kwargs={"product_pk": self.product.id})
        res1 = self.client.post(url, {"rating": 5, "title": "Verified review", "description": "Real buyer"})
        self.assertTrue(res1.data["data"]["is_verified_buyer"])

        # Bob has not ordered this product -> unverified buyer
        self.client.force_authenticate(user=self.shopper)
        res2 = self.client.post(url, {"rating": 4, "title": "Unverified review", "description": "Browsed in store"})
        self.assertFalse(res2.data["data"]["is_verified_buyer"])

    def test_multi_review_average_rating_calculation(self):
        # Alice gives 5
        Review.objects.create(
            product=self.product,
            user=self.buyer,
            rating=5,
            title="5 star",
            description="Superb quality",
        )
        # Bob gives 4
        Review.objects.create(
            product=self.product,
            user=self.shopper,
            rating=4,
            title="4 star",
            description="Very good jacket",
        )
        # Carol gives 2
        Review.objects.create(
            product=self.product,
            user=self.other_user,
            rating=2,
            title="2 star",
            description="Did not fit well",
        )

        self.product.refresh_from_db()
        # Average of 5, 4, 2 = 11 / 3 = 3.67
        self.assertEqual(float(self.product.average_rating), 3.67)
        self.assertEqual(self.product.review_count, 3)

    def test_product_detail_and_card_serializers_include_ratings(self):
        Review.objects.create(
            product=self.product,
            user=self.buyer,
            rating=5,
            title="Top tier blazer",
            description="Warm and elegant",
        )

        # 1. Product Detail endpoint
        detail_url = reverse("public-product-detail", kwargs={"product_pk": self.product.id})
        detail_res = self.client.get(detail_url)
        self.assertEqual(detail_res.status_code, status.HTTP_200_OK)
        detail_data = detail_res.data["data"]
        self.assertEqual(detail_data["average_rating"], 5.0)
        self.assertEqual(detail_data["review_count"], 1)

        # 2. Product List endpoint
        list_url = reverse("public-product-list")
        list_res = self.client.get(list_url)
        self.assertEqual(list_res.status_code, status.HTTP_200_OK)
        results = list_res.data["data"]["results"]
        matching_card = next(p for p in results if p["id"] == self.product.id)
        self.assertEqual(matching_card["average_rating"], 5.0)
        self.assertEqual(matching_card["review_count"], 1)

    def test_product_list_ordering_by_rating(self):
        # Product 1 has rating 5.0
        Review.objects.create(
            product=self.product,
            user=self.buyer,
            rating=5,
            title="Great",
            description="Love it",
        )
        # Product 2 has rating 2.0
        Review.objects.create(
            product=self.product2,
            user=self.shopper,
            rating=2,
            title="Okay",
            description="Average",
        )

        url = reverse("public-product-list")
        res = self.client.get(url, {"ordering": "top_rated"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.data["data"]["results"]
        # Product 1 (5.0) must come before Product 2 (2.0)
        ids = [p["id"] for p in results]
        self.assertLess(ids.index(self.product.id), ids.index(self.product2.id))

    def test_list_reviews_pagination_and_breakdown(self):
        # Create reviews from multiple users
        names = [
            "Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot",
            "Golf", "Hotel", "India", "Juliet", "Kilo", "Lima",
        ]
        users = [
            User.objects.create_user(email=f"user{i}@test.com", name=f"User {names[i]}", password="StrongPass123!")
            for i in range(12)
        ]
        # 6 reviews with 5 stars, 4 with 4 stars, 2 with 3 stars
        for i, u in enumerate(users):
            star = 5 if i < 6 else (4 if i < 10 else 3)
            Review.objects.create(
                product=self.product,
                user=u,
                rating=star,
                title=f"Review #{i+1}",
                description="Helpful test feedback",
            )

        url = reverse("product-reviews-list-create", kwargs={"product_pk": self.product.id})
        res = self.client.get(url, {"page": 1, "page_size": 5})

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data["success"])
        data = res.data["data"]

        # Pagination fields
        self.assertEqual(data["count"], 12)
        self.assertEqual(data["total_pages"], 3)
        self.assertEqual(data["current_page"], 1)
        self.assertEqual(len(data["results"]), 5)
        self.assertIsNotNone(data["next"])

        # Rating summary & star breakdown
        summary = data["rating_summary"]
        self.assertEqual(summary["total_reviews"], 12)
        self.assertEqual(summary["rating_breakdown"]["5"], 6)
        self.assertEqual(summary["rating_breakdown"]["4"], 4)
        self.assertEqual(summary["rating_breakdown"]["3"], 2)
        self.assertEqual(summary["rating_breakdown"]["2"], 0)
        self.assertEqual(summary["rating_breakdown"]["1"], 0)

    def test_filter_reviews_by_rating_and_letter(self):
        Review.objects.create(
            product=self.product,
            user=self.buyer,
            rating=5,
            title="Awesome Cashmere Coat",
            description="Warm and stylish",
        )
        Review.objects.create(
            product=self.product,
            user=self.shopper,
            rating=3,
            title="Average Fit",
            description="Shoulders were tight",
        )
        Review.objects.create(
            product=self.product,
            user=self.other_user,
            rating=5,
            title="Best Coat Ever",
            description="Worth every rupee",
        )

        url = reverse("product-reviews-list-create", kwargs={"product_pk": self.product.id})

        # 1. Filter by rating = 5 -> 2 results
        res_r5 = self.client.get(url, {"rating": 5})
        self.assertEqual(len(res_r5.data["data"]["results"]), 2)

        # 2. Filter by letter = A -> titles starting with 'A'
        res_letter = self.client.get(url, {"letter": "a"})
        self.assertEqual(len(res_letter.data["data"]["results"]), 2)
        titles = {r["title"] for r in res_letter.data["data"]["results"]}
        self.assertEqual(titles, {"Awesome Cashmere Coat", "Average Fit"})

        # 3. Filter by rating = 5 AND letter = A -> 1 result
        res_both = self.client.get(url, {"rating": 5, "letter": "A"})
        self.assertEqual(len(res_both.data["data"]["results"]), 1)
        self.assertEqual(res_both.data["data"]["results"][0]["title"], "Awesome Cashmere Coat")

    def test_filter_reviews_verified_only(self):
        # Alice is verified buyer
        Review.objects.create(
            product=self.product,
            user=self.buyer,
            rating=5,
            title="Verified",
            description="Received in mail",
        )
        # Bob is unverified
        Review.objects.create(
            product=self.product,
            user=self.shopper,
            rating=4,
            title="Unverified",
            description="Saw in store",
        )

        url = reverse("product-reviews-list-create", kwargs={"product_pk": self.product.id})
        res = self.client.get(url, {"verified_only": "true"})

        self.assertEqual(len(res.data["data"]["results"]), 1)
        self.assertEqual(res.data["data"]["results"][0]["title"], "Verified")

    def test_update_and_delete_own_review(self):
        review = Review.objects.create(
            product=self.product,
            user=self.buyer,
            rating=3,
            title="Initial 3 star",
            description="Decent quality",
        )
        self.product.refresh_from_db()
        self.assertEqual(float(self.product.average_rating), 3.0)

        url = reverse("review-detail", kwargs={"review_pk": review.id})

        # Unauthorized cannot edit
        res_anon = self.client.patch(url, {"rating": 5})
        self.assertEqual(res_anon.status_code, status.HTTP_401_UNAUTHORIZED)

        # Other user cannot edit
        self.client.force_authenticate(user=self.shopper)
        res_other = self.client.patch(url, {"rating": 5})
        self.assertEqual(res_other.status_code, status.HTTP_403_FORBIDDEN)

        # Author can edit
        self.client.force_authenticate(user=self.buyer)
        res_author = self.client.patch(url, {
            "rating": 5,
            "title": "Updated: Actually 5 stars!",
            "description": "After wearing it for a week, it is incredible.",
        })
        self.assertEqual(res_author.status_code, status.HTTP_200_OK)
        self.assertEqual(res_author.data["data"]["rating"], 5)
        self.assertEqual(res_author.data["data"]["title"], "Updated: Actually 5 stars!")

        # Check product stats recomputed to 5.0
        self.product.refresh_from_db()
        self.assertEqual(float(self.product.average_rating), 5.0)

        # Delete review
        del_res = self.client.delete(url)
        self.assertEqual(del_res.status_code, status.HTTP_200_OK)

        # Check review is gone and product stats reset
        self.product.refresh_from_db()
        self.assertEqual(float(self.product.average_rating), 0.0)
        self.assertEqual(self.product.review_count, 0)

    def test_customer_my_reviews(self):
        Review.objects.create(
            product=self.product,
            user=self.buyer,
            rating=5,
            title="Blazer review",
            description="Warm blazer",
        )
        Review.objects.create(
            product=self.product2,
            user=self.buyer,
            rating=4,
            title="Linen shirt review",
            description="Cool shirt",
        )

        self.client.force_authenticate(user=self.buyer)
        url = reverse("customer-my-reviews")
        res = self.client.get(url)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.data["data"]["results"]
        self.assertEqual(len(results), 2)
        titles = {r["title"] for r in results}
        self.assertEqual(titles, {"Blazer review", "Linen shirt review"})
