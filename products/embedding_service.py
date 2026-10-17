import hashlib
import logging
import threading
from typing import List, Optional

from django.core.cache import cache
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_CACHE_TTL = 60 * 60 * 24


class EmbeddingService:
    _instance: Optional["EmbeddingService"] = None
    _lock = threading.Lock()

    def __init__(self):
        logger.info(f"Loading embedding model: {EMBEDDING_MODEL_NAME}...")
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
        if product.description:
            parts.append(product.description)
        if hasattr(product, "store") and product.store and getattr(product.store, "name", None):
            parts.append(f"Store: {product.store.name}")
        return ". ".join(parts).strip()

    def embed_product(self, product) -> List[float]:
        text = self.build_product_text(product)
        return self.embed_text(text)
