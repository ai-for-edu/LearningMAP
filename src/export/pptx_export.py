"""Export slide JSON data to PPTX file using python-pptx."""
from __future__ import annotations

import io
import logging
import re
import tempfile
from html.parser import HTMLParser

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Emu, Pt

logger = logging.getLogger(__name__)

# Canvas: 1000 x 562.5 px → 16:9 slide (13.333 x 7.5 inches)
CANVAS_W = 1000
CANVAS_H = 562.5
SLIDE_W_INCHES = 13.333
SLIDE_H_INCHES = 7.5
PX_TO_EMU = int(914400 * SLIDE_W_INCHES / CANVAS_W)  # ~12192 EMU per px


def px(value: float) -> int:
    """Convert canvas px to EMU."""
    return int(value * PX_TO_EMU)


def hex_to_rgb(hex_color: str) -> RGBColor:
    """Convert hex color string to RGBColor."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = h[0] * 2 + h[1] * 2 + h[2] * 2
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


class SimpleHTMLTextParser(HTMLParser):
    """Parse simple HTML content into styled text runs."""

    def __init__(self) -> None:
        super().__init__()
        self.runs: list[dict] = []
        self._current_styles: dict = {}
        self._in_p = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        attr_dict = dict(attrs)
        if tag == "p":
            self._in_p = True
            style = attr_dict.get("style", "")
            self._current_styles = self._parse_inline_style(style)
        elif tag in ("strong", "b"):
            self._current_styles["bold"] = True
        elif tag in ("em", "i"):
            self._current_styles["italic"] = True
        elif tag == "u":
            self._current_styles["underline"] = True
        elif tag == "span":
            style = attr_dict.get("style", "")
            self._current_styles.update(self._parse_inline_style(style))

    def handle_endtag(self, tag: str) -> None:
        if tag == "p":
            self.runs.append({"text": "\n", **self._current_styles})
            self._in_p = False
        elif tag in ("strong", "b"):
            self._current_styles.pop("bold", None)
        elif tag in ("em", "i"):
            self._current_styles.pop("italic", None)
        elif tag == "u":
            self._current_styles.pop("underline", None)

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.runs.append({"text": data, **dict(self._current_styles)})

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
            elif key == "font-weight" and val in ("bold", "700", "800", "900"):
                result["bold"] = True
            elif key == "text-align":
                result["text_align"] = val
        return result


def _render_latex_to_image(latex: str) -> io.BytesIO | None:
    """Render LaTeX formula to PNG image using matplotlib."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(6, 1.5))
        ax.text(0.5, 0.5, f"${latex}$", fontsize=20, ha="center", va="center",
                transform=ax.transAxes)
        ax.axis("off")
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                    transparent=True, pad_inches=0.1)
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
            fill.fore_color.rgb = hex_to_rgb(bg["color"])

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
        txBox.fill.solid()
        txBox.fill.fore_color.rgb = hex_to_rgb(el["fill"])

    tf = txBox.text_frame
    tf.word_wrap = True

    # Parse HTML content
    parser = SimpleHTMLTextParser()
    parser.feed(el.get("content", ""))

    default_color = el.get("defaultColor", "#333333")
    default_size = 16

    # Clear default paragraph
    first = True
    current_p = tf.paragraphs[0]

    for run_data in parser.runs:
        text = run_data["text"]
        if text == "\n":
            if not first:
                current_p = tf.add_paragraph()
            first = False
            continue

        run = current_p.add_run()
        run.text = text

        # Font styling
        size = run_data.get("font_size", default_size)
        run.font.size = Pt(size)

        color = run_data.get("color", default_color)
        if color:
            run.font.color.rgb = hex_to_rgb(color)

        if run_data.get("bold"):
            run.font.bold = True
        if run_data.get("italic"):
            run.font.italic = True
        if run_data.get("underline"):
            run.font.underline = True


def _add_shape_element(slide, el: dict) -> None:
    """Add a rectangle shape with fill color."""
    shape = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        left=px(el["left"]), top=px(el["top"]),
        width=px(el["width"]), height=px(el["height"]),
    )
    fill_color = el.get("fill", "#5b9bd5")
    shape.fill.solid()
    shape.fill.fore_color.rgb = hex_to_rgb(fill_color)
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
    connector.line.color.rgb = hex_to_rgb(color)
    stroke = el.get("width", 2)
    connector.line.width = Pt(stroke)


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
        txBox.text_frame.paragraphs[0].add_run().text = latex
