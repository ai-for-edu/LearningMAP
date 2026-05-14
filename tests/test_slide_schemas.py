from src.llm.slide_schemas import (
    SlideData, TextElement, LatexElement, ShapeElement, LineElement, ImageElement, SlideOutline
)


def test_text_element():
    el = TextElement(
        id="text_001", type="text", left=60, top=80, width=880, height=76,
        content='<p style="font-size:32px;"><strong>Title</strong></p>',
        defaultFontName="", defaultColor="#333333",
    )
    assert el.type == "text"
    assert el.width == 880


def test_latex_element():
    el = LatexElement(
        id="latex_001", type="latex", left=100, top=200, width=300, height=120,
        latex="a^2 + b^2 = c^2", color="#000000",
    )
    assert el.latex == "a^2 + b^2 = c^2"


def test_shape_element():
    el = ShapeElement(
        id="shape_001", type="shape", left=60, top=200, width=400, height=100,
        path="M 0 0 L 1 0 L 1 1 L 0 1 Z", viewBox=[1, 1],
        fill="#5b9bd5", fixedRatio=False,
    )
    assert el.fill == "#5b9bd5"


def test_line_element():
    el = LineElement(
        id="line_001", type="line", left=100, top=200, width=3,
        start=[0, 0], end=[200, 0], style="solid", color="#5b9bd5",
        points=["", "arrow"],
    )
    assert el.width == 3  # stroke thickness, not length


def test_image_element():
    el = ImageElement(
        id="img_001", type="image", left=100, top=150, width=400, height=300,
        src="img_1", fixedRatio=True,
    )
    assert el.src == "img_1"


def test_slide_data():
    slide = SlideData(
        background={"type": "solid", "color": "#ffffff"},
        elements=[
            TextElement(id="t1", type="text", left=60, top=80, width=880, height=76,
                        content="<p>Title</p>", defaultFontName="", defaultColor="#333"),
        ],
    )
    assert len(slide.elements) == 1


def test_slide_outline():
    outline = SlideOutline(
        title="Pythagorean theorem concept", description="Introduction to the Pythagorean theorem",
        keyPoints=["a²+b²=c²", "right triangle"],
    )
    assert outline.title == "Pythagorean theorem concept"
