"""Response generation: grounded replies, escalation, and out-of-scope messages.

Hard constraint from the challenge: answers must be grounded in the provided
support corpus and must not introduce unsupported claims. The ``_grounded``
builder therefore only ever emits text that is copied verbatim from a retrieved
article chunk (with a small, static framing line that contains no new facts).
If retrieval cannot find a sufficiently similar document, we escalate rather
than guess.
"""

from __future__ import annotations

import re

from corpus_engine import Chunk, Hit

# ---------------------------------------------------------------------------
# Text helpers.
# ---------------------------------------------------------------------------

_MAX_BODY_CHARS = 1080


def _trim_sentences(text: str, limit: int = _MAX_BODY_CHARS) -> str:
    """Return ``text`` trimmed at a sentence boundary, favouring full sentences."""
    clean = re.sub(r"[ \t]+", " ", text).strip()
    if len(clean) <= limit:
        return clean
    cut = clean[:limit]
    # Prefer the last sentence end before the cut.
    for sep in (". ", ".\n", "? ", "! "):
        idx = cut.rfind(sep)
        if idx > 40:
            segments = clean[: idx + 1].rsplit("\n", 1)
            return segments[0].strip()
    return cut.rsplit(" ", 1)[0] + "…"


def _strip_markdown(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def _select_answer_text(hits: list[Hit]) -> str:
    """Pick the most useful chunk text from the top retrieval hits.

    We prefer the top chunk, but if it is only a heading/intro or very short we
    merge in the next chunks that belong to the same article to get a complete,
    readable answer.
    """
    if not hits:
        return ""

    top = hits[0]
    if len(top.chunk.text) >= 220:
        return _strip_markdown(top.chunk.text)

    # Otherwise concatenate chunks from the same doc until we have enough.
    combined = [top.chunk.text]
    doc = top.chunk.doc_id
    for hit in hits[1:]:
        if hit.chunk.doc_id == doc and len(" ".join(combined)) < 500:
            combined.append(hit.chunk.text)
    merged = " ".join(t for t in combined if t.strip())
    return _strip_markdown(merged) if merged.strip() else top.chunk.text.strip()


# ---------------------------------------------------------------------------
# Message builders.
# ---------------------------------------------------------------------------

def reply_message(question: str, hits: list[Hit], product_area: str,
                  request_type: str) -> str:
    """Build a corpus-grounded reply."""
    body = _select_answer_text(hits)
    if not body:
        return ("I couldn't find documentation in our knowledge base that covers "
                "this specific question, so I've escalated it to a human support "
                "specialist to make sure you get an accurate answer.")

    if len(body) > _MAX_BODY_CHARS:
        body = _trim_sentences(body, _MAX_BODY_CHARS)

    opener = ("Thanks for reaching out. Here is the relevant information from "
              f"our {product_area.replace('_', ' ')} documentation:")
    return f"{opener}\n\n{body}"


def escalation_message(reason: str, domain: str) -> str:
    """A safe escalation note (no invented policy)."""
    return (
        "Thanks for reaching out. This request touches a sensitive or "
        f"high-risk area ({reason}). I can't resolve it automatically, so I've "
        "escalated it to a human support specialist who can review your account "
        "or situation safely and help you directly."
    )


def out_of_scope_message() -> str:
    """A firm but polite refusal for out-of-scope / irrelevant requests."""
    return ("I'm sorry, this is outside the scope of what I'm able to help with. "
            "Please reach out with a support-related question or concern.")


def need_more_info_message() -> str:
    """When the ticket is too vague to act on, ask for the missing detail."""
    return ("Thanks for reaching out. I'd love to help, but I need a bit more "
            "detail — could you tell me which product you're using, what happened, "
            "and any error message you saw?")


# ---------------------------------------------------------------------------
# Justification builders.
# ---------------------------------------------------------------------------

def reply_justification(hits: list[Hit], product_area: str,
                        request_type: str, top_score: float) -> str:
    if hits:
        doc = hits[0].chunk.doc_id
        return (f"Grounded answer in '{doc}' ({product_area}); "
                f"request type '{request_type}'; top match score {top_score:.2f}. "
                "No sensitive or high-risk content detected.")
    return (f"No sufficiently similar corpus document found ({product_area}); "
            "would reply only if grounded.")


def escalation_justification(reason: str, domain: str, request_type: str,
                             product_area: str) -> str:
    return (f"Escalated due to {reason} in {domain}; requires a human. "
            f"Request type '{request_type}'; product area '{product_area}'. "
            "No safe automated resolution exists.")


def invalid_justification(reason: str = "out-of-scope") -> str:
    return (f"Marked invalid ({reason}); not a legitimate supported support request, "
            "so it is flagged rather than answered with unsupported content.")
