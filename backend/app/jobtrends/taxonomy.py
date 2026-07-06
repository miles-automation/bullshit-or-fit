"""Keyword taxonomy for the jobtrends extract stage.

Canonical keyword -> alias patterns, matched case-insensitively against a post's
raw text. Presence is counted per-post (a keyword hit once per post, no matter
how many times it appears). Add rows freely and re-run `extract` — nothing here
touches the raw corpus, so the taxonomy can evolve without re-fetching.

Ported from the reference jobtrends.py and grouped for readability. `category`
is advisory metadata (handy for future rollups); the extract stage keys only on
the canonical keyword.
"""

from __future__ import annotations

# category -> {canonical keyword: [alias regex fragments]}
TAXONOMY: dict[str, dict[str, list[str]]] = {
    "language": {
        "python": [r"python"],
        "typescript": [r"typescript", r"\bts\b"],
        "javascript": [r"javascript", r"\bjs\b", r"node(?:\.js)?"],
        "go": [r"\bgo\b", r"golang"],
        "rust": [r"\brust\b"],
        "java": [r"\bjava\b"],
        "ruby": [r"\bruby\b", r"rails"],
        "elixir": [r"elixir"],
        "scala": [r"\bscala\b"],
        "cpp": [r"\bc\+\+\b", r"\bcpp\b"],
    },
    "framework": {
        "fastapi": [r"fastapi", r"fast api"],
        "django": [r"django"],
        "flask": [r"flask"],
        "react": [r"react(?:\.js)?", r"react native"],
        "vue": [r"\bvue(?:\.js)?\b"],
        "nextjs": [r"next\.?js"],
    },
    "data": {
        "postgres": [r"postgres", r"postgresql", r"\bpsql\b"],
        "mysql": [r"\bmysql\b"],
        "mongodb": [r"mongo(?:db)?"],
        "redis": [r"\bredis\b"],
        "kafka": [r"\bkafka\b"],
    },
    "infra": {
        "kubernetes": [r"kubernetes", r"\bk8s\b"],
        "docker": [r"\bdocker\b"],
        "aws": [r"\baws\b"],
        "gcp": [r"\bgcp\b", r"google cloud"],
        "azure": [r"\bazure\b"],
        "terraform": [r"terraform"],
    },
    "ai": {
        "mcp": [r"\bmcp\b", r"model context protocol"],
        "llm": [r"\bllms?\b", r"large language model"],
        "rag": [r"\brag\b", r"retrieval[- ]augmented"],
        "agents": [r"\bagent(?:s|ic)?\b"],
        "claude": [r"claude"],
        "openai": [r"openai", r"\bgpt-?\d?\b"],
        "pytorch": [r"pytorch", r"\btensorflow\b"],
    },
    "role": {
        "remote": [r"remote"],
        "onsite": [r"onsite", r"on-site", r"in[- ]office"],
        "hybrid": [r"hybrid"],
        "staff": [r"\bstaff\b"],
        "principal": [r"principal"],
        "senior": [r"\bsenior\b", r"\bsr\.?\b"],
        "founding": [r"founding (?:engineer|eng)"],
        "fulltime": [r"full[- ]time", r"\bft\b"],
        "contract": [r"\bcontract\b", r"contractor"],
    },
}


def flat_keywords() -> dict[str, list[str]]:
    """Flatten the taxonomy to {canonical keyword: [alias fragments]}."""
    flat: dict[str, list[str]] = {}
    for group in TAXONOMY.values():
        flat.update(group)
    return flat


def keyword_category() -> dict[str, str]:
    """{canonical keyword: category} for optional rollups."""
    return {kw: cat for cat, group in TAXONOMY.items() for kw in group}
