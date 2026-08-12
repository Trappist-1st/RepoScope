from __future__ import annotations

from tree_sitter import Node, Query, QueryCursor

from app.models.schemas import Definition, SuperTypeRef, SymbolKind
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


def _simple_type_name(node: Node, source: bytes) -> str | None:
    """Reduce a type expression to a simple identifier (strip generics / packages)."""
    if node.type in {"identifier", "type_identifier", "property_identifier"}:
        return _text(node, source)
    if node.type == "generic_type":
        name = node.child_by_field_name("type") or node.child_by_field_name("name")
        if name is not None:
            return _simple_type_name(name, source)
        for child in node.children:
            hit = _simple_type_name(child, source)
            if hit:
                return hit
    if node.type in {"scoped_type_identifier", "member_expression", "attribute"}:
        last = None
        stack = [node]
        while stack:
            cur = stack.pop()
            if cur.type in {"identifier", "type_identifier", "property_identifier"}:
                last = _text(cur, source)
            stack.extend(reversed(cur.children))
        return last
    for child in node.children:
        hit = _simple_type_name(child, source)
        if hit:
            return hit
    return None


def _extract_bases(def_node: Node, source: bytes, language: str) -> list[SuperTypeRef]:
    """Extract extends/implements simple names from a class/interface definition node."""
    if def_node.type not in _CLASS_NODE_TYPES:
        return []

    bases: list[SuperTypeRef] = []
    seen: set[tuple[str, str]] = set()

    def _add(name: str | None, relation: str) -> None:
        if not name or name in {"object", "Object", "Any", "Protocol"}:
            return
        key = (name, relation)
        if key in seen:
            return
        seen.add(key)
        bases.append(SuperTypeRef(name=name, relation=relation))  # type: ignore[arg-type]

    if language == "python":
        supers = def_node.child_by_field_name("superclasses")
        if supers is not None:
            for child in supers.children:
                if child.type in {"identifier", "attribute"}:
                    _add(_simple_type_name(child, source), "extends")
        return bases

    if language == "java":
        for child in def_node.children:
            if child.type == "superclass":
                _add(_simple_type_name(child, source), "extends")
            elif child.type in {"super_interfaces", "extends_interfaces"}:
                for gc in child.children:
                    if gc.type == "type_list":
                        for t in gc.children:
                            rel = (
                                "implements"
                                if def_node.type == "class_declaration"
                                else "extends"
                            )
                            _add(_simple_type_name(t, source), rel)
                    elif gc.type in {
                        "type_identifier",
                        "generic_type",
                        "scoped_type_identifier",
                    }:
                        rel = (
                            "implements"
                            if def_node.type == "class_declaration"
                            else "extends"
                        )
                        _add(_simple_type_name(gc, source), rel)
            elif child.type == "interfaces":
                for gc in child.children:
                    _add(_simple_type_name(gc, source), "implements")
        return bases

    # javascript / typescript / tsx
    for child in def_node.children:
        if child.type == "class_heritage":
            for hc in child.children:
                if hc.type == "extends_clause":
                    _add(_simple_type_name(hc, source), "extends")
                elif hc.type == "implements_clause":
                    for ic in hc.children:
                        _add(_simple_type_name(ic, source), "implements")
        elif child.type == "extends_clause":
            _add(_simple_type_name(child, source), "extends")
        elif child.type == "implements_clause":
            for ic in child.children:
                _add(_simple_type_name(ic, source), "implements")
    return bases


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

            bases: list[SuperTypeRef] = []
            if kind == SymbolKind.CLASS:
                bases = _extract_bases(def_node, source, language)

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
                    bases=bases,
                )
            )

        definitions.sort(key=lambda d: (d.start_line, d.end_line, d.name))
        return definitions
