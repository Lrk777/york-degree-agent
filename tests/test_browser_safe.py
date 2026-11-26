"""The web app runs these modules in Pyodide, which has no bs4/yaml/requests: keep it that way."""
import builtins
import importlib
import sys

BROWSER_MODULES = "codes descparse tree catalog freetext transcript audit suggest report retrieval agent webapi".split()


def test_browser_modules_need_no_server_libraries(monkeypatch):
    real = builtins.__import__

    def guard(name, *a, **k):
        if name.split(".")[0] in ("bs4", "yaml", "requests", "sklearn", "lxml"):
            raise ImportError(f"blocked in browser: {name}")
        return real(name, *a, **k)
    for m in list(sys.modules):
        if m.startswith("ydn"):
            monkeypatch.delitem(sys.modules, m)
    monkeypatch.setattr(builtins, "__import__", guard)
    for m in BROWSER_MODULES:
        importlib.import_module(f"ydn.{m}")
