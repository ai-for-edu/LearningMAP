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
    """LLM-generated, question-bank-grounded context for one knowledge component."""

    考察重点: str = Field(..., description="核心概念 / 子技能 / 思维点，80-150 字")
    考察形式: str = Field(..., description="题型分布 + 常见题干模式 + 情境套路，80-150 字")
    易错点: str = Field(..., description="从 analysis 归纳的高频错误，50-120 字")
    难度提示: str = Field(..., description="数字量级 / 嵌套深度 / 多步性，50-100 字")
