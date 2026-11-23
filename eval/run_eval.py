"""LeaseLens eval: 25 questions with known answers and pages.

Uploads the sample lease, asks every question through /chat-query, and scores:
  - answer correct  : the answer contains the expected facts
  - citation correct: the expected page is among the cited source pages
  - top-1 citation  : the FIRST cited source contains the expected page

Run from the project root (app running locally or live):
    python eval/run_eval.py --pdf eval/Sample_Lease_Northwind_Coffee.pdf
    python eval/run_eval.py --base-url https://leaselens-n5em.onrender.com --pdf ...

Writes eval/results.csv and prints a summary table for the README.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import time
import unicodedata
from pathlib import Path

import httpx

# (question, expected pages, required facts)
# Each required fact is a regex; ALL must appear in the answer (case-insensitive).
CASES: list[tuple[str, set[int], list[str]]] = [
    ("Who is the landlord?", {1}, [r"crescent harbor"]),
    ("Who is the tenant?", {1}, [r"northwind"]),
    ("What is the address of the premises?", {1, 2}, [r"4500 meridian"]),
    ("How many rentable square feet are leased?", {1, 2}, [r"3,?250"]),
    ("When does the lease term start?", {1, 3}, [r"april 1,? 2026"]),
    ("When does the lease expire?", {1, 3}, [r"march 31,? 2031"]),
    ("What can the premises be used for?", {2}, [r"coffee", r"roast"]),
    ("How many parking spaces does the tenant get?", {2}, [r"\b(six|6)\b"]),
    ("What is the monthly base rent in the first year?", {3, 4}, [r"9,?750"]),
    ("By how much does base rent increase each year?", {3}, [r"(3\s?%|three percent)"]),
    ("What is the late charge?", {3}, [r"(5\s?%|five percent)"]),
    ("What is the monthly base rent in year 3?", {4}, [r"10,?343\.78"]),
    ("What is the monthly base rent in year 5?", {4}, [r"10,?973\.71"]),
    ("How much is the security deposit?", {5}, [r"29,?250"]),
    ("What is the tenant's share of operating expenses?", {5}, [r"12\.5"]),
    ("Is there a cap on operating expense increases?", {5},
     [r"(no cap|not capped|uncapped|no limit|no .{0,20}cap|there is no)"]),
    ("Does the tenant have an exclusive use right?", {6}, [r"coffee"]),
    ("Who pays to repair or replace the HVAC system?", {7}, [r"tenant"]),
    ("Can the tenant sublet without the landlord's consent?", {8}, [r"consent"]),
    ("Can the landlord relocate the tenant?", {8}, [r"\b(60|sixty)\b"]),
    ("What is the renewal option?", {9}, [r"\b(5|five)\b", r"95"]),
    ("Can the landlord terminate the lease early?", {9}, [r"\b180\b"]),
    ("How much liability insurance must the tenant carry?", {10},
     [r"(2,?000,?000|2 million)"]),
    ("Who personally guarantees the lease?", {11}, [r"daniel reyes"]),
    ("What is the holdover rent?", {11}, [r"200"]),
]


def pages_of(source: dict) -> set[int]:
    """Pull page numbers out of a source dict, whatever its shape."""
    found: set[int] = set()
    for key in ("pages", "page", "page_start", "page_number"):
        val = source.get(key)
        if val is None:
            continue
        if isinstance(val, (list, tuple)):
            items = val
        else:
            items = re.findall(r"\d+", str(val))
        for item in items:
            try:
                found.add(int(item))
            except (TypeError, ValueError):
                pass
    return found


def ask(client: httpx.Client, base: str, question: str, doc_id: str,
        retries: int) -> httpx.Response:
    """POST one question; on 503 (Groq busy or rate-limited) wait and retry."""
    wait = 15
    for attempt in range(retries + 1):
        r = client.post(f"{base}/chat-query", json={"query": question, "doc_id": doc_id})
        if r.status_code != 503 or attempt == retries:
            return r
        print(f"    503 (AI busy), retrying in {wait}s...")
        time.sleep(wait)
        wait = min(wait * 2, 60)
    return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out", default="eval/results.csv")
    ap.add_argument("--pause", type=float, default=2.0,
                    help="seconds to wait between questions")
    ap.add_argument("--retries", type=int, default=4,
                    help="retries when the AI service returns 503")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    client = httpx.Client(timeout=600)

    print(f"Uploading {args.pdf} to {base} ...")
    pdf_bytes = Path(args.pdf).read_bytes()
    wait = 15
    for attempt in range(args.retries + 1):
        res = client.post(f"{base}/abstract-lease",
                          files={"file": (Path(args.pdf).name, pdf_bytes, "application/pdf")})
        if res.status_code != 503 or attempt == args.retries:
            break
        print(f"    503 (AI busy), retrying upload in {wait}s...")
        time.sleep(wait)
        wait = min(wait * 2, 60)
    res.raise_for_status()
    doc_id = res.json()["doc_id"]
    print(f"doc_id = {doc_id}\n")

    rows = []
    errors = 0
    for i, (question, want_pages, facts) in enumerate(CASES, 1):
        t0 = time.perf_counter()
        r = ask(client, base, question, doc_id, args.retries)
        secs = time.perf_counter() - t0
        time.sleep(args.pause)  # stay under Groq's free-tier rate limit
        if r.status_code != 200:
            print(f"{i:2}. ERROR {r.status_code}: {question}")
            errors += 1
            rows.append([question, f"ERROR {r.status_code}", False, False, False, "",
                         f"{secs:.1f}"])
            continue
        body = r.json()
        answer = body.get("answer", "")
        # LLMs often emit non-breaking spaces; normalise so "April 1, 2026" matches.
        answer = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", answer))
        sources = body.get("sources", [])

        answer_ok = all(re.search(f, answer, re.IGNORECASE) for f in facts)
        cited = [pages_of(s) for s in sources]
        all_pages = set().union(*cited) if cited else set()
        cite_ok = bool(all_pages & want_pages)
        top1_ok = bool(cited and cited[0] & want_pages)

        mark = "OK " if answer_ok and cite_ok else "XX "
        print(f"{i:2}. {mark} answer={'Y' if answer_ok else 'N'} "
              f"cite={'Y' if cite_ok else 'N'}  {question}")
        rows.append([question, answer.replace("\n", " "), answer_ok, cite_ok, top1_ok,
                     ";".join(str(p) for p in sorted(all_pages)), f"{secs:.1f}"])

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["question", "answer", "answer_correct", "citation_correct",
                    "top1_citation_correct", "cited_pages", "seconds"])
        w.writerows(rows)

    n = len(rows)
    a = sum(r[2] for r in rows)
    c = sum(r[3] for r in rows)
    t1 = sum(r[4] for r in rows)
    both = sum(r[2] and r[3] for r in rows)
    print("\n| Metric | Result |")
    print("| --- | --- |")
    print(f"| Answer correct | {a}/{n} ({a / n:.0%}) |")
    print(f"| Correct page cited | {c}/{n} ({c / n:.0%}) |")
    print(f"| Correct page cited first | {t1}/{n} ({t1 / n:.0%}) |")
    print(f"| Correct answer AND correct page | {both}/{n} ({both / n:.0%}) |")
    if errors:
        print(f"\nWARNING: {errors} question(s) got no answer (service errors). "
              "Re-run before quoting these numbers.")
    print(f"\nDetails saved to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
