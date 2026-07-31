import os
import sys
import importlib
import inspect
from typing import Any, Callable, Dict, List, Optional

from core.logger import AppLogger


PLUGIN_DIR = "plugins"


class PluginManager:
    def __init__(self, plugin_dir: str = PLUGIN_DIR) -> None:
        self.plugin_dir = plugin_dir
        self._before_hooks: List[Callable] = []
        self._after_hooks: List[Callable] = []
        self._before_validate_hooks: List[Callable] = []
        self._after_failure_hooks: List[Callable] = []
        self._after_retry_hooks: List[Callable] = []
        self._before_archive_hooks: List[Callable] = []
        self._plugins: Dict[str, Any] = {}
        self.load_all()

    def load_all(self) -> None:
        if not os.path.isdir(self.plugin_dir):
            os.makedirs(self.plugin_dir, exist_ok=True)
            init_file = os.path.join(self.plugin_dir, "__init__.py")
            if not os.path.exists(init_file):
                with open(init_file, "w") as f:
                    f.write("")
            return

        sys.path.insert(0, os.path.dirname(self.plugin_dir))

        for fname in sorted(os.listdir(self.plugin_dir)):
            if not fname.endswith(".py") or fname.startswith("_"):
                continue
            mod_name = fname[:-3]
            try:
                mod = importlib.import_module(f"{os.path.basename(self.plugin_dir)}.{mod_name}")
                self._register_module(mod, mod_name)
            except Exception as e:
                AppLogger.warn(f"Plugin load failed: {mod_name} -> {e}")

        sys.path.pop(0)

    def _register_module(self, mod: Any, name: str) -> None:
        hooks = 0
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if not callable(attr) or attr_name.startswith("_"):
                continue

            if hasattr(attr, "_before_send"):
                self._before_hooks.append(attr)
                hooks += 1
            if hasattr(attr, "_after_send"):
                self._after_hooks.append(attr)
                hooks += 1
            if hasattr(attr, "_before_validate"):
                self._before_validate_hooks.append(attr)
                hooks += 1
            if hasattr(attr, "_after_failure"):
                self._after_failure_hooks.append(attr)
                hooks += 1
            if hasattr(attr, "_after_retry"):
                self._after_retry_hooks.append(attr)
                hooks += 1
            if hasattr(attr, "_before_archive"):
                self._before_archive_hooks.append(attr)
                hooks += 1

        if hooks:
            self._plugins[name] = mod
            AppLogger.info(f"Plugin loaded: {name} ({hooks} hook(s))")

    def run_before_send(self, context: Dict[str, Any]) -> Dict[str, Any]:
        for hook in self._before_hooks:
            try:
                result = hook(context)
                if isinstance(result, dict):
                    context.update(result)
            except Exception as e:
                AppLogger.warn(f"before_send plugin error: {e}")
        return context

    def run_after_send(self, context: Dict[str, Any], result: Dict[str, Any]) -> None:
        for hook in self._after_hooks:
            try:
                hook(context, result)
            except Exception as e:
                AppLogger.warn(f"after_send plugin error: {e}")

    def run_before_validate(self, context: Dict[str, Any]) -> Dict[str, Any]:
        for hook in self._before_validate_hooks:
            try:
                result = hook(context)
                if isinstance(result, dict):
                    context.update(result)
            except Exception as e:
                AppLogger.warn(f"before_validate plugin error: {e}")
        return context

    def run_after_failure(self, context: Dict[str, Any]) -> None:
        for hook in self._after_failure_hooks:
            try:
                hook(context)
            except Exception as e:
                AppLogger.warn(f"after_failure plugin error: {e}")

    def run_after_retry(self, context: Dict[str, Any], attempt: int) -> None:
        for hook in self._after_retry_hooks:
            try:
                hook(context, attempt)
            except Exception as e:
                AppLogger.warn(f"after_retry plugin error: {e}")

    def run_before_archive(self, context: Dict[str, Any]) -> Dict[str, Any]:
        for hook in self._before_archive_hooks:
            try:
                result = hook(context)
                if isinstance(result, dict):
                    context.update(result)
            except Exception as e:
                AppLogger.warn(f"before_archive plugin error: {e}")
        return context

    @property
    def plugin_count(self) -> int:
        return len(self._plugins)


def hook_before_send(func: Callable) -> Callable:
    func._before_send = True
    return func


def hook_after_send(func: Callable) -> Callable:
    func._after_send = True
    return func


def hook_before_validate(func: Callable) -> Callable:
    func._before_validate = True
    return func


def hook_after_failure(func: Callable) -> Callable:
    func._after_failure = True
    return func


def hook_after_retry(func: Callable) -> Callable:
    func._after_retry = True
    return func


def hook_before_archive(func: Callable) -> Callable:
    func._before_archive = True
    return func
