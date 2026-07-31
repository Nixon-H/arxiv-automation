import pytest

from engine.templates import TemplateEngine


@pytest.fixture
def template_dir(tmp_path):
    d = tmp_path / "templates"
    d.mkdir()
    (d / "template.txt").write_text("Dear {{ last_name }},\n{{ paper_title }}\nBest,\n{{ your_name }}")
    (d / "template.html").write_text("<html><body>Dear {{ last_name }},<br>{{ paper_title }}</body></html>")
    return d


class TestTemplateEngine:
    def test_render_basic(self, template_dir):
        eng = TemplateEngine(
            txt_paths=[str(template_dir / "template.txt")],
            html_paths=[str(template_dir / "template.html")],
        )
        ctx = {"last_name": "Smith", "paper_title": "Q Computing", "your_name": "Alice", "arxiv_category": "cs.AI"}
        result = eng.render_all(ctx)
        assert "Dear Smith" in result["text_body"]
        assert "Q Computing" in result["text_body"]
        assert "Alice" in result["text_body"]
        assert "Dear Smith" in result["html_body"]

    def test_render_subject(self, template_dir):
        eng = TemplateEngine(
            txt_paths=[str(template_dir / "template.txt")],
            html_paths=[str(template_dir / "template.html")],
        )
        ctx = {"last_name": "Jones", "paper_title": "ML", "your_name": "Bob", "arxiv_category": "cs.LG"}
        subject = eng.render_subject(ctx)
        assert isinstance(subject, str)
        assert len(subject) > 0

    def test_render_signature(self, template_dir):
        eng = TemplateEngine(
            txt_paths=[str(template_dir / "template.txt")],
            html_paths=[str(template_dir / "template.html")],
        )
        ctx = {"your_name": "Alice", "arxiv_category": "cs.AI"}
        sig = eng.render_signature(ctx)
        assert isinstance(sig, str)
        assert len(sig) > 0

    def test_missing_template(self):
        eng = TemplateEngine(txt_paths=["/nonexistent.txt"], html_paths=["/nonexistent.html"])
        ctx = {"last_name": "X", "paper_title": "Y", "your_name": "Z", "arxiv_category": "cs.AI"}
        result = eng.render_all(ctx)
        assert ctx["last_name"] in result["text_body"]

    def test_missing_context_var(self, template_dir):
        eng = TemplateEngine(
            txt_paths=[str(template_dir / "template.txt")],
            html_paths=[str(template_dir / "template.html")],
        )
        ctx = {"last_name": "A"}
        result = eng.render_all(ctx)
        assert "A" in result["text_body"]
