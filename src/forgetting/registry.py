"""Lookup table for ForgettingModel implementations."""
from __future__ import annotations

from src.forgetting.base import ForgettingModel
from src.forgetting.ebbinghaus import EbbinghausV1

_REGISTRY: dict[str, type] = {
    "ebbinghaus_v1": EbbinghausV1,
}

DEFAULT_MODEL_NAME = "ebbinghaus_v1"


def get_forgetting_model(name: str | None = None) -> ForgettingModel:
    key = name or DEFAULT_MODEL_NAME
    cls = _REGISTRY.get(key)
    if cls is None:
        raise ValueError(f"unknown forgetting model: {key}")
    return cls()
