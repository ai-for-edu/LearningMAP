from src.prompts.loader import render_prompt


def test_render_intent_prompt():
    result = render_prompt(
        "intent", messages=[{"role": "user", "content": "I want to learn systems of linear equations"}]
    )
    assert "systems of linear equations" in result
    assert len(result) > 50


def test_render_knowledge_graph_prompt():
    result = render_prompt("knowledge_graph", topic="Pythagorean theorem", max_nodes=8)
    assert "Pythagorean theorem" in result
    assert "8" in result


def test_render_quiz_prompt():
    nodes = [{"name": "linear equations", "description": "basics"}]
    result = render_prompt(
        "quiz_generate", knowledge_points=nodes, questions_per_point=2
    )
    assert "linear equations" in result


def test_render_kc_skill_from_bank_prompt():
    samples = [
        {
            "type": "fill-in",
            "content": "A school offers 4 interest classes: dance, singing, chess, painting ... how many different ways can a student enroll?",
            "options": {},
            "answer": "$$24$$",
            "analysis": "Xiao Yu selects first, 4 choices ... total 4×3×2=24 (ways)",
        },
        {
            "type": "single-choice",
            "content": "A bookshelf has 2 English books and 4 Chinese books ...",
            "options": {"A": "6", "B": "8", "C": "14", "D": "26"},
            "answer": "D",
            "analysis": "Three cases: 2×4+2×3+4×3=26.",
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
    assert "Question 1" in result and "Question 2" in result
    assert "fill-in" in result
    # both answers must appear
    assert "24" in result
    assert "26" in result
    # must instruct JSON-only output so the Classba backend returns parseable text
    assert "JSON" in result
    assert "Output exactly one JSON object" in result
    # must instruct the model to emit the new description field
    assert "description" in result
    assert "applicable scenario" in result


def test_render_kc_skill_block_with_content():
    from src.prompts.loader import render_kc_skill_block

    block = render_kc_skill_block(
        {
            "description": "desc",
            "考察重点": "focus content",
            "考察形式": "format content",
            "易错点": "mistake content",
            "难度提示": "difficulty content",
        }
    )
    assert "Assessment focus" in block
    assert "focus content" in block
    assert "mistake content" in block
    assert "Difficulty cues" in block


def test_render_kc_skill_block_empty_returns_empty_string():
    from src.prompts.loader import render_kc_skill_block

    assert render_kc_skill_block(None) == ""
    assert render_kc_skill_block({}) == ""


def test_quiz_generate_renders_kc_skill_block():
    block = "## KC test block\n- common mistakes: example"
    result = render_prompt(
        "quiz_generate",
        knowledge_points=[{"name": "multiplication principle", "description": "desc"}],
        questions_per_point=2,
        skill_instructions="",
        kc_skill_block=block,
    )
    assert "KC test block" in result
    assert "common mistakes: example" in result


def test_quiz_generate_omits_kc_skill_block_when_empty():
    result = render_prompt(
        "quiz_generate",
        knowledge_points=[{"name": "multiplication principle", "description": "desc"}],
        questions_per_point=2,
        skill_instructions="",
        kc_skill_block="",
    )
    # The block would have a distinctive heading — verify it's absent
    assert "KC test block" not in result


def test_lesson_point_renders_kc_skill_block():
    block = "## KC lesson block\n- difficulty cues: example"
    result = render_prompt(
        "lesson_point",
        topic="Pythagorean theorem",
        knowledge_point_name="right triangle",
        knowledge_point_description="desc",
        pedagogy_instructions="No specific pedagogical requirement",
        knowledge_skill="no additional teaching strategy",
        point_index=1,
        total_points=1,
        kc_skill_block=block,
    )
    assert "KC lesson block" in result
    assert "difficulty cues: example" in result


def test_slide_outline_renders_kc_skill_block():
    block = "## KC lesson block\n- difficulty cues: slide test"
    result = render_prompt(
        "slide_outline",
        topic="counting",
        weak_points=[{"name": "multiplication principle", "description": "desc"}],
        kc_skill_block=block,
    )
    assert "KC lesson block" in result
    assert "slide test" in result


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
        focus_directive="The student is still weak on distributive law.",
    )
    assert "Focus Directive" in out
    assert "distributive law" in out


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
        focus_directive="A is mastered, B is still weak.",
    )
    assert "B is still weak" in out


