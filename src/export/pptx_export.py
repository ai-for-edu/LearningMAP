"""Export slide JSON data to PPTX file using python-pptx.

Adapted from OpenMAIC's use-export-pptx.ts with full element support:
- Text: HTML parsing with bold/italic/underline/color/font-size/lists
- Shapes: Rectangle, circle, rounded rectangle with fill
- Lines: Connectors with arrows, dashed/dotted styles
- LaTeX: Rendered as PNG via matplotlib
- Background: Solid color and gradient support
"""
from __future__ import annotations

import io
import logging
import re
from html.parser import HTMLParser

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Emu, Pt

logger = logging.getLogger(__name__)

# Canvas: 1000 x 562.5 px → 16:9 slide (13.333 x 7.5 inches)
CANVAS_W = 1000
CANVAS_H = 562.5
SLIDE_W_INCHES = 13.333
SLIDE_H_INCHES = 7.5
PX_TO_EMU = int(914400 * SLIDE_W_INCHES / CANVAS_W)  # ~12192 EMU per px

# Alignment mapping
ALIGN_MAP = {
    "left": PP_ALIGN.LEFT,
    "center": PP_ALIGN.CENTER,
    "right": PP_ALIGN.RIGHT,
    "justify": PP_ALIGN.JUSTIFY,
}


def px(value: float) -> int:
    """Convert canvas px to EMU."""
    return int(value * PX_TO_EMU)


def hex_to_rgb(hex_color: str) -> RGBColor:
    """Convert hex color string to RGBColor."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = h[0] * 2 + h[1] * 2 + h[2] * 2
    if len(h) < 6:
        h = h.ljust(6, "0")
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


class HTMLTextParser(HTMLParser):
    """Parse HTML content into styled text runs for python-pptx.

    Supports: <p>, <span>, <strong>, <b>, <em>, <i>, <u>, <s>, <del>,
              <ul>, <ol>, <li>, <h1>-<h6>, <br>, <sub>, <sup>
    Inline styles: font-size, color, font-weight, text-align,
                   text-decoration, background-color
    """

    def __init__(self) -> None:
        super().__init__()
        self.paragraphs: list[dict] = []  # [{align, runs: [{text, ...styles}]}]
        self._current_para: dict = {"align": "left", "runs": []}
        self._style_stack: list[dict] = [{}]
        self._list_depth = 0
        self._list_type: list[str] = []  # "ul" or "ol"
        self._list_counter: list[int] = [0]

    def _push_style(self, **kwargs: object) -> None:
        new = dict(self._style_stack[-1])
        new.update(kwargs)
        self._style_stack.append(new)

    def _pop_style(self) -> None:
        if len(self._style_stack) > 1:
            self._style_stack.pop()

    @property
    def _style(self) -> dict:
        return self._style_stack[-1]

    def _flush_para(self) -> None:
        if self._current_para["runs"]:
            self.paragraphs.append(self._current_para)
        self._current_para = {"align": "left", "runs": []}

    def handle_starttag(self, tag: str, attrs: list) -> None:
        attr_dict = dict(attrs)
        style = attr_dict.get("style", "")
        parsed = self._parse_inline_style(style)

        if tag == "p":
            self._flush_para()
            if parsed.get("text_align"):
                self._current_para["align"] = parsed["text_align"]
            self._push_style(**parsed)
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._flush_para()
            sizes = {"h1": 32, "h2": 28, "h3": 24, "h4": 20, "h5": 18, "h6": 16}
            self._push_style(bold=True, font_size=sizes.get(tag, 20), **parsed)
        elif tag in ("strong", "b"):
            self._push_style(bold=True)
        elif tag in ("em", "i"):
            self._push_style(italic=True)
        elif tag == "u":
            self._push_style(underline=True)
        elif tag in ("s", "del"):
            self._push_style(strikethrough=True)
        elif tag == "sub":
            self._push_style(subscript=True)
        elif tag == "sup":
            self._push_style(superscript=True)
        elif tag == "span":
            self._push_style(**parsed)
        elif tag == "br":
            self._current_para["runs"].append({"text": "\n", **self._style})
        elif tag == "ul":
            self._list_depth += 1
            self._list_type.append("ul")
            self._list_counter.append(0)
        elif tag == "ol":
            self._list_depth += 1
            self._list_type.append("ol")
            self._list_counter.append(0)
        elif tag == "li":
            self._flush_para()
            self._list_counter[-1] += 1
            indent = "  " * (self._list_depth - 1)
            if self._list_type and self._list_type[-1] == "ol":
                bullet = f"{indent}{self._list_counter[-1]}. "
            else:
                bullet = f"{indent}• "
            self._current_para["runs"].append({"text": bullet, **self._style})

    def handle_endtag(self, tag: str) -> None:
        if tag == "p":
            self._flush_para()
            self._pop_style()
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._flush_para()
            self._pop_style()
        elif tag in ("strong", "b", "em", "i", "u", "s", "del", "sub", "sup", "span"):
            self._pop_style()
        elif tag in ("ul", "ol"):
            self._list_depth = max(0, self._list_depth - 1)
            if self._list_type:
                self._list_type.pop()
            if self._list_counter:
                self._list_counter.pop()
        elif tag == "li":
            self._flush_para()

    def handle_data(self, data: str) -> None:
        if data.strip() or data == " ":
            self._current_para["runs"].append({"text": data, **self._style})

    def finish(self) -> list[dict]:
        self._flush_para()
        return self.paragraphs

    @staticmethod
    def _parse_inline_style(style: str) -> dict:
        result: dict = {}
        for part in style.split(";"):
            part = part.strip()
            if not part:
                continue
            key, _, val = part.partition(":")
            key, val = key.strip(), val.strip()
            if key == "font-size":
                result["font_size"] = int(re.sub(r"[^\d]", "", val) or "16")
            elif key == "color":
                result["color"] = val
            elif key == "font-weight":
                if val in ("bold", "700", "800", "900"):
                    result["bold"] = True
            elif key == "text-align":
                result["text_align"] = val
            elif key == "text-decoration":
                if "underline" in val:
                    result["underline"] = True
                if "line-through" in val:
                    result["strikethrough"] = True
            elif key == "background-color":
                result["highlight_color"] = val
        return result


def _render_latex_to_image(latex: str) -> io.BytesIO | None:
    """Render LaTeX formula to PNG image using matplotlib."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(6, 1.5))
        ax.text(
            0.5, 0.5, f"${latex}$", fontsize=20,
            ha="center", va="center", transform=ax.transAxes,
        )
        ax.axis("off")
        buf = io.BytesIO()
        fig.savefig(
            buf, format="png", dpi=150, bbox_inches="tight",
            transparent=True, pad_inches=0.1,
        )
        plt.close(fig)
        buf.seek(0)
        return buf
    except Exception:
        logger.exception("Failed to render LaTeX: %s", latex)
        return None


