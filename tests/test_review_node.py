"""Unit tests for src.agent.nodes.review."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch  # noqa: F401  # used by Tasks 4/5 mock-LLM tests


def _make_item(
    subtopic: str,
    *,
    last_seen_days_ago: float,
    last_accuracy: float,
    session_count: int = 1,
) -> dict:
    """Build a fake mastery item with a relative last_seen_at timestamp."""
    from datetime import datetime, timezone
    seen_at = (datetime.now(timezone.utc) - timedelta(days=last_seen_days_ago))
    return {
        "subtopic": subtopic,
        "topic": "T",
        "last_seen_at": seen_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session_count": session_count,
        "total_questions": 4,
        "total_correct": 2,
        "last_accuracy": last_accuracy,
    }


class TestSelectCandidateKps:

    def test_empty_input_returns_empty(self) -> None:
        from src.agent.nodes.review import select_candidate_kps
        assert select_candidate_kps([]) == []

    def test_excludes_recently_seen_within_1_day(self) -> None:
        from src.agent.nodes.review import select_candidate_kps
        items = [
            _make_item("FreshlyDone", last_seen_days_ago=0.5, last_accuracy=0.6),
            _make_item("OldButGood", last_seen_days_ago=10, last_accuracy=0.6),
        ]
        names = [c["subtopic"] for c in select_candidate_kps(items)]
        assert "FreshlyDone" not in names
        assert "OldButGood" in names

    def test_takes_all_when_few_items(self) -> None:
        from src.agent.nodes.review import select_candidate_kps
        items = [
            _make_item(f"K{i}", last_seen_days_ago=10, last_accuracy=0.5)
            for i in range(3)
        ]
        assert len(select_candidate_kps(items)) == 3

    def test_caps_at_5_when_many_items(self) -> None:
        from src.agent.nodes.review import select_candidate_kps
        items = [
            _make_item(f"K{i}", last_seen_days_ago=10, last_accuracy=0.5)
            for i in range(20)
        ]
        assert len(select_candidate_kps(items)) == 5

    def test_priority_prefers_stale_and_uncertain(self) -> None:
        from src.agent.nodes.review import select_candidate_kps
        items = [
            # Recent + certain (low priority)
            _make_item("Recent", last_seen_days_ago=2, last_accuracy=1.0),
            # Stale + uncertain (highest)
            _make_item("StaleUncertain", last_seen_days_ago=14, last_accuracy=0.5),
            # Stale + certain
            _make_item("StaleCertain", last_seen_days_ago=14, last_accuracy=1.0),
            # Recent + uncertain
            _make_item("RecentUncertain", last_seen_days_ago=2, last_accuracy=0.5),
        ]
        names = [c["subtopic"] for c in select_candidate_kps(items)]
        assert names[0] == "StaleUncertain"

    def test_deterministic_for_same_input(self) -> None:
        from src.agent.nodes.review import select_candidate_kps
        items = [
            _make_item("A", last_seen_days_ago=10, last_accuracy=0.4),
            _make_item("B", last_seen_days_ago=10, last_accuracy=0.6),
        ]
        first = select_candidate_kps(items)
        second = select_candidate_kps(items)
        assert first == second

    def test_equal_priority_preserves_input_order(self) -> None:
        from src.agent.nodes.review import select_candidate_kps
        # Two items with identical staleness and uncertainty → equal priority.
        # Output order must match input order (stable tie-break).
        items = [
            _make_item("First", last_seen_days_ago=10, last_accuracy=0.5),
            _make_item("Second", last_seen_days_ago=10, last_accuracy=0.5),
        ]
        names = [c["subtopic"] for c in select_candidate_kps(items)]
        assert names == ["First", "Second"]


class TestRenderDirective:

    def test_only_weak_kps(self) -> None:
        from src.agent.nodes.review import _render_directive
        out = _render_directive(weak_kps=["分配律", "交换律"], strong_kps=[])
        assert "分配律" in out and "交换律" in out
        assert "薄弱" in out or "巩固" in out

    def test_only_strong_kps(self) -> None:
        from src.agent.nodes.review import _render_directive
        out = _render_directive(weak_kps=[], strong_kps=["求根公式"])
        assert "求根公式" in out
        assert "已掌握" in out or "略" in out

    def test_both_present(self) -> None:
        from src.agent.nodes.review import _render_directive
        out = _render_directive(weak_kps=["A"], strong_kps=["B"])
        assert "A" in out and "B" in out

    def test_empty_returns_empty_string(self) -> None:
        from src.agent.nodes.review import _render_directive
        assert _render_directive(weak_kps=[], strong_kps=[]) == ""


class TestGradeReview:

    def _make_q(self, qid: str, kp: str, answer_idx: int):
        from src.llm.schemas import QuizQuestion
        return QuizQuestion(
            id=qid, knowledge_point=kp,
            question="Q?", options=["a", "b", "c", "d"], answer=answer_idx,
        )

    def test_all_correct(self) -> None:
        from src.agent.nodes.review import grade_review
        qs = [self._make_q("q1", "A", 0), self._make_q("q2", "B", 1)]
        result = grade_review(qs, [0, 1])
        assert result.weak_kps == []
        assert set(result.strong_kps) == {"A", "B"}
        assert result.score == 1.0

    def test_all_wrong(self) -> None:
        from src.agent.nodes.review import grade_review
        qs = [self._make_q("q1", "A", 0), self._make_q("q2", "B", 1)]
        result = grade_review(qs, [1, 0])
        assert set(result.weak_kps) == {"A", "B"}
        assert result.strong_kps == []
        assert result.score == 0.0

    def test_mixed(self) -> None:
        from src.agent.nodes.review import grade_review
        qs = [self._make_q("q1", "A", 0), self._make_q("q2", "B", 1), self._make_q("q3", "C", 2)]
        result = grade_review(qs, [0, 0, 2])  # A correct, B wrong, C correct
        assert result.weak_kps == ["B"]
        assert set(result.strong_kps) == {"A", "C"}
        assert abs(result.score - 2/3) < 1e-9
        assert "B" in result.focus_directive

    def test_unanswered_treated_as_wrong(self) -> None:
        from src.agent.nodes.review import grade_review
        qs = [self._make_q("q1", "A", 0), self._make_q("q2", "B", 1)]
        # Only one answer provided
        result = grade_review(qs, [0])
        assert "B" in result.weak_kps
        assert "A" in result.strong_kps

    def test_malformed_dict_question_skipped(self) -> None:
        """A malformed dict (missing keys) should not crash; it's skipped + logged."""
        from src.agent.nodes.review import grade_review
        # First question is well-formed; second is missing 'knowledge_point' key
        good = self._make_q("q1", "A", 0)
        bad = {"id": "q2", "answer": 1}  # missing 'knowledge_point'
        result = grade_review([good, bad], [0, 1])
        # Only the well-formed question contributed
        assert result.strong_kps == ["A"]
        assert result.weak_kps == []
        # score = 1 correct out of total 2 questions (the bad one wasn't graded)
        # implementation choice: the malformed q still counts in `total` for the score
        # denominator, since len(questions) is 2.
        assert abs(result.score - 0.5) < 1e-9


