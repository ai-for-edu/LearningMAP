"""Phase 4: hnswlib index wrapper unit tests.

Uses ``InMemoryLabelStore`` and ``FakeEmbedder``-style numpy vectors so no
SQLite or real model is involved. The end-to-end SQLite-backed integration
is exercised in ``test_ingest.py`` and ``test_retriever.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from app.index.hnsw_index import HnswIndex
from app.index.label_store import InMemoryLabelStore
from app.embedding.fake_embedder import FakeEmbedder


def _make_index(tmp_path: Path, *, dim: int = 16) -> HnswIndex:
    idx = HnswIndex(
        dim=dim,
        index_path=tmp_path / "hnsw.bin",
        label_store=InMemoryLabelStore(),
        max_elements=64,
    )
    idx.load_or_init()
    return idx


def test_empty_index_search_returns_empty(tmp_path: Path) -> None:
    idx = _make_index(tmp_path)
    assert idx.count == 0
    out = idx.search(np.ones(16, dtype=np.float32) / 4.0, k=5)
    assert out == []


def test_add_one_and_search_returns_self(tmp_path: Path) -> None:
    idx = _make_index(tmp_path)
    e = FakeEmbedder(dimension=16)
    vec = e.encode(["quadratic equations"])
    idx.add_batch(["m_a"], vec)
    out = idx.search(vec, k=1)
    assert len(out) == 1
    mid, sim = out[0]
    assert mid == "m_a"
    assert sim == pytest.approx(1.0, abs=1e-5)


def test_add_batch_and_search_returns_top_k(tmp_path: Path) -> None:
    idx = _make_index(tmp_path)
    e = FakeEmbedder(dimension=16)
    vectors = e.encode(
        [
            "quadratic equations",
            "calculus derivatives",
            "geometry pythagoras",
        ]
    )
    idx.add_batch(["m_a", "m_b", "m_c"], vectors)
    assert idx.count == 3

    query = e.encode(["quadratic"])
    out = idx.search(query, k=3)
    assert len(out) == 3
    assert out[0][0] == "m_a"
    assert out[0][1] == pytest.approx(1.0, abs=1e-5)


def test_save_and_reload_roundtrip(tmp_path: Path) -> None:
    e = FakeEmbedder(dimension=16)
    vectors = e.encode(["quadratic", "calculus", "geometry"])

    idx = _make_index(tmp_path)
    idx.add_batch(["m_a", "m_b", "m_c"], vectors)
    idx.save()

    # New instance pointing at the same file. Reuse label store contents.
    store = InMemoryLabelStore()
    # Replicate the mapping the first instance assigned.
    store.assign("m_a")
    store.assign("m_b")
    store.assign("m_c")
    idx2 = HnswIndex(
        dim=16,
        index_path=tmp_path / "hnsw.bin",
        label_store=store,
        max_elements=64,
    )
    idx2.load_or_init()
    assert idx2.count == 3

    query = e.encode(["quadratic"])
    out = idx2.search(query, k=1)
    assert out[0][0] == "m_a"
    assert out[0][1] == pytest.approx(1.0, abs=1e-5)


def test_search_k_clamped_to_count(tmp_path: Path) -> None:
    idx = _make_index(tmp_path)
    e = FakeEmbedder(dimension=16)
    idx.add_batch(["m_a", "m_b"], e.encode(["quadratic", "calculus"]))
    out = idx.search(e.encode(["quadratic"]), k=100)
    assert len(out) == 2


def test_resize_when_exceeding_initial_capacity(tmp_path: Path) -> None:
    idx = HnswIndex(
        dim=16,
        index_path=tmp_path / "hnsw.bin",
        label_store=InMemoryLabelStore(),
        max_elements=2,
    )
    idx.load_or_init()
    e = FakeEmbedder(dimension=16)
    idx.add_batch(["m_a", "m_b"], e.encode(["quadratic", "calculus"]))
    # Adding a third item must trigger resize, not raise.
    idx.add_batch(["m_c"], e.encode(["geometry"]))
    assert idx.count == 3


@dataclass(frozen=True)
class _RebuildRec:
    memory_id: str
    retrieval_text: str


def test_rebuild_from_iterator(tmp_path: Path) -> None:
    idx = _make_index(tmp_path)
    e = FakeEmbedder(dimension=16)
    records = [
        _RebuildRec("m_a", "quadratic equations review"),
        _RebuildRec("m_b", "calculus derivatives intro"),
        _RebuildRec("m_c", "geometry pythagoras theorem"),
    ]
    n = idx.rebuild_from(records, e, batch_size=2)
    assert n == 3
    assert idx.count == 3
    out = idx.search(e.encode(["quadratic"]), k=1)
    assert out[0][0] == "m_a"


def test_search_dimension_mismatch_raises(tmp_path: Path) -> None:
    idx = _make_index(tmp_path)
    # Must have at least one vector — the empty-index short-circuit would
    # otherwise return [] before the dimension check ever runs.
    e = FakeEmbedder(dimension=16)
    idx.add_batch(["m_a"], e.encode(["quadratic"]))
    with pytest.raises(ValueError):
        idx.search(np.zeros(8, dtype=np.float32), k=1)


def test_add_dimension_mismatch_raises(tmp_path: Path) -> None:
    idx = _make_index(tmp_path)
    bad = np.zeros((1, 8), dtype=np.float32)
    with pytest.raises(ValueError):
        idx.add_batch(["m_a"], bad)


def test_orphan_label_skipped_on_search(tmp_path: Path) -> None:
    """If a label survives in the index but is missing from the label store,
    search must skip it instead of crashing."""
    store = InMemoryLabelStore()
    idx = HnswIndex(
        dim=16,
        index_path=tmp_path / "hnsw.bin",
        label_store=store,
        max_elements=8,
    )
    idx.load_or_init()
    e = FakeEmbedder(dimension=16)
    idx.add_batch(["m_a", "m_b"], e.encode(["quadratic", "calculus"]))
    # Manually wipe one mapping to simulate divergence.
    label_b = store.label_for("m_b")
    assert label_b is not None
    store._fwd.pop("m_b")  # type: ignore[attr-defined]
    store._rev.pop(label_b)  # type: ignore[attr-defined]
    out = idx.search(e.encode(["quadratic"]), k=2)
    ids = [mid for mid, _ in out]
    assert "m_a" in ids
    assert "m_b" not in ids
