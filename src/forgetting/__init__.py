"""Forgetting curve subsystem.

Public surface kept tiny on purpose — callers use the Protocol type
and the factory; implementations are private.
"""

from src.forgetting.base import ForgettingModel
from src.forgetting.registry import DEFAULT_MODEL_NAME, get_forgetting_model

__all__ = ["ForgettingModel", "get_forgetting_model", "DEFAULT_MODEL_NAME"]
