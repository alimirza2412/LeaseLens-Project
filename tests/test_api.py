"""API tests for LeaseLens.

These tests never load the embedding model or call Groq. The real store and
engine are swapped for small fakes, so the whole suite runs in a few seconds
and needs no API key or internet.

Run from the project root:
    python -m pytest -q
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from groq import APIConnectionError

import backend.main as main


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeStore:
    def child_count(self) -> int:
        return 12

    def parent_count(self) -> int:
        return 3

    def index_document(self, parents, children) -> None:
        pass


class FakeEngine:
    def chat(self, query, doc_id):
        return (
            f"Answer to: {query}",
            [{"page": 4, "text": "Rent is payable monthly in advance."}],
        )


class GroqDownEngine:
    """Behaves like Groq being unreachable."""

    def chat(self, query, doc_id):
        raise APIConnectionError(
            request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat")
        )


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
@pytest.fixture
def client():
    # Not using `with TestClient(...)` on purpose: that would run the lifespan
    # handler and load the real embedding model. We set fakes instead.
    main.app.state.store = FakeStore()
    main.app.state.engine = FakeEngine()
    return TestClient(main.app)


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #
def test_health_reports_ok_and_counts(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["child_count"] == 12
    assert body["parent_count"] == 3


# --------------------------------------------------------------------------- #
# Upload validation
# --------------------------------------------------------------------------- #
def test_upload_rejects_non_pdf_filename(client):
    res = client.post(
        "/abstract-lease",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert res.status_code == 400
    assert "PDF" in res.json()["detail"]


def test_upload_rejects_empty_file(client):
    res = client.post(
        "/abstract-lease",
        files={"file": ("lease.pdf", b"", "application/pdf")},
    )
    assert res.status_code == 400
    assert "empty" in res.json()["detail"].lower()


def test_upload_rejects_fake_pdf(client):
    res = client.post(
        "/abstract-lease",
        files={"file": ("lease.pdf", b"this is not a pdf", "application/pdf")},
    )
    assert res.status_code == 400
    assert "not a valid PDF" in res.json()["detail"]


def test_upload_rejects_file_over_size_limit(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 10)
    res = client.post(
        "/abstract-lease",
        files={"file": ("lease.pdf", b"%PDF-1.7 " + b"x" * 50, "application/pdf")},
    )
    assert res.status_code == 413


def test_upload_reports_pdf_with_no_text(client, monkeypatch):
    monkeypatch.setattr(main, "extract_pages", lambda _bytes: [])
    res = client.post(
        "/abstract-lease",
        files={"file": ("scan.pdf", b"%PDF-1.7 scanned", "application/pdf")},
    )
    assert res.status_code == 422
    assert "scanned" in res.json()["detail"].lower()


def test_upload_reports_damaged_pdf(client, monkeypatch):
    def broken(_bytes):
        raise RuntimeError("PDF parser crashed")

    monkeypatch.setattr(main, "extract_pages", broken)
    res = client.post(
        "/abstract-lease",
        files={"file": ("broken.pdf", b"%PDF-1.7 broken", "application/pdf")},
    )
    assert res.status_code == 422
    assert "Could not read this PDF" in res.json()["detail"]


# --------------------------------------------------------------------------- #
# Chat
# --------------------------------------------------------------------------- #
def test_chat_returns_answer_with_sources(client):
    res = client.post("/chat-query", json={"query": "When is rent due?"})
    assert res.status_code == 200
    body = res.json()
    assert "When is rent due?" in body["answer"]
    assert body["sources"][0]["page"] == 4


def test_chat_rejects_blank_question(client):
    res = client.post("/chat-query", json={"query": "   "})
    assert res.status_code == 400


def test_chat_rejects_very_long_question(client):
    res = client.post("/chat-query", json={"query": "a" * (main.MAX_QUERY_CHARS + 1)})
    assert res.status_code == 422


def test_chat_returns_503_when_groq_is_down(client):
    main.app.state.engine = GroqDownEngine()
    res = client.post("/chat-query", json={"query": "When is rent due?"})
    assert res.status_code == 503
    assert "try again" in res.json()["detail"].lower()
