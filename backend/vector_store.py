"""Vector store for hierarchical retrieval.

Two coordinated stores:

  1. **ChromaDB child index** — embeddings of the small child chunks. This is
     what vector search runs against. Each child's metadata carries the
     `parent_id` it belongs to plus a `doc_id` for per-document filtering.

  2. **Parent doc-store** — a persistent JSON map of `parent_id -> full parent
     section`. When a child matches, we look up and return the *whole* parent so
     the LLM never sees a truncated clause.

The `VectorStore` owns the (expensive) embedding model and both stores, and is
instantiated exactly once via the FastAPI lifespan handler.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

from . import config
from .ingestion import ChildChunk, ParentChunk


class VectorStore:
    def __init__(self) -> None:
        self._embedder = SentenceTransformer(config.EMBEDDING_MODEL_NAME)

        self._client = chromadb.PersistentClient(
            path=config.CHROMA_PERSIST_DIR,
            settings=Settings(anonymized_telemetry=False, allow_reset=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=config.CHROMA_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

        # Parent doc-store (persisted JSON), guarded for concurrent writes.
        self._lock = threading.Lock()
        self._parents: dict[str, dict[str, Any]] = self._load_parents()

    # ------------------------------------------------------------------ #
    # Parent doc-store persistence
    # ------------------------------------------------------------------ #
    def _load_parents(self) -> dict[str, dict[str, Any]]:
        path = config.PARENT_STORE_PATH
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    return json.load(fh)
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_parents(self) -> None:
        os.makedirs(os.path.dirname(config.PARENT_STORE_PATH), exist_ok=True)
        tmp = config.PARENT_STORE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._parents, fh, ensure_ascii=False)
        os.replace(tmp, config.PARENT_STORE_PATH)  # atomic swap

    # ------------------------------------------------------------------ #
    # Embedding helpers
    # ------------------------------------------------------------------ #
    def _embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embedder.encode(
            texts, normalize_embeddings=True, show_progress_bar=False,
            convert_to_numpy=True,
        ).tolist()

    def _embed_query(self, text: str) -> list[float]:
        return self._embedder.encode(
            config.BGE_QUERY_PREFIX + text, normalize_embeddings=True,
            show_progress_bar=False, convert_to_numpy=True,
        ).tolist()

    # ------------------------------------------------------------------ #
    # Write path
    # ------------------------------------------------------------------ #
    def index_document(
        self, parents: list[ParentChunk], children: list[ChildChunk],
        batch_size: int = 128,
    ) -> None:
        """Persist parents to the doc-store and embed children into Chroma."""
        with self._lock:
            for p in parents:
                self._parents[p.id] = {
                    "text": p.text,
                    "pages": p.pages,
                    "doc_id": p.doc_id,
                    "source": p.source,
                }
            self._save_parents()

        for start in range(0, len(children), batch_size):
            batch = children[start : start + batch_size]
            self._collection.add(
                ids=[c.id for c in batch],
                documents=[c.text for c in batch],
                embeddings=self._embed_documents([c.text for c in batch]),
                metadatas=[
                    {
                        "parent_id": c.parent_id,
                        "doc_id": c.doc_id,
                        "source": c.source,
                        "page_start": c.page_start,
                        "pages": ",".join(str(p) for p in c.pages),
                    }
                    for c in batch
                ],
            )

    # ------------------------------------------------------------------ #
    # Read path: search children -> return full parents
    # ------------------------------------------------------------------ #
    def retrieve_parents(
        self, query: str, k: int, doc_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Search child vectors, resolve to distinct parents, return top-k.

        Each result: {parent_id, text, pages, source, doc_id, score}.
        `score` is the best (lowest) child cosine distance for that parent.
        """
        if self._collection.count() == 0:
            return []

        # Over-fetch children: many children share a parent, so we need a wide
        # net to surface k *distinct* parents.
        n_children = max(k * 6, 20)
        where = {"doc_id": doc_id} if doc_id else None

        raw = self._collection.query(
            query_embeddings=[self._embed_query(query)],
            n_results=n_children,
            where=where,
            include=["metadatas", "distances"],
        )
        metadatas = raw["metadatas"][0]
        distances = raw["distances"][0]

        seen: dict[str, dict[str, Any]] = {}
        for meta, dist in zip(metadatas, distances):
            pid = meta.get("parent_id")
            if not pid or pid in seen:
                continue
            parent = self._parents.get(pid)
            if not parent:
                continue
            seen[pid] = {
                "parent_id": pid,
                "text": parent["text"],
                "pages": parent.get("pages", []),
                "source": parent.get("source", meta.get("source", "lease")),
                "doc_id": parent.get("doc_id", meta.get("doc_id", "")),
                "score": float(dist),
            }
            if len(seen) >= k:
                break

        return list(seen.values())

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #
    def child_count(self) -> int:
        return self._collection.count()

    def parent_count(self) -> int:
        return len(self._parents)
