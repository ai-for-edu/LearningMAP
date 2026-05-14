from __future__ import annotations

import logging
from src.agent.state import LearningState
from src.llm.client import get_raw_client, get_model_name
from src.memory_client import retrieve_memory, DEFAULT_USER_ID
from src.skills.loader import get_skill_section
from src.agent.nodes._helpers import memory_snapshot_block
from langchain_core.messages import AIMessage

logger = logging.getLogger(__name__)

_BASE_SYSTEM_PROMPT = "You are a friendly AI learning assistant. Help students with their questions. Respond in the same language as the student."

# NOTE (PR-4): chat is the only agent node that does NOT use a Jinja
# template. The streaming variant (chat_node_stream) and runtime decisions
# (≥10-char threshold for retrieve_memory) make Python construction
# more direct than j2 + filters. Other 3 nodes (knowledge_graph / quiz /
# lesson) use render_prompt(...) and inject {{- memory_snapshot_block }}
# at template end.


def _build_context_summary(state: LearningState) -> str:
    """Build a concise summary of the current learning session for the LLM."""
    parts = []

    topic = state.get("topic")
    if topic:
        parts.append(f"学习主题：{topic}")

    # Knowledge graph — node names + levels only
    graph = state.get("knowledge_graph")
    if graph:
        nodes = graph.nodes if hasattr(graph, "nodes") else graph.get("nodes", [])
        level_map = {"prerequisite": "前置", "core": "核心", "extension": "拓展"}
        node_strs = []
        for n in nodes:
            name = n.name if hasattr(n, "name") else n.get("name", "")
            level = n.level if hasattr(n, "level") else n.get("level", "")
            node_strs.append(f"{name}({level_map.get(level, level)})")
        parts.append(f"知识图谱：{', '.join(node_strs)}")

    # Diagnosis result — score + mastered/weak breakdown
    diagnosis = state.get("diagnosis_result")
    if diagnosis:
        score = diagnosis.score if hasattr(diagnosis, "score") else diagnosis.get("score", 0)
        total = diagnosis.total if hasattr(diagnosis, "total") else diagnosis.get("total", 0)
        correct = diagnosis.correct if hasattr(diagnosis, "correct") else diagnosis.get("correct", 0)
        breakdown = diagnosis.breakdown if hasattr(diagnosis, "breakdown") else diagnosis.get("breakdown", [])

        mastered = []
        weak = []
        for b in breakdown:
            name = b.name if hasattr(b, "name") else b.get("name", "")
            status = b.status if hasattr(b, "status") else b.get("status", "")
            if status == "mastered":
                mastered.append(name)
            elif status == "weak":
                weak.append(name)

        parts.append(f"诊断结果：{score}%（{correct}/{total}）")
        if mastered:
            parts.append(f"  已掌握：{', '.join(mastered)}")
        if weak:
            parts.append(f"  薄弱：{', '.join(weak)}")

    # Lesson status
    lesson = state.get("lesson_content")
    if lesson:
        parts.append("教案：已生成")

    if not parts:
        return ""

    return "## 当前学习上下文\n" + "\n".join(f"- {p}" for p in parts) + "\n\n基于以上上下文回答用户问题。如果用户问到诊断、知识点等，用上下文中的数据回答。"


def _cta_kwargs(topic: str | None) -> dict:
    """Return AIMessage extra-kwargs payload for a learn-topic CTA, or {} if no topic.

    Centralizes the CTA shape so chat_node and the SSE handler can't drift.
    """
    if not topic:
        return {}
    return {"additional_kwargs": {"cta": {"type": "learn_topic", "topic": topic}}}


