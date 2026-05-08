"""Render a frozen-snapshot Markdown block for a user.

Pulls the structured data from `SqliteRepo`, formats it via Jinja, and
enforces a hard character budget by dropping low-priority sections whole.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.api.schemas import SnapshotResponse
from app.db.sqlite_repo import (
    MasteryRecord,
    NoteRecord,
    ProgressRecord,
    SqliteRepo,
)

_TEMPLATE_DIR = Path(__file__).parent
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(),
    trim_blocks=False,
    lstrip_blocks=False,
)
_template = _env.get_template("snapshot.j2")


_STATUS_LABELS = {
    "in_progress": "已开始",
    "mastered": "已掌握",
    "abandoned": "长期未碰",
}


def _elapsed_label(then_ts: int, now_ts: int) -> str:
    age_h = max((now_ts - then_ts) / 3600, 0)
    if age_h < 24:
        return "今天"
    age_d = age_h / 24
    if age_d < 7:
        return f"{int(age_d)}天前"
    if age_d < 30:
        return f"{int(age_d / 7)}周前"
    return f"{int(age_d / 30)}月前"


def _trend_label(samples: list) -> str | None:
    """`samples` 必须按时间升序排好。监单调上升/下降。"""
    pts = samples[-3:]
    if len(pts) < 3:
        return None
    if pts[0].score < pts[1].score < pts[2].score:
        return "上升中"
    if pts[0].score > pts[1].score > pts[2].score:
        return "退步中"
    return None


@dataclass(frozen=True)
class _ProgressView:
    topic: str
    status_label: str
    elapsed_label: str
    mastery_pct: int


@dataclass(frozen=True)
class _WeakKpView:
    knowledge_point: str
    score_pct: int
    trend: str | None


def _build_progress_views(
    progress: list[ProgressRecord],
    now_ts: int,
) -> list[_ProgressView]:
    return [
        _ProgressView(
            topic=p.topic,
            status_label=_STATUS_LABELS.get(p.status, p.status),
            elapsed_label=_elapsed_label(p.started_at, now_ts),
            mastery_pct=int(round(p.mastery_overall * 100)),
        )
        for p in progress
    ]


def _build_weak_kp_views(
    repo: SqliteRepo,
    weak_kps: list[MasteryRecord],
) -> list[_WeakKpView]:
    out: list[_WeakKpView] = []
    for k in weak_kps:
        history = repo.list_mastery_for_kp(k.user_id, k.topic, k.knowledge_point)
        out.append(
            _WeakKpView(
                knowledge_point=k.knowledge_point,
                score_pct=int(round(k.score * 100)),
                trend=_trend_label(history),
            )
        )
    return out


def _render_section(
    *,
    progress: list,
    weak_kps: list,
    misconceptions: list[NoteRecord],
    style_pref: list[NoteRecord],
    goals: list[NoteRecord],
) -> str:
    return _template.render(
        progress=progress,
        weak_kps=weak_kps,
        misconceptions=misconceptions,
        style_pref=style_pref,
        goals=goals,
    )


def render_snapshot(
    repo: SqliteRepo,
    *,
    user_id: str,
    now_ts: int,
    budget: int = 3500,
) -> SnapshotResponse:
    progress = repo.list_progress_for_user(user_id)
    weak_kps = repo.list_weak_mastery(user_id=user_id, threshold=0.7)

    misconceptions = repo.list_active_notes(
        user_id=user_id,
        category="misconception",
        limit=10,
    )
    styles = repo.list_active_notes(user_id=user_id, category="style", limit=10)
    prefs = repo.list_active_notes(user_id=user_id, category="preference", limit=10)
    goals = repo.list_active_notes(user_id=user_id, category="goal", limit=10)

    progress_views = _build_progress_views(progress, now_ts)
    weak_kp_views = _build_weak_kp_views(repo, weak_kps)

    sections: list[tuple[str, str]] = []

    if progress_views:
        sections.append(
            (
                "progress",
                _render_section(
                    progress=progress_views,
                    weak_kps=weak_kp_views,
                    misconceptions=[],
                    style_pref=[],
                    goals=[],
                ),
            )
        )
    if misconceptions:
        sections.append(
            (
                "misconception",
                _render_section(
                    progress=[],
                    weak_kps=[],
                    misconceptions=misconceptions,
                    style_pref=[],
                    goals=[],
                ),
            )
        )
    style_pref_combined = list(styles) + list(prefs)
    if style_pref_combined:
        sections.append(
            (
                "style_pref",
                _render_section(
                    progress=[],
                    weak_kps=[],
                    misconceptions=[],
                    style_pref=style_pref_combined,
                    goals=[],
                ),
            )
        )
    if goals:
        sections.append(
            (
                "goal",
                _render_section(
                    progress=[],
                    weak_kps=[],
                    misconceptions=[],
                    style_pref=[],
                    goals=goals,
                ),
            )
        )

    out_chunks: list[str] = []
    used = 0
    included: dict[str, int] = {}
    for name, text in sections:
        if used + len(text) <= budget:
            out_chunks.append(text)
            used += len(text)
            included[name] = len(text)

    full_total = sum(len(t) for _, t in sections)
    truncated = full_total > used
    return SnapshotResponse(
        snapshot_md="".join(out_chunks),
        char_count=used,
        truncated=truncated,
        sections=included,
    )
