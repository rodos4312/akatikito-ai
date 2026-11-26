"""Characterization tests for html_navigation.analyze (Q4.5 safety net)."""
from __future__ import annotations

from akatikito.html_navigation import analyze
from akatikito.syntax_index import symbols


def test_html_analyze_scripts_styles_handlers_and_refs():
    html = """<!doctype html>
<html>
<head>
  <style>.btn { color: red; }</style>
  <style lang="scss">.bad { color: blue; }</style>
  <script src="app/main.js"></script>
  <script type="application/json">{"x": 1}</script>
</head>
<body>
  <button id="go" onclick="runAction(&quot;ok&quot;)">Go</button>
  <script>
    function initApp() { return 42; }
  </script>
  <svg><script>function insideSvg() {}</script></svg>
</body>
</html>
"""
    res = symbols(html, ".html")
    names = [s["name"] for s in res["symbols"]]
    assert "button#go[1]" in names
    assert "button#go[1].onclick" in names
    assert any(n.endswith("initApp") for n in names)
    assert "html-script:app/main.js" in res["refs"]
    assert res["injections"]
    assert res["style_injections"]
    assert "foreign SVG/MathML" in res["notice"]
    assert "μη υποστηριζόμενη γλώσσα style" in res["notice"]


def test_html_analyze_base_href_clears_relative_refs():
    html = '<html><head><base href="https://example.com/"><script src="local.js"></script></head></html>'
    res = symbols(html, ".html")
    assert res["refs"] == []
    assert "<base href>" in res["notice"]


def test_html_analyze_limits_and_parser_failures():
    limits = {"bytes": 10, "nodes": 100, "symbols": 100}
    over = analyze("<html><body>too long</body></html>", ".html", lambda b, n: None, lambda c, l: {}, limits)
    assert over["symbols"] == []
    assert "όριο 2 MiB" in over["notice"]

    big_limits = {"bytes": 10000, "nodes": 100, "symbols": 100}

    def fail_parse(_raw, _lang):
        raise ImportError("missing tree_sitter_html")

    missing = analyze("<html></html>", ".html", fail_parse, lambda c, l: {}, big_limits)
    assert missing["cacheable"] is False
    assert missing.get("missing_parser") is True

    timeout = analyze("<html></html>", ".html", lambda b, n: None, lambda c, l: {}, big_limits)
    assert timeout["cacheable"] is False
    assert "Χρονικό όριο" in timeout["notice"]
