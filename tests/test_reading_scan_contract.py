"""Characterization tests for reading.scan_definitions across all 6 language families (Q4.4)."""
from __future__ import annotations

from akatikito.reading import scan_definitions


def _names(blocks):
    return [(start, end, name) for start, end, name, _ in blocks]


def test_scan_definitions_empty_and_unsupported():
    assert scan_definitions([], ".js") == []
    assert scan_definitions(["def foo(): pass"], ".py") == []


def test_scan_definitions_javascript():
    src = [
        "class Widget {",
        "  constructor(x) {",
        "    this.x = x;",
        "  }",
        "  render() {",
        "    return this.x;",
        "  }",
        "}",
        "function helper(a) {",
        "  return a + 1;",
        "}",
    ]
    blocks = _names(scan_definitions(src, ".js"))
    assert blocks == [
        (1, 8, "Widget"),
        (2, 4, "Widget.constructor"),
        (5, 7, "Widget.render"),
        (9, 11, "helper"),
    ]


def test_scan_definitions_rust():
    src = [
        "pub struct Engine {",
        "    id: u32,",
        "}",
        "impl Engine {",
        "    pub fn start(&self) {",
        "        println!(\"go\");",
        "    }",
        "}",
        "fn free_fn() {",
        "}",
    ]
    blocks = _names(scan_definitions(src, ".rs"))
    assert blocks == [
        (1, 3, "Engine"),
        (4, 8, "impl Engine"),
        (5, 7, "Engine.start"),
        (9, 10, "free_fn"),
    ]


def test_scan_definitions_go():
    src = [
        "type Server struct {",
        "    port int",
        "}",
        "func (s *Server) Start() error {",
        "    return nil",
        "}",
        "func main() {",
        "}",
    ]
    blocks = _names(scan_definitions(src, ".go"))
    assert blocks == [
        (1, 3, "Server"),
        (4, 6, "Server.Start"),
        (7, 8, "main"),
    ]


def test_scan_definitions_php():
    src = [
        "<?php",
        "class Greeter {",
        "    public function hello($name) {",
        "        return $name;",
        "    }",
        "}",
        "function standalone() {",
        "    return 1;",
        "}",
    ]
    blocks = _names(scan_definitions(src, ".php"))
    assert blocks == [
        (2, 6, "Greeter"),
        (3, 5, "Greeter.hello"),
        (7, 9, "standalone"),
    ]


def test_scan_definitions_java_and_csharp():
    src = [
        "namespace Demo {",
        "  public class Service {",
        "    public int Compute(int x) {",
        "      return x * 2;",
        "    }",
        "    public int Double(int x) => x * 2;",
        "  }",
        "}",
    ]
    blocks = _names(scan_definitions(src, ".cs"))
    assert blocks == [
        (2, 7, "Demo.Service"),
        (3, 5, "Demo.Service.Compute"),
        (6, 6, "Demo.Service.Double"),
    ]


def test_scan_definitions_cpp_and_headers():
    src = [
        "namespace core {",
        "class Buffer {",
        "public:",
        "    int size() {",
        "        return 0;",
        "    }",
        "};",
        "int core::reset(int v) {",
        "    return v;",
        "}",
        "}",
    ]
    blocks = _names(scan_definitions(src, ".cpp"))
    assert blocks == [
        (2, 7, "core.Buffer"),
        (4, 6, "core.Buffer.size"),
        (8, 10, "core::reset"),
    ]
