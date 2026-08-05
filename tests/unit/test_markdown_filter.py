"""Tests for the web/markdown.render_markdown filter.

The filter renders LLM- and engineer-produced text as HTML for display,
escaping any raw HTML in the input so it can be marked Jinja-safe at
the template boundary without introducing XSS.
"""

from __future__ import annotations

from markupsafe import Markup

from predictive_review.web.markdown import render_markdown


def test_inline_code_renders_as_code_tag():
    out = render_markdown("Use `outerHTML` to swap.")
    assert "<code>outerHTML</code>" in out


def test_fenced_code_block_renders_as_pre_code():
    src = "```\nimport mistune\n```"
    out = render_markdown(src)
    assert "<pre>" in out
    assert "<code>" in out
    assert "import mistune" in out


def test_bullet_list_renders_as_ul():
    out = render_markdown("- one\n- two\n- three")
    assert out.count("<li>") == 3
    assert "<ul>" in out


def test_paragraphs_render_as_p_tags():
    out = render_markdown("first paragraph\n\nsecond paragraph")
    assert "<p>first paragraph</p>" in out
    assert "<p>second paragraph</p>" in out


def test_raw_html_in_input_is_escaped_not_rendered():
    """An LLM (or engineer) could emit a <script> tag inside the text;
    the filter must escape it so the output is harmless when marked safe."""
    out = render_markdown("watch out: <script>alert(1)</script>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_empty_input_returns_empty_markup():
    assert render_markdown("") == Markup("")
    assert render_markdown(None) == Markup("")


def test_output_is_markupsafe_so_jinja_does_not_re_escape():
    """The filter returns Markup so {{ x | markdown }} renders the HTML
    rather than escaping the angle brackets back to entities."""
    assert isinstance(render_markdown("hello"), Markup)
