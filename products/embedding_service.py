import hashlib
import logging
import os
import threading
from typing import List, Optional

from django.core.cache import cache
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_CACHE_TTL = 60 * 60 * 24

CATEGORY_SYNONYMS = {
    "ONE_PIECE": "dress, gown, jumpsuit, romper, kimono, robe, suit",
    "TOP": "shirt, t-shirt, tee, hoodie, sweatshirt, sweater, blouse, polo, tank top",
    "BOTTOM": "pants, trousers, jeans, denim, shorts, skirt, cargo, joggers",
    "FOOTWEAR": "shoes, sneakers, boots, heels, loafers, running shoes, sandals, slippers",
    "OUTERWEAR": "jacket, coat, blazer, cardigan, windbreaker, parka, fleece",
    "HEADWEAR": "hat, cap, beanie, beret, headband",
    "EYEWEAR": "sunglasses, shades, spectacles, glasses",
    "ACCESSORIES": "belt, bag, handbag, backpack, wallet, scarf, jewelry, watch",
}


class EmbeddingService:
    _instance: Optional["EmbeddingService"] = None
    _lock = threading.Lock()

    def __init__(self):
        logger.info(f"Loading embedding model: {EMBEDDING_MODEL_NAME}...")
        try:
            # First attempt standard loading
            self.model = SentenceTransformer(EMBEDDING_MODEL_NAME)
        except Exception as e:
            logger.warning(f"Standard loading of {EMBEDDING_MODEL_NAME} failed ({e}), trying local cache...")
            try:
                self.model = SentenceTransformer(EMBEDDING_MODEL_NAME, local_files_only=True)
            except Exception:
                # If neither worked, retry with HF_HUB_OFFLINE=1
                os.environ["HF_HUB_OFFLINE"] = "1"
                self.model = SentenceTransformer(EMBEDDING_MODEL_NAME)
        logger.info("Embedding model loaded successfully.")

    @classmethod
    def get_instance(cls) -> "EmbeddingService":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def embed_text(self, text: str) -> List[float]:
        clean_text = text.strip()
        if not clean_text:
            return []

        embedding = self.model.encode(clean_text, normalize_embeddings=True)
        return embedding.tolist()

    def embed_texts(self, texts: List[str], batch_size: int = 64) -> List[List[float]]:
        cleaned = [t.strip() for t in texts]
        valid_indices = [i for i, t in enumerate(cleaned) if t]
        if not valid_indices:
            return [[] for _ in texts]

        valid_texts = [cleaned[i] for i in valid_indices]
        embeddings = self.model.encode(
            valid_texts,
            batch_size=batch_size,
            show_progress_bar=False,
            normalize_embeddings=True,
        )

        results = [[] for _ in texts]
        for idx, emb in zip(valid_indices, embeddings):
            results[idx] = emb.tolist()
        return results

    def embed_query_cached(self, query: str) -> List[float]:
        clean_query = query.strip().lower()
        if not clean_query:
            return []

        query_hash = hashlib.sha256(clean_query.encode("utf-8")).hexdigest()
        cache_key = f"sem_search:query_vec:{query_hash}"

        cached_vec = cache.get(cache_key)
        if cached_vec is not None:
            return cached_vec

        vec = self.embed_text(clean_query)
        if vec:
            try:
                cache.set(cache_key, vec, timeout=EMBEDDING_CACHE_TTL)
            except Exception as e:
                logger.warning(f"Failed to cache query embedding in Redis: {e}")

        return vec

    def build_product_text(self, product) -> str:
        parts = [product.name]

        # Target demographic / category type (e.g. Men, Women, Kids, Ethnic, Formal, Sports)
        if hasattr(product, "product_type") and product.product_type:
            pt_display = (
                product.get_product_type_display()
                if hasattr(product, "get_product_type_display")
                else str(product.product_type)
            )
            parts.append(f"Style: {pt_display}")

        # Apparel category & synonyms
        if hasattr(product, "category") and product.category:
            cat_display = (
                product.get_category_display()
                if hasattr(product, "get_category_display")
                else str(product.category)
            )
            parts.append(f"Category: {cat_display}")
            cat_code = str(product.category)
            if cat_code in CATEGORY_SYNONYMS:
                parts.append(f"Tags: {CATEGORY_SYNONYMS[cat_code]}")

        # Primary color & variant colors
        colors = set()
        if hasattr(product, "color") and product.color:
            colors.add(product.color.strip())

        if hasattr(product, "_prefetched_objects_cache") and "variants" in product._prefetched_objects_cache:
            for v in product.variants.all():
                if v.color_name:
                    colors.add(v.color_name.strip())
        elif hasattr(product, "variants"):
            for v_color in product.variants.values_list("color_name", flat=True):
                if v_color:
                    colors.add(v_color.strip())

        if colors:
            parts.append(f"Colors: {', '.join(sorted(colors))}")

        if product.description:
            parts.append(product.description.strip())

        if hasattr(product, "store") and product.store and getattr(product.store, "name", None):
            parts.append(f"Store: {product.store.name}")

        return ". ".join(parts).strip()

    def embed_product(self, product) -> List[float]:
        text = self.build_product_text(product)
        return self.embed_text(text)
