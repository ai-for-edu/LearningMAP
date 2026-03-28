from __future__ import annotations

from pathlib import Path
from jinja2 import Environment, FileSystemLoader

_PROMPTS_DIR = Path(__file__).parent
_env = Environment(loader=FileSystemLoader(str(_PROMPTS_DIR)), keep_trailing_newline=True)


def render_prompt(template_name: str, **kwargs: object) -> str:
    """Render a Jinja2 prompt template."""
    template = _env.get_template(f"{template_name}.j2")
    return template.render(**kwargs)
