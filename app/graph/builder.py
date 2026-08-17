from __future__ import annotations

import re
from pathlib import Path

from app.graph.resolver import SymbolResolver, symbol_ref
from app.models.schemas import (
    CallEdge,
    Definition,
    DependencyGraph,
    FileDependencyEdge,
    InheritEdge,
    SymbolKind,
)
from app.parsing.languages import detect_language, get_parser

# --- import regex fallbacks (also validated via tree-sitter when possible) ---

_PY_IMPORT_FROM = re.compile(
    r"^\s*from\s+([.\w]+)\s+import\s+(.+)$",
    re.MULTILINE,
)
_PY_IMPORT = re.compile(r"^\s*import\s+([\w.]+(?:\s*,\s*[\w.]+)*)", re.MULTILINE)

_JS_FROM = re.compile(
    r"""(?:import|export)\s+(?:type\s+)?[\s\w{},*]+?\s+from\s+['"]([^'"]+)['"]""",
    re.MULTILINE,
)
_JS_REQUIRE = re.compile(r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)""")
_JS_SIDE_EFFECT = re.compile(r"""import\s+['"]([^'"]+)['"]""")

_JAVA_IMPORT = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)\s*;", re.MULTILINE)

_CALL_NAME = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
# `def foo(` / `function foo(` / `class Foo(` — a declaration, not a call site.
# The regex fallback would otherwise report every definition as calling itself.
_DECL_CALL = re.compile(
    r"\b(?:def|function|fun|func|sub|class)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("
)
# obj.method( — receiver hint for Java/JS-ish regex fallback
_RECEIVER_CALL = re.compile(
    r"\b(?:this\.)?([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)\s*\("
)


def _top_level_symbols(definitions: list[Definition]) -> dict[str, Definition]:
    """name -> definition for non-method top-level symbols."""
    out: dict[str, Definition] = {}
    for d in definitions:
        if d.kind == SymbolKind.METHOD:
            continue
        out[d.name] = d
    return out


class DependencyGraphBuilder:
    def build(
        self,
        repo_id: str,
        commit_hash: str | None,
        files: dict[str, str],
        definitions_by_file: dict[str, list[Definition]],
        *,
        origin_paths: set[str] | None = None,
        advanced: bool = False,
    ) -> DependencyGraph:
        """
        Build dependency graph.

        When ``origin_paths`` is set, only emit edges that *originate* from those
        files (imports/calls/inherit from those files). Resolution still uses the
        full ``files`` / ``definitions_by_file`` maps — for incremental merge.

        ``advanced`` swaps call resolution for the six-strategy cascade in
        :class:`SymbolResolver`, which scores every edge and records the line it
        came from. The legacy branch is kept verbatim so the flag is a true
        rollback switch.
        """
        path_index = self._build_path_index(files.keys())
        file_edges: list[FileDependencyEdge] = []
        call_edges: list[CallEdge] = []
        origins = {p.replace("\\", "/") for p in origin_paths} if origin_paths else None

        # Map: local binding name -> target file (best-effort)
        import_map_by_file: dict[str, dict[str, str]] = {}
        # Map: local binding -> original symbol name (handles `import x as y`)
        import_symbol_by_file: dict[str, dict[str, str]] = {}

        for file_path, content in files.items():
            language = detect_language(file_path)
            targets, name_to_file, local_to_symbol = self._resolve_imports(
                file_path, content, language, path_index, files
            )
            import_map_by_file[file_path] = name_to_file
            import_symbol_by_file[file_path] = local_to_symbol
            if origins is not None and file_path not in origins:
                continue
            import_lines = self._import_lines(content) if advanced else {}
            for target in sorted(targets):
                if target != file_path and target in files:
                    file_edges.append(
                        FileDependencyEdge(
                            source=file_path,
                            target=target,
                            edge_type="imports",
                            import_line=import_lines.get(Path(target).stem),
                        )
                    )

        # Global symbol index: simple_name -> list[(file, definition)]
        global_symbols: dict[str, list[tuple[str, Definition]]] = {}
        for fpath, defs in definitions_by_file.items():
            for d in defs:
                if d.kind == SymbolKind.METHOD:
                    continue
                global_symbols.setdefault(d.name, []).append((fpath, d))

        resolver = SymbolResolver(
            files=files,
            definitions_by_file=definitions_by_file,
            path_index=path_index,
            import_map_by_file=import_map_by_file,
            import_symbol_by_file=import_symbol_by_file,
        )
        inherit_edges: list[InheritEdge] = []

        for file_path, content in files.items():
            if origins is not None and file_path not in origins:
                continue
            defs = definitions_by_file.get(file_path, [])
            local = _top_level_symbols(defs)
            # also index methods by short name for same-file calls
            local_methods = {
                d.name: d for d in defs if d.kind == SymbolKind.METHOD
            }
            language = detect_language(file_path)
            field_types = (
                self._java_field_types(content) if language == "java" else {}
            )
            import_map = import_map_by_file.get(file_path, {})
            import_symbols = import_symbol_by_file.get(file_path, {})

            # --- inherit / implements resolution (extract → resolve) ---
            for d in defs:
                if d.kind != SymbolKind.CLASS or not d.bases:
                    continue
                child_ref = symbol_ref(file_path, d)
                for base in d.bases:
                    parent_ref = resolver.resolve_type(
                        base.name,
                        from_file=file_path,
                        prefer_impl=False,
                    )
                    if parent_ref is None:
                        continue
                    inherit_edges.append(
                        InheritEdge(
                            child=child_ref,
                            parent=parent_ref,
                            relation=base.relation,
                            same_file=parent_ref.startswith(f"{file_path}::"),
                            decl_line=d.start_line if advanced else None,
                            resolution_strategy="type_resolved" if advanced else "legacy",
                        )
                    )

            callers = self._iter_call_sites(
                file_path, content, defs, drop_declarations=advanced
            )
            if advanced:
                call_edges.extend(
                    self._resolve_calls_cascade(
                        callers,
                        file_path=file_path,
                        resolver=resolver,
                        field_types=field_types,
                        language=language,
                    )
                )
                continue

            for caller_ref, callee_name, receiver, _line in callers:
                # same-file resolution
                if callee_name in local:
                    callee_ref = symbol_ref(file_path, local[callee_name])
                    call_edges.append(
                        CallEdge(
                            caller=caller_ref,
                            callee=callee_ref,
                            same_file=True,
                        )
                    )
                    continue
                if callee_name in local_methods and not receiver:
                    callee_ref = symbol_ref(file_path, local_methods[callee_name])
                    call_edges.append(
                        CallEdge(
                            caller=caller_ref,
                            callee=callee_ref,
                            same_file=True,
                        )
                    )
                    continue

                # Java/Spring: field.method() / Type.staticMethod()
                if receiver:
                    type_name = field_types.get(receiver, receiver)
                    resolved = resolver.resolve_method_on_type(
                        type_name=type_name,
                        method_name=callee_name,
                        from_file=file_path,
                        prefer_impl=True,
                    )
                    if resolved is not None:
                        call_edges.append(
                            CallEdge(
                                caller=caller_ref,
                                callee=resolved,
                                same_file=resolved.startswith(f"{file_path}::"),
                            )
                        )
                        continue

                # cross-file via import map (imported symbol called directly,
                # including `from x import login as auth_login` → resolve "login")
                imported_file = import_map.get(callee_name)
                if imported_file and imported_file in definitions_by_file:
                    resolve_name = import_symbols.get(callee_name, callee_name)
                    for d in definitions_by_file[imported_file]:
                        if d.name == resolve_name and d.kind != SymbolKind.METHOD:
                            call_edges.append(
                                CallEdge(
                                    caller=caller_ref,
                                    callee=symbol_ref(imported_file, d),
                                    same_file=False,
                                )
                            )
                            break
                    continue

                # weak fallback: unique global name
                candidates = global_symbols.get(callee_name, [])
                if len(candidates) == 1 and candidates[0][0] != file_path:
                    other_file, d = candidates[0]
                    call_edges.append(
                        CallEdge(
                            caller=caller_ref,
                            callee=symbol_ref(other_file, d),
                            same_file=False,
                        )
                    )

        # dedupe
        file_edges = self._dedupe_file_edges(file_edges)
        call_edges = self._dedupe_call_edges(call_edges)
        inherit_edges = self._dedupe_inherit_edges(inherit_edges)
        return DependencyGraph(
            repo_id=repo_id,
            commit_hash=commit_hash,
            file_edges=file_edges,
            call_edges=call_edges,
            inherit_edges=inherit_edges,
        )

    def structural_facts(
        self,
        file_path: str,
        content: str,
        definitions: list[Definition],
    ) -> tuple[set[str], set[str]]:
        """Edge-producing facts of one file, with line numbers stripped out.

        Feeds the AST structure hash: two revisions agreeing on these sets
        produce the same edges, so the graph can be reused as-is.
        """
        calls = {
            f"{caller}|{receiver or ''}|{name}"
            for caller, name, receiver, _line in self._iter_call_sites(
                file_path, content, definitions
            )
        }
        imports = {
            " ".join(line.split())
            for line in content.splitlines()
            if line.strip().startswith(("import ", "from ")) or "require(" in line
        }
        return calls, imports

    def _resolve_calls_cascade(
        self,
        call_sites: list[tuple[str, str, str | None, int | None]],
        *,
        file_path: str,
        resolver: SymbolResolver,
        field_types: dict[str, str],
        language: str | None,
    ) -> list[CallEdge]:
        """Resolve one file's call sites through the six-strategy cascade.

        Every edge carries the line it was observed on, so downstream tools can
        cite `file:line` for the relationship itself and not just the endpoints.
        """
        edges: list[CallEdge] = []
        for caller_ref, callee_name, receiver, line in call_sites:
            hit = resolver.resolve_call(
                callee_name,
                from_file=file_path,
                receiver=receiver,
                field_types=field_types,
                language=language,
            )
            if hit is None:
                continue
            edges.append(
                CallEdge(
                    caller=caller_ref,
                    callee=hit.symbol_ref,
                    same_file=hit.file_path == file_path,
                    confidence=hit.confidence,
                    resolution_strategy=hit.strategy,
                    call_line=line,
                )
            )
        return edges

    def merge_update(
        self,
        existing: DependencyGraph,
        changed_paths: list[str],
        deleted_paths: list[str],
        partial_graph: DependencyGraph,
        *,
        rebuild_origins: set[str] | None = None,
    ) -> DependencyGraph:
        """Merge a partial rebuild into an existing graph.

        Drops edges that touch changed/deleted files, and all *outgoing* edges
        from ``rebuild_origins`` (those are replaced by ``partial_graph``).
        """
        drop_touch = {p.replace("\\", "/") for p in (changed_paths + deleted_paths)}
        rebuild = {p.replace("\\", "/") for p in (rebuild_origins or set())} | drop_touch

        def keep_file_edge(e: FileDependencyEdge) -> bool:
            if e.source in rebuild:
                return False
            if e.target in drop_touch:
                return False
            return True

        def keep_call_edge(e: CallEdge) -> bool:
            src = e.caller.split("::", 1)[0]
            dst = e.callee.split("::", 1)[0]
            if src in rebuild:
                return False
            if dst in drop_touch:
                return False
            return True

        def keep_inherit_edge(e: InheritEdge) -> bool:
            src = e.child.split("::", 1)[0]
            dst = e.parent.split("::", 1)[0]
            if src in rebuild:
                return False
            if dst in drop_touch:
                return False
            return True

        file_edges = [e for e in existing.file_edges if keep_file_edge(e)]
        call_edges = [e for e in existing.call_edges if keep_call_edge(e)]
        inherit_edges = [e for e in existing.inherit_edges if keep_inherit_edge(e)]
        file_edges.extend(partial_graph.file_edges)
        call_edges.extend(partial_graph.call_edges)
        inherit_edges.extend(partial_graph.inherit_edges)
        return DependencyGraph(
            repo_id=partial_graph.repo_id or existing.repo_id,
            commit_hash=partial_graph.commit_hash or existing.commit_hash,
            file_edges=self._dedupe_file_edges(file_edges),
            call_edges=self._dedupe_call_edges(call_edges),
            inherit_edges=self._dedupe_inherit_edges(inherit_edges),
        )

    @staticmethod
    def _dedupe_file_edges(edges: list[FileDependencyEdge]) -> list[FileDependencyEdge]:
        seen: set[tuple[str, str, str]] = set()
        out: list[FileDependencyEdge] = []
        for e in edges:
            key = (e.source, e.target, e.edge_type)
            if key in seen:
                continue
            seen.add(key)
            out.append(e)
        return out

    @staticmethod
    def _dedupe_call_edges(edges: list[CallEdge]) -> list[CallEdge]:
        seen: set[tuple[str, str, str]] = set()
        out: list[CallEdge] = []
        for e in edges:
            key = (e.caller, e.callee, e.edge_type)
            if key in seen:
                continue
            seen.add(key)
            out.append(e)
        return out

    @staticmethod
    def _dedupe_inherit_edges(edges: list[InheritEdge]) -> list[InheritEdge]:
        seen: set[tuple[str, str, str]] = set()
        out: list[InheritEdge] = []
        for e in edges:
            key = (e.child, e.parent, e.relation)
            if key in seen:
                continue
            seen.add(key)
            out.append(e)
        return out

    @staticmethod
    def _import_lines(content: str) -> dict[str, int]:
        """module stem -> first line that imports it, for edge citations."""
        out: dict[str, int] = {}
        for i, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            if not (stripped.startswith(("import ", "from ")) or "require(" in stripped):
                continue
            for token in re.findall(r"[A-Za-z_][\w.]*", stripped):
                stem = token.split(".")[-1]
                out.setdefault(stem, i)
        return out

    @staticmethod
    def _build_path_index(paths) -> dict[str, str]:
        """stem / dotted module-ish keys -> file path."""
        index: dict[str, str] = {}
        for p in paths:
            posix = p.replace("\\", "/")
            index[posix] = p
            stem = Path(posix).stem
            index[stem] = p
            no_ext = posix.rsplit(".", 1)[0]
            index[no_ext] = p
            dotted = no_ext.replace("/", ".")
            index[dotted] = p
            # python package: pkg/module.py -> pkg.module
            parts = Path(posix).with_suffix("").parts
            if parts and parts[-1] == "__init__":
                index[".".join(parts[:-1])] = p
        return index

    def _resolve_imports(
        self,
        file_path: str,
        content: str,
        language: str | None,
        path_index: dict[str, str],
        files: dict[str, str],
    ) -> tuple[set[str], dict[str, str], dict[str, str]]:
        """Return (import targets, local_name→file, local_name→original_symbol)."""
        targets: set[str] = set()
        name_to_file: dict[str, str] = {}
        local_to_symbol: dict[str, str] = {}

        if language == "python":
            for mod in _PY_IMPORT.findall(content):
                for part in mod.split(","):
                    part = part.strip().split(" as ")[0].strip()
                    resolved = self._resolve_python_module(file_path, part, path_index, files)
                    if resolved:
                        targets.add(resolved)
                        local = part.split(".")[-1]
                        name_to_file[local] = resolved
                        local_to_symbol[local] = local
            for mod, names in _PY_IMPORT_FROM.findall(content):
                resolved = self._resolve_python_module(file_path, mod, path_index, files)
                if resolved:
                    targets.add(resolved)
                    for raw in names.split(","):
                        raw = raw.strip()
                        if not raw or raw.startswith("("):
                            continue
                        # `login as auth_login` → local=auth_login, original=login
                        if " as " in raw:
                            original, _, alias = raw.partition(" as ")
                            original = original.strip()
                            alias = alias.strip()
                            if not original or original == "*" or not alias:
                                continue
                            name_to_file[alias] = resolved
                            local_to_symbol[alias] = original
                        else:
                            name = raw.strip()
                            if name and name != "*":
                                name_to_file[name] = resolved
                                local_to_symbol[name] = name

        elif language in {"javascript", "typescript", "tsx"}:
            specs = set(_JS_FROM.findall(content))
            specs |= set(_JS_REQUIRE.findall(content))
            specs |= set(_JS_SIDE_EFFECT.findall(content))
            for spec in specs:
                resolved = self._resolve_js_spec(file_path, spec, path_index, files)
                if resolved:
                    targets.add(resolved)
                    # crude: last path segment as imported binding hint
                    local = Path(spec).stem
                    name_to_file[local] = resolved
                    local_to_symbol[local] = local

        elif language == "java":
            for pkg in _JAVA_IMPORT.findall(content):
                simple = pkg.split(".")[-1]
                # try match by class file name
                candidate = path_index.get(simple)
                if candidate:
                    targets.add(candidate)
                    name_to_file[simple] = candidate
                    local_to_symbol[simple] = simple

        return targets, name_to_file, local_to_symbol

    def _resolve_python_module(
        self,
        file_path: str,
        module: str,
        path_index: dict[str, str],
        files: dict[str, str],
    ) -> str | None:
        if module.startswith("."):
            # relative import: count dots
            dots = len(module) - len(module.lstrip("."))
            rest = module.lstrip(".")
            base = Path(file_path).parent
            for _ in range(dots - 1):
                base = base.parent
            if rest:
                candidate = (base / rest.replace(".", "/")).as_posix()
            else:
                candidate = base.as_posix()
            for key in (candidate + ".py", candidate + "/__init__.py", candidate):
                if key in files:
                    return key
                hit = path_index.get(key)
                if hit:
                    return hit
            return path_index.get(candidate)

        # absolute-ish within repo
        for key in (
            module,
            module.replace(".", "/") + ".py",
            module.replace(".", "/") + "/__init__.py",
            module.split(".")[-1],
        ):
            hit = path_index.get(key)
            if hit and hit in files:
                return hit
        return None

    def _resolve_js_spec(
        self,
        file_path: str,
        spec: str,
        path_index: dict[str, str],
        files: dict[str, str],
    ) -> str | None:
        if not spec.startswith("."):
            return None  # skip packages
        base = Path(file_path).parent
        joined = (base / spec).as_posix()
        candidates = [
            joined,
            joined + ".js",
            joined + ".ts",
            joined + ".tsx",
            joined + ".jsx",
            joined + "/index.js",
            joined + "/index.ts",
        ]
        for c in candidates:
            norm = Path(c).as_posix()
            if norm in files:
                return norm
            hit = path_index.get(norm)
            if hit:
                return hit
        stem = Path(spec).stem
        return path_index.get(stem) if path_index.get(stem) in files else None

    def _java_field_types(self, content: str) -> dict[str, str]:
        """fieldName -> simple type name (best-effort via tree-sitter + regex)."""
        out: dict[str, str] = {}
        try:
            source = content.encode("utf-8")
            tree = get_parser("java").parse(source)
            stack = [tree.root_node]
            while stack:
                node = stack.pop()
                stack.extend(reversed(node.children))
                if node.type != "field_declaration":
                    continue
                type_node = None
                decl_names: list[str] = []
                for child in node.children:
                    if child.type in {"type_identifier", "generic_type", "array_type"}:
                        type_node = child
                    if child.type == "variable_declarator":
                        for gc in child.children:
                            if gc.type == "identifier":
                                decl_names.append(
                                    source[gc.start_byte : gc.end_byte].decode(
                                        "utf-8", errors="replace"
                                    )
                                )
                if type_node is None or not decl_names:
                    continue
                raw = source[type_node.start_byte : type_node.end_byte].decode(
                    "utf-8", errors="replace"
                )
                simple = raw.split("<", 1)[0].split(".")[-1].rstrip("[]").strip()
                for name in decl_names:
                    out[name] = simple
        except Exception:
            pass

        # regex fallback / complement
        for m in re.finditer(
            r"(?:private|protected|public|static|final|\s)+"
            r"([A-Z][\w.]*(?:\[\])?)\s+([a-zA-Z_]\w*)\s*[;=]",
            content,
        ):
            simple = m.group(1).split(".")[-1].rstrip("[]")
            out.setdefault(m.group(2), simple)
        return out

    def _iter_call_sites(
        self,
        file_path: str,
        content: str,
        definitions: list[Definition],
        *,
        drop_declarations: bool = False,
    ) -> list[tuple[str, str, str | None, int | None]]:
        """
        Yield (caller_ref, callee_simple_name, receiver_or_None, line).
        Prefer mapping call lines to enclosing definition; fall back to file::module.

        ``drop_declarations`` suppresses the regex fallback's habit of reading a
        `def foo(` header as a call to ``foo``. It is off by default so the
        legacy path keeps producing byte-identical edges.
        """
        language = detect_language(file_path)
        results: list[tuple[str, str, str | None, int | None]] = []

        # Build line -> enclosing definition (innermost wins)
        line_owner: dict[int, Definition] = {}
        for d in sorted(
            definitions,
            key=lambda x: (x.end_line - x.start_line),
            reverse=True,  # large spans first; smaller methods overwrite
        ):
            for ln in range(d.start_line, d.end_line + 1):
                line_owner[ln] = d

        if language:
            try:
                results.extend(
                    self._calls_via_tree_sitter(file_path, content, language, line_owner)
                )
                if results:
                    return results
            except Exception:
                pass

        # regex fallback — prefer receiver.method captures
        seen_spans: set[tuple[int, str, str | None]] = set()
        for i, line in enumerate(content.splitlines(), start=1):
            owner = line_owner.get(i)
            caller = (
                symbol_ref(file_path, owner) if owner else f"{file_path}::__module__"
            )
            declared = (
                {m.group(1) for m in _DECL_CALL.finditer(line)}
                if drop_declarations
                else set()
            )
            for match in _RECEIVER_CALL.finditer(line):
                receiver, name = match.group(1), match.group(2)
                if name in {"if", "for", "while", "switch", "catch", "return"}:
                    continue
                key = (i, name, receiver)
                if key in seen_spans:
                    continue
                seen_spans.add(key)
                results.append((caller, name, receiver, i))
            for match in _CALL_NAME.finditer(line):
                name = match.group(1)
                if name in {"if", "for", "while", "switch", "catch", "return", "function", "class"}:
                    continue
                if name in declared:
                    continue
                # skip if already captured as receiver.method on this line
                if any(s[0] == i and s[1] == name for s in seen_spans):
                    continue
                key = (i, name, None)
                if key in seen_spans:
                    continue
                seen_spans.add(key)
                results.append((caller, name, None, i))
        return results

    def _calls_via_tree_sitter(
        self,
        file_path: str,
        content: str,
        language: str,
        line_owner: dict[int, Definition],
    ) -> list[tuple[str, str, str | None, int | None]]:
        source = content.encode("utf-8")
        parser = get_parser(language)
        tree = parser.parse(source)
        results: list[tuple[str, str, str | None, int | None]] = []

        call_types = {
            "call",  # python
            "call_expression",  # js/ts/java-ish
            "method_invocation",  # java
        }

        stack = [tree.root_node]
        while stack:
            node = stack.pop()
            stack.extend(reversed(node.children))
            if node.type not in call_types:
                continue
            name, receiver = self._extract_call_target(node, source, language)
            if not name:
                continue
            line = node.start_point[0] + 1
            owner = line_owner.get(line)
            caller = symbol_ref(file_path, owner) if owner else f"{file_path}::__module__"
            results.append((caller, name, receiver, line))
        return results

    def _extract_call_target(
        self, node, source: bytes, language: str
    ) -> tuple[str | None, str | None]:
        """Return (callee_name, receiver_name_or_None)."""
        # python: call -> function: identifier | attribute
        fn = node.child_by_field_name("function")
        if fn is not None:
            if fn.type == "identifier":
                return (
                    source[fn.start_byte : fn.end_byte].decode("utf-8", errors="replace"),
                    None,
                )
            if fn.type == "attribute":
                attr = fn.child_by_field_name("attribute")
                obj = fn.child_by_field_name("object")
                name = (
                    source[attr.start_byte : attr.end_byte].decode("utf-8", errors="replace")
                    if attr is not None
                    else None
                )
                receiver = None
                if obj is not None and obj.type == "identifier":
                    receiver = source[obj.start_byte : obj.end_byte].decode(
                        "utf-8", errors="replace"
                    )
                return name, receiver
            if fn.type == "member_expression":
                prop = fn.child_by_field_name("property")
                obj = fn.child_by_field_name("object")
                name = (
                    source[prop.start_byte : prop.end_byte].decode("utf-8", errors="replace")
                    if prop is not None
                    else None
                )
                receiver = None
                if obj is not None and obj.type == "identifier":
                    receiver = source[obj.start_byte : obj.end_byte].decode(
                        "utf-8", errors="replace"
                    )
                return name, receiver

        # java method_invocation: object + name
        name_node = node.child_by_field_name("name")
        if name_node is not None:
            name = source[name_node.start_byte : name_node.end_byte].decode(
                "utf-8", errors="replace"
            )
            receiver = self._java_receiver_name(node.child_by_field_name("object"), source)
            return name, receiver

        # js call_expression fallback
        for child in node.children:
            if child.type == "identifier":
                return (
                    source[child.start_byte : child.end_byte].decode("utf-8", errors="replace"),
                    None,
                )
        return None, None

    @staticmethod
    def _java_receiver_name(obj_node, source: bytes) -> str | None:
        if obj_node is None:
            return None
        if obj_node.type == "identifier":
            return source[obj_node.start_byte : obj_node.end_byte].decode(
                "utf-8", errors="replace"
            )
        if obj_node.type == "field_access":
            # this.taskSubmitService -> taskSubmitService (last identifier)
            last = None
            for child in obj_node.children:
                if child.type == "identifier":
                    last = source[child.start_byte : child.end_byte].decode(
                        "utf-8", errors="replace"
                    )
            return last
        return None
