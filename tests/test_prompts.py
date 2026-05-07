from src.prompts.loader import render_prompt


def test_render_intent_prompt():
    result = render_prompt(
        "intent", messages=[{"role": "user", "content": "我想学二元一次方程"}]
    )
    assert "二元一次方程" in result
    assert len(result) > 50


def test_render_knowledge_graph_prompt():
    result = render_prompt("knowledge_graph", topic="勾股定理", max_nodes=8)
    assert "勾股定理" in result
    assert "8" in result


def test_render_quiz_prompt():
    nodes = [{"name": "一元一次方程", "description": "基础"}]
    result = render_prompt(
        "quiz_generate", knowledge_points=nodes, questions_per_point=2
    )
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
    # must instruct JSON-only output so the Classba backend returns parseable text
    assert "JSON" in result
    assert "只输出一个 JSON 对象" in result
    # must instruct the model to emit the new description field
    assert "description" in result
    assert "适用场景" in result


def test_render_kc_skill_block_with_content():
    from src.prompts.loader import render_kc_skill_block

    block = render_kc_skill_block(
        {
            "description": "desc",
            "考察重点": "重点内容",
            "考察形式": "形式内容",
            "易错点": "易错内容",
            "难度提示": "难度内容",
        }
    )
    assert "考察重点" in block
    assert "重点内容" in block
    assert "易错内容" in block
    assert "难度内容" in block


def test_render_kc_skill_block_empty_returns_empty_string():
    from src.prompts.loader import render_kc_skill_block

    assert render_kc_skill_block(None) == ""
    assert render_kc_skill_block({}) == ""


def test_quiz_generate_renders_kc_skill_block():
    block = "## KC 测试块\n- 易错点：示例"
    result = render_prompt(
        "quiz_generate",
        knowledge_points=[{"name": "乘法原理", "description": "desc"}],
        questions_per_point=2,
        skill_instructions="",
        kc_skill_block=block,
    )
    assert "KC 测试块" in result
    assert "易错点：示例" in result


def test_quiz_generate_omits_kc_skill_block_when_empty():
    result = render_prompt(
        "quiz_generate",
        knowledge_points=[{"name": "乘法原理", "description": "desc"}],
        questions_per_point=2,
        skill_instructions="",
        kc_skill_block="",
    )
    # The block would have a distinctive heading — verify it's absent
    assert "当前知识点的出题" not in result
    assert "KC 测试块" not in result


def test_lesson_point_renders_kc_skill_block():
    block = "## KC 课件块\n- 难度提示：示例"
    result = render_prompt(
        "lesson_point",
        topic="勾股定理",
        knowledge_point_name="直角三角形",
        knowledge_point_description="desc",
        pedagogy_instructions="无特定教学法要求",
        knowledge_skill="无额外教学技能",
        point_index=1,
        total_points=1,
        kc_skill_block=block,
    )
    assert "KC 课件块" in result
    assert "难度提示：示例" in result


def test_slide_outline_renders_kc_skill_block():
    block = "## KC 课件块\n- 难度提示：slide 测试"
    result = render_prompt(
        "slide_outline",
        topic="计数",
        weak_points=[{"name": "乘法原理", "description": "desc"}],
        kc_skill_block=block,
    )
    assert "KC 课件块" in result
    assert "slide 测试" in result


def test_knowledge_graph_prompt_omits_section_when_no_directive() -> None:
    from src.prompts.loader import render_prompt

    out = render_prompt(
        "knowledge_graph",
        topic="X",
        max_nodes=8,
        skill_instructions="",
        focus_directive="",
    )
    assert "Focus Directive" not in out


def test_knowledge_graph_prompt_includes_directive_when_provided() -> None:
    from src.prompts.loader import render_prompt

    out = render_prompt(
        "knowledge_graph",
        topic="X",
        max_nodes=8,
        skill_instructions="",
        focus_directive="学生在 分配律 上仍薄弱.",
    )
    assert "Focus Directive" in out
    assert "分配律" in out


def test_lesson_plan_prompt_omits_section_when_no_directive() -> None:
    from src.prompts.loader import render_prompt

    out = render_prompt(
        "lesson_plan",
        topic="X",
        weak_points=[],
        skill_instructions="",
        focus_directive="",
    )
    assert "Focus Directive" not in out


