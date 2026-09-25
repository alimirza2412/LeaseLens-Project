"""Document ingestion with Parent-Child (hierarchical) chunking.

Why hierarchical? A lease clause's meaning often depends on the full section
around it ("...except as provided in Section 12.3..."). If we embed and later
feed the LLM tiny fragments, fine-print conditions get cut mid-sentence.

So we build two layers:

  * **Parent chunks** (~800-1200 chars): complete legal sections that carry
    full context. These are what the LLM ultimately reads.
  * **Child chunks** (~200 chars): small, focused sub-vectors carved out of
    each parent. These are what we embed and search over — smaller vectors
    give sharper semantic matches.

Vector search hits a *child*; we then resolve its `parent_id` and hand the LLM
the whole *parent*. Page numbers are mapped from character offsets so every
parent knows which physical page(s) it came from.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from io import BytesIO

import pdfplumber
from langchain_text_splitters import RecursiveCharacterTextSplitter

from . import config

# Legal text favours these boundaries: section breaks, numbered clauses,
# sentences, then words as a last resort.
_LEGAL_SEPARATORS = ["\n\n", "\n", ". ", "; ", ", ", " ", ""]

_PARENT_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=config.PARENT_CHUNK_SIZE,
    chunk_overlap=config.PARENT_CHUNK_OVERLAP,
    separators=_LEGAL_SEPARATORS,
    add_start_index=True,  # lets us map each parent back to a page
    length_function=len,
)

_CHILD_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=config.CHILD_CHUNK_SIZE,
    chunk_overlap=config.CHILD_CHUNK_OVERLAP,
    separators=_LEGAL_SEPARATORS,
    length_function=len,
)


@dataclass
class ParentChunk:
    id: str
    text: str
    pages: list[int]
    doc_id: str
    source: str


@dataclass
class ChildChunk:
    id: str
    text: str
    parent_id: str
    doc_id: str
    source: str
    page_start: int
    pages: list[int] = field(default_factory=list)


def _clean(text: str) -> str:
    return " ".join(text.split()).strip()


def extract_pages(file_bytes: bytes) -> list[tuple[int, str]]:
    """Return [(page_number, cleaned_text), ...] using pdfplumber.

    Pages that yield no text (blank / image-only) are skipped.
    """
    pages: list[tuple[int, str]] = []
    with pdfplumber.open(BytesIO(file_bytes)) as pdf:
        for idx, page in enumerate(pdf.pages):
            text = _clean(page.extract_text() or "")
            if text:
                pages.append((idx + 1, text))  # 1-based, human-facing
    return pages


def _pages_for_span(
    start: int, end: int, page_spans: list[tuple[int, int, int]]
) -> list[int]:
    """Which physical pages overlap the char range [start, end)?"""
    hits = [
        pg
        for (s, e, pg) in page_spans
        if not (e <= start or s >= end)  # ranges overlap
    ]
    if hits:
        return sorted(set(hits))
    # Fallback: nearest page by start offset.
    nearest = min(page_spans, key=lambda x: abs(x[0] - start), default=(0, 0, 1))
    return [nearest[2]]


def build_parent_child(
    pages: list[tuple[int, str]], source: str
) -> tuple[str, list[ParentChunk], list[ChildChunk]]:
    """Turn extracted pages into (doc_id, parent_chunks, child_chunks)."""
    doc_id = str(uuid.uuid4())

    # Concatenate pages into one document, recording each page's char span so
    # we can attribute parents (which may straddle page breaks) to pages.
    full_text = ""
    page_spans: list[tuple[int, int, int]] = []  # (start, end, page_number)
    for page_number, text in pages:
        start = len(full_text)
        full_text += text + "\n\n"
        page_spans.append((start, len(full_text), page_number))

    if not full_text.strip():
        raise ValueError(
            "No extractable text found in the PDF. It may be a scanned "
            "image-only document that requires OCR."
        )

    parents: list[ParentChunk] = []
    children: list[ChildChunk] = []

    for p_idx, doc in enumerate(_PARENT_SPLITTER.create_documents([full_text])):
        p_text = doc.page_content.strip()
        if not p_text:
            continue
        start = doc.metadata.get("start_index", 0)
        p_pages = _pages_for_span(start, start + len(p_text), page_spans)
        parent_id = f"{doc_id}:p{p_idx}"

        parents.append(
            ParentChunk(
                id=parent_id,
                text=p_text,
                pages=p_pages,
                doc_id=doc_id,
                source=source,
            )
        )

        for c_idx, c_text in enumerate(_CHILD_SPLITTER.split_text(p_text)):
            c_text = c_text.strip()
            if not c_text:
                continue
            children.append(
                ChildChunk(
                    id=f"{parent_id}:c{c_idx}",
                    text=c_text,
                    parent_id=parent_id,
                    doc_id=doc_id,
                    source=source,
                    page_start=p_pages[0] if p_pages else 0,
                    pages=p_pages,
                )
            )

    if not children:
        raise ValueError("PDF produced no chunkable text.")

    return doc_id, parents, children