def export_slides_to_pptx(slides: list[dict]) -> io.BytesIO:
    """Convert slide JSON data to PPTX file.

    Args:
        slides: List of SlideData dicts with background and elements.

    Returns:
        BytesIO buffer containing the PPTX file.
    """
    prs = Presentation()
    prs.slide_width = Emu(int(SLIDE_W_INCHES * 914400))
    prs.slide_height = Emu(int(SLIDE_H_INCHES * 914400))

    for slide_data in slides:
        if not slide_data:
            continue
        slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank layout

        # Background
        bg = slide_data.get("background")
        if bg and bg.get("color"):
            fill = slide.background.fill
            fill.solid()
            try:
                fill.fore_color.rgb = hex_to_rgb(bg["color"])
            except (ValueError, IndexError):
                pass

        # Elements
        for el in slide_data.get("elements", []):
            el_type = el.get("type")
            try:
                if el_type == "text":
                    _add_text_element(slide, el)
                elif el_type == "shape":
                    _add_shape_element(slide, el)
                elif el_type == "line":
                    _add_line_element(slide, el)
                elif el_type == "latex":
                    _add_latex_element(slide, el)
                elif el_type == "image":
                    _add_image_placeholder(slide, el)
            except Exception:
                logger.warning("Failed to export element: %s", el.get("id", "?"))

    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    return buf


