"""Symbol resolution for cross-file calls and inheritance (CodeGraph-style resolve phase).

Extract phase produces simple names; this module maps them onto symbol_ref targets
using import maps, path indexes, and definition tables.
"""

from __future__ import annotations

from pathlib import Path

from app.models.schemas import Definition, SymbolKind


def symbol_ref(file_path: str, definition: Definition) -> str:
    if definition.parent_name:
        return f"{file_path}::{definition.parent_name}.{definition.name}"
    return f"{file_path}::{definition.name}"


class SymbolResolver:
    """Resolve type / method names to `file::Symbol` refs within a repository snapshot."""

    def __init__(
        self,
        *,
        files: dict[str, str],
        definitions_by_file: dict[str, list[Definition]],
        path_index: dict[str, str],
        import_map_by_file: dict[str, dict[str, str]],
    ) -> None:
        self.files = files
        self.definitions_by_file = definitions_by_file
        self.path_index = path_index
        self.import_map_by_file = import_map_by_file
        # simple class/interface name -> [(file, definition)]
        self._types: dict[str, list[tuple[str, Definition]]] = {}
        for fpath, defs in definitions_by_file.items():
            for d in defs:
                if d.kind == SymbolKind.CLASS:
                    self._types.setdefault(d.name, []).append((fpath, d))

    def resolve_type(
        self,
        type_name: str,
        *,
        from_file: str,
        prefer_impl: bool = False,
    ) -> str | None:
        """
        Resolve a simple type name to a class/interface symbol_ref.

        Order: import map → path stem → unique global class name.
        When prefer_impl=True (Spring-style), try `{Type}Impl` first.
        """
        type_name = (type_name or "").split("<", 1)[0].rstrip("[]").strip()
        if not type_name:
            return None

        import_map = self.import_map_by_file.get(from_file, {})

        if prefer_impl:
            impl = self._resolve_named_type(f"{type_name}Impl", from_file, import_map)
            if impl is not None:
                return impl

        return self._resolve_named_type(type_name, from_file, import_map)

    def resolve_method_on_type(
        self,
        *,
        type_name: str,
        method_name: str,
        from_file: str,
        prefer_impl: bool = True,
    ) -> str | None:
        """Resolve `receiver.method` given a declared/static type name."""
        type_name = (type_name or "").split("<", 1)[0].rstrip("[]").strip()
        if not type_name or not method_name:
            return None

        import_map = self.import_map_by_file.get(from_file, {})

        # Prefer concrete *Impl when present (Spring style interface + impl)
        if prefer_impl:
            impl_name = f"{type_name}Impl"
            impl_file = self._file_for_type_name(impl_name, import_map)
            hit = self._lookup_method(impl_file, method_name, type_hint=impl_name)
            if hit is not None:
                return hit

        target_file = self._file_for_type_name(type_name, import_map)
        return self._lookup_method(target_file, method_name, type_hint=type_name)

    def _resolve_named_type(
        self,
        type_name: str,
        from_file: str,
        import_map: dict[str, str],
    ) -> str | None:
        target = self._file_for_type_name(type_name, import_map)
        if target and target in self.definitions_by_file:
            for d in self.definitions_by_file[target]:
                if d.kind == SymbolKind.CLASS and d.name == type_name:
                    return symbol_ref(target, d)
            # file matched by stem but class name may differ — take first class
            for d in self.definitions_by_file[target]:
                if d.kind == SymbolKind.CLASS:
                    return symbol_ref(target, d)

        # same-file
        for d in self.definitions_by_file.get(from_file, []):
            if d.kind == SymbolKind.CLASS and d.name == type_name:
                return symbol_ref(from_file, d)

        candidates = self._types.get(type_name, [])
        if len(candidates) == 1:
            fpath, d = candidates[0]
            return symbol_ref(fpath, d)
        if len(candidates) > 1:
            # Prefer a candidate imported from from_file's directory / unique path hit
            imported = import_map.get(type_name)
            for fpath, d in candidates:
                if imported and fpath == imported:
                    return symbol_ref(fpath, d)
        return None

    def _file_for_type_name(self, type_name: str, import_map: dict[str, str]) -> str | None:
        if type_name in import_map:
            return import_map[type_name]
        hit = self.path_index.get(type_name)
        if hit and hit in self.files:
            return hit
        # stem / suffix match (e.g. path_index keys that end with TypeName)
        for key, path in self.path_index.items():
            if Path(str(path)).stem == type_name or key.endswith(type_name):
                if path in self.files:
                    return path
        return None

    def _lookup_method(
        self,
        target: str | None,
        method_name: str,
        *,
        type_hint: str,
    ) -> str | None:
        if not target or target not in self.definitions_by_file:
            return None
        methods = [
            d
            for d in self.definitions_by_file[target]
            if d.kind == SymbolKind.METHOD and d.name == method_name
        ]
        if not methods:
            return None
        preferred_parents = {
            type_hint,
            f"{type_hint}Impl",
            type_hint.removesuffix("Impl"),
        }
        preferred = [d for d in methods if d.parent_name in preferred_parents]
        chosen = preferred[0] if preferred else methods[0]
        return symbol_ref(target, chosen)