def chat_node(state: LearningState) -> dict:
    """Handle general conversation with learning context awareness.

    Reads and writes the active conversation track based on session_mode:
    chat mode → chat_messages; learning mode → messages.
    """
    session_mode = state.get("session_mode", "chat")
    track_key = "chat_messages" if session_mode == "chat" else "messages"
    messages = state.get(track_key, [])

    # Build system prompt with context summary
    system_prompt = _BASE_SYSTEM_PROMPT

    # ② Skill teaching method
    skill_text = get_skill_section(state, "chat")
    if skill_text:
        system_prompt += f"\n\n## 教学方法\n{skill_text}"

    # ③ Current session context
    # Learning artifacts (topic, knowledge graph, diagnosis, lesson) belong to
    # the learning track only — don't leak them into chat-mode LLM context.
    if state.get("session_mode") != "chat":
        context = _build_context_summary(state)
        if context:
            system_prompt += f"\n\n{context}"

    # Frozen snapshot (PR-4): cache-friendly, before retrieve_memory
    system_prompt += memory_snapshot_block(state)

    # Per-turn L1 retrieval (existing). PR-4 adds ≥10-char threshold to skip
    # tiny utterances ("嗯"/"继续") that produce noisy embeddings. NOT env-gated:
    # pre-PR-4 this was unconditional, so env=off keeps L1 retrieval enabled.
    user_msg = messages[-1].content if messages else ""
    if len(user_msg.strip()) >= 10:
        memory_context = retrieve_memory(DEFAULT_USER_ID, user_msg)
        if memory_context:
            system_prompt += f"\n\n## 历史学习记忆\n{memory_context}\n\n参考以上历史记录回答，但不要直接复述。"

    # Convert to OpenAI format
    api_messages = [{"role": "system", "content": system_prompt}]
    for msg in messages:
        if hasattr(msg, "type"):
            role = "assistant" if msg.type == "ai" else "user"
            api_messages.append({"role": role, "content": msg.content})
        elif isinstance(msg, dict):
            api_messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})

    try:
        client = get_raw_client()
        response = client.chat.completions.create(
            model=get_model_name(),
            max_tokens=1024,
            messages=api_messages,
        )
        reply = response.choices[0].message.content
    except Exception:
        logger.exception("LLM call failed in chat_node")
        reply = "抱歉，AI 服务暂时不可用，请稍后再试。"
    extra_kwargs = _cta_kwargs(state.get("pending_cta_topic"))
    return {
        track_key: [AIMessage(content=reply, **extra_kwargs)],
        "mode": "chat",
        "pending_cta_topic": None,  # always clear, even if it wasn't set
    }


def chat_node_stream(state: dict):
    """Streaming version of chat_node. Yields token strings.

    Not called by LangGraph — used directly by the SSE endpoint.
    The SSE caller owns: CTA attachment, state mutation, and persistence
    (chat_node_stream is a pure token generator).

    Reads from the active conversation track based on session_mode.
    """
    session_mode = state.get("session_mode", "chat")
    track_key = "chat_messages" if session_mode == "chat" else "messages"
    messages = state.get(track_key, [])

    system_prompt = _BASE_SYSTEM_PROMPT

    skill_text = get_skill_section(state, "chat")
    if skill_text:
        system_prompt += f"\n\n## 教学方法\n{skill_text}"

    # Learning artifacts (topic, knowledge graph, diagnosis, lesson) belong to
    # the learning track only — don't leak them into chat-mode LLM context.
    if state.get("session_mode") != "chat":
        context = _build_context_summary(state)
        if context:
            system_prompt += f"\n\n{context}"

    # Frozen snapshot (PR-4) — same as chat_node
    system_prompt += memory_snapshot_block(state)

    user_msg = messages[-1].content if messages and hasattr(messages[-1], "content") else ""
    if len(user_msg.strip()) >= 10:
        memory_context = retrieve_memory(DEFAULT_USER_ID, user_msg)
        if memory_context:
            system_prompt += f"\n\n## 历史学习记忆\n{memory_context}\n\n参考以上历史记录回答，但不要直接复述。"

    api_messages = [{"role": "system", "content": system_prompt}]
    for msg in messages:
        if hasattr(msg, "type"):
            role = "assistant" if msg.type == "ai" else "user"
            api_messages.append({"role": role, "content": msg.content})
        elif isinstance(msg, dict):
            api_messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})

    client = get_raw_client()
    stream = client.chat.completions.create(
        model=get_model_name(),
        max_tokens=4096,
        messages=api_messages,
        stream=True,
    )
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            yield delta.content
