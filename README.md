# 🔍 LeaseLens — Commercial Lease Abstractor & Risk Engine

[![tests](https://github.com/alimirza2412/LeaseLens-Project/actions/workflows/tests.yml/badge.svg)](https://github.com/alimirza2412/LeaseLens-Project/actions/workflows/tests.yml)

> **Abstract any lease with ease.**

**Live demo:** <https://leaselens-n5em.onrender.com>
(free server: the first visit after a quiet period can take about a minute to wake up)

A RAG application for commercial real estate. Upload a lease PDF and get back a
**structured abstract** (parties, rent, term, CAM type, options), an
**automated risk matrix** (uncapped CAM, co-tenancy, exclusive use,
indemnification…), **Excel + PDF exports**, and a **grounded Q&A chat** —
every answer cited to the page.

The UI is a single-page app (HTML/CSS/JS) served directly by the FastAPI
backend, so you run **one** process and open **one** URL.

## Results

Measured with [`eval/run_eval.py`](eval/run_eval.py): 25 questions with known
answers and pages, asked against a 12-page test lease
([`eval/Sample_Lease_Northwind_Coffee.pdf`](eval/Sample_Lease_Northwind_Coffee.pdf)),
3 runs.

| Metric | Result |
|---|---|
| Correct answer **and** correct page cited | **74 / 75** (99%) |
| Correct page among cited sources | **75 / 75** (100%) |
| Correct page cited first | 66 / 75 (88%) |
| Risks flagged on the test lease | 7 (the lease contains 7 planted risks) |

The test lease is short and written for testing, so these numbers show the
pipeline works end to end; longer real-world leases are the next thing to test.

## Why it's more than "chat with PDF"

| Capability | How it works |
|---|---|
| **Parent-Child chunking** | Search runs on ~200-char child vectors for sharp matches; the LLM always receives the full ~1000-char parent section so fine-print conditions are never cut mid-sentence. |
| **Structured extraction** | Groq `openai/gpt-oss-120b` at temperature 0 in JSON mode, validated against a strict `LeaseAbstract` Pydantic model. |
| **Risk engine** | A tenant-protective prompt scans retrieved sections and emits `RiskFlag` objects (severity, page, clause). |
| **Grounded chat** | Temperature 0.1, answers only from retrieved parent sections, refuses when unsupported, cites pages. |
| **Excel + PDF export** | Server-side `openpyxl` (2-sheet workbook) and `reportlab` (formatted report with colour-coded risk table). |
| **Runs in 512 MB** | Embeddings use `fastembed` (ONNX Runtime) instead of PyTorch: the same `bge-small-en-v1.5` model, small enough for a free 512 MB server. |
| **Fails politely** | Upload size limit (20 MB), real-PDF check, clear errors for scanned or damaged PDFs, and a 503 "try again" message when the LLM service is busy instead of a crash. |

## Architecture

```
┌──────────────────────────────┐        ┌─────────────────────────────────────┐
│  Browser (HTML/CSS/JS SPA)    │  REST  │            FastAPI backend           │
│  Tab1 Abstract + Risk matrix  │ ─────▶ │  /abstract-lease  ingestion.py       │
│  Tab2 Legal Q&A chat          │  same  │                   vector_store.py    │
│  Excel / PDF download         │ origin │  /chat-query      abstractor_engine  │
│  (served by StaticFiles)      │ ◀───── │  /export/{xlsx,pdf}  exports.py      │
└──────────────────────────────┘        │  /api/health      schemas.py · main  │
                                          └───────┬──────────────────────────────┘
                                                  │
        ┌─────────────────┬───────────────────────┼─────────────────────┐
        ▼                 ▼                        ▼                     ▼
  bge-small-en-v1.5   ChromaDB (children)   parent_store.json        Groq API
  (fastembed / ONNX)  ./chroma_db           (full parent sections)   gpt-oss-120b
```

## Project layout

```
LeaseLens-Project/
├── Dockerfile                # production image (no PyTorch, fits 512 MB)
├── requirements.txt
├── .env.example
├── .github/workflows/tests.yml   # CI: runs the tests on every push
├── backend/
│   ├── config.py             # all tunables
│   ├── schemas.py            # LeaseAbstract, RiskFlag, LeaseReport
│   ├── ingestion.py          # pdfplumber + parent-child splitter + page map
│   ├── vector_store.py       # Chroma child index + JSON parent doc-store
│   ├── abstractor_engine.py  # Groq extraction, risk detection, chat
│   ├── exports.py            # Excel + PDF builders
│   └── main.py               # FastAPI endpoints, input checks, static frontend
├── frontend/
│   ├── index.html            # single-page dashboard
│   ├── styles.css
│   └── app.js                # REST client, tabs, chat, downloads
├── tests/
│   └── test_api.py           # 11 API tests (no model or API key needed)
└── eval/
    ├── run_eval.py           # 25-question accuracy + citation eval
    ├── Sample_Lease_Northwind_Coffee.pdf   # fictional 12-page test lease
    └── results.csv           # latest eval output
```

## Run locally

Requires **Python 3.11**.

```bash
python -m venv venv
source venv/bin/activate          # Windows PowerShell: venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env              # then set GROQ_API_KEY (https://console.groq.com/keys)
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Open **<http://localhost:8000>**. API docs: <http://localhost:8000/docs>.
The first run downloads the embedding model (about 70 MB) once.

### Run with Docker

```bash
docker build -t leaselens .
docker run -p 10000:10000 -e GROQ_API_KEY=your_key leaselens
```

Then open <http://localhost:10000>.

## Tests and eval

```bash
pip install pytest httpx
python -m pytest -q                                          # 11 tests, a few seconds

# with the app running:
python eval/run_eval.py --pdf eval/Sample_Lease_Northwind_Coffee.pdf
# or against the live demo:
python eval/run_eval.py --base-url https://leaselens-n5em.onrender.com \
                        --pdf eval/Sample_Lease_Northwind_Coffee.pdf
```

The tests swap the embedding model and Groq for small fakes, so they need no API
key or internet. The eval retries automatically when the LLM service is
rate-limited and warns if any question got no answer.

## Deployment

Deployed on **Render** (free web service, Docker runtime, Singapore region).
Render builds the `Dockerfile` on every push to `main`. Settings:

- Environment variable: `GROQ_API_KEY`
- Health check path: `/api/health`

## API reference

### `POST /abstract-lease`
Multipart form, field `file` (PDF, max 20 MB). Ingests and analyzes; returns a
`LeaseReport` (abstract + risks + chunk counts + `doc_id`).

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

## Notes and known limits

- **Persistence**: child vectors live in `./chroma_db`; full parent sections in
  `./chroma_db/parent_store.json`. Delete the folder to reset. On the free
  Render plan, uploads are cleared whenever the server restarts.
- **Scanned leases**: image-only PDFs return HTTP 422 (they need OCR first).
- **Multi-lease**: each upload gets a `doc_id`; chat filters by it so leases
  don't cross-contaminate.
- **Run-to-run variation**: even at temperature 0 the hosted model can word
  answers differently between runs, which is why the eval reports 3 runs. In
  one run the abstract inferred a lease type ("NNN") that the test lease does
  not state; tightening the extraction prompt to return "Not specified" in that
  case is the next fix.
