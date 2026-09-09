# Support Triage Agent — HackerRank Orchestrate

A terminal-based agent that triages support tickets across three ecosystems —
**HackerRank**, **Claude**, and **Visa** — using **only the support corpus**
shipped in the repository's `data/` directory.

## How it works

The agent is a deterministic, offline RAG-style pipeline (no external LLM API,
no network, no secrets). For each ticket it:

1. **Retrieves** the most relevant support articles using a lightweight
   TF-IDF + cosine-similarity index over heading-delimited chunks of the corpus.
   The index weights the article title and heading (the most discriminative
   text) and is restricted to the ticket's company domain when known.
2. **Classifies** the request type into `product_issue`, `feature_request`,
   `bug`, or `invalid` using explicit keyword heuristics.
3. **Routes for safety**: it escalates privileged-action requests (score
   changes, seat/access restoration, refunds, certificate edits, subscription
   changes), financial/PII/identity/security signals, system-wide outages, and
   any ticket the corpus cannot confidently answer. It replies when it can
   ground a safe, helpful answer in the corpus.
4. **Generates** a reply by extracting text verbatim from the retrieved articles
   (never inventing policy), plus a concise `justification` tracing the decision
   to the retrieved document.

## Files

| File              | Purpose                                                          |
| ----------------- | ---------------------------------------------------------------- |
| `main.py`         | Entry point: reads `support_tickets.csv`, writes `output.csv` + log |
| `corpus_engine.py`| Corpus loading, chunking, TF-IDF index, and retrieval           |
| `rules.py`        | Request-type classification, safety/escalation routing, domain inference |
| `response.py`     | Grounded reply / escalation / out-of-scope message builders      |

## Requirements

- Python 3.10+
- Standard library only. No third-party dependencies.

## Run

From the repository root (or with `--corpus-dir` / `--tickets` pointing at
your copies of `data/` and `support_tickets.csv`):

```bash
# Uses the repo's own data/ + support_tickets/support_tickets.csv
cd code
python main.py

# Or explicitly point at the inputs
python main.py \
  --corpus-dir ../data \
  --tickets ../support_tickets/support_tickets.csv \
  --out ../support_tickets/output.csv \
  --log ../log.txt
```

### Output schema

`output.csv` uses exactly the columns required by the problem statement:

| Column          | Values                                              |
| --------------- | --------------------------------------------------- |
| `status`        | `replied`, `escalated`                              |
| `request_type`  | `product_issue`, `feature_request`, `bug`, `invalid` |
| `product_area`  | most relevant support category                      |
| `response`      | user-facing answer grounded in the corpus           |
| `justification` | concise explanation of the decision                 |

The input `issue`, `subject`, and `company` columns are carried through so the
output rows line up 1:1 with the input.

## Determinism & grounding

- Retrieval/classification are fully deterministic — the same input always
  yields the same output.
- Every `response` is built from copied corpus text; if no sufficiently similar
  article exists the agent **escalates** rather than guessing.
- Secrets are never required; there are no API keys or env vars to configure.
