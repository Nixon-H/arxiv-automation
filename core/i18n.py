import json
import os


class Translator:
    def __init__(self, locale: str = "en", locales_dir: str = "locales") -> None:
        self.locale = locale
        self.locales_dir = locales_dir
        self._strings: dict[str, str] = {}
        self._fallback: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        self._fallback = self._load_file("en")
        if self.locale != "en":
            self._strings = self._load_file(self.locale)
        else:
            self._strings = dict(self._fallback)

    def _load_file(self, locale: str) -> dict[str, str]:
        path = os.path.join(self.locales_dir, f"{locale}.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        return {}

    def translate(self, key: str, **kwargs: str) -> str:
        template = self._strings.get(key) or self._fallback.get(key) or key
        return template.format(**kwargs)

    def __call__(self, key: str, **kwargs: str) -> str:
        return self.translate(key, **kwargs)
