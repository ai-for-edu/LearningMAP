from src.prompts.loader import render_prompt


def test_render_intent_prompt():
    result = render_prompt("intent", messages=[{"role": "user", "content": "我想学二元一次方程"}])
    assert "二元一次方程" in result
    assert len(result) > 50


def test_render_knowledge_graph_prompt():
    result = render_prompt("knowledge_graph", topic="勾股定理", max_nodes=8)
    assert "勾股定理" in result
    assert "8" in result


def test_render_quiz_prompt():
    nodes = [{"name": "一元一次方程", "description": "基础"}]
    result = render_prompt("quiz_generate", knowledge_points=nodes, questions_per_point=2)
    assert "一元一次方程" in result
