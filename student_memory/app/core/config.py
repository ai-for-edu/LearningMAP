"""Application configuration via pydantic-settings.

All settings are loaded from environment variables with the ``SM_`` prefix
or from a ``.env`` file in the project root. Defaults match the values
documented in ``ARCHITECTURE.md`` §11 and ``todo.md`` §10.7.

Tests should never read the global ``get_settings()`` instance directly;
instead they construct an isolated ``Settings(...)`` and inject it via the
FastAPI dependency override system.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # service ----------------------------------------------------------------
    service_host: str = "127.0.0.1"
    service_port: int = 8090
    log_level: str = "INFO"

    # storage ----------------------------------------------------------------
    data_dir: Path = Field(default=Path("data"))
    sqlite_path: Path = Field(default=Path("data/memory.db"))
    index_path: Path = Field(default=Path("data/hnsw.bin"))

    # embedding --------------------------------------------------------------
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024
    embedding_device: str | None = None
    use_fake_embedder: bool = False  # set SM_USE_FAKE_EMBEDDER=1 to skip the real model

    # retrieval --------------------------------------------------------------
    top_k: int = 5
    recall_k: int = 50

    # scoring weights (sum must equal 1.0) -----------------------------------
    w_sim: float = 0.75
    w_time: float = 0.20
    w_match: float = 0.05
    half_life_days: float = 30.0
    topic_bonus: float = 0.03
    subtopic_bonus: float = 0.02

    # hnsw tuning ------------------------------------------------------------
    hnsw_max_elements: int = 100_000
    hnsw_ef_construction: int = 200
    hnsw_m: int = 16
    hnsw_ef_search: int = 64

    @model_validator(mode="after")
    def _check_weights(self) -> "Settings":
        total = self.w_sim + self.w_time + self.w_match
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"w_sim + w_time + w_match must equal 1.0, got {total:.6f}"
            )
        if self.half_life_days <= 0:
            raise ValueError(f"half_life_days must be > 0, got {self.half_life_days}")
        if self.recall_k < self.top_k:
            raise ValueError(
                f"recall_k ({self.recall_k}) must be >= top_k ({self.top_k})"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide singleton. Tests should override via FastAPI Depends."""
    return Settings()
