"""Small, dependency-free text utilities shared by chunking, caching and local answering."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable

_TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)
_WORD_RE = re.compile(r"[a-z0-9]+(?:['\u2019][a-z]+)?")
# Split after ., ! or ? when followed by whitespace and an uppercase letter, digit, quote or
# bracket. Common abbreviations are protected first so "e.g. Foo" is not split.
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])[\"')\]]?\s+(?=[\"'(\[]?[A-Z0-9])")
_ABBREVIATIONS = (
    "e.g.",
    "i.e.",
    "etc.",
    "vs.",
    "mr.",
    "mrs.",
    "ms.",
    "dr.",
    "no.",
    "fig.",
    "approx.",
)
_ABBREV_RE = re.compile(
    "|".join(re.escape(a) for a in _ABBREVIATIONS),
    re.IGNORECASE,
)
_DOT_PLACEHOLDER = "\u2024"  # one-dot leader: looks like '.', never matches the splitter

STOPWORDS = frozenset(
    """a about above after again against all am an and any are as at be because been before
    being below between both but by can could did do does doing down during each few for from
    further had has have having he her here hers herself him himself his how i if in into is it
    its itself just me more most my myself no nor not now of off on once only or other our ours
    ourselves out over own same she should so some such than that the their theirs them
    themselves then there these they this those through to too under until up very was we were
    what when where which while who whom why will with would you your yours yourself
    yourselves""".split()
)


def estimate_tokens(text: str) -> int:
    """Approximate model tokens as words + punctuation marks (close to WordPiece for English)."""
    return len(_TOKEN_RE.findall(text))


def normalize_whitespace(text: str) -> str:
    return " ".join(text.split())


def split_sentences(text: str) -> list[str]:
    text = normalize_whitespace(text)
    if not text:
        return []
    protected = _ABBREV_RE.sub(lambda m: m.group(0).replace(".", _DOT_PLACEHOLDER), text)
    parts = _SENTENCE_BOUNDARY_RE.split(protected)
    return [p.replace(_DOT_PLACEHOLDER, ".").strip() for p in parts if p.strip()]


def words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKC", text).lower()
    return _WORD_RE.findall(folded)


def stem(word: str) -> str:
    """Tiny suffix-stripping stemmer: enough to match 'policies'/'policy', 'running'/'run'."""
    for suffix, replacement in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: len(word) - len(suffix)] + replacement
    return word


def content_terms(text: str) -> list[str]:
    return [stem(w) for w in words(text) if w not in STOPWORDS and len(w) > 1]


def term_vector(texts: Iterable[str]) -> Counter[str]:
    vector: Counter[str] = Counter()
    for text in texts:
        vector.update(content_terms(text))
    return vector


def cosine(a: Counter[str], b: Counter[str]) -> float:
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    dot = sum(count * b[term] for term, count in a.items())
    norm = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
    return dot / norm if norm else 0.0


def normalize_question(question: str) -> str:
    """Canonical form used for cache keys: case, punctuation and spacing don't matter."""
    folded = unicodedata.normalize("NFKC", question).casefold()
    return " ".join(words(folded))
