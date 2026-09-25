"""The brain: structured extraction, risk detection, and grounded chat.

All three tasks are RAG-driven. Because a lease is 50-150 pages we never dump
the whole document into the model. Instead we run *targeted retrieval queries*
(one family for the abstract, one for risks), gather the matching full parent
sections, dedupe them, and hand that focused context to Groq.

  * Extraction & risk → temperature 0.0 + JSON mode (deterministic, schema-locked)
  * Chat            → temperature 0.1, grounded, refuses when unsupported
"""
from __future__ import annotations

import json
from typing import Any

from groq import Groq

from . import config
from .schemas import LeaseAbstract, RiskFlag
from .vector_store import VectorStore

# Retrieval probes that collectively surface the sections needed to fill the
# LeaseAbstract schema.
_ABSTRACT_QUERIES = [
    "tenant lessee name and landlord lessor name parties to the lease",
    "leased premises property address demised premises location",
    "lease term commencement date and expiration date",
    "monthly base rent amount and annual rent escalation increases CPI",
    "security deposit amount held by landlord",
    "common area maintenance CAM operating expenses triple net NNN gross lease",
    "renewal option extension term of the lease",
    "early termination rights break option cancellation",
]

# Retrieval probes tuned to the specific pitfalls the risk engine hunts for.
_RISK_QUERIES = [
    "common area maintenance CAM operating expenses cap uncapped pass-through",
    "co-tenancy clause anchor tenant vacancy right to terminate reduced rent",
    "exclusive use restriction prohibited competing tenants use clause",
    "indemnification hold harmless liability tenant obligations",
    "default remedies penalties late fees acceleration liquidated damages",
    "insurance holdover personal guaranty assignment subletting restrictions",
]

_ABSTRACT_SYSTEM = """You are a meticulous commercial real estate lease \
abstractor. Extract the requested fields ONLY from the provided lease context. \
Do not invent or assume values.

Return a single JSON object with EXACTLY these keys:
  "tenant_name" (string)
  "landlord_name" (string)
  "property_address" (string)
  "lease_commencement_date" (string)
  "lease_expiration_date" (string)
  "monthly_base_rent" (number, USD, no symbols or commas)
  "annual_rent_escalation" (string, e.g. "3% annually" or "CPI indexed")
  "security_deposit" (number, USD, no symbols or commas)
  "cam_charges_type" (string, e.g. "Triple Net (NNN)", "Gross Lease", "Modified Gross")
  "renewal_options" (string)
  "early_termination_rights" (string)
  "page_citations" (array of integers — the page numbers you drew facts from)

Rules:
- If a value is not stated in the context, use "Not specified" for strings and 0 for numbers.
- Base rent and security deposit must be plain numbers (e.g. 12500.0).
- page_citations must list the page numbers (from the [page N] labels) that support your answer.
- Output ONLY the JSON object, nothing else."""

_RISK_SYSTEM = """You are a commercial lease risk analyst protecting a TENANT. \
Review the provided lease context and flag dangerous or unfavorable clauses. \
Focus especially on:
  - Uncapped CAM / operating expenses (no annual cap on pass-through costs)
  - Broad co-tenancy clauses (rights triggered when an anchor/other tenants leave)
  - Exclusive use restrictions (limits on landlord renting to competitors, or on tenant's use)
  - Indemnification, high penalties, personal guaranties, or one-sided default remedies

Return a single JSON object with one key "risks" whose value is an array. Each \
array element is an object with EXACTLY these keys:
  "severity" (one of "HIGH", "MEDIUM", "LOW")
  "issue_title" (short string)
  "explanation" (1-3 sentence plain-English explanation of the danger)
  "page_number" (integer page where the clause appears; use the [page N] labels)
  "section_clause" (the section/clause reference or a short quote)

Rules:
- Only flag issues actually supported by the context. Do NOT fabricate.
- If you find no notable risks, return {"risks": []}.
- Assign HIGH to clauses that expose the tenant to open-ended cost or termination.
- Output ONLY the JSON object, nothing else."""

_CHAT_SYSTEM = f"""You are a precise assistant answering questions about a \
commercial lease. Answer ONLY from the CONTEXT below — never use outside \
knowledge. Cite the page number(s) inline like (page 12). If the context does \
not contain the answer, reply EXACTLY:
"{config.CHAT_REFUSAL}"
Be concise, factual, and never fabricate figures, dates, or page numbers."""


def _format_context(parents: list[dict[str, Any]]) -> str:
    blocks = []
    for i, p in enumerate(parents, start=1):
        pages = ", ".join(str(x) for x in p.get("pages", [])) or "?"
        blocks.append(f"[Section {i} | page {pages}]\n{p['text']}")
    return "\n\n".join(blocks)


