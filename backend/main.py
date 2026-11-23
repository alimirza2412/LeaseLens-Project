"""FastAPI app for the Commercial Lease Abstractor & Risk Engine.

Endpoints:
  POST /abstract-lease — upload a lease PDF; ingest (parent-child), extract the
                         structured abstract, run the risk engine; return a
                         full LeaseReport.
  POST /chat-query     — ask a grounded question about an ingested lease.
  GET  /api/health     — DB status + chunk counts.

Heavy resources (embedding model, ChromaDB, parent store, Groq client) are
created once in the lifespan handler and reused for every request.
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from groq import APIError as GroqAPIError
from pydantic import BaseModel, Field

from . import config
from .abstractor_engine import AbstractorEngine
from .exports import build_excel, build_pdf
from .ingestion import build_parent_child, extract_pages
from .schemas import LeaseReport
from .vector_store import VectorStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("leaselens")
# Hide ChromaDB 0.5.4's harmless telemetry error spam.
logging.getLogger("chromadb.telemetry").setLevel(logging.CRITICAL)
logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.CRITICAL)

# --------------------------------------------------------------------------- #
# Input limits
# --------------------------------------------------------------------------- #
MAX_UPLOAD_MB = 20
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
MAX_QUERY_CHARS = 1000

AI_BUSY_MESSAGE = (
    "The AI service is busy or unavailable right now. Please try again in a minute."
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = VectorStore()
    app.state.store = store
    app.state.engine = AbstractorEngine(store)
    yield


app = FastAPI(
    title="LeaseLens — Commercial Lease Abstractor & Risk Engine",
    version="2.2.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=MAX_QUERY_CHARS)
    doc_id: str | None = Field(
        default=None, description="Restrict retrieval to one ingested lease."
    )


class ChatResponse(BaseModel):
    answer: str
    sources: list[dict[str, Any]]


class HealthResponse(BaseModel):
    status: str
    db_connected: bool
    child_count: int
    parent_count: int
    collection: str


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    store: VectorStore = app.state.store
    try:
        return HealthResponse(
            status="ok",
            db_connected=True,
            child_count=store.child_count(),
            parent_count=store.parent_count(),
            collection=config.CHROMA_COLLECTION_NAME,
        )
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=f"DB error: {exc}") from exc


# NOTE: this handler is a plain `def` (not `async def`) ON PURPOSE. It does
# heavy *blocking* work (embedding on CPU + LLM calls). A sync endpoint is run
# by FastAPI in a worker threadpool, so uvicorn's event loop stays free to
# serve health checks and other requests concurrently. An `async def` here
# would block the single event loop for ~50s and make the whole app appear
# frozen in the browser.
@app.post("/abstract-lease", response_model=LeaseReport)
def abstract_lease(file: UploadFile = File(...)) -> LeaseReport:
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    # Read at most one byte over the limit so a huge upload can't fill memory.
    file_bytes = file.file.read(MAX_UPLOAD_BYTES + 1)  # sync read (threadpool)
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File is too large. The limit is {MAX_UPLOAD_MB} MB.",
        )
    if not file_bytes.startswith(b"%PDF"):
        raise HTTPException(
            status_code=400,
            detail="This file is not a valid PDF, even though its name ends in .pdf.",
        )

    t0 = time.perf_counter()
    log.info("[abstract] '%s' (%d bytes) — ingesting…", file.filename, len(file_bytes))

    # 1. Ingest with parent-child chunking.
    try:
        pages = extract_pages(file_bytes)
        if not pages:
            raise ValueError(
                "No extractable text found. The PDF may be scanned/image-only."
            )
        doc_id, parents, children = build_parent_child(pages, file.filename)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("[abstract] could not read PDF '%s'", file.filename)
        raise HTTPException(
            status_code=422,
            detail="Could not read this PDF. It may be damaged or password-protected.",
        ) from exc

    store: VectorStore = app.state.store
    log.info("[abstract] embedding %d children across %d sections…",
             len(children), len(parents))
    store.index_document(parents, children)

    # 2. Extract structured abstract + 3. run the risk engine.
    engine: AbstractorEngine = app.state.engine
    try:
        log.info("[abstract] extracting structured terms…")
        abstract = engine.extract_abstract(doc_id)
        log.info("[abstract] scanning risks…")
        risks = engine.detect_risks(doc_id)
    except GroqAPIError as exc:
        log.warning("[abstract] Groq call failed: %s", exc)
        raise HTTPException(status_code=503, detail=AI_BUSY_MESSAGE) from exc

    log.info("[abstract] DONE in %.1fs — %d risks", time.perf_counter() - t0,
             len(risks))
    return LeaseReport(
        doc_id=doc_id,
        source=file.filename,
        abstract=abstract,
        risks=risks,
        num_parents=len(parents),
        num_children=len(children),
    )


@app.post("/chat-query", response_model=ChatResponse)
def chat_query(req: ChatRequest) -> ChatResponse:
    query = req.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Please type a question.")

    engine: AbstractorEngine = app.state.engine
    try:
        answer, sources = engine.chat(query, req.doc_id)
    except GroqAPIError as exc:
        log.warning("[chat] Groq call failed: %s", exc)
        raise HTTPException(status_code=503, detail=AI_BUSY_MESSAGE) from exc
    return ChatResponse(answer=answer, sources=sources)


def _safe_stem(name: str) -> str:
    stem = (name or "lease").rsplit(".", 1)[0]
    keep = "".join(c if c.isalnum() or c in "-_ " else "_" for c in stem)
    return keep.strip().replace(" ", "_") or "lease"


@app.post("/export/xlsx")
def export_xlsx(report: LeaseReport) -> Response:
    data = build_excel(report.model_dump())
    fname = f"lease_abstract_{_safe_stem(report.source)}.xlsx"
    return Response(
        content=data,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.post("/export/pdf")
def export_pdf(report: LeaseReport) -> Response:
    data = build_pdf(report.model_dump())
    fname = f"lease_report_{_safe_stem(report.source)}.pdf"
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# Serve the HTML/CSS/JS frontend from the same origin as the API. Mounted LAST
# so the API routes above take precedence over the "/" catch-all.
_FRONTEND_DIR = config.PROJECT_ROOT / "frontend"
app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
