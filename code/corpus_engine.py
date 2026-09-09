"""Corpus loading, chunking, and retrieval for the support triage agent.

The challenge requires answers to be grounded *only* in the provided support
corpus. This module loads every markdown article under ``data/``, parses its
front-matter, splits the body into heading-delimited chunks, and builds a
TF-IDF + cosine-similarity index over those chunks. Retrieval is restricted to
the ticket's company domain when known, which keeps `product_area` and the
response text anchored to the right ecosystem.

Design notes
------------
* No external ML/vector-db dependencies -> fully reproducible offline.
* Chunking by markdown heading is crucial: articles are long, but each section
  answers one specific question, so fine-grained chunks improve top-k precision.
* The front-matter ``breadcrumbs`` is used as the ground-truth category label,
  which becomes the ``product_area`` output column.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Tokenization (light stemmer + stopwords, no external deps).
# ---------------------------------------------------------------------------
_WORD_RE = re.compile(r"[A-Za-z0-9]+")

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "he", "in", "is", "it", "its", "of", "on", "or", "that", "the", "their",
    "this", "to", "was", "we", "were", "will", "with", "you", "your", "not",
    "no", "can", "do", "does", "did", "have", "i", "my", "me", "our", "us",
    "if", "then", "so", "but", "just", "please", "help", "need", "want",
    "there", "them", "they", "these", "those", "also", "more", "very",
}

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def _stem(word: str) -> str:
    """Light suffix stripping so 'charge'/'charged' cross-match."""
    if len(word) <= 3:
        return word
    if word.endswith("ing") and len(word) >= 6:
        word = word[:-3]
    elif word.endswith("ed") and len(word) >= 5:
        word = word[:-2]
    elif word.endswith("ies") and len(word) >= 5:
        word = word[:-3] + "y"
    elif word.endswith("es") and len(word) >= 5 and not word.endswith(("ss", "us")):
        word = word[:-2]
    elif word.endswith("s") and len(word) >= 4 and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    if len(word) > 3 and word.endswith("e"):
        word = word[:-1]
    return word


def tokenize(text: str) -> list[str]:
    """Lowercase, stem, and drop stopwords/short tokens."""
    raw = [t.lower() for t in _WORD_RE.findall(text or "")]
    return [_stem(t) for t in raw if len(t) > 2 and t not in _STOPWORDS]


# ---------------------------------------------------------------------------
# Front-matter + markdown cleaning.
# ---------------------------------------------------------------------------
def _parse_frontmatter(raw: str) -> tuple[dict, str]:
    """Return (meta_dict, body) for a raw file with '---' front-matter."""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", raw, flags=re.S)
    if not m:
        return {}, raw
    fm = m.group(1)
    body = raw[m.end():]

    meta: dict = {}
    breadcrumbs: list[str] = []
    current_key: str | None = None
    for line in fm.splitlines():
        stripped = line.rstrip()
        if current_key == "breadcrumbs" and stripped.lstrip().startswith("-"):
            item = stripped.lstrip()[1:].strip().strip('"').strip("'")
            breadcrumbs.append(item)
            continue
        if ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            meta[key] = val
            current_key = key
    if breadcrumbs:
        meta["breadcrumbs"] = breadcrumbs
    return meta, body


def _clean_line(line: str) -> str:
    """Strip markdown decoration from a single line."""
    # images entirely removed
    if line.lstrip().startswith("!["):
        return ""
    # links keep the label, drop the URL
    line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)
    line = re.sub(r"https?://\S+", " ", line)
    line = re.sub(r"\[email[^\]]*\]", " ", line)
    line = re.sub(r"/cdn-cgi/l/email-protection[^\s]*", " ", line)
    # heading/list markers
    line = re.sub(r"^#{1,6}\s*", "", line)
    line = re.sub(r"^\s*[-*+]\s+", "", line)
    line = re.sub(r"^\s*\d+\.\s+", "", line)
    line = re.sub(r"^>\s*", "", line)  # blockquote marker
    # drop pure markdown/horizontal-rule noise
    if re.fullmatch(r"[-_*]{3,}", line.strip()):
        return ""
    return line.strip()


def _chunk_body(body: str) -> list[tuple[str, str]]:
    """Split a cleaned body into (heading, text) sections on markdown headings."""
    sections: list[tuple[str, str]] = []
    current_heading = ""
    current_lines: list[str] = []

    def flush() -> None:
        text = "\n".join(current_lines).strip()
        if text:
            sections.append((current_heading, text))

    for raw_line in body.splitlines():
        m = _HEADING_RE.match(raw_line)
        if m:
            flush()
            current_heading = _clean_line(raw_line)
            current_lines = []
        else:
            cleaned = _clean_line(raw_line)
            if cleaned:
                current_lines.append(cleaned)
    flush()
    return sections


# ---------------------------------------------------------------------------
# Product-area mapping (category -> human/rubric-friendly label).
# ---------------------------------------------------------------------------
_VISA_DOC_AREA_HINTS = {
    "travel": "travel_support",
    "cheque": "travel_support",
    "fraud": "fraud_security",
    "dispute": "dispute_resolution",
    "security": "security",
    "data-secur": "security",
    "charge": "dispute_resolution",
    "rule": "card_usage",
}

_HACKERRANK_AREA = {
    "screen": "screen",
    "hackerrank_community": "community",
    "general-help": "general_help",
    "interviews": "interviews",
    "integrations": "integrations",
    "library": "library",
    "settings": "settings",
    "skillup": "skillup",
    "chakra": "chakra",
    "engage": "engage",
    "uncategorized": "general",
}

_CLAUDE_AREA = {
    "privacy-and-legal": "privacy",
    "team-and-enterprise-plans": "team_and_enterprise_plans",
    "claude-api-and-console": "api_and_console",
    "amazon-bedrock": "amazon_bedrock",
    "claude-code": "claude_code",
    "claude-for-education": "claude_for_education",
    "claude-for-government": "claude_for_government",
    "claude-for-nonprofits": "claude_for_nonprofits",
    "claude-in-chrome": "claude_in_chrome",
    "claude-desktop": "claude_desktop",
    "claude-mobile-apps": "claude_mobile_apps",
    "connectors": "connectors",
    "identity-management-sso-jit-scim": "identity_management",
    "safeguards": "safeguards",
    "pro-and-max-plans": "pro_and_max_plans",
}


def map_domain(path: Path) -> str:
    """Return the ecosystem (hackerrank / claude / visa) from the corpus path."""
    for part in path.parts:
        if part in ("hackerrank", "claude", "visa"):
            return part
    return "unknown"


def map_product_area(domain: str, rel_parts: list[str], slug: str = "") -> str:
    """Map a document's category path to a concise product-area label."""
    if domain == "hackerrank":
        top = rel_parts[0] if rel_parts else ""
        return _HACKERRANK_AREA.get(top, "general")

    if domain == "claude":
        # 'claude/claude/<leaf>/...' -> use the leaf category for the top 'claude'
        # bucket so we surface e.g. 'conversation_management' for delete/rename.
        if len(rel_parts) >= 2 and rel_parts[0] == "claude":
            leaf = rel_parts[1]
            return leaf.replace("-", "_")
        top = rel_parts[0] if rel_parts else ""
        return _CLAUDE_AREA.get(top, top.replace("-", "_"))

    if domain == "visa":
        slug_low = slug.lower()
        # leaf category is more specific (consumer / small-business)
        joined = " ".join(rel_parts).lower()
        for hint, area in _VISA_DOC_AREA_HINTS.items():
            if hint in slug_low or hint in joined:
                return area
        if "small-business" in joined:
            return "small_business"
        if "consumer" in joined or not joined:
            return "general_support"
        return "general_support"

    return "general"


