"""Triage rules: request-type classification, risk routing, domain inference.

The agent must decide for every ticket:
  * ``status``        -> ``replied`` (safe, grounded answer) or ``escalated``
  * ``request_type``  -> ``product_issue`` | ``feature_request`` | ``bug`` | ``invalid``
  * ``product_area``  -> most relevant support category

The decision logic is deterministic and explicit. It prioritises safety:
  1. Clearly out-of-scope / malicious / irretrievably vague input  -> ``invalid``
  2. Requests that imply a privileged action we cannot perform
     (score changes, seat restoration, refund/ban, certificate edits) -> escalate
  3. High-risk signals that must reach a human (payment, PII, minors,
     self-harm, legal) -> escalate
  4. Otherwise -> reply, grounded in the corpus, when a sufficiently similar
     document exists; else escalate rather than inventing policy.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Company -> corpus domain.
# ---------------------------------------------------------------------------
_COMPANY_TO_DOMAIN = {
    "hackerrank": "hackerrank",
    "claude": "claude",
    "visa": "visa",
}

_DOMAIN_KEYWORDS = {
    "hackerrank": [
        "hackerrank", "coding", "challenge", "assessment", "test", "problem",
        "submission", "editor", "interview", "codepair", "certificate", "score",
        "recruiter", "apply tab", "resume builder", "mock interview",
    ],
    "claude": [
        "claude", "anthropic", "prompt", "model", "context", "message",
        "conversation", "token", "api", "artifact", "project", "chat", "bedrock",
        "lti", "claudebot", "workspace",
    ],
    "visa": [
        "visa", "card", "payment", "transaction", "merchant", "charge", "emi",
        "credit", "debit", "bank", "fraud", "dispute", "currency", "rewards",
        "wallet", "cash", "cheque", "traveller", "issuer", "chargeback",
    ],
}


def infer_domain(company: str, text: str) -> str | None:
    """Map the explicit company label, else infer from content keywords."""
    comp = (company or "").strip().lower()
    if comp in _COMPANY_TO_DOMAIN and comp != "none":
        return _COMPANY_TO_DOMAIN[comp]

    low = (text or "").lower()
    best, best_score = None, 0
    for domain, kws in _DOMAIN_KEYWORDS.items():
        score = sum(1 for kw in kws if kw in low)
        if score > best_score:
            best, best_score = domain, score
    return best if best_score > 0 else None


# ---------------------------------------------------------------------------
# Out-of-scope / invalid detection.
# ---------------------------------------------------------------------------
_OUT_OF_SCOPE_PATTERNS = [
    r"iron man", r"who is", r"what is the name of", r"write me a poem",
    r"delete all files", r"give me the code to", r"rm -rf", r"format my",
    r"are you a robot", r"tell me a joke", r"summary of this chat",
]

# A ticket that is *only* a greeting/thank-you carries no support need. We only
# treat it as invalid when it is genuinely just a salutation (short), so normal
# tickets that start with "Hello" or end with "Thanks" are NOT flagged.
_PURE_GREETING_RE = re.compile(
    r"^\s*(thanks|thank you|hello|hi|good (morning|afternoon|evening)|hey)"
    r"[\s,!.?]*\s*$", re.I)
_SHORT_GREETING_RE = re.compile(r"thanks|thank you|hello\b|hi\b", re.I)



_INJECTION_PATTERNS = [
    r"internal rules", r"internal logic", r"documents retrieved",
    r"exact logic", r"show me the system prompt", r"reveal your",
    r"list all the steps you use", r"how do you decide",
    r"dumps the", r"affiche toutes", r"logic exacte",
]

_MALICIOUS_PATTERNS = [
    r"delete all files", r"wipe the system", r"destroy the database",
    r"steal", r"hack into", r"bypass security",
]

_VAGUE_PATTERNS = [
    r"it'?s not working", r"help", r"not working, help", r"please help",
    r"what happened", r"something is wrong",
]


# ---------------------------------------------------------------------------
# Privileged-action requests (we can't perform these -> escalate).
# ---------------------------------------------------------------------------
_ACTION_ESCALATION_PATTERNS = [
    # directly performing an outcome that requires privileged access
    (r"increase my score", "score/result modification"),
    (r"review my answers", "score/result review"),
    (r"tell the company to", "contacting a third party"),
    (r"move me to the next round", "contacting a third party"),
    (r"restore my access", "account access restoration"),
    (r"restore my seat", "workspace seat restoration"),
    (r"restore.*even though i am not", "privileged access change"),
    (r"make visa refund me", "billing/refund action"),
    (r"ban the seller", "merchant enforcement"),
    (r"refund me", "billing/refund action"),
    (r"give me my money", "billing/refund action"),
    (r"give me the refund", "billing/refund action"),
    (r"update (my|the) (name|certificate)", "certificate/data modification"),
    (r"(name|certificate).*(incorrect|wrong|incorrectly)", "certificate/data modification"),
    (r"pause our subscription", "subscription/billing change"),
    (r"change my score", "score modification"),
    (r"please make.*refund", "billing/refund action"),
]

# ---------------------------------------------------------------------------
# High-risk signals -> escalate (with the exception of pure "how to report"
# guidance which we ground in the corpus, e.g. lost/stolen card).
# ---------------------------------------------------------------------------
_RISK_PATTERNS = [
    (r"\bpayment\b", "payment"),
    (r"\bbilling\b", "billing"),
    (r"invoice", "billing"),
    (r"refund", "refund"),
    (r"chargeback", "billing"),
    (r"order id", "payment"),
    (r"\b(card|pan)\s*(number|no)?\b[:\s]*\d", "payment-card data"),
    (r"\b\d{13,19}\b", "payment-card number"),
    (r"identity (has been|was|is)?\s*stolen", "identity theft"),
    (r"my identity", "identity theft"),
    (r"fraud", "fraud"),
    (r"security vulnerability", "security"),
    (r"bug bounty", "security"),
    (r"\bminor\b", "minor"),
    (r"under 18", "minor"),
    (r"suicid", "self-harm"),
    (r"self[- ]harm", "self-harm"),
    (r"medical", "medical"),
    (r"legal", "legal"),
    (r"law enforcement", "legal"),
    (r"gdp[ar]", "legal/regulatory"),
]

# ---------------------------------------------------------------------------
# Outage / system-wide bug -> escalate to engineering.
# ---------------------------------------------------------------------------
_OUTAGE_PATTERNS = [
    r"stopped working completely", r"none of the submissions",
    r"site is down", r"all requests are failing", r"all requests to .* failing",
    r"not working across", r"is down", r"resume builder is down",
    r"completely, all requests",
]

# ---------------------------------------------------------------------------
# How-to / FAQ indicators (favours a reply).
# ---------------------------------------------------------------------------
_HOW_TO_PATTERNS = [
    r"how (do|can|to|should)", r"what (is|are|do)", r"where (do|can|is)",
    r"why (do|is|does)", r"can you (tell|explain|confirm)", r"please (tell|advise)",
    r"steps", r"instructions", r"guide", r"is it possible", r"best practice",
    r"can we (extend|increase)", r"can i (add|delete|remove|create|change|set)",
    r"how to", r"next steps", r"report", r"set up", r"setup", r"configure",
]


def _has_any(text: str, patterns: list[str]) -> list[str]:
    return [pat for pat in patterns if re.search(pat, text, flags=re.I)]


def _is_pure_greeting(text: str) -> bool:
    """True only when the whole ticket is a salutation/thanks with no request."""
    stripped = re.sub(r"[^a-z0-9 ,.!?']", " ", (text or "").lower()).strip()
    if not stripped:
        return False
    if _PURE_GREETING_RE.match(text):
        return True
    words = [w for w in re.split(r"\s+", stripped) if w]
    if len(words) <= 5 and _SHORT_GREETING_RE.search(text):
        return True
    return False


def detect_out_of_scope(text: str) -> str | None:
    """Return a reason if the ticket is out-of-scope / invalid, else None."""
    low = text.lower()
    for pat in _OUT_OF_SCOPE_PATTERNS:
        if re.search(pat, text, flags=re.I):
            return "out-of-scope"
    if _is_pure_greeting(text):
        return "out-of-scope"
    # A ticket asking us to reveal our internal decision process is not a
    # legitimate support question.
    if _has_any(text, _INJECTION_PATTERNS) and not _has_any(text, [
        r"blocked", r"carte", r"card", r"travel", r"unlock",
    ]):
        return "prompt-injection"
    return None


def detect_vague(text: str) -> bool:
    """True if the ticket carries essentially no actionable detail."""
    return bool(_has_any(text, _VAGUE_PATTERNS))


def classify_request_type(text: str) -> str:
    """Best-fit request classification using explicit heuristics."""
    low = (text or "").lower()

    # Out-of-scope / malicious / pure greeting -> invalid.
    if detect_out_of_scope(text):
        return "invalid"

    # Feature request signals.
    if re.search(r"can we extend|can you add|feature request|it would be (nice|great)"
                 r"|could you (add|enable)|ability to (add|extend)|please add \w+",
                 text, re.I):
        return "feature_request"

    # Bug / outage signals.
    if re.search(r"not working|doesn'?t work|is down|broken|error|failed|fails"
                 r"|crash|stopped|bug|instead|issue|unable to|can'?t see"
                 r"|blocker|showing error|not receiving|not functioning",
                 text, re.I):
        return "bug"

    # Default for legitimate support requests.
    return "product_issue"


def decide_escalation(text: str) -> str | None:
    """Return an escalation reason if the ticket must go to a human, else None."""
    # Privileged actions first (we cannot perform them).
    for pat, reason in _ACTION_ESCALATION_PATTERNS:
        if re.search(pat, text, flags=re.I):
            return reason

    # High-risk signals (financial / PII / identity / minors / self-harm ...).
    for pat, reason in _RISK_PATTERNS:
        if re.search(pat, text, flags=re.I):
            return reason

    # System-wide outage -> engineering.
    for pat in _OUTAGE_PATTERNS:
        if re.search(pat, text, flags=re.I):
            return "system-outage"

    return None


def is_how_to(text: str) -> bool:
    """True if the ticket reads like a help/FAQ question we can ground."""
    return bool(_has_any(text, _HOW_TO_PATTERNS))


def is_malicious(text: str) -> bool:
    return bool(_has_any(text, _MALICIOUS_PATTERNS))
