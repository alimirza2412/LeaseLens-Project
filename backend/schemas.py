"""Pydantic v2 schemas — the rigid contract for extraction and risk output.

These models are the backbone of the abstractor: the LLM is forced to return
JSON that validates against `LeaseAbstract` / `RiskFlag`, and `LeaseReport`
bundles a full analysis for the API + UI. Validators are defensive so that
messy LLM output (currency strings, nulls, stringified numbers) never crashes
the pipeline.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def _coerce_money(value: Any) -> float:
    """Turn '$12,500.00', '12500', None, '' → float. Never raises."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^0-9.\-]", "", str(value))
    if cleaned in ("", "-", ".", "-."):
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


class LeaseAbstract(BaseModel):
    """Structured summary of the key commercial terms of a lease."""

    tenant_name: str = "Not specified"
    landlord_name: str = "Not specified"
    property_address: str = "Not specified"
    lease_commencement_date: str = "Not specified"
    lease_expiration_date: str = "Not specified"
    monthly_base_rent: float = 0.0
    annual_rent_escalation: str = "Not specified"
    security_deposit: float = 0.0
    cam_charges_type: str = "Not specified"
    renewal_options: str = "Not specified"
    early_termination_rights: str = "Not specified"
    page_citations: list[int] = Field(default_factory=list)

    @field_validator("monthly_base_rent", "security_deposit", mode="before")
    @classmethod
    def _money(cls, v: Any) -> float:
        return _coerce_money(v)

    @field_validator(
        "tenant_name",
        "landlord_name",
        "property_address",
        "lease_commencement_date",
        "lease_expiration_date",
        "annual_rent_escalation",
        "cam_charges_type",
        "renewal_options",
        "early_termination_rights",
        mode="before",
    )
    @classmethod
    def _string(cls, v: Any) -> str:
        if v is None:
            return "Not specified"
        s = str(v).strip()
        return s or "Not specified"

    @field_validator("page_citations", mode="before")
    @classmethod
    def _pages(cls, v: Any) -> list[int]:
        if v is None:
            return []
        if isinstance(v, (int, float)):
            v = [v]
        out: list[int] = []
        for item in v if isinstance(v, (list, tuple)) else [v]:
            try:
                out.append(int(item))
            except (ValueError, TypeError):
                continue
        # unique, sorted, drop non-positive
        return sorted({p for p in out if p > 0})


class RiskFlag(BaseModel):
    """A single detected legal/financial pitfall in the lease."""

    severity: Literal["HIGH", "MEDIUM", "LOW"] = "MEDIUM"
    issue_title: str = "Unspecified risk"
    explanation: str = ""
    page_number: int = 0
    section_clause: str = "Not specified"

    @field_validator("severity", mode="before")
    @classmethod
    def _sev(cls, v: Any) -> str:
        s = str(v or "").strip().upper()
        return s if s in ("HIGH", "MEDIUM", "LOW") else "MEDIUM"

    @field_validator("page_number", mode="before")
    @classmethod
    def _page(cls, v: Any) -> int:
        try:
            return max(0, int(v))
        except (ValueError, TypeError):
            return 0

    @field_validator("issue_title", "explanation", "section_clause", mode="before")
    @classmethod
    def _text(cls, v: Any) -> str:
        return str(v).strip() if v is not None else ""


class LeaseReport(BaseModel):
    """Full analysis returned by /abstract-lease."""

    doc_id: str
    source: str
    abstract: LeaseAbstract
    risks: list[RiskFlag] = Field(default_factory=list)
    num_parents: int = 0
    num_children: int = 0