class AbstractorEngine:
    def __init__(self, store: VectorStore) -> None:
        if not config.GROQ_API_KEY:
            raise RuntimeError(
                "GROQ_API_KEY is not set. Copy .env.example to .env and add "
                "your key."
            )
        self._client = Groq(
            api_key=config.GROQ_API_KEY,
            timeout=config.GROQ_TIMEOUT,
            max_retries=config.GROQ_MAX_RETRIES,
        )
        self._store = store

    # ------------------------------------------------------------------ #
    # Shared retrieval: gather distinct parents across many probe queries
    # ------------------------------------------------------------------ #
    def _gather_context(
        self, queries: list[str], doc_id: str, max_parents: int
    ) -> list[dict[str, Any]]:
        collected: dict[str, dict[str, Any]] = {}
        for q in queries:
            for parent in self._store.retrieve_parents(
                q, k=config.ABSTRACT_PARENTS_PER_QUERY, doc_id=doc_id
            ):
                pid = parent["parent_id"]
                # Keep the best (lowest-distance) score if seen twice.
                if pid not in collected or parent["score"] < collected[pid]["score"]:
                    collected[pid] = parent
        ranked = sorted(collected.values(), key=lambda p: p["score"])
        return ranked[:max_parents]

    def _json_completion(self, system: str, user: str) -> dict[str, Any]:
        """Call Groq in JSON mode and parse. Returns {} on parse failure."""
        completion = self._client.chat.completions.create(
            model=config.GROQ_MODEL,
            temperature=config.TEMPERATURE_EXTRACTION,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        content = completion.choices[0].message.content or "{}"
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {}

    # ------------------------------------------------------------------ #
    # 1. Structured extraction
    # ------------------------------------------------------------------ #
    def extract_abstract(self, doc_id: str) -> LeaseAbstract:
        parents = self._gather_context(
            _ABSTRACT_QUERIES, doc_id, config.ABSTRACT_MAX_PARENTS
        )
        if not parents:
            return LeaseAbstract()

        user = (
            f"LEASE CONTEXT:\n{_format_context(parents)}\n\n"
            "Extract the lease abstract as the specified JSON object."
        )
        data = self._json_completion(_ABSTRACT_SYSTEM, user)
        try:
            return LeaseAbstract(**data)
        except Exception:
            # Validators are lenient, but guard against a totally malformed shape.
            return LeaseAbstract()

    # ------------------------------------------------------------------ #
    # 2. Risk detection
    # ------------------------------------------------------------------ #
    def detect_risks(self, doc_id: str) -> list[RiskFlag]:
        parents = self._gather_context(
            _RISK_QUERIES, doc_id, config.ABSTRACT_MAX_PARENTS
        )
        if not parents:
            return []

        user = (
            f"LEASE CONTEXT:\n{_format_context(parents)}\n\n"
            "Identify tenant-side risks as the specified JSON object."
        )
        data = self._json_completion(_RISK_SYSTEM, user)
        raw_risks = data.get("risks", []) if isinstance(data, dict) else []

        flags: list[RiskFlag] = []
        for item in raw_risks:
            if not isinstance(item, dict):
                continue
            try:
                flags.append(RiskFlag(**item))
            except Exception:
                continue

        # Surface the scariest clauses first.
        order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        flags.sort(key=lambda f: order.get(f.severity, 3))
        return flags

    # ------------------------------------------------------------------ #
    # 3. Grounded chat
    # ------------------------------------------------------------------ #
    def chat(
        self, query: str, doc_id: str | None
    ) -> tuple[str, list[dict[str, Any]]]:
        parents = self._store.retrieve_parents(
            query, k=config.CHAT_TOP_PARENTS, doc_id=doc_id
        )
        if not parents:
            return config.CHAT_REFUSAL, []

        user = (
            f"CONTEXT:\n{_format_context(parents)}\n\n"
            f"QUESTION: {query}\n\n"
            "Answer using only the context above, citing page numbers."
        )
        completion = self._client.chat.completions.create(
            model=config.GROQ_MODEL,
            temperature=config.TEMPERATURE_CHAT,
            messages=[
                {"role": "system", "content": _CHAT_SYSTEM},
                {"role": "user", "content": user},
            ],
        )
        answer = (completion.choices[0].message.content or "").strip()

        sources = [
            {
                "parent_id": p["parent_id"],
                "pages": p.get("pages", []),
                "source": p.get("source", "lease"),
                "score": p["score"],
                "snippet": p["text"][:600],
            }
            for p in parents
        ]
        return answer, sources