# ---------------------------------------------------------------------------
# Document model.
# ---------------------------------------------------------------------------
@dataclass
class Doc:
    """A single corpus article (meta + original file content)."""

    doc_id: str            # relative posix path
    domain: str
    title: str
    breadcrumbs: list[str]
    product_area: str
    path: str


@dataclass
class Chunk:
    """A heading-delimited section of an article."""

    chunk_id: str
    doc_id: str
    domain: str
    title: str
    heading: str
    text: str
    product_area: str
    doc_path: str = ""


@dataclass
class Hit:
    """A retrieval result."""

    chunk: Chunk
    score: float


# ---------------------------------------------------------------------------
# Corpus loading.
# ---------------------------------------------------------------------------
def load_corpus(corpus_dir: Path) -> list[Chunk]:
    """Recursively load and chunk every markdown article under ``corpus_dir``."""
    corpus_dir = Path(corpus_dir)
    if not corpus_dir.exists():
        return []

    chunks: list[Chunk] = []
    for path in sorted(corpus_dir.rglob("*.md")):
        try:
            raw = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        meta, body = _parse_frontmatter(raw)
        rel = path.relative_to(corpus_dir)
        rel_parts = list(rel.parts)
        # doc_id: e.g. 'claude/privacy-and-legal/8896518-....md'
        doc_id = rel.as_posix()
        # The first path segment is the domain folder; the rest is the category.
        domain = map_domain(path)
        cat_parts = rel_parts[1:] if rel_parts else []
        title = meta.get("title", path.stem).replace("_", " ").replace("-", " ")
        breadcrumbs = meta.get("breadcrumbs") or []
        slug = path.stem
        product_area = map_product_area(domain, cat_parts, slug)

        for idx, (heading, text) in enumerate(_chunk_body(body)):
            heading = heading or title
            chunk_id = f"{doc_id}#{idx}"
            chunks.append(Chunk(
                chunk_id=chunk_id,
                doc_id=doc_id,
                domain=domain,
                title=title,
                heading=heading,
                text=text,
                product_area=product_area,
                doc_path=str(path),
            ))
    return chunks


