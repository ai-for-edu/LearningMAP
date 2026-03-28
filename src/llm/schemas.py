from pydantic import BaseModel


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
