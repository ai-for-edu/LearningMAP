"""Phase 1 smoke test: the package imports cleanly and /health works."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_create_app_imports_cleanly() -> None:
    # Importing app.main must NOT pull torch / sentence-transformers.
    import sys

    import app.main  # noqa: F401

    assert "torch" not in sys.modules
    assert "sentence_transformers" not in sys.modules


def test_health_endpoint_returns_ok() -> None:
    from app.main import create_app

    app = create_app()
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_settings_weight_validation() -> None:
    from pydantic import ValidationError

    from app.core.config import Settings

    # Sane defaults pass.
    s = Settings()
    assert abs(s.w_sim + s.w_time + s.w_match - 1.0) < 1e-9

    # Mis-summed weights raise.
    import pytest

    with pytest.raises(ValidationError):
        Settings(w_sim=0.5, w_time=0.4, w_match=0.2)

    with pytest.raises(ValidationError):
        Settings(half_life_days=0)

    with pytest.raises(ValidationError):
        Settings(top_k=10, recall_k=5)