# ---------------------------------------------------------------------------
# TF-IDF index.
# ---------------------------------------------------------------------------
@dataclass
class CorpusIndex:
    """Lightweight TF-IDF + cosine-similarity search over chunks."""

    chunks: list[Chunk]
    _idf: dict[str, float] = field(default_factory=dict)
    _vectors: list[dict[str, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._build()

    def _build(self) -> None:
        df: Counter[str] = Counter()
        tfs: list[Counter[str]] = []
        for chunk in self.chunks:
            # The article title and heading are the most discriminative signals
            # (help-centre titles usually paraphrase the exact question), so give
            # the title double weight to pull the right article to the top.
            source = f"{chunk.title} {chunk.title} {chunk.heading} {chunk.text}"
            tf = Counter(tokenize(source))
            tfs.append(tf)
            for term in tf:
                df[term] += 1
        n = len(self.chunks)
        self._idf = {
            term: math.log((1 + n) / (1 + count)) + 1.0
            for term, count in df.items()
        }
        for tf in tfs:
            vec: dict[str, float] = {}
            norm = 0.0
            for term, count in tf.items():
                weight = (1.0 + math.log(count)) * self._idf.get(term, 0.0)
                vec[term] = weight
                norm += weight * weight
            if norm > 0:
                inv = 1.0 / math.sqrt(norm)
                vec = {t: w * inv for t, w in vec.items()}
            self._vectors.append(vec)

    def search(self, query: str, top_k: int = 4,
               domain: str | None = None, min_score: float = 0.0) -> list[Hit]:
        """Return top-k chunks ordered by cosine similarity to the query."""
        if not self.chunks:
            return []

        q_tf = Counter(tokenize(query))
        if not q_tf:
            return []

        q_vec: dict[str, float] = {}
        q_norm = 0.0
        for term, count in q_tf.items():
            weight = (1.0 + math.log(count)) * self._idf.get(term, 0.0)
            q_vec[term] = weight
            q_norm += weight * weight
        if q_norm == 0:
            return []
        inv = 1.0 / math.sqrt(q_norm)
        q_vec = {t: w * inv for t, w in q_vec.items()}

        scored: list[Hit] = []
        for chunk, vec in zip(self.chunks, self._vectors):
            if domain and chunk.domain != domain:
                continue
            if not vec:
                continue
            score = sum(q_vec.get(term, 0.0) * weight for term, weight in vec.items())
            if score > min_score:
                scored.append(Hit(chunk=chunk, score=score))

        scored.sort(key=lambda hit: hit.score, reverse=True)
        return scored[:top_k]
