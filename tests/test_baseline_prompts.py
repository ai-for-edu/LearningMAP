"""PR-4 baseline byte-identity tests.

Each test renders a j2 template with empty memory_snapshot_block and
asserts the output exactly equals the captured pre-PR-4 baseline string.
The whitespace control `{{- memory_snapshot_block }}` in templates ensures
empty var produces byte-identical output to pre-PR-4.

Regenerate baselines with:
  uv run python -c "from src.prompts.loader import render_prompt; \\
                    print(repr(render_prompt('<name>', **kwargs)))"
"""
from __future__ import annotations

from src.prompts.loader import render_prompt


# Captured from pre-PR-4 knowledge_graph.j2 with empty var; regenerate if
# the template content (other than {{- memory_snapshot_block }}) ever changes.
EXPECTED_KG_BASELINE = 'You are an educational knowledge graph builder. Given a learning topic, generate a knowledge graph of related concepts.\n\n\n\n\n\nTopic: 勾股定理\nMaximum nodes: 8\n\nGenerate a knowledge graph with:\n1. The core topic and its prerequisite concepts\n2. Each node has: id, name, description, level (prerequisite/core/extension)\n3. Edges show prerequisite relationships (source → target means source is prerequisite of target)\n4. Include 4-8 nodes covering prerequisites and core concepts\n\nReturn a structured knowledge graph.\n'


def test_knowledge_graph_baseline_env_off():
    out = render_prompt(
        "knowledge_graph",
        topic="勾股定理",
        max_nodes=8,
        skill_instructions="",
        focus_directive="",
        review_weak_kps=[],
        review_strong_kps=[],
        existing_mastery=[],
        memory_snapshot_block="",
    )
    assert out == EXPECTED_KG_BASELINE
