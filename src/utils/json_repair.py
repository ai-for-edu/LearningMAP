r"""JSON parsing with fallback strategies for AI-generated responses.

Adapted from OpenMAIC's json-repair.ts. Handles common issues like:
- Code block wrappers (```json ... ```)
- LaTeX escapes breaking JSON (\frac, \sqrt, etc.)
- Truncated JSON arrays/objects
- Control characters
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def parse_json_response(response: str) -> Any | None:
    """Parse JSON from an AI response with multiple fallback strategies.

    Args:
        response: Raw LLM response text.

    Returns:
        Parsed JSON object, or None if all strategies fail.
    """
    # Strategy 1: Extract from markdown code blocks
    code_blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)```", response)
    for block in code_blocks:
        block = block.strip()
        if block.startswith("{") or block.startswith("["):
            result = _try_parse_json(block)
            if result is not None:
                logger.debug("Parsed JSON from code block")
                return result

    # Strategy 2: Find JSON structure directly in response
    json_start_arr = response.find("[")
    json_start_obj = response.find("{")

    if json_start_arr != -1 or json_start_obj != -1:
        if json_start_arr == -1:
            start = json_start_obj
        elif json_start_obj == -1:
            start = json_start_arr
        else:
            start = min(json_start_arr, json_start_obj)

        # Find matching close bracket with bracket counting
        depth = 0
        end = -1
        in_string = False
        escape_next = False

        for i in range(start, len(response)):
            char = response[i]

            if escape_next:
                escape_next = False
                continue

            if char == "\\" and in_string:
                escape_next = True
                continue

            if char == '"' and not escape_next:
                in_string = not in_string
                continue

            if not in_string:
                if char in "[{":
                    depth += 1
                elif char in "]}":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break

        if end != -1:
            json_str = response[start:end + 1]
            result = _try_parse_json(json_str)
            if result is not None:
                logger.debug("Parsed JSON from response body")
                return result

    # Strategy 3: Try the whole response
    result = _try_parse_json(response.strip())
    if result is not None:
        logger.debug("Parsed raw response as JSON")
        return result

    logger.error("Failed to parse JSON. First 500 chars: %s", response[:500])
    return None


def _try_parse_json(json_str: str) -> Any | None:
    """Try to parse JSON with various fixes for common AI issues.

    Args:
        json_str: JSON string to parse.

    Returns:
        Parsed object or None.
    """
    # Attempt 1: Parse as-is
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass

    # Attempt 2: Fix common AI response issues
    try:
        fixed = json_str

        # Fix 1: Handle LaTeX escapes that break JSON
        # Double-escape backslash+letter inside strings (except valid JSON escapes)
        def _fix_latex_in_string(match: re.Match) -> str:
            content = match.group(1)
            # Double-escape \letter that isn't a valid JSON escape
            content = re.sub(
                r"\\([a-zA-Z])",
                r"\\\\\\1",  # Replace \x with \\x (escaped for JSON)
                content,
            )
            return f'"{content}"'

        fixed = re.sub(r'"((?:[^"\\]|\\.)*?)"', _fix_latex_in_string, fixed)

        # Fix 2: Fix truncated JSON — find last complete element
        trimmed = fixed.strip()

        # Strategy: find the last complete JSON object in the elements array
        # by looking for the last "}," or "}" before truncation
        if trimmed.startswith("{") and not trimmed.endswith("}"):
            # Try to salvage: find last complete element in "elements" array
            last_complete = fixed.rfind("},")
            last_close = fixed.rfind("}")

            if last_complete > 0 and "elements" in fixed:
                # Cut after last complete element, close array and object
                salvaged = fixed[:last_complete + 1] + "]}"
                try:
                    result = json.loads(salvaged)
                    logger.warning("Fixed truncated JSON by cutting at last complete element")
                    return result
                except json.JSONDecodeError:
                    pass

            # Simpler: just close unclosed braces
            open_braces = fixed.count("{")
            close_braces = fixed.count("}")
            open_brackets = fixed.count("[")
            close_brackets = fixed.count("]")
            if open_braces > close_braces or open_brackets > close_brackets:
                fixed += "]" * (open_brackets - close_brackets)
                fixed += "}" * (open_braces - close_braces)
                logger.warning("Fixed truncated JSON by closing braces")

        elif trimmed.startswith("[") and not trimmed.endswith("]"):
            last_obj = fixed.rfind("}")
            if last_obj > 0:
                fixed = fixed[:last_obj + 1] + "]"
                logger.warning("Fixed truncated JSON array")

        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # Attempt 3: Remove control characters
    try:
        fixed = json_str
        fixed = re.sub(
            r"[\x00-\x1f\x7f]",
            lambda m: {"\\n": "\n", "\\r": "\r", "\\t": "\t"}.get(
                repr(m.group())[1:-1], ""
            ),
            fixed,
        )
        return json.loads(fixed)
    except (json.JSONDecodeError, ValueError):
        pass

    return None
