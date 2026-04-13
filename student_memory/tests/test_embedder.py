"""Phase 3: Embedder ABC contract tests against the FakeEmbedder."""

from __future__ import annotations

import sys

import numpy as np
import pytest

from app.embedding.fake_embedder import FakeEmbedder


def test_fake_embedder_shape_and_dtype() -> None:
    e = FakeEmbedder(dimension=16)
    out = e.encode(["hello", "world", "quadratic discriminant"])
    assert out.shape == (3, 16)
    assert out.dtype == np.float32


def test_fake_embedder_l2_normalized() -> None:
    e = FakeEmbedder(dimension=16)
    out = e.encode(
        [
            "quadratic equations",
            "calculus derivatives",
            "random unrelated text",
            "",
        ]
    )
    norms = np.linalg.norm(out, axis=1)
    np.testing.assert_allclose(norms, np.ones(4), atol=1e-5)


def test_fake_embedder_empty_input() -> None:
    e = FakeEmbedder(dimension=16)
    out = e.encode([])
    assert out.shape == (0, 16)
    assert out.dtype == np.float32


def test_fake_embedder_deterministic() -> None:
    e = FakeEmbedder(dimension=16)
    a = e.encode(["quadratic equations"])
    b = e.encode(["quadratic equations"])
    np.testing.assert_array_equal(a, b)


def test_fake_embedder_known_keywords_have_high_similarity() -> None:
    e = FakeEmbedder(dimension=16)
    out = e.encode(
        [
            "quadratic equations review",
            "quadratic discriminant practice",
            "calculus derivatives",
        ]
    )
    # rows 0 and 1 share "quadratic"; row 2 shares nothing.
    sim_01 = float(out[0] @ out[1])
    sim_02 = float(out[0] @ out[2])
    assert sim_01 > sim_02
    assert sim_01 > 0.5
    assert abs(sim_02) < 1e-6


def test_fake_embedder_dimension_too_small_raises() -> None:
    with pytest.raises(ValueError):
        FakeEmbedder(dimension=4)


def test_fake_embedder_does_not_import_torch() -> None:
    # This is the important guard: the test embedder must be ML-free.
    assert "torch" not in sys.modules
    assert "sentence_transformers" not in sys.modules


def test_local_embedder_import_does_not_pull_torch() -> None:
    # Importing the module is fine; only encode() should pull the model.
    import app.embedding.local_embedder  # noqa: F401

    assert "torch" not in sys.modules
    assert "sentence_transformers" not in sys.modules


@pytest.mark.ml
def test_real_bge_embedder_smoke() -> None:  # pragma: no cover - opt-in
    from app.embedding.local_embedder import BgeEmbedder

    e = BgeEmbedder("BAAI/bge-small-zh-v1.5", dimension=512)
    out = e.encode(["你好", "world"])
    assert out.shape == (2, 512)
    np.testing.assert_allclose(np.linalg.norm(out, axis=1), np.ones(2), atol=1e-4)
