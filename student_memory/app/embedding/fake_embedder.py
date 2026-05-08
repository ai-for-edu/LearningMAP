"""Deterministic ``Embedder`` for tests and `SM_USE_FAKE_EMBEDDER=1` runs.

Maps a curated set of keywords to orthogonal one-hot unit vectors so
tests can pin similarity values exactly. Texts containing none of the
known keywords hash to a stable unit vector via SHA-256, which keeps
unknown texts far from the curated basis but still deterministic.

This implementation has zero ML dependencies — only ``hashlib`` and
``numpy``. Importing it must NEVER pull torch or sentence-transformers.
It lives under ``app.embedding`` (not under ``tests/``) so the wire-up
in ``app.embedding.factory`` can import it from a path that survives a
``pip install .`` deploy where ``tests/`` is not packaged.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

import numpy as np

from app.embedding.base import Embedder

_KEYWORDS: tuple[str, ...] = (
    "quadratic",
    "discriminant",
    "calculus",
    "derivative",
    "geometry",
    "pythagoras",
    "algebra",
    "trigonometry",
)
"""Each keyword owns one dimension in the basis. Order matters — index in
this tuple equals the basis dimension index. Default test dimension is
``len(_KEYWORDS) + 8 = 16`` so unknown-text hashes have somewhere to go
without colliding with the curated basis.
"""

_DEFAULT_DIM = 16
_TOKEN_RE = re.compile(r"[a-zA-Z\u4e00-\u9fff]+")


class FakeEmbedder(Embedder):
    """Deterministic, model-free embedder for tests."""

    def __init__(self, dimension: int = _DEFAULT_DIM, model_name: str = "fake") -> None:
        if dimension < len(_KEYWORDS):
            raise ValueError(
                f"FakeEmbedder dimension must be >= {len(_KEYWORDS)} "
                f"to fit the curated basis, got {dimension}"
            )
        self._dim = dimension
        self._model_name = model_name

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return self._model_name

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        if len(texts) == 0:
            return np.zeros((0, self._dim), dtype=np.float32)
        out = np.zeros((len(texts), self._dim), dtype=np.float32)
        for i, text in enumerate(texts):
            out[i] = self._encode_one(text)
        return out

    # ------------------------------------------------------------------
    def _encode_one(self, text: str) -> np.ndarray:
        v = np.zeros(self._dim, dtype=np.float32)
        if not text:
            # Empty string → first hash slot. Still unit vector.
            v[len(_KEYWORDS)] = 1.0
            return v
        lower = text.lower()
        hits = 0
        for idx, kw in enumerate(_KEYWORDS):
            if kw in lower:
                v[idx] = 1.0
                hits += 1
        if hits == 0:
            # Hash unknown text into one of the non-keyword slots so
            # unknown texts stay orthogonal to the curated basis.
            tail_slots = self._dim - len(_KEYWORDS)
            if tail_slots <= 0:  # pragma: no cover - guarded by __init__
                tail_slots = 1
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            slot = digest[0] % tail_slots
            v[len(_KEYWORDS) + slot] = 1.0
            return v
        # L2-normalize so cosine similarity equals dot product.
        norm = float(np.linalg.norm(v))
        if norm > 0:
            v /= norm
        return v
