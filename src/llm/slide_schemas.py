"""Pydantic models for PPT slide elements, matching OpenMAIC's schema."""
from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel


class TextElement(BaseModel):
    """Text element for PPT slides."""

    id: str
    type: Literal["text"]
    left: float
    top: float
    width: float
    height: float
    content: str
    defaultFontName: str = ""
    defaultColor: str = "#333333"
    rotate: float = 0
    lineHeight: float = 1.5
    opacity: float = 1
    fill: str | None = None


class LatexElement(BaseModel):
    """LaTeX formula element for PPT slides."""

    id: str
    type: Literal["latex"]
    left: float
    top: float
    width: float
    height: float
    latex: str
    color: str = "#000000"
    align: str = "center"


class ShapeElement(BaseModel):
    """Shape element for PPT slides."""

    id: str
    type: Literal["shape"]
    left: float
    top: float
    width: float
    height: float
    path: str
    viewBox: list[float]
    fill: str = "#5b9bd5"
    fixedRatio: bool = False


class LineElement(BaseModel):
    """Line element for PPT slides."""

    id: str
    type: Literal["line"]
    left: float
    top: float
    width: float
    start: list[float]
    end: list[float]
    style: str = "solid"
    color: str = "#333333"
    points: list[str] = ["", ""]


class ImageElement(BaseModel):
    """Image element for PPT slides."""

    id: str
    type: Literal["image"]
    left: float
    top: float
    width: float
    height: float
    src: str
    fixedRatio: bool = True


PPTElement = Union[TextElement, LatexElement, ShapeElement, LineElement, ImageElement]


class SlideData(BaseModel):
    """Single slide containing background and elements."""

    background: dict | None = None
    elements: list[PPTElement] = []


class SlideOutline(BaseModel):
    """Outline for a single slide used in planning stage."""

    title: str
    description: str
    keyPoints: list[str]