def test_lesson_point_prompt_omits_directive_when_empty() -> None:
    from src.prompts.loader import render_prompt

    out = render_prompt(
        "lesson_point",
        topic="X",
        knowledge_point_name="A",
        knowledge_point_description="...",
        pedagogy_instructions="x",
        knowledge_skill="no additional teaching strategy",
        point_index=1,
        total_points=1,
        kc_skill_block="",
        focus_directive="",
    )
    assert "Review Feedback Directive" not in out


def test_lesson_point_prompt_includes_directive_when_provided() -> None:
    from src.prompts.loader import render_prompt

    out = render_prompt(
        "lesson_point",
        topic="X",
        knowledge_point_name="A",
        knowledge_point_description="...",
        pedagogy_instructions="x",
        knowledge_skill="no additional teaching strategy",
        point_index=1,
        total_points=1,
        kc_skill_block="",
        focus_directive="The student is weak on distributive law, has mastered quadratic formula.",
    )
    assert "Review Feedback Directive" in out
    assert "distributive law" in out


# ---- KG prompt: existing_mastery block ----


class TestKnowledgeGraphMasteryBlock:
    def test_no_mastery_no_block(self) -> None:
        text = render_prompt(
            "knowledge_graph",
            topic="quadratic equations",
            max_nodes=10,
            skill_instructions="",
            focus_directive="",
            review_weak_kps=[],
            review_strong_kps=[],
            existing_mastery=[],
        )
        assert "Prior Mastery Hints" not in text

    def test_mastery_block_lists_subtopics(self) -> None:
        text = render_prompt(
            "knowledge_graph",
            topic="quadratic equations",
            max_nodes=10,
            skill_instructions="",
            focus_directive="",
            review_weak_kps=[],
            review_strong_kps=[],
            existing_mastery=[
                {
                    "subtopic": "discriminant",
                    "last_accuracy": 0.5,
                    "last_seen_at": "2026-04-30T10:00:00Z",
                    "status": "weak",
                },
                {
                    "subtopic": "quadratic formula",
                    "last_accuracy": 1.0,
                    "last_seen_at": "2026-04-30T11:00:00Z",
                    "status": "mastered",
                },
            ],
        )
        assert "discriminant" in text
        assert "quadratic formula" in text
        # The block must instruct the LLM to reuse subtopic names as node names
        assert "reuse" in text.lower()

    def test_mastery_block_handles_null_accuracy(self) -> None:
        text = render_prompt(
            "knowledge_graph",
            topic="quadratic equations",
            max_nodes=10,
            skill_instructions="",
            focus_directive="",
            review_weak_kps=[],
            review_strong_kps=[],
            existing_mastery=[
                {
                    "subtopic": "polar coordinates",
                    "last_accuracy": None,  # never tested
                    "last_seen_at": "2026-04-30T10:00:00Z",
                    "status": "unseen",
                },
            ],
        )
        # Must not leak Python repr "None" into the prompt
        assert "None" not in text
        # Should present a human-readable fallback
        assert "not tested" in text

    def test_mastery_block_formats_accuracy_as_percent(self) -> None:
        text = render_prompt(
            "knowledge_graph",
            topic="quadratic equations",
            max_nodes=10,
            skill_instructions="",
            focus_directive="",
            review_weak_kps=[],
            review_strong_kps=[],
            existing_mastery=[
                {
                    "subtopic": "discriminant",
                    "last_accuracy": 0.8,
                    "last_seen_at": "2026-04-30T10:00:00Z",
                    "status": "mastered",
                },
            ],
        )
        assert "80%" in text
        # Should NOT contain the raw 0.8 float
        assert "0.8," not in text
