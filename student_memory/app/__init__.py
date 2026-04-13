"""Local student memory service.

A FastAPI application providing recency-aware semantic retrieval over
student learning records. SQLite is the source of truth; hnswlib is a
derived index that can be rebuilt from SQLite at any time.

See `../ARCHITECTURE.md`, `../todo.md`, and `../CLAUDE.md` (one level up,
in the repository root) for the full design.
"""

__version__ = "0.1.0"
