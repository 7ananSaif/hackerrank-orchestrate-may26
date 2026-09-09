"""Command-line entry point for the HackerRank Orchestrate support agent.

The challenge requires a **terminal-based** agent that reads
``support_tickets/support_tickets.csv`` and writes ``support_tickets/output.csv``
with the columns:

    issue, subject, company, response, product_area, status, request_type, justification

where ``status`` is ``replied`` | ``escalated`` and ``request_type`` is
``product_issue`` | ``feature_request`` | ``bug`` | ``invalid``.

Usage
-----
    python main.py                          # uses the repo's own data/ + support_tickets/
    python main.py --corpus-dir ../data --tickets ../support_tickets/support_tickets.csv
    python main.py --out ../support_tickets/output.csv --log ../log.txt

Everything is deterministic; no external network or LLM API is required.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path

from corpus_engine import CorpusIndex, load_corpus
from rules import (classify_request_type, decide_escalation, detect_out_of_scope,
                   detect_vague, infer_domain, is_how_to, is_malicious)
from response import (escalation_justification, escalation_message,
                      invalid_justification, need_more_info_message,
                      out_of_scope_message, reply_justification, reply_message)

# ---------------------------------------------------------------------------
# Tunables.
# ---------------------------------------------------------------------------
MIN_SCORE = 0.10   # below this a retrieved doc is not trusted to ground a reply
TOP_K = 4

_OUTPUT_COLUMNS = [
    "issue", "subject", "company", "response",
    "product_area", "status", "request_type", "justification",
]

# Hard-escalation reasons: actions/payments we can never perform automatically.
_HARD_ESCALATIONS = {
    "score/result modification", "score/result review", "contacting a third party",
    "account access restoration", "workspace seat restoration", "privileged access change",
    "billing/refund action", "merchant enforcement", "certificate/data modification",
    "subscription/billing change", "score modification",
    "payment", "billing", "refund", "chargeback",
    "payment-card data", "payment-card number",
    "minor", "self-harm", "medical", "legal", "legal/regulatory",
    "system-outage",
}


def _default_product_area(domain: str | None) -> str:
    return {
        "hackerrank": "general_help",
        "claude": "general",
        "visa": "general_support",
    }.get(domain or "", "general")


def _handle_ticket(issue: str, subject: str, company: str,
                   index: CorpusIndex) -> dict[str, str]:
    text = f"{issue} {subject}".strip()
    domain = infer_domain(company, text)
    request_type = classify_request_type(text)

    esc = decide_escalation(text)
    oos = detect_out_of_scope(text)
    malicious = is_malicious(text)
    vague = detect_vague(text)
    howto = is_how_to(text)

    hits = index.search(text, top_k=TOP_K, domain=domain, min_score=MIN_SCORE)
    grounded = bool(hits) and hits[0].score >= MIN_SCORE
    top_score = hits[0].score if hits else 0.0
    product_area = (hits[0].chunk.product_area if hits
                    else _default_product_area(domain))

    # 1) Malicious / out-of-scope -> invalid + replied (safe refusal).
    if malicious or oos:
        return {
            "response": out_of_scope_message(),
            "product_area": product_area,
            "status": "replied",
            "request_type": "invalid",
            "justification": invalid_justification(oos or "malicious"),
        }

    # 2) Hard escalations (privileged actions, payments, data modification, ...).
    if esc and esc in _HARD_ESCALATIONS:
        return {
            "response": escalation_message(esc, domain or "unknown"),
            "product_area": product_area,
            "status": "escalated",
            "request_type": request_type,
            "justification": escalation_justification(
                esc, domain or "unknown", request_type, product_area),
        }

    # 3) Soft escalations (fraud / identity / security reporting). These may be
    #    answerable when the corpus gives concrete public guidance and the ticket
    #    reads like a "how to report" question; otherwise escalate.
    if esc:
        if grounded and howto:
            return {
                "response": reply_message(text, hits, product_area, request_type),
                "product_area": product_area,
                "status": "replied",
                "request_type": request_type,
                "justification": reply_justification(hits, product_area,
                                                     request_type, top_score),
            }
        return {
            "response": escalation_message(esc, domain or "unknown"),
            "product_area": product_area,
            "status": "escalated",
            "request_type": request_type,
            "justification": escalation_justification(
                esc, domain or "unknown", request_type, product_area),
        }

    # 4) No escalation signal.
    if grounded:
        return {
            "response": reply_message(text, hits, product_area, request_type),
            "product_area": product_area,
            "status": "replied",
            "request_type": request_type,
            "justification": reply_justification(hits, product_area,
                                                 request_type, top_score),
        }

    # 5) Not grounded. If it is a genuine how-to we cannot answer, ask for detail;
    #    otherwise escalate rather than inventing a policy.
    if howto and not vague:
        return {
            "response": need_more_info_message(),
            "product_area": product_area,
            "status": "replied",
            "request_type": request_type,
            "justification": (
                "No sufficiently similar corpus document found; requested "
                "clarification instead of guessing."),
        }
    return {
        "response": escalation_message("no reliable grounding", domain or "unknown"),
        "product_area": product_area,
        "status": "escalated",
        "request_type": request_type,
        "justification": (
            "No grounded answer available in the provided corpus; escalated to a "
            "human rather than generating unsupported content."),
    }


# ---------------------------------------------------------------------------
# CSV I/O.
# ---------------------------------------------------------------------------
def _row_value(row: dict[str, str], *keys: str) -> str:
    norm = {k.strip().lower(): k for k in row}
    for key in keys:
        if key.lower() in norm:
            val = row[norm[key.lower()]]
            return "" if val is None else str(val).strip()
    return ""


def _load_tickets(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            return []
        return [dict(row) for row in reader]


def run(corpus_dir: Path, tickets: Path, out: Path, log: Path) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(log), level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        encoding="utf-8", force=True,
    )

    print(f"[info] Loading corpus from {corpus_dir}")
    chunks = load_corpus(corpus_dir)
    if not chunks:
        print("[error] No corpus documents found.", file=sys.stderr)
        return 1
    index = CorpusIndex(chunks)
    print(f"[info] Indexed {len(chunks)} chunk(s) from "
          f"{len(set(c.doc_id for c in chunks))} article(s).")

    if not tickets.exists():
        print(f"[error] Tickets file not found: {tickets}", file=sys.stderr)
        return 1
    rows = _load_tickets(tickets)
    print(f"[info] Loaded {len(rows)} ticket(s).")

    results: list[dict[str, str]] = []
    for i, row in enumerate(rows, start=1):
        issue = _row_value(row, "Issue", "issue", "Description", "description")
        subject = _row_value(row, "Subject", "subject")
        company = _row_value(row, "Company", "company", "Domain", "domain")

        decision = _handle_ticket(issue, subject, company, index)
        out_row = {
            "issue": issue,
            "subject": subject,
            "company": company,
            "response": decision["response"],
            "product_area": decision["product_area"],
            "status": decision["status"],
            "request_type": decision["request_type"],
            "justification": decision["justification"],
        }
        results.append(out_row)

        logging.info("=== Ticket %d ===", i)
        logging.info("ISSUE      : %s", issue)
        logging.info("SUBJECT    : %s", subject)
        logging.info("COMPANY    : %s", company)
        logging.info("DOMAIN     : %s", infer_domain(company, f"{issue} {subject}"))
        logging.info("TYPE       : %s", decision["request_type"])
        logging.info("AREA       : %s", decision["product_area"])
        logging.info("STATUS     : %s", decision["status"])
        logging.info("JUSTIF     : %s", decision["justification"])
        logging.info("RESPONSE   :\n%s", decision["response"].replace("\n", "\n  "))
        logging.info("---")

        print(f"[{i}/{len(rows)}] {decision['status']:9s} "
              f"{decision['request_type']:15s} {decision['product_area']:24s}")

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(results)
    print(f"[done] Wrote {len(results)} row(s) to {out}")
    print(f"[done] Log written to {log}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Support triage agent (HackerRank/Claude/Visa).")
    parser.add_argument("--corpus-dir", default=Path(__file__).parent.parent / "data",
                        type=Path)
    parser.add_argument("--tickets",
                        default=Path(__file__).parent.parent / "support_tickets" / "support_tickets.csv",
                        type=Path)
    parser.add_argument("--out",
                        default=Path(__file__).parent.parent / "support_tickets" / "output.csv",
                        type=Path)
    parser.add_argument("--log", default=Path(__file__).parent.parent / "log.txt",
                        type=Path)
    args = parser.parse_args(argv)
    return run(args.corpus_dir, args.tickets, args.out, args.log)


if __name__ == "__main__":
    sys.exit(main())
