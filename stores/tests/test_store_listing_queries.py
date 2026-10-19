from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from stores.models import (
    BusinessVerification,
    Store,
    StoreMembership,
    StoreRole,
    StoreStatus,
    VerificationStatus,
)

User = get_user_model()


class StoreListingOptimizationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.seller = User.objects.create_user(
            email="seller@example.com",
            name="Primary Seller",
            password="StrongPassword123!",
            is_seller=True,
            is_email_verified=True,
        )
        self.other_seller = User.objects.create_user(
            email="manager@example.com",
            name="Manager Seller",
            password="StrongPassword123!",
            is_seller=True,
            is_email_verified=True,
        )
        self.client.force_authenticate(user=self.seller)

    def test_store_listing_member_count_and_role_accuracy(self):
        """Verifies that annotated member_count and prefetched my_role match reality."""
        # Store 1: Owner, 2 members
        store1 = Store.objects.create(
            name="Store Alpha",
            slug="store-alpha",
            status=StoreStatus.ACTIVE,
            created_by=self.seller,
        )
        StoreMembership.objects.create(store=store1, user=self.seller, role=StoreRole.OWNER)
        StoreMembership.objects.create(store=store1, user=self.other_seller, role=StoreRole.MANAGER)
        BusinessVerification.objects.create(
            store=store1,
            pan_number="ABCDE1234F",
            gst_number="22AAAAA0000A1Z5",
            status=VerificationStatus.APPROVED,
        )

        # Store 2: Manager, 1 member
        store2 = Store.objects.create(
            name="Store Beta",
            slug="store-beta",
            status=StoreStatus.ACTIVE,
            created_by=self.other_seller,
        )
        StoreMembership.objects.create(store=store2, user=self.other_seller, role=StoreRole.OWNER)
        StoreMembership.objects.create(store=store2, user=self.seller, role=StoreRole.MANAGER)

        response = self.client.get("/stores/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["data"]["results"]
        self.assertEqual(len(results), 2)

        data_map = {item["id"]: item for item in results}

        # Store 1 checks
        s1_data = data_map[store1.id]
        self.assertEqual(s1_data["member_count"], 2)
        self.assertEqual(s1_data["my_role"], StoreRole.OWNER)
        self.assertTrue(s1_data["is_business_verified"])

        # Store 2 checks
        s2_data = data_map[store2.id]
        self.assertEqual(s2_data["member_count"], 2)
        self.assertEqual(s2_data["my_role"], StoreRole.MANAGER)
        self.assertFalse(s2_data["is_business_verified"])

    def test_store_listing_avoids_n_plus_one_queries(self):
        """Listing N stores must not generate 2N+1 queries; query count must remain constant."""
        # Create 5 stores for this user
        for i in range(5):
            store = Store.objects.create(
                name=f"Batch Store {i}",
                slug=f"batch-store-{i}",
                status=StoreStatus.ACTIVE,
                created_by=self.seller,
            )
            StoreMembership.objects.create(store=store, user=self.seller, role=StoreRole.OWNER)
            StoreMembership.objects.create(store=store, user=self.other_seller, role=StoreRole.MANAGER)
            BusinessVerification.objects.create(
                store=store,
                pan_number=f"ABCDE123{i}F",
                gst_number=f"22AAAAA000{i}A1Z5",
                status=VerificationStatus.APPROVED if i % 2 == 0 else VerificationStatus.PENDING,
            )

        # In previous un-optimized implementation, 5 stores took:
        # 1 (user/auth) + 1 (count) + 1 (stores) + 5*(my_role) + 5*(member_count) + 5*(business_verification) = 18 queries.
        # With member_count annotated, business_verification select_related, and memberships prefetched:
        # Total queries is exactly 3 regardless of store count:
        # 1. Paginator count query
        # 2. Main store query (with annotated member_count and select_related business_verification)
        # 3. Batch prefetch of memberships
        with self.assertNumQueries(3):
            response = self.client.get("/stores/")
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(len(response.data["data"]["results"]), 5)
