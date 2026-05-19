from typing import Literal

from pydantic import BaseModel, Field, model_validator


MasteryStatus = Literal["mastered", "weak", "forgotten", "unseen"]


class MasteryMeta(BaseModel):
    """Per-node mastery snapshot — what the student knows about this node."""

    last_accuracy: float | None = None
    last_seen_at: str
    session_count: int
    retention: float  # output of the active ForgettingModel, in [0, 1]
    status: MasteryStatus
    forgetting_model: str  # e.g. "ebbinghaus_v1" — for future A/B tracking


class KnowledgeNode(BaseModel):
    id: str
    name: str
    description: str
    level: str  # "prerequisite" | "core" | "extension"
    mastery: MasteryMeta | None = None


class KnowledgeEdge(BaseModel):
    source: str
    target: str
    relation: str  # "prerequisite"


class KnowledgeGraph(BaseModel):
    topic: str
    nodes: list[KnowledgeNode]
    edges: list[KnowledgeEdge]


class QuizQuestion(BaseModel):
    id: str
    knowledge_point: str
    question: str
    options: list[str]
    answer: int  # index into options


class DiagnosisBreakdown(BaseModel):
    name: str
    status: str  # "mastered" | "weak" | "skipped"
    detail: str


class DiagnosisResult(BaseModel):
    score: int
    total: int
    correct: int
    incorrect: int
    breakdown: list[DiagnosisBreakdown]


class KCSkill(BaseModel):
    """LLM-generated, question-bank-grounded context for one knowledge component.

    The ``description`` field mirrors the ``description`` frontmatter of
    ``skills/*/SKILL.md`` — a one-line index string a consuming model can
    read to decide how / when this skill applies. The other four fields
    are the subject-matter payload.
    """

    description: str = Field(
        ...,
        description=(
            "One-sentence index + applicable scenario (40-100 chars, in English). "
            "Must state both 'what this skill covers' and 'when to invoke it'. "
            "Example: 'Use when a student confuses step-by-step counting with "
            "classification counting in multi-step problems. Grounded in the "
            "multiplication principle.'"
        ),
    )
    assessment_focus: str = Field(..., description="Core concepts / sub-skills / thinking points, 80-150 chars")
    assessment_format: str = Field(
        ..., description="Question-type distribution + common stem patterns + scenario conventions, 80-150 chars"
    )
    common_mistakes: str = Field(..., description="High-frequency errors inferred from analysis, 50-120 chars")
    difficulty_cues: str = Field(..., description="Numeric magnitude / nesting depth / multi-step complexity, 50-100 chars")


class ResolverMapping(BaseModel):
    """LLM output for the kc_skill_resolver fallback call.

    Maps figure-node names to the best-matching kc_skills.json route,
    or None if no reasonable match exists.
    """

    mapping: dict[str, str | None] = Field(
        ...,
        description="{node_name: full_route | null} — one entry per unresolved node.",
    )


class ReviewResult(BaseModel):
    """Output of submit-review: which KPs need reinforcement vs were retained.

    focus_directive is the rendered Chinese sentence injected into
    knowledge_graph and lesson prompts so the next-round content is biased
    toward the weak KPs.
    """

    weak_kps: list[str]
    strong_kps: list[str]
    score: float = Field(..., ge=0.0, le=1.0)
    focus_directive: str


# ----------------------------------------------------------------------
# Hybrid agent memory (PR-2): reviewer & progress aggregator schemas
# ----------------------------------------------------------------------
NoteCategory = Literal["misconception", "preference", "style", "goal"]
ConsolidationAction = Literal["ADD", "UPDATE", "DELETE", "NOOP", "REJECT"]
MasterySource = Literal["quiz", "review", "lesson_completion"]


class FactCandidate(BaseModel):
    """One fact extracted from a learning session by LLM call A."""

    category: NoteCategory
    content: str = Field(..., min_length=1, max_length=200)


class ExtractResult(BaseModel):
    """Output schema for LLM call A. Hard cap of 5 — also enforced by prompt."""

    candidates: list[FactCandidate] = Field(default_factory=list, max_length=5)


class ConsolidationDecision(BaseModel):
    """Output schema for LLM call B. Field combinations validated below."""

    action: ConsolidationAction
    target_id: int | None = None
    new_content: str | None = Field(default=None, min_length=1, max_length=200)
    reason: str = Field(..., min_length=1, max_length=200)

    @model_validator(mode="after")
    def _check_action_fields(self) -> "ConsolidationDecision":
        a, t, n = self.action, self.target_id, self.new_content
        if a == "ADD" and (t is not None or n is None):
            raise ValueError("ADD requires target_id=None and new_content not None")
        if a == "UPDATE" and (t is None or n is None):
            raise ValueError("UPDATE requires both target_id and new_content")
        if a == "DELETE" and t is None:
            raise ValueError("DELETE requires target_id")
        if a == "NOOP" and t is None:
            raise ValueError("NOOP requires target_id")
        if a == "REJECT" and t is not None:
            raise ValueError("REJECT must have target_id=None")
        # Spec §6.5: new_content required only for ADD/UPDATE — implied None for the rest.
        # Without this rule, a malformed LLM emitting DELETE with new_content
        # would slip past validation and pollute the audit trail with stray
        # content under a non-writing event.
        if a in {"DELETE", "NOOP", "REJECT"} and n is not None:
            raise ValueError(f"{a} requires new_content=None")
        return self


class MasterySample(BaseModel):
    """One KP-level mastery measurement, written via /progress."""

    kp: str = Field(..., min_length=1, max_length=200)
    score: float = Field(..., ge=0.0, le=1.0)
    source: MasterySource
