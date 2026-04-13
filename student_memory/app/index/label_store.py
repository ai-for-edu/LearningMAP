"""Bidirectional ``label ↔ memory_id`` mapping for the hnsw index.

Two implementations:

- ``SqliteLabelStore`` — production. Backed by the ``hnsw_labels`` table
  in the same SQLite file as ``memories``, so ingestion can write the
  memory row and assign the label inside one transaction. The atomicity
  guarantee is the whole point of this abstraction.
- ``InMemoryLabelStore`` — for unit tests of ``HnswIndex`` that do not
  want a SQLite dependency. Tests use it via ``InMemoryLabelStore()``.

Both implementations honor the same contract.
"""

from __future__ import annotations

import abc
import threading
from collections.abc import Sequence

from app.db.sqlite_repo import SqliteRepo


class LabelStore(abc.ABC):
    """Abstract bidirectional ``label ↔ memory_id`` mapping."""

    @abc.abstractmethod
    def assign(self, memory_id: str) -> int: ...

    @abc.abstractmethod
    def assign_batch(self, memory_ids: Sequence[str]) -> list[int]: ...

    @abc.abstractmethod
    def label_for(self, memory_id: str) -> int | None: ...

    @abc.abstractmethod
    def memory_id_for(self, label: int) -> str | None: ...

    @abc.abstractmethod
    def all(self) -> list[tuple[int, str]]: ...

    @abc.abstractmethod
    def clear(self) -> None: ...


class SqliteLabelStore(LabelStore):
    def __init__(self, repo: SqliteRepo) -> None:
        self._repo = repo

    def assign(self, memory_id: str) -> int:
        return self._repo.assign_labels([memory_id])[0]

    def assign_batch(self, memory_ids: Sequence[str]) -> list[int]:
        return self._repo.assign_labels(memory_ids)

    def label_for(self, memory_id: str) -> int | None:
        return self._repo.get_label(memory_id)

    def memory_id_for(self, label: int) -> str | None:
        return self._repo.get_memory_id(label)

    def all(self) -> list[tuple[int, str]]:
        return self._repo.all_labels()

    def clear(self) -> None:
        self._repo.clear_labels()


class InMemoryLabelStore(LabelStore):
    """Test-only in-memory implementation."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next: int = 1
        self._fwd: dict[str, int] = {}
        self._rev: dict[int, str] = {}

    def assign(self, memory_id: str) -> int:
        with self._lock:
            if memory_id in self._fwd:
                raise ValueError(f"memory_id already mapped: {memory_id}")
            label = self._next
            self._next += 1
            self._fwd[memory_id] = label
            self._rev[label] = memory_id
            return label

    def assign_batch(self, memory_ids: Sequence[str]) -> list[int]:
        return [self.assign(mid) for mid in memory_ids]

    def label_for(self, memory_id: str) -> int | None:
        return self._fwd.get(memory_id)

    def memory_id_for(self, label: int) -> str | None:
        return self._rev.get(label)

    def all(self) -> list[tuple[int, str]]:
        return sorted(self._rev.items())

    def clear(self) -> None:
        with self._lock:
            self._next = 1
            self._fwd.clear()
            self._rev.clear()
