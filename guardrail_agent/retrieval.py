"""Tiny keyword retriever so the project runs with zero external services.
Swap for a vector store (FAISS / pgvector / Chroma) in a real deployment."""
from __future__ import annotations

import re
from pathlib import Path

from .types import Doc

DOCS_DIR = Path(__file__).resolve().parent.parent / "data" / "docs"

STOP = set("""a an the is are was were be been do does did you your yours i my me we our us it its this that these those
what whats how when where why who which can could would should will to of for in on at by and or with about please tell
give much many there their they them any some from as if so but not no yes have has had get got s acme""".split())


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def stems(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if len(w) < 2 or w in STOP:
            continue
        if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
            w = w[:-1]
        out.add(w)
    return out


class Retriever:
    def __init__(self, docs: list[Doc] | None = None, extra_docs: list[Doc] | None = None):
        self.docs = docs if docs is not None else self._load()
        self.docs = self.docs + list(extra_docs or [])

    @staticmethod
    def _load() -> list[Doc]:
        return [Doc(p.stem, p.read_text().strip()) for p in sorted(DOCS_DIR.glob("*.md"))]

    def search(self, query: str, k: int = 3, min_score: int = 1) -> list[Doc]:
        q = stems(_EMAIL.sub(" ", query))  # never use a user's email address as search terms
        scored = [(len(q & stems(d.text)), d) for d in self.docs]
        scored = [(s, d) for s, d in scored if s >= min_score]
        scored.sort(key=lambda x: -x[0])
        return [d for _, d in scored[:k]]
