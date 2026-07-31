import os
import random
import time
import re
from typing import Dict, Any, List, Optional

from core.exceptions import TemplateRenderError
from core.logger import AppLogger

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_ENTITY_RE = re.compile(r"&[a-zA-Z]+;|&#\d+;")
_HTML_ENTITY_MAP = {
    "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'",
    "&nbsp;": " ", "&mdash;": "—", "&ndash;": "–",
}


def strip_html(html: str) -> str:
    def replace_entity(m: re.Match) -> str:
        return _HTML_ENTITY_MAP.get(m.group(0), " ")
    text = _HTML_ENTITY_RE.sub(replace_entity, html)
    text = _HTML_TAG_RE.sub("", text)
    text = re.sub(r"\n\s*\n", "\n\n", text)
    return text.strip()


SUBJECT_TEMPLATES = [
    "arXiv Endorsement Request: {{ your_paper_title }}",
    "Endorsement Request for arXiv Submission: {{ your_paper_title }}",
    "Could You Endorse My arXiv Paper? ({{ your_paper_title }})",
    "Request for arXiv Endorsement — {{ your_paper_title }}",
    "Seeking Endorsement: {{ your_paper_title }} (arXiv {{ arxiv_category }})",
]

SIGNATURE_PROFILES = [
    {
        "name": "{{ your_name }}",
        "title": "Independent Researcher",
        "extra": "",
    },
    {
        "name": "{{ your_name }}",
        "title": "Independent Researcher",
        "extra": "\nhttps://scholar.google.com/citations?user=XXXXXXXX",
    },
]


class TemplateEngine:
    def __init__(
        self,
        txt_paths: Optional[List[str]] = None,
        html_paths: Optional[List[str]] = None,
        auto_reload: bool = True,
    ) -> None:
        self.txt_paths = txt_paths or ["template.txt"]
        self.html_paths = html_paths or ["template.html"]
        self.auto_reload = auto_reload
        self._txt_cache: List[str] = []
        self._html_cache: List[str] = []
        self._txt_mtimes: List[float] = []
        self._html_mtimes: List[float] = []
        self._load_templates()

    def _load_templates(self) -> None:
        self._txt_cache.clear()
        self._html_cache.clear()
        self._txt_mtimes.clear()
        self._html_mtimes.clear()

        for path in self.txt_paths:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    self._txt_cache.append(f.read())
                self._txt_mtimes.append(os.path.getmtime(path))
            else:
                AppLogger.warn(f"Text template not found: {path}")

        for path in self.html_paths:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    self._html_cache.append(f.read())
                self._html_mtimes.append(os.path.getmtime(path))
            else:
                AppLogger.warn(f"HTML template not found: {path}")

        if not self._txt_cache:
            self._txt_cache.append(
                "Dear Dr./Mr./Ms. {{ last_name }},\n\n"
                "Regarding your paper: {{ paper_title }}..."
            )
        if not self._html_cache:
            self._html_cache.append(
                "<p>Dear Dr./Mr./Ms. {{ last_name }},</p>"
                "<p>Regarding your paper: <em>{{ paper_title }}</em>...</p>"
            )

    def _check_reload(self) -> None:
        if not self.auto_reload:
            return
        needs_reload = False
        for i, path in enumerate(self.txt_paths):
            if os.path.exists(path) and i < len(self._txt_mtimes):
                if os.path.getmtime(path) != self._txt_mtimes[i]:
                    needs_reload = True
                    break
        if not needs_reload:
            for i, path in enumerate(self.html_paths):
                if os.path.exists(path) and i < len(self._html_mtimes):
                    if os.path.getmtime(path) != self._html_mtimes[i]:
                        needs_reload = True
                        break
        if needs_reload:
            AppLogger.info("Templates changed on disk — reloading")
            self._load_templates()

    def _interpolate(self, template: str, context: Dict[str, Any]) -> str:
        result = template
        for key, val in context.items():
            result = result.replace("{{ " + key + " }}", str(val))
            result = result.replace("{{" + key + "}}", str(val))
            result = result.replace("{ " + key + " }", str(val))
            result = result.replace("{" + key + "}", str(val))
        return result

    def render_subject(self, context: Dict[str, Any]) -> str:
        self._check_reload()
        template = random.choice(SUBJECT_TEMPLATES)
        return self._interpolate(template, context)

    def generate_plain_text(self, html_body: str) -> str:
        return strip_html(html_body)

    def get_cache_stats(self) -> Dict[str, Any]:
        self._check_reload()
        return {
            "txt_templates": len(self._txt_cache),
            "html_templates": len(self._html_cache),
            "subject_templates": len(SUBJECT_TEMPLATES),
            "signatures": len(SIGNATURE_PROFILES),
        }

    def render_text(self, context: Dict[str, Any], variant: Optional[int] = None) -> str:
        self._check_reload()
        if variant is not None and 0 <= variant < len(self._txt_cache):
            idx = variant
        else:
            idx = random.randrange(len(self._txt_cache))
        return self._interpolate(self._txt_cache[idx], context)

    def render_file(self, path: str, context: Dict[str, Any]) -> str:
        """Render an arbitrary template file (e.g. follow-up template)."""
        if not os.path.exists(path):
            raise TemplateRenderError(f"Template file not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            return self._interpolate(f.read(), context)

    def render_html(self, context: Dict[str, Any], variant: Optional[int] = None) -> str:
        self._check_reload()
        if variant is not None and 0 <= variant < len(self._html_cache):
            idx = variant
        else:
            idx = random.randrange(len(self._html_cache))
        return self._interpolate(self._html_cache[idx], context)

    def render_signature(self, context: Dict[str, Any], variant: Optional[int] = None) -> str:
        if variant is not None and 0 <= variant < len(SIGNATURE_PROFILES):
            idx = variant
        else:
            idx = random.randrange(len(SIGNATURE_PROFILES))
        profile = SIGNATURE_PROFILES[idx]
        sig = self._interpolate(profile["name"], context)
        sig += "\n" + self._interpolate(profile["title"], context)
        if profile["extra"]:
            sig += self._interpolate(profile["extra"], context)
        return sig

    def get_required_vars(self) -> List[str]:
        required: List[str] = []
        for t in self._txt_cache + self._html_cache:
            required.extend(re.findall(r"\{\{\s*(\w+)\s*\}\}", t))
        for s in SUBJECT_TEMPLATES:
            required.extend(re.findall(r"\{\{\s*(\w+)\s*\}\}", s))
        for p in SIGNATURE_PROFILES:
            for v in p.values():
                required.extend(re.findall(r"\{\{\s*(\w+)\s*\}\}", str(v)))
        return sorted(set(required))

    def validate_context(self, context: Dict[str, Any]) -> List[str]:
        missing: List[str] = []
        for var in self.get_required_vars():
            if var not in context or context[var] is None:
                missing.append(var)
        return missing

    def render_all(
        self, context: Dict[str, Any]
    ) -> Dict[str, str]:
        return {
            "subject": self.render_subject(context),
            "text_body": self.render_text(context),
            "html_body": self.render_html(context),
            "signature": self.render_signature(context),
        }
