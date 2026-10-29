import logging
import math
from datetime import timedelta
from typing import List, Optional, Tuple

from django.core.cache import cache
from django.db import connection
from django.db.models import Case, Count, Prefetch, Q, When
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
    ProductStatus,
    ProductVariant,
    ProductView,
    SearchHistory,
    UserTasteProfile,
    normalize_category_param,
)

logger = logging.getLogger(__name__)

COLD_START_CACHE_TTL = 60 * 15  # 15 minutes
MAX_HISTORY_SIGNALS = 150       # Cap recent interactions to keep taste profile fresh
COLD_START_THRESHOLD = 3        # Min weighted interactions before personal vector activates
PROFILE_REFRESH_STALENESS = timedelta(hours=6)


class RecommendationService:
    """
    Personalized recommendation engine combining multi-signal interaction tracking
    and pgvector cosine distance search in a unified 384-dimensional vector space.

    Signals & Weights:
      - Purchased products:       5.0
      - Virtual try-ons:          3.5
      - Cart items:               3.0
      - Outfit combo items:       2.5
      - Wishlisted products:      2.0
      - Search queries:           1.5
      - Viewed products:          1.0
    """

    WEIGHT_PURCHASE = 5.0
    WEIGHT_TRYON = 3.5
    WEIGHT_CART = 3.0
    WEIGHT_COMBO = 2.5
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

        # 1. Purchases (weight 5.0) - completed or active orders
        try:
            purchases = (
                OrderItem.objects.filter(
                    order__user=user,
                    product__isnull=False,
                )
                .exclude(order__status__in=[OrderStatus.CANCELLED, OrderStatus.FAILED, OrderStatus.REFUNDED])
                .select_related("product__embedding_record", "product__store")
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

        # 2. Virtual Try-On Requests (weight 3.5) - direct engagement signal
        try:
            from tryon.models import TryOnRequest
            tryon_requests = (
                TryOnRequest.objects.filter(
                    user=user,
                    product__isnull=False,
                )
                .select_related("product__embedding_record", "product__store")
                .order_by("-created_at")[:20]
            )
            for req in tryon_requests:
                prod = req.product
                vec = None
                if hasattr(prod, "embedding_record") and prod.embedding_record:
                    vec = prod.embedding_record.embedding
                if not vec:
                    vec = embedding_svc.embed_product(prod)
                if vec:
                    weighted_vectors.append((vec, cls.WEIGHT_TRYON))
                    interaction_count += 1
        except Exception as e:
            logger.warning(f"Error fetching try-on requests for user {user.id}: {e}")

        # 3. Cart Items (weight 3.0)
        try:
            cart_items = (
                CartItem.objects.filter(cart__user=user, product__isnull=False)
                .select_related("product__embedding_record", "product__store")
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

        # 4. Combo Items (weight 2.5) - outfit curation
        try:
            from tryon.models import ComboItem
            combo_items = (
                ComboItem.objects.filter(combo__user=user, product__isnull=False)
                .select_related("product__embedding_record", "product__store")
                .order_by("-created_at")[:20]
            )
            for item in combo_items:
                prod = item.product
                vec = None
                if hasattr(prod, "embedding_record") and prod.embedding_record:
                    vec = prod.embedding_record.embedding
                if not vec:
                    vec = embedding_svc.embed_product(prod)
                if vec:
                    weighted_vectors.append((vec, cls.WEIGHT_COMBO))
                    interaction_count += 1
        except Exception as e:
            logger.warning(f"Error fetching combo items for user {user.id}: {e}")

        # 5. Wishlist Items (weight 2.0)
        try:
            wishlist_items = (
                WishlistItem.objects.filter(user=user, product__isnull=False)
                .select_related("product__embedding_record", "product__store")
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

        # 6. Search History (weight 1.5)
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

        # 7. Product Views (weight 1.0)
        try:
            views = (
                ProductView.objects.filter(user=user, product__isnull=False)
                .select_related("product__embedding_record", "product__store")
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
        normalized_cat = normalize_category_param(category) if category else None
        cache_key = f"rec:cold_start:{normalized_cat or 'all'}:{limit}"
        cached_ids = cache.get(cache_key)

        prefetch_variants = Prefetch(
            "variants",
            queryset=ProductVariant.objects.filter(is_active=True).prefetch_related("images", "sizes"),
        )

        if cached_ids:
            whens = [When(id=pk, then=pos) for pos, pk in enumerate(cached_ids)]
            return (
                Product.objects.filter(
                    id__in=cached_ids,
                    status=ProductStatus.ACTIVE,
                    store__status=StoreStatus.ACTIVE,
                    store__archived_at__isnull=True,
                    stock__gt=0,
                )
                .select_related("store")
                .prefetch_related("images", "sizes", prefetch_variants)
                .order_by(Case(*whens))
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
                    filter=Q(
                        order_items__order__created_at__gte=thirty_days_ago,
                    )
                    & ~Q(
                        order_items__order__status__in=[
                            OrderStatus.CANCELLED,
                            OrderStatus.FAILED,
                            OrderStatus.REFUNDED,
                        ]
                    ),
                )
            )
            .order_by("-recent_sales", "-created_at")
        )

        if normalized_cat:
            qs = qs.filter(category=normalized_cat)

        product_ids = list(qs.values_list("id", flat=True)[:limit])
        if product_ids:
            cache.set(cache_key, product_ids, timeout=COLD_START_CACHE_TTL)
        else:
            return Product.objects.none()

        whens = [When(id=pk, then=pos) for pos, pk in enumerate(product_ids)]
        return (
            Product.objects.filter(id__in=product_ids)
            .select_related("store")
            .prefetch_related("images", "sizes", prefetch_variants)
            .order_by(Case(*whens))
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

        normalized_cat = normalize_category_param(category) if category else None

        # Retrieve or compute profile; recompute if cold-start threshold not met or profile is stale
        profile = getattr(user, "taste_profile", None)
        should_recompute = (
            profile is None
            or profile.interaction_count < COLD_START_THRESHOLD
            or (profile.updated_at and profile.updated_at < timezone.now() - PROFILE_REFRESH_STALENESS)
        )
        if should_recompute:
            profile = cls.update_user_taste_profile(user)

        if not profile or profile.interaction_count < COLD_START_THRESHOLD:
            return cls.get_cold_start_recommendations(limit=limit, category=category)

        prefetch_variants = Prefetch(
            "variants",
            queryset=ProductVariant.objects.filter(is_active=True).prefetch_related("images", "sizes"),
        )

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
            .prefetch_related("images", "sizes", prefetch_variants)
        )

        if normalized_cat:
            qs = qs.filter(category=normalized_cat)

        # Exclude completed/valid purchased products
        purchased_ids = (
            OrderItem.objects.filter(
                order__user=user,
                product__isnull=False,
            )
            .exclude(order__status__in=[OrderStatus.CANCELLED, OrderStatus.FAILED, OrderStatus.REFUNDED])
            .values_list("product_id", flat=True)
        )

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
            return (
                Product.objects.filter(id__in=p_ids)
                .select_related("store")
                .prefetch_related("images", "sizes", prefetch_variants)
                .order_by(Case(*whens))
            )

    @classmethod
    def get_similar_products(cls, product, limit: int = 10, same_category: bool = False):
        """
        Finds products semantically and visually similar to the target product.
        Used on Product Detail pages to power 'Similar Styles' carousels.
        """
        if not product:
            return Product.objects.none()

        embedding_svc = EmbeddingService.get_instance()
        target_vec = None
        if hasattr(product, "embedding_record") and product.embedding_record:
            target_vec = product.embedding_record.embedding
        if not target_vec:
            target_vec = embedding_svc.embed_product(product)

        if not target_vec:
            return Product.objects.none()

        prefetch_variants = Prefetch(
            "variants",
            queryset=ProductVariant.objects.filter(is_active=True).prefetch_related("images", "sizes"),
        )

        qs = (
            Product.objects.filter(
                status=ProductStatus.ACTIVE,
                store__status=StoreStatus.ACTIVE,
                store__archived_at__isnull=True,
                stock__gt=0,
                embedding_record__isnull=False,
            )
            .exclude(pk=product.pk)
            .select_related("store")
            .prefetch_related("images", "sizes", prefetch_variants)
        )

        if same_category and product.category:
            qs = qs.filter(category=product.category)

        supports_pgvector = (connection.vendor == "postgresql")
        if supports_pgvector:
            qs = qs.annotate(
                distance=CosineDistance("embedding_record__embedding", target_vec)
            ).order_by("distance", "-created_at")
            return qs[:limit]
        else:
            products = list(qs)
            t_emb = list(target_vec)

            def calc_dist(p):
                if hasattr(p, "embedding_record") and p.embedding_record and p.embedding_record.embedding:
                    p_emb = list(p.embedding_record.embedding)
                    dot = sum(a * b for a, b in zip(t_emb, p_emb))
                    return 1.0 - dot
                return 1.0

            products.sort(key=calc_dist)
            p_ids = [p.id for p in products[:limit]]
            if not p_ids:
                return Product.objects.none()
            whens = [When(id=pid, then=pos) for pos, pid in enumerate(p_ids)]
            return (
                Product.objects.filter(id__in=p_ids)
                .select_related("store")
                .prefetch_related("images", "sizes", prefetch_variants)
                .order_by(Case(*whens))
            )

    @classmethod
    def get_recommended_combos(cls, product) -> List[dict]:
        """
        Generates 3 curated outfit combinations ('Complete the Look') for a given product:
          - Combo 1 (Regular): Classic base outfit pairing (e.g. Top + Bottom, no outerwear).
          - Combo 2 (Layered): Layered look with outerwear (e.g. Top + Bottom + Outerwear, or One-Piece + Outerwear).
          - Combo 3 (Dynamic): Evaluates alternative regular vs layered candidate looks based on style affinity
                               score and dynamically selects the higher-scoring look (not fixed).

        Strictly enforces category constraints via ComboValidationService:
          - Max 1 item per category.
          - Max 3 items per combo.
          - ONE_PIECE and TOP/BOTTOM are mutually exclusive! (One-Piece pairs only with Outerwear).
        """
        if not product:
            return []

        from decimal import Decimal
        from tryon.models import TRYON_SUPPORTED_CATEGORIES, ComboItem
        from tryon.services import ComboValidationService
        from .models import ProductCategory, ProductType

        embedding_svc = EmbeddingService.get_instance()
        target_vec = None
        if hasattr(product, "embedding_record") and product.embedding_record:
            target_vec = product.embedding_record.embedding
        if not target_vec:
            target_vec = embedding_svc.embed_product(product)

        if not target_vec:
            return []

        target_cat = product.category
        t_vec = list(target_vec)

        prefetch_variants = Prefetch(
            "variants",
            queryset=ProductVariant.objects.filter(is_active=True).prefetch_related("images", "sizes"),
        )

        # Retrieve co-occurring product IDs from existing Combos and Orders to add social proof boost
        co_combo_ids = set()
        try:
            co_combo_ids = set(
                ComboItem.objects.filter(
                    combo__items__product=product
                ).exclude(product=product).values_list("product_id", flat=True)
            )
        except Exception:
            pass

        co_order_ids = set()
        try:
            co_order_ids = set(
                OrderItem.objects.filter(
                    order__items__product=product
                ).exclude(product=product).values_list("product_id", flat=True)
            )
        except Exception:
            pass

        def get_candidates(category: str, limit: int = 8) -> List[Tuple[Product, float]]:
            """Fetches active, in-stock products in the specified category, ranked by style similarity and affinity boosts."""
            qs = (
                Product.objects.filter(
                    category=category,
                    status=ProductStatus.ACTIVE,
                    store__status=StoreStatus.ACTIVE,
                    store__archived_at__isnull=True,
                    stock__gt=0,
                    embedding_record__isnull=False,
                )
                .exclude(pk=product.pk)
                .select_related("store", "embedding_record")
                .prefetch_related("images", "sizes", prefetch_variants)
            )

            scored = []
            for cand in qs:
                emb = cand.embedding_record.embedding if cand.embedding_record else None
                if not emb:
                    continue
                c_vec = list(emb)
                # Cosine dot product (since embeddings are L2 normalized, dot product = cosine similarity)
                dot = sum(a * b for a, b in zip(t_vec, c_vec))
                sim = max(0.0, min(1.0, dot))

                # Demographic / Gender alignment boost
                demographic_boost = 0.0
                if product.product_type and cand.product_type:
                    if product.product_type == cand.product_type:
                        demographic_boost = 0.05
                    elif cand.product_type == ProductType.UNISEX or product.product_type == ProductType.UNISEX:
                        demographic_boost = 0.03
                    elif {product.product_type, cand.product_type} == {ProductType.MEN, ProductType.WOMEN}:
                        demographic_boost = -0.25

                # Co-occurrence boost (social proof / outfit pairing history)
                co_boost = 0.0
                if cand.id in co_combo_ids:
                    co_boost += 0.15
                if cand.id in co_order_ids:
                    co_boost += 0.10

                final_item_score = max(0.0, min(1.0, sim + demographic_boost + co_boost))
                scored.append((cand, final_item_score))

            scored.sort(key=lambda x: x[1], reverse=True)
            return scored[:limit]

        def calc_similarity(p1, p2) -> float:
            """Calculates pairwise cosine similarity between two products."""
            e1 = p1.embedding_record.embedding if hasattr(p1, "embedding_record") and p1.embedding_record else None
            e2 = p2.embedding_record.embedding if hasattr(p2, "embedding_record") and p2.embedding_record else None
            if e1 and e2:
                dot = sum(a * b for a, b in zip(e1, e2))
                return max(0.0, min(1.0, dot))
            return 0.75

        def build_combo(combo_id: str, title: str, look_type: str, is_layered: bool, combo_products: List[Product], score: float) -> Optional[dict]:
            """Validates constraints via ComboValidationService and formats combo payload."""
            if not combo_products:
                return None

            cats = [p.category for p in combo_products]
            try:
                ComboValidationService.validate_category_composition(cats)
            except Exception as e:
                logger.warning(f"Combo {combo_id} failed category validation: {e}")
                return None

            is_tryon_ready = all(c in TRYON_SUPPORTED_CATEGORIES for c in cats)
            total_price = sum((p.price for p in combo_products), Decimal("0.00"))
            clamped_score = round(max(0.50, min(0.99, score)), 2)

            items_data = []
            for p in combo_products:
                items_data.append({
                    "role": "CURRENT_PRODUCT" if p.id == product.id else "COMPLEMENTARY",
                    "category": p.category,
                    "category_display": p.get_category_display() if hasattr(p, "get_category_display") else p.category,
                    "product": p,
                })

            return {
                "id": combo_id,
                "title": title,
                "look_type": look_type,
                "is_layered": is_layered,
                "score": clamped_score,
                "total_price": total_price,
                "currency": getattr(product, "currency", "INR") or "INR",
                "is_tryon_ready": is_tryon_ready,
                "product_ids": [p.id for p in combo_products],
                "items": items_data,
            }

        combos: List[dict] = []

        if target_cat == ProductCategory.ONE_PIECE:
            # Special Case: ONE_PIECE cannot combine with TOP or BOTTOM!
            # Only OUTERWEAR can be paired.
            outers = get_candidates(ProductCategory.OUTERWEAR, limit=6)

            # Combo 1 (Regular): Clean / Minimalist pairing (or solo if no outerwear exists)
            if outers:
                o1, s1 = outers[0]
                c1 = build_combo("combo-1", "Clean Minimalist Look", "REGULAR", False, [product, o1], s1)
                if c1:
                    combos.append(c1)
            else:
                c1 = build_combo("combo-1", "Solo Statement", "REGULAR", False, [product], 0.90)
                if c1:
                    combos.append(c1)

            # Combo 2 (Layered): Statement Outerwear styling
            if len(outers) > 1:
                o2, s2 = outers[1]
                c2 = build_combo("combo-2", "Layered Statement Look", "LAYERED", True, [product, o2], s2)
                if c2:
                    combos.append(c2)
            elif outers:
                o1, s1 = outers[0]
                c2 = build_combo("combo-2", "Layered Streetwear Accent", "LAYERED", True, [product, o1], s1)
                if c2:
                    combos.append(c2)

            # Combo 3 (Dynamic): Alternative styling if candidates available
            if len(outers) > 2:
                o3, s3 = outers[2]
                c3 = build_combo("combo-3", "Editorial Contrast Look", "LAYERED" if s3 >= 0.75 else "REGULAR", s3 >= 0.75, [product, o3], s3)
                if c3:
                    combos.append(c3)
            elif len(outers) > 1:
                o_alt, s_alt = outers[1]
                c3 = build_combo("combo-3", "Editorial Contrast Look", "LAYERED", True, [product, o_alt], s_alt)
                if c3:
                    combos.append(c3)

        elif target_cat == ProductCategory.OUTERWEAR:
            # Target is OUTERWEAR: Can layer over separates (TOP + BOTTOM) or over ONE_PIECE
            tops = get_candidates(ProductCategory.TOP, limit=6)
            bottoms = get_candidates(ProductCategory.BOTTOM, limit=6)
            one_pieces = get_candidates(ProductCategory.ONE_PIECE, limit=6)

            # Combo 1 (Regular): Classic Separates Duo under outerwear
            if tops and bottoms:
                t1, st1 = tops[0]
                b1, sb1 = bottoms[0]
                score1 = (st1 + sb1 + calc_similarity(t1, b1)) / 3.0
                c1 = build_combo("combo-1", "Tailored Separates Look", "REGULAR", False, [product, t1, b1], score1)
                if c1:
                    combos.append(c1)

            # Combo 2 (Layered): Layered over a One-Piece dress/jumpsuit (or alternate separates if no one-pieces)
            if one_pieces:
                op1, sop1 = one_pieces[0]
                c2 = build_combo("combo-2", "One-Piece Silhouette Look", "LAYERED", True, [product, op1], sop1)
                if c2:
                    combos.append(c2)
            elif len(tops) > 1 and len(bottoms) > 1:
                t2, st2 = tops[1]
                b2, sb2 = bottoms[1]
                score2 = (st2 + sb2 + calc_similarity(t2, b2)) / 3.0
                c2 = build_combo("combo-2", "Statement Layered Look", "LAYERED", True, [product, t2, b2], score2)
                if c2:
                    combos.append(c2)

            # Combo 3 (Dynamic): Compare Candidate Separates vs Candidate One-Piece
            cand_sep = None
            if len(tops) > 1 and len(bottoms) > 1:
                t_alt, st_alt = tops[1]
                b_alt, sb_alt = bottoms[1]
                score_sep = (st_alt + sb_alt + calc_similarity(t_alt, b_alt)) / 3.0
                cand_sep = ("REGULAR", False, [product, t_alt, b_alt], score_sep, "Urban Separates Styling")

            cand_op = None
            if len(one_pieces) > 1:
                op_alt, sop_alt = one_pieces[1]
                cand_op = ("LAYERED", True, [product, op_alt], sop_alt, "Modern One-Piece Contrast")

            if cand_sep and cand_op:
                winner = cand_op if cand_op[3] >= cand_sep[3] else cand_sep
                c3 = build_combo("combo-3", winner[4], winner[0], winner[1], winner[2], winner[3])
                if c3:
                    combos.append(c3)
            elif cand_sep:
                c3 = build_combo("combo-3", cand_sep[4], cand_sep[0], cand_sep[1], cand_sep[2], cand_sep[3])
                if c3:
                    combos.append(c3)
            elif cand_op:
                c3 = build_combo("combo-3", cand_op[4], cand_op[0], cand_op[1], cand_op[2], cand_op[3])
                if c3:
                    combos.append(c3)

        else:
            # Target is TOP, BOTTOM, or other category (FOOTWEAR, etc.)
            partner_cat = ProductCategory.BOTTOM if target_cat == ProductCategory.TOP else ProductCategory.TOP
            partners = get_candidates(partner_cat, limit=6)
            outers = get_candidates(ProductCategory.OUTERWEAR, limit=6)

            # Combo 1 (Regular): At least one base outfit without outerwear (2 items)
            if partners:
                p1, sp1 = partners[0]
                c1 = build_combo("combo-1", "Clean Everyday Look", "REGULAR", False, [product, p1], sp1)
                if c1:
                    combos.append(c1)

            # Combo 2 (Layered): At least one layered look with outerwear (3 items)
            if partners and outers:
                p_cand = partners[0][0]
                o1, so1 = outers[0]
                score_lay = (calc_similarity(product, p_cand) + calc_similarity(product, o1) + calc_similarity(p_cand, o1)) / 3.0
                c2 = build_combo("combo-2", "Statement Layered Look", "LAYERED", True, [product, p_cand, o1], score_lay)
                if c2:
                    combos.append(c2)
            elif len(partners) > 1:
                # Fallback if no outerwear in catalog
                p2, sp2 = partners[1]
                c2 = build_combo("combo-2", "Alternative Everyday Look", "REGULAR", False, [product, p2], sp2)
                if c2:
                    combos.append(c2)

            # Combo 3 (Dynamic Choice): Evaluate candidate alternative Regular vs candidate alternative Layered
            cand_regular = None
            if len(partners) > 1:
                p_alt, sp_alt = partners[1]
                cand_regular = ("REGULAR", False, [product, p_alt], sp_alt, "Modern Minimalist Look")

            cand_layered = None
            if partners and len(outers) > 1:
                p_for_lay = partners[1][0] if len(partners) > 1 else partners[0][0]
                o2, so2 = outers[1]
                score_lay2 = (calc_similarity(product, p_for_lay) + calc_similarity(product, o2) + calc_similarity(p_for_lay, o2)) / 3.0
                cand_layered = ("LAYERED", True, [product, p_for_lay, o2], score_lay2, "Urban Layered Look")

            # Dynamic selection based on style affinity score
            if cand_regular and cand_layered:
                # Whichever one has a higher score is picked dynamically!
                winner = cand_layered if cand_layered[3] >= cand_regular[3] else cand_regular
                c3 = build_combo("combo-3", winner[4], winner[0], winner[1], winner[2], winner[3])
                if c3:
                    combos.append(c3)
            elif cand_layered:
                c3 = build_combo("combo-3", cand_layered[4], cand_layered[0], cand_layered[1], cand_layered[2], cand_layered[3])
                if c3:
                    combos.append(c3)
            elif cand_regular:
                c3 = build_combo("combo-3", cand_regular[4], cand_regular[0], cand_regular[1], cand_regular[2], cand_regular[3])
                if c3:
                    combos.append(c3)

        return combos