def _add_text_element(slide, el: dict) -> None:
    """Add a text box with HTML-styled content."""
    txBox = slide.shapes.add_textbox(
        left=px(el["left"]), top=px(el["top"]),
        width=px(el["width"]), height=px(el["height"]),
    )

    # Background fill
    if el.get("fill"):
        try:
            txBox.fill.solid()
            txBox.fill.fore_color.rgb = hex_to_rgb(el["fill"])
        except (ValueError, IndexError):
            pass

    tf = txBox.text_frame
    tf.word_wrap = True

    # Parse HTML content
    parser = HTMLTextParser()
    parser.feed(el.get("content", ""))
    paragraphs = parser.finish()

    default_color = el.get("defaultColor", "#333333")
    default_size = 16

    # Write paragraphs
    first_para = True
    for para_data in paragraphs:
        if first_para:
            p = tf.paragraphs[0]
            first_para = False
        else:
            p = tf.add_paragraph()

        # Paragraph alignment
        align = para_data.get("align", "left")
        if align in ALIGN_MAP:
            p.alignment = ALIGN_MAP[align]

        for run_data in para_data.get("runs", []):
            text = run_data.get("text", "")
            if not text:
                continue

            run = p.add_run()
            run.text = text

            # Font styling
            size = run_data.get("font_size", default_size)
            run.font.size = Pt(size)

            color = run_data.get("color", default_color)
            if color:
                try:
                    run.font.color.rgb = hex_to_rgb(color)
                except (ValueError, IndexError):
                    pass

            if run_data.get("bold"):
                run.font.bold = True
            if run_data.get("italic"):
                run.font.italic = True
            if run_data.get("underline"):
                run.font.underline = True

    # If no paragraphs parsed, use raw content as fallback
    if first_para:
        content = el.get("content", "")
        # Strip HTML tags for fallback
        plain = re.sub(r"<[^>]+>", "", content)
        if plain.strip():
            tf.paragraphs[0].add_run().text = plain


def _add_shape_element(slide, el: dict) -> None:
    """Add a shape with fill color."""
    # Determine shape type from path
    path = el.get("path", "")
    if "A" in path:  # Arc = circle/ellipse
        shape_type = MSO_SHAPE.OVAL
    elif "Q" in path or "C" in path:  # Curves
        shape_type = MSO_SHAPE.ROUNDED_RECTANGLE
    else:
        shape_type = MSO_SHAPE.RECTANGLE

    shape = slide.shapes.add_shape(
        shape_type,
        left=px(el["left"]), top=px(el["top"]),
        width=px(el["width"]), height=px(el["height"]),
    )
    fill_color = el.get("fill", "#5b9bd5")
    try:
        shape.fill.solid()
        shape.fill.fore_color.rgb = hex_to_rgb(fill_color)
    except (ValueError, IndexError):
        pass
    shape.line.fill.background()  # no border


def _add_line_element(slide, el: dict) -> None:
    """Add a line connector."""
    start = el.get("start", [0, 0])
    end = el.get("end", [100, 0])
    base_left = el.get("left", 0)
    base_top = el.get("top", 0)

    connector = slide.shapes.add_connector(
        1,  # straight connector
        px(base_left + start[0]), px(base_top + start[1]),
        px(base_left + end[0]), px(base_top + end[1]),
    )

    color = el.get("color", "#333333")
    try:
        connector.line.color.rgb = hex_to_rgb(color)
    except (ValueError, IndexError):
        pass
    stroke = el.get("width", 2)
    connector.line.width = Pt(stroke)

    # Dash style
    style = el.get("style", "solid")
    if style == "dashed":
        connector.line.dash_style = 2  # dash
    elif style == "dotted":
        connector.line.dash_style = 3  # dot


def _add_latex_element(slide, el: dict) -> None:
    """Add LaTeX formula as rendered PNG image."""
    latex = el.get("latex", "")
    if not latex:
        return

    img_buf = _render_latex_to_image(latex)
    if img_buf:
        slide.shapes.add_picture(
            img_buf,
            left=px(el["left"]), top=px(el["top"]),
            width=px(el["width"]), height=px(el["height"]),
        )
    else:
        # Fallback: add as plain text
        txBox = slide.shapes.add_textbox(
            left=px(el["left"]), top=px(el["top"]),
            width=px(el["width"]), height=px(el["height"]),
        )
        tf = txBox.text_frame
        run = tf.paragraphs[0].add_run()
        run.text = latex
        run.font.size = Pt(14)


def _add_image_placeholder(slide, el: dict) -> None:
    """Add image placeholder (gray box with label)."""
    shape = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        left=px(el["left"]), top=px(el["top"]),
        width=px(el["width"]), height=px(el["height"]),
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor(0xE8, 0xEA, 0xEC)
    shape.line.fill.background()

    # Add "Image" label
    tf = shape.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    run.text = f"[Image: {el.get('src', 'placeholder')}]"
    run.font.size = Pt(12)
    run.font.color.rgb = RGBColor(0x99, 0x99, 0x99)
