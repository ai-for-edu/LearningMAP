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


def test_render_kc_skill_from_bank_prompt():
    samples = [
        {
            "type": "填空",
            "content": "学校有舞蹈、唱歌、围棋、绘画四种兴趣班……一共有多少种不同的报名方法？",
            "options": {},
            "answer": "$$24$$",
            "analysis": "小宇先选，有 4 种……一共有 4×3×2=24（种）",
        },
        {
            "type": "单选",
            "content": "书架上有 2 本英语书、4 本语文书……",
            "options": {"A": "6", "B": "8", "C": "14", "D": "26"},
            "answer": "D",
            "analysis": "分三类：2×4+2×3+4×3=26。",
        },
    ]
    result = render_prompt(
        "kc_skill_from_bank",
        route="拓展思维----计数模块----加乘原理----乘法原理----乘法原理里的其他类型",
        samples=samples,
        sample_count=2,
        total_count=42,
    )
    assert "拓展思维----计数模块----加乘原理" in result
    assert "考察重点" in result
    assert "考察形式" in result
    assert "易错点" in result
    assert "难度提示" in result
    assert "题目 1" in result and "题目 2" in result
    assert "填空" in result
    # both answers must appear
    assert "24" in result
    assert "26" in result
