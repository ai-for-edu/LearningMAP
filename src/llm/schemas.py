from pydantic import BaseModel, Field


class KnowledgeNode(BaseModel):
    id: str
    name: str
    description: str
    level: str  # "prerequisite" | "core" | "extension"


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
    read to decide how / when this skill applies. The other four Chinese
    fields are the subject-matter payload.
    """

    description: str = Field(
        ...,
        description=(
            "英文或中文皆可的一句话索引 + 适用场景，40-100 字。必须同时说明"
            "『这个 skill 讲什么』和『什么时候该调用它』。示例：『当学生处理"
            "多步骤计数问题且出现分步与分类混淆时使用。构建在乘法原理。』"
        ),
    )
    考察重点: str = Field(..., description="核心概念 / 子技能 / 思维点，80-150 字")
    考察形式: str = Field(..., description="题型分布 + 常见题干模式 + 情境套路，80-150 字")
    易错点: str = Field(..., description="从 analysis 归纳的高频错误，50-120 字")
    难度提示: str = Field(..., description="数字量级 / 嵌套深度 / 多步性，50-100 字")


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