def test_lesson_plan_prompt_includes_directive_when_provided() -> None:
    from src.prompts.loader import render_prompt

    out = render_prompt(
        "lesson_plan",
        topic="X",
        weak_points=[],
        skill_instructions="",
        focus_directive="A 已掌握, B 仍薄弱.",
    )
    assert "B 仍薄弱" in out


def test_lesson_point_prompt_omits_directive_when_empty() -> None:
    from src.prompts.loader import render_prompt

    out = render_prompt(
        "lesson_point",
        topic="X",
        knowledge_point_name="A",
        knowledge_point_description="...",
        pedagogy_instructions="x",
        knowledge_skill="无额外教学技能",
        point_index=1,
        total_points=1,
        kc_skill_block="",
        focus_directive="",
    )
    assert "复习反馈指令" not in out


def test_lesson_point_prompt_includes_directive_when_provided() -> None:
    from src.prompts.loader import render_prompt

    out = render_prompt(
        "lesson_point",
        topic="X",
        knowledge_point_name="A",
        knowledge_point_description="...",
        pedagogy_instructions="x",
        knowledge_skill="无额外教学技能",
        point_index=1,
        total_points=1,
        kc_skill_block="",
        focus_directive="学生在 分配律 上薄弱，已掌握 求根公式。",
    )
    assert "复习反馈指令" in out
    assert "分配律" in out


# ---- KG prompt: existing_mastery block ----


class TestKnowledgeGraphMasteryBlock:
    def test_no_mastery_no_block(self) -> None:
        text = render_prompt(
            "knowledge_graph",
            topic="二次方程",
            max_nodes=10,
            skill_instructions="",
            focus_directive="",
            review_weak_kps=[],
            review_strong_kps=[],
            existing_mastery=[],
        )
        assert "已有学习记录" not in text
        assert "Prior Mastery Hints" not in text

    def test_mastery_block_lists_subtopics(self) -> None:
        text = render_prompt(
            "knowledge_graph",
            topic="二次方程",
            max_nodes=10,
            skill_instructions="",
            focus_directive="",
            review_weak_kps=[],
            review_strong_kps=[],
            existing_mastery=[
                {
                    "subtopic": "判别式",
                    "last_accuracy": 0.5,
                    "last_seen_at": "2026-04-30T10:00:00Z",
                    "status": "weak",
                },
                {
                    "subtopic": "求根公式",
                    "last_accuracy": 1.0,
                    "last_seen_at": "2026-04-30T11:00:00Z",
                    "status": "mastered",
                },
            ],
        )
        assert "判别式" in text
        assert "求根公式" in text
        # The block must instruct the LLM to reuse subtopic names as node names
        assert "复用" in text or "reuse" in text.lower()

    def test_mastery_block_handles_null_accuracy(self) -> None:
        text = render_prompt(
            "knowledge_graph",
            topic="二次方程",
            max_nodes=10,
            skill_instructions="",
            focus_directive="",
            review_weak_kps=[],
            review_strong_kps=[],
            existing_mastery=[
                {
                    "subtopic": "极坐标",
                    "last_accuracy": None,  # never tested
                    "last_seen_at": "2026-04-30T10:00:00Z",
                    "status": "unseen",
                },
            ],
        )
        # Must not leak Python repr "None" into the prompt
        assert "None" not in text
        # Should present a human-readable fallback
        assert "未测试" in text

    def test_mastery_block_formats_accuracy_as_percent(self) -> None:
        text = render_prompt(
            "knowledge_graph",
            topic="二次方程",
            max_nodes=10,
            skill_instructions="",
            focus_directive="",
            review_weak_kps=[],
            review_strong_kps=[],
            existing_mastery=[
                {
                    "subtopic": "判别式",
                    "last_accuracy": 0.8,
                    "last_seen_at": "2026-04-30T10:00:00Z",
                    "status": "mastered",
                },
            ],
        )
        assert "80%" in text
        # Should NOT contain the raw 0.8 float
        assert "0.8，" not in text
