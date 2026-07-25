from __future__ import annotations

from tree_sitter import Node, Query, QueryCursor

from app.models.schemas import Definition, SymbolKind
from app.parsing.languages import get_language, get_parser

# Captures:
#   @name  — identifier
#   @def   — whole definition node
#   @kind  — unused; kind inferred from node type / nesting

_PYTHON_QUERY = """
(function_definition
  name: (identifier) @name) @def

(class_definition
  name: (identifier) @name) @def
"""

_JS_QUERY = """
(function_declaration
  name: (identifier) @name) @def

(class_declaration
  name: (identifier) @name) @def

(method_definition
  name: (property_identifier) @name) @def

(export_statement
  (function_declaration
    name: (identifier) @name) @def)

(lexical_declaration
  (variable_declarator
    name: (identifier) @name
    value: [(arrow_function) (function_expression)]) @def)
"""

_TS_QUERY = """
(function_declaration
  name: (identifier) @name) @def

(class_declaration
  name: (type_identifier) @name) @def

(method_definition
  name: (property_identifier) @name) @def

(export_statement
  declaration: (function_declaration
    name: (identifier) @name) @def)

(lexical_declaration
  (variable_declarator
    name: (identifier) @name
    value: (arrow_function)) @def)

(lexical_declaration
  (variable_declarator
    name: (identifier) @name
    value: (function_expression)) @def)
"""

_JAVA_QUERY = """
(method_declaration
  name: (identifier) @name) @def

(constructor_declaration
  name: (identifier) @name) @def

(class_declaration
  name: (identifier) @name) @def

(interface_declaration
  name: (identifier) @name) @def
"""

_QUERIES: dict[str, str] = {
    "python": _PYTHON_QUERY,
    "javascript": _JS_QUERY,
    "typescript": _TS_QUERY,
    "tsx": _TS_QUERY,
    "java": _JAVA_QUERY,
}

_CLASS_NODE_TYPES = {
    "class_definition",
    "class_declaration",
    "interface_declaration",
}

_METHOD_NODE_TYPES = {
    "method_definition",
    "method_declaration",
    "constructor_declaration",
}

_FUNCTION_NODE_TYPES = {
    "function_definition",
    "function_declaration",
    "lexical_declaration",
    "variable_declarator",
}


def _line_span(node: Node) -> tuple[int, int]:
    # tree-sitter points are 0-based; convert to 1-based inclusive
    start = node.start_point[0] + 1
    end = node.end_point[0] + 1
    return start, end


def _text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _find_enclosing_class(node: Node, source: bytes) -> str | None:
    parent = node.parent
    while parent is not None:
        if parent.type in _CLASS_NODE_TYPES:
            for child in parent.children:
                if child.type in {"identifier", "type_identifier", "property_identifier"}:
                    return _text(child, source)
            # fallback: look for name field
            name_node = parent.child_by_field_name("name")
            if name_node is not None:
                return _text(name_node, source)
            return None
        parent = parent.parent
    return None


def _has_class_ancestor(node: Node) -> bool:
    parent = node.parent
    while parent is not None:
        if parent.type in _CLASS_NODE_TYPES:
            return True
        parent = parent.parent
    return False


def _infer_kind(def_node: Node) -> SymbolKind:
    ntype = def_node.type
    if ntype in _CLASS_NODE_TYPES:
        return SymbolKind.CLASS
    if ntype in _METHOD_NODE_TYPES:
        return SymbolKind.METHOD
    if _has_class_ancestor(def_node) and ntype in _FUNCTION_NODE_TYPES | _METHOD_NODE_TYPES:
        return SymbolKind.METHOD
    if ntype in _FUNCTION_NODE_TYPES:
        return SymbolKind.FUNCTION
    if _has_class_ancestor(def_node):
        return SymbolKind.METHOD
    return SymbolKind.FUNCTION


class AstParser:
    def parse_definitions(self, content: str, language: str) -> list[Definition]:
        if language not in _QUERIES:
            raise ValueError(f"Unsupported language: {language}")

        source = content.encode("utf-8")
        parser = get_parser(language)
        tree = parser.parse(source)
        lang = get_language(language)
        query = Query(lang, _QUERIES[language])
        cursor = QueryCursor(query)

        definitions: list[Definition] = []
        seen: set[tuple[str, int, int, str]] = set()

        for match in cursor.matches(tree.root_node):
            captures = {name: nodes for name, nodes in match[1].items()}
            name_nodes = captures.get("name") or []
            def_nodes = captures.get("def") or []
            if not name_nodes or not def_nodes:
                continue

            name_node = name_nodes[0]
            def_node = def_nodes[0]
            name = _text(name_node, source)
            start_line, end_line = _line_span(def_node)
            kind = _infer_kind(def_node)

            parent_name: str | None = None
            if kind == SymbolKind.METHOD:
                parent_name = _find_enclosing_class(def_node, source)

            key = (name, start_line, end_line, kind.value)
            if key in seen:
                continue
            seen.add(key)

            definitions.append(
                Definition(
                    name=name,
                    kind=kind,
                    start_line=start_line,
                    end_line=end_line,
                    language=language if language != "tsx" else "typescript",
                    parent_name=parent_name,
                )
            )

        definitions.sort(key=lambda d: (d.start_line, d.end_line, d.name))
        return definitions
