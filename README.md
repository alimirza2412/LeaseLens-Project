# 🔍 LeaseLens — Commercial Lease Abstractor & Risk Engine

> **Abstract any lease with ease.**

An enterprise-grade RAG application for commercial real estate. Upload a
50–150 page lease PDF and get back a **structured abstract** (parties, rent,
term, CAM type, options), an **automated risk matrix** (uncapped CAM,
co-tenancy, exclusive use, indemnification…), **Excel + PDF exports**, and a
**grounded Q&A chat** — all cited to the page.

The UI is a clean, light single-page app (HTML/CSS/JS) served directly by the
FastAPI backend, so you run **one** process and open **one** URL.

## Why it's more than "chat with PDF"

| Capability | How it works |
|---|---|
| **Parent-Child chunking** | Search runs on ~200-char child vectors for sharp matches; the LLM always receives the full ~1000-char parent section so fine-print conditions are never cut mid-sentence. |
| **Structured extraction** | Groq `llama-3.3-70b-versatile` @ `temp 0.0` in JSON mode, validated against a rigid `LeaseAbstract` Pydantic model. |
| **Risk engine** | A tenant-protective prompt scans retrieved sections and emits `RiskFlag` objects (severity, page, clause). |
| **Grounded chat** | `temp 0.1`, answers only from retrieved parent sections, refuses when unsupported, cites pages. |
| **Excel + PDF export** | Server-side `openpyxl` (2-sheet workbook) and `reportlab` (formatted report with colour-coded risk table). |
| **Performance** | Embedder, ChromaDB, parent store, and Groq client are FastAPI lifespan singletons. |

## Architecture

```
┌──────────────────────────────┐        ┌─────────────────────────────────────┐
│  Browser (HTML/CSS/JS SPA)    │  REST  │            FastAPI backend           │
│  · light pastel dashboard     │ ─────▶ │                                      │
│  Tab1 Abstract + Risk matrix  │  same  │  /abstract-lease  ingestion.py       │
│  Tab2 Legal Q&A chat          │ origin │                   vector_store.py    │
│  · Excel / PDF download       │ ◀───── │  /chat-query      abstractor_engine  │
│    (served by StaticFiles)    │        │  /export/{xlsx,pdf}  exports.py      │
└──────────────────────────────┘        │  /api/health      schemas.py · main  │
                                          └───────┬──────────────────────────────┘
                                                  │
        ┌─────────────────┬───────────────────────┼─────────────────────┐
        ▼                 ▼                        ▼                     ▼
  bge-small-en-v1.5   ChromaDB (children)   parent_store.json        Groq API
  (embeddings)        ./chroma_db           (full parent sections)   llama-3.3-70b
```

## Project layout

```
Rag-Project/
├── requirements.txt
├── .env.example
├── backend/
│   ├── config.py             # all tunables
│   ├── schemas.py            # LeaseAbstract, RiskFlag, LeaseReport
│   ├── ingestion.py          # pdfplumber + parent-child splitter + page map
│   ├── vector_store.py       # Chroma child index + JSON parent doc-store
│   ├── abstractor_engine.py  # Groq extraction, risk detection, chat
│   ├── exports.py            # Excel + PDF builders
│   └── main.py               # FastAPI endpoints + static frontend mount
└── frontend/
    ├── index.html            # light single-page dashboard
    ├── styles.css            # soft pastel theme (no bright gradients)
    └── app.js                # REST client, tabs, chat, downloads
```

## Setup

### 1. Install

```bash
python -m venv .venv
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# macOS/Linux:
# source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

```bash
# Windows: copy .env.example .env    |    macOS/Linux: cp .env.example .env
```

Set `GROQ_API_KEY` in `.env` (get one at <https://console.groq.com/keys>).
First run downloads the embedding model (~130 MB) to a local cache, once.

### 3. Run (one command)

From the project root:

```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

Then open **<http://localhost:8000>** — the dashboard *and* the API live on the
same origin. Interactive API docs: <http://localhost:8000/docs>.

Upload a lease, click **Run Abstraction & Risk Scan**, then explore the two
tabs and download the Excel / PDF report.

## API reference

### `POST /abstract-lease`
Multipart form, field `file` (PDF). Ingests + analyzes; returns a `LeaseReport`
(abstract + risks + chunk counts + `doc_id`).

### `POST /chat-query`
```json
{ "query": "What is the holdover rent?", "doc_id": "…" }
```
Returns `{ "answer": "…", "sources": [ { "pages": [22], "snippet": "…", "score": 0.31 } ] }`.

### `POST /export/xlsx` · `POST /export/pdf`
Body: a `LeaseReport` JSON (the object returned by `/abstract-lease`). Returns
the file as an attachment.

### `GET /api/health`
```json
{ "status": "ok", "db_connected": true, "child_count": 640,
  "parent_count": 120, "collection": "lease_children" }
```

## Notes

- **Persistence**: child vectors live in `./chroma_db`; full parent sections in
  `./chroma_db/parent_store.json`. Delete the folder to reset.
- **Scanned leases**: image-only PDFs return HTTP 422 (need OCR first).
- **Multi-lease**: each upload gets a `doc_id`; chat filters by it so leases
  don't cross-contaminate.
- **Fonts**: the UI requests *Plus Jakarta Sans* from Google Fonts and falls
  back to system fonts if offline — no functional dependency on the network.
```
