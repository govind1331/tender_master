"""
Embedding utility
=================
Uses HuggingFace sentence-transformers locally — no API key required.
Model is cached after first load (singleton pattern).

Default: all-MiniLM-L6-v2  (384-dim, ~90MB, CPU-friendly)
Override via env: EMBEDDING_MODEL=all-mpnet-base-v2  (768-dim, higher quality)

If you change the model you MUST also update EMBEDDING_DIM in config
and re-run a full re-ingestion (vectors are not cross-compatible).
"""
from __future__ import annotations
from functools import lru_cache
from typing import Union
import numpy as np
import structlog
import ollama as _ollama

logger = structlog.get_logger()


@lru_cache(maxsize=1)
def _get_model(model_name: str):
    """Load and cache the sentence-transformer model (once per process)."""
    from sentence_transformers import SentenceTransformer
    logger.info("embedding.model_loading", model=model_name)
    model = SentenceTransformer(model_name)
    logger.info("embedding.model_ready", model=model_name)
    return model

@lru_cache(maxsize=1)
def get_ollama_client(host: str):
    return _ollama.Client(host=host)

def embed_text(text: str, model_name: str) -> list[float]:
    """Return a normalised embedding vector for a single text string."""
    model = _get_model(model_name)
    vec: np.ndarray = model.encode(text, normalize_embeddings=True)
    return vec.tolist()


def embed_batch(texts: list[str], model_name: str) -> list[list[float]]:
    """Embed a batch of texts (faster than one-by-one for ingestion)."""
    model = _get_model(model_name)
    vecs: np.ndarray = model.encode(texts, normalize_embeddings=True, batch_size=32)
    return vecs.tolist()
