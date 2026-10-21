import logging
import math
from datetime import timedelta
from typing import List, Optional, Tuple

from django.core.cache import cache
from django.db import connection
from django.db.models import Case, Count, Q, When
from django.utils import timezone
from pgvector.django import CosineDistance

from accounts.models import WishlistItem
from cart.models import CartItem
from orders.models import OrderItem, OrderStatus
from stores.models import StoreStatus

from .embedding_service import EmbeddingService
from .models import (
    Product,
    ProductCategory,
    ProductEmbedding,
    ProductStatus,
    ProductView,
    SearchHistory,
    UserTasteProfile,
)

logger = logging.getLogger(__name__)

COLD_START_CACHE_TTL = 60 * 15  # 15 minutes
MAX_HISTORY_SIGNALS = 150       # Cap recent interactions to keep taste profile fresh
COLD_START_THRESHOLD = 3        # Min weighted interactions before personal vector activates


class RecommendationService:
    """
    Personalized recommendation engine combining multi-signal interaction tracking
    and pgvector cosine distance search in a unified 384-dimensional vector space.

    Signals & Weights:
      - Purchased products:       5.0
      - Cart items:               3.0
      - Wishlisted products:      2.0
      - Search queries:           1.5
      - Viewed products:          1.0
    """

    WEIGHT_PURCHASE = 5.0
    WEIGHT_CART = 3.0
    WEIGHT_WISHLIST = 2.0
    WEIGHT_SEARCH = 1.5
    WEIGHT_VIEW = 1.0

    @staticmethod
    def _normalize_vector(vec: List[float]) -> List[float]:
        norm = math.sqrt(sum(x * x for x in vec))
        if norm == 0:
            return vec
        return [x / norm for x in vec]

    @classmethod
    def compute_taste_vector(cls, user) -> Tuple[Optional[List[float]], int]:
        """
        Gathers multi-source user signals and computes the normalized 384-dim taste vector.
        Returns: (taste_vector or None, total_interaction_count)
        """
        if not user or not user.is_authenticated:
            return None, 0

        embedding_svc = EmbeddingService.get_instance()
        weighted_vectors: List[Tuple[List[float], float]] = []
        interaction_count = 0

        # 1. Purchases (weight 5.0)
        try:
            purchases = (
                OrderItem.objects.filter(
                    order__user=user,
                    product__isnull=False,
                )
                .exclude(order__status__in=[OrderStatus.CANCELLED, OrderStatus.RETURNED])
                .select_related("product__embedding_record")
                .order_by("-order__created_at")[:40]
            )
            for item in purchases:
                prod = item.product
                vec = None
                if hasattr(prod, "embedding_record") and prod.embedding_record:
                    vec = prod.embedding_record.embedding
                if not vec:
                    vec = embedding_svc.embed_product(prod)
                if vec:
                    weighted_vectors.append((vec, cls.WEIGHT_PURCHASE))
                    interaction_count += 1
        except Exception as e:
            logger.warning(f"Error fetching purchases for user {user.id}: {e}")

        # 2. Cart Items (weight 3.0)
        try:
            cart_items = (
                CartItem.objects.filter(cart__user=user)
                .select_related("product__embedding_record")
                .order_by("-updated_at")[:20]
            )
            for item in cart_items:
                prod = item.product
                vec = None
                if hasattr(prod, "embedding_record") and prod.embedding_record:
                    vec = prod.embedding_record.embedding
                if not vec:
                    vec = embedding_svc.embed_product(prod)
                if vec:
                    weighted_vectors.append((vec, cls.WEIGHT_CART))
                    interaction_count += 1
        except Exception as e:
            logger.warning(f"Error fetching cart items for user {user.id}: {e}")

        # 3. Wishlist Items (weight 2.0)
        try:
            wishlist_items = (
                WishlistItem.objects.filter(user=user)
                .select_related("product__embedding_record")
                .order_by("-created_at")[:30]
            )
            for item in wishlist_items:
                prod = item.product
                vec = None
                if hasattr(prod, "embedding_record") and prod.embedding_record:
                    vec = prod.embedding_record.embedding
                if not vec:
                    vec = embedding_svc.embed_product(prod)
                if vec:
                    weighted_vectors.append((vec, cls.WEIGHT_WISHLIST))
                    interaction_count += 1
        except Exception as e:
            logger.warning(f"Error fetching wishlist items for user {user.id}: {e}")

        # 4. Search History (weight 1.5)
        try:
            searches = (
                SearchHistory.objects.filter(user=user)
                .order_by("-last_searched_at")[:20]
            )
            for s in searches:
                vec = s.embedding
                if not vec:
                    vec = embedding_svc.embed_query_cached(s.query)
                    if vec:
                        s.embedding = vec
                        s.save(update_fields=["embedding"])
                if vec:
                    # Scale slightly by repeat searches, capped at 2.5
                    effective_weight = min(cls.WEIGHT_SEARCH + (s.search_count - 1) * 0.2, 2.5)
                    weighted_vectors.append((vec, effective_weight))
                    interaction_count += 1
        except Exception as e:
            logger.warning(f"Error fetching search history for user {user.id}: {e}")

        # 5. Product Views (weight 1.0)
        try:
            views = (
                ProductView.objects.filter(user=user)
                .select_related("product__embedding_record")
                .order_by("-last_viewed_at")[:40]
            )
            for v in views:
                prod = v.product
                vec = None
                if hasattr(prod, "embedding_record") and prod.embedding_record:
                    vec = prod.embedding_record.embedding
                if not vec:
                    vec = embedding_svc.embed_product(prod)
                if vec:
                    effective_weight = min(cls.WEIGHT_VIEW + (v.view_count - 1) * 0.1, 1.8)
                    weighted_vectors.append((vec, effective_weight))
                    interaction_count += 1
        except Exception as e:
            logger.warning(f"Error fetching product views for user {user.id}: {e}")

        if not weighted_vectors:
            return None, 0

        # Compute weighted sum
        dim = len(weighted_vectors[0][0])
        sum_vec = [0.0] * dim
        total_weight = 0.0

        for vec, weight in weighted_vectors:
            for i in range(dim):
                sum_vec[i] += vec[i] * weight
            total_weight += weight

        if total_weight == 0:
            return None, 0

        avg_vec = [val / total_weight for val in sum_vec]
        normalized_vec = cls._normalize_vector(avg_vec)
        return normalized_vec, interaction_count

    @classmethod
    def update_user_taste_profile(cls, user) -> Optional[UserTasteProfile]:
        """Recomputes and saves UserTasteProfile for the user."""
        vector, count = cls.compute_taste_vector(user)
        if not vector:
            return None

        profile, _ = UserTasteProfile.objects.update_or_create(
            user=user,
            defaults={
                "embedding": vector,
                "interaction_count": count,
            },
        )
        return profile

    @classmethod
    def get_cold_start_recommendations(cls, limit: int = 20, category: Optional[str] = None):
        """
        Returns trending / popular active products.
        Cached in Redis to guarantee ultra-fast response for new users or anonymous visitors.
        """
        cache_key = f"rec:cold_start:{category or 'all'}:{limit}"
        cached_ids = cache.get(cache_key)
        if cached_ids:
            return (
                Product.objects.filter(id__in=cached_ids)
                .select_related("store")
                .prefetch_related("images", "sizes")
            )

        thirty_days_ago = timezone.now() - timedelta(days=30)
        qs = (
            Product.objects.filter(
                status=ProductStatus.ACTIVE,
                store__status=StoreStatus.ACTIVE,
                store__archived_at__isnull=True,
                stock__gt=0,
            )
            .annotate(
                recent_sales=Count(
                    "order_items",
                    filter=Q(order_items__order__created_at__gte=thirty_days_ago),
                )
            )
            .order_by("-recent_sales", "-created_at")
        )

        if category:
            cat_upper = category.strip().upper()
            if cat_upper in ProductCategory.values:
                qs = qs.filter(category=cat_upper)

        product_ids = list(qs.values_list("id", flat=True)[:limit])
        if product_ids:
            cache.set(cache_key, product_ids, timeout=COLD_START_CACHE_TTL)

        return (
            Product.objects.filter(id__in=product_ids)
            .select_related("store")
            .prefetch_related("images", "sizes")
        )

    @classmethod
    def get_recommendations(
        cls,
        user=None,
        limit: int = 20,
        exclude_cart: bool = True,
        category: Optional[str] = None,
    ):
        """
        Returns personalized product recommendations for the user using pgvector kNN.
        Falls back to trending cold-start products when user interaction count is below threshold.
        """
        if not user or not user.is_authenticated:
            return cls.get_cold_start_recommendations(limit=limit, category=category)

        # Retrieve or compute profile
        profile = getattr(user, "taste_profile", None)
        if not profile:
            profile = cls.update_user_taste_profile(user)

        if not profile or profile.interaction_count < COLD_START_THRESHOLD:
            return cls.get_cold_start_recommendations(limit=limit, category=category)

        # Base active product queryset
        qs = (
            Product.objects.filter(
                status=ProductStatus.ACTIVE,
                store__status=StoreStatus.ACTIVE,
                store__archived_at__isnull=True,
                stock__gt=0,
                embedding_record__isnull=False,
            )
            .select_related("store")
            .prefetch_related("images", "sizes")
        )

        if category:
            cat_upper = category.strip().upper()
            if cat_upper in ProductCategory.values:
                qs = qs.filter(category=cat_upper)

        # Exclude already purchased products
        purchased_ids = OrderItem.objects.filter(
            order__user=user,
            product__isnull=False,
        ).exclude(order__status__in=[OrderStatus.CANCELLED, OrderStatus.RETURNED]).values_list("product_id", flat=True)

        if purchased_ids:
            qs = qs.exclude(id__in=purchased_ids)

        # Exclude cart items if requested
        if exclude_cart:
            cart_ids = CartItem.objects.filter(
                cart__user=user,
                product__isnull=False,
            ).values_list("product_id", flat=True)
            if cart_ids:
                qs = qs.exclude(id__in=cart_ids)

        # pgvector kNN query using CosineDistance if on PostgreSQL; Python cosine sort on SQLite (tests)
        supports_pgvector = (connection.vendor == "postgresql")
        if supports_pgvector:
            qs = qs.annotate(
                distance=CosineDistance("embedding_record__embedding", profile.embedding)
            ).order_by("distance", "-created_at")
            return qs[:limit]
        else:
            products = list(qs)
            profile_emb = list(profile.embedding)
            def calc_dist(p):
                if hasattr(p, "embedding_record") and p.embedding_record and p.embedding_record.embedding:
                    p_emb = list(p.embedding_record.embedding)
                    dot = sum(a * b for a, b in zip(profile_emb, p_emb))
                    return 1.0 - dot
                return 1.0
            products.sort(key=calc_dist)
            p_ids = [p.id for p in products[:limit]]
            if not p_ids:
                return Product.objects.none()
            whens = [When(id=pid, then=pos) for pos, pid in enumerate(p_ids)]
            return Product.objects.filter(id__in=p_ids).select_related("store").prefetch_related("images", "sizes").order_by(Case(*whens))
