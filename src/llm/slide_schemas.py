"""Pydantic models for PPT slide elements, matching OpenMAIC's schema."""
from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, Field


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
    points: list[str] = Field(default_factory=lambda: ["", ""])


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


class ChartData(BaseModel):
    """Chart data structure."""

    labels: list[str]
    legends: list[str]
    series: list[list[float]]


class ChartElement(BaseModel):
    """Chart element for PPT slides (bar, line, pie, etc.)."""

    id: str
    type: Literal["chart"]
    left: float
    top: float
    width: float
    height: float
    chartType: str  # bar, column, line, pie, ring, area, radar, scatter
    data: ChartData
    themeColors: list[str] = Field(default_factory=lambda: ["#5b9bd5", "#ed7d31"])
    fill: str | None = None
    textColor: str | None = None
    lineColor: str | None = None


class TableCellStyle(BaseModel):
    """Table cell styling."""

    bold: bool | None = None
    em: bool | None = None
    underline: bool | None = None
    strikethrough: bool | None = None
    color: str | None = None
    backcolor: str | None = None
    fontsize: str | None = None
    fontname: str | None = None
    align: str | None = None


class TableCell(BaseModel):
    """Single table cell."""

    id: str
    colspan: int = 1
    rowspan: int = 1
    text: str = ""
    style: TableCellStyle | None = None


class TableElement(BaseModel):
    """Table element for PPT slides."""

    id: str
    type: Literal["table"]
    left: float
    top: float
    width: float
    height: float
    colWidths: list[float]
    data: list[list[TableCell]]
    outline: dict | None = None
    cellMinHeight: float = 36
    theme: dict | None = None


PPTElement = Union[TextElement, LatexElement, ShapeElement, LineElement, ImageElement, ChartElement, TableElement]


class SlideData(BaseModel):
    """Single slide containing background and elements."""

    background: dict | None = None
    elements: list[PPTElement] = Field(default_factory=list)


class SlideOutline(BaseModel):
    """Outline for a single slide used in planning stage."""

    title: str
    description: str
    keyPoints: list[str]
