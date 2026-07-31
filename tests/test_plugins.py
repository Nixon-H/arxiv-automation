import os
import tempfile

from engine.plugins import PluginManager, hook_after_send, hook_before_send


class TestPluginManager:
    def test_init_no_plugins_dir(self):
        pm = PluginManager(plugin_dir="/tmp/__nonexistent_plugin_dir__")
        assert pm.plugin_count == 0

    def test_run_before_send_no_plugins(self):
        pm = PluginManager(plugin_dir="/tmp/__nonexistent_plugin_dir__")
        ctx = {"key": "value"}
        result = pm.run_before_send(ctx)
        assert result == ctx

    def test_run_after_send_no_plugins(self):
        pm = PluginManager(plugin_dir="/tmp/__nonexistent_plugin_dir__")
        pm.run_after_send({"a": 1}, {"b": 2})
        assert True

    def test_init_creates_plugins_dir(self):
        with tempfile.TemporaryDirectory() as base:
            plugin_dir = os.path.join(base, "my_plugins")
            pm = PluginManager(plugin_dir=plugin_dir)
            assert os.path.isdir(plugin_dir)
            assert pm.plugin_count == 0

    def test_init_with_existing_dir(self):
        with tempfile.TemporaryDirectory() as plugin_dir:
            pm = PluginManager(plugin_dir=plugin_dir)
            assert pm.plugin_count == 0

    def test_hook_decorators(self):
        @hook_before_send
        def my_before(ctx):
            return ctx

        @hook_after_send
        def my_after(ctx, result):
            pass

        assert hasattr(my_before, "_before_send")
        assert my_before._before_send is True
        assert hasattr(my_after, "_after_send")
        assert my_after._after_send is True
