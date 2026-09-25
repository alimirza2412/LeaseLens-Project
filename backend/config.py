"""Central configuration for the Commercial Lease Abstractor & Risk Engine.

Every tunable constant lives here so the rest of the codebase never has to
guess at a magic number. Values are read once at import time.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# Disable ChromaDB's product telemetry. Version 0.5.4 has a posthog signature
# mismatch that logs a scary (but harmless) ERROR on every event; turning
# telemetry off at the source removes the noise entirely.
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("CHROMA_TELEMETRY_IMPL", "chromadb.telemetry.product.posthog.Posthog")

# ---- Groq / LLM ----
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL: str = "openai/gpt-oss-120b"
TEMPERATURE_EXTRACTION: float = 0.0  # deterministic structured extraction
TEMPERATURE_CHAT: float = 0.1        # grounded conversational answers
# Never let a stalled/half-dropped HTTP connection hang a request forever.
GROQ_TIMEOUT: float = 45.0           # seconds per LLM call
GROQ_MAX_RETRIES: int = 2            # auto-retry transient network failures

# ---- Embeddings ----
EMBEDDING_MODEL_NAME: str = "BAAI/bge-small-en-v1.5"
# bge models want this instruction prefix on the *query* side only.
BGE_QUERY_PREFIX: str = "Represent this sentence for searching relevant passages: "

# ---- Vector store (ChromaDB, child index) ----
CHROMA_PERSIST_DIR: str = str(PROJECT_ROOT / "chroma_db")
CHROMA_COLLECTION_NAME: str = "lease_children"
PARENT_STORE_PATH: str = str(PROJECT_ROOT / "chroma_db" / "parent_store.json")

# ---- Parent-Child (hierarchical) chunking ----
PARENT_CHUNK_SIZE: int = 1000    # full legal section (~800-1200 chars)
PARENT_CHUNK_OVERLAP: int = 150
CHILD_CHUNK_SIZE: int = 200      # small sub-vector
CHILD_CHUNK_OVERLAP: int = 40

# ---- Retrieval ----
# How many distinct parent sections to surface per query.
CHAT_TOP_PARENTS: int = 4
# During abstraction/risk we cast a wider net across many targeted queries.
ABSTRACT_PARENTS_PER_QUERY: int = 3
ABSTRACT_MAX_PARENTS: int = 14   # cap total context sent to the LLM

# ---- Grounding ----
CHAT_REFUSAL: str = (
    "I cannot find the answer to this question in the provided lease document."
)