class TestGenerateReviewQuestions:

    @patch("src.agent.nodes.review.get_llm_client")
    def test_calls_llm_with_correct_count_and_returns_questions(
        self, mock_get_client
    ) -> None:
        from src.agent.nodes.review import generate_review_questions
        from src.llm.schemas import QuizQuestion

        # Mock instructor client to return 2 fake questions
        mock_client = mock_get_client.return_value
        mock_client.chat.completions.create.return_value = [
            QuizQuestion(id="rq_0", knowledge_point="A",
                         question="Qa", options=["x", "y", "z", "w"], answer=0),
            QuizQuestion(id="rq_1", knowledge_point="B",
                         question="Qb", options=["x", "y", "z", "w"], answer=1),
        ]

        candidates = [
            _make_item("A", last_seen_days_ago=10, last_accuracy=0.5),
            _make_item("B", last_seen_days_ago=15, last_accuracy=0.8),
        ]
        questions = generate_review_questions(candidates)

        assert len(questions) == 2
        assert {q.knowledge_point for q in questions} == {"A", "B"}

        # Verify the LLM was called with response_model + messages
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert "response_model" in call_kwargs
        assert "messages" in call_kwargs

    @patch("src.agent.nodes.review.get_llm_client")
    def test_empty_candidates_skips_llm_returns_empty(
        self, mock_get_client
    ) -> None:
        from src.agent.nodes.review import generate_review_questions
        result = generate_review_questions([])
        assert result == []
        mock_get_client.assert_not_called()

    @patch("src.agent.nodes.review.get_llm_client")
    def test_llm_failure_returns_empty(self, mock_get_client) -> None:
        from src.agent.nodes.review import generate_review_questions
        mock_get_client.return_value.chat.completions.create.side_effect = (
            RuntimeError("boom")
        )
        candidates = [_make_item("A", last_seen_days_ago=10, last_accuracy=0.5)]
        assert generate_review_questions(candidates) == []

    @patch("src.agent.nodes.review.get_llm_client")
    def test_uses_reviewer_model_env(self, mock_get_client) -> None:
        """REVIEWER_MODEL env var must override LLM_MODEL for review calls."""
        import os
        from src.agent.nodes.review import generate_review_questions
        from src.llm.schemas import QuizQuestion

        mock_client = mock_get_client.return_value
        mock_client.chat.completions.create.return_value = [
            QuizQuestion(id="rq_0", knowledge_point="A",
                         question="Q", options=["a", "b", "c", "d"], answer=0),
        ]
        candidates = [_make_item("A", last_seen_days_ago=10, last_accuracy=0.5)]

        with patch.dict(os.environ, {"REVIEWER_MODEL": "test-cheap-model"}):
            generate_review_questions(candidates)

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["model"] == "test-cheap-model"

    @patch("src.agent.nodes.review.get_llm_client")
    def test_none_last_accuracy_does_not_crash(self, mock_get_client) -> None:
        """A candidate with last_accuracy=None must not crash the template."""
        from src.agent.nodes.review import generate_review_questions
        from src.llm.schemas import QuizQuestion

        mock_client = mock_get_client.return_value
        mock_client.chat.completions.create.return_value = [
            QuizQuestion(id="rq_0", knowledge_point="A",
                         question="Q", options=["a", "b", "c", "d"], answer=0),
        ]
        item = _make_item("A", last_seen_days_ago=10, last_accuracy=0.5)
        item["last_accuracy"] = None  # override to None

        questions = generate_review_questions([item])
        assert len(questions) == 1
        # Verify prompt rendered without crashing — must contain "0%"
        rendered = mock_client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        assert "0%" in rendered  # last_accuracy=None coerced to 0.0
