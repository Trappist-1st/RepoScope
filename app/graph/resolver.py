"""Symbol resolution for cross-file calls and inheritance (CodeGraph-style resolve phase).

Extract phase produces simple names; this module maps them onto symbol_ref targets
using import maps, path indexes, and definition tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from app.models.schemas import Definition, ResolutionStrategy, SymbolKind

# Confidence per cascade tier. Ordering mirrors the blueprint: an import
# statement is stronger evidence than a globally unique simple name, which in
# turn beats a fuzzy string match.
CONF_IMPORT_MAP = 0.95
CONF_SAME_MODULE = 0.90
CONF_IMPORT_SUFFIX = 0.85
CONF_UNIQUE_NAME = 0.75
CONF_UNIQUE_NAME_UNREACHABLE = 0.60
CONF_IMPORT_DISTANCE = 0.55
CONF_FUZZY_FLOOR = 0.30
FUZZY_THRESHOLD = 0.82


@dataclass(frozen=True)
class ResolvedRef:
    """A resolved callee plus how much we trust the resolution."""

    symbol_ref: str
    confidence: float
    strategy: ResolutionStrategy

    @property
    def file_path(self) -> str:
        return self.symbol_ref.split("::", 1)[0]


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
        import_symbol_by_file: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self.files = files
        self.definitions_by_file = definitions_by_file
        self.path_index = path_index
        self.import_map_by_file = import_map_by_file
        self.import_symbol_by_file = import_symbol_by_file or {}
        # simple class/interface name -> [(file, definition)]
        self._types: dict[str, list[tuple[str, Definition]]] = {}
        # simple name -> [(file, definition)] for every non-method symbol
        self._by_simple_name: dict[str, list[tuple[str, Definition]]] = {}
        # (file, simple name) -> definition, for same-file lookups
        self._local: dict[tuple[str, str], Definition] = {}
        for fpath, defs in definitions_by_file.items():
            for d in defs:
                if d.kind == SymbolKind.CLASS:
                    self._types.setdefault(d.name, []).append((fpath, d))
                if d.kind != SymbolKind.METHOD:
                    self._by_simple_name.setdefault(d.name, []).append((fpath, d))
                self._local.setdefault((fpath, d.name), d)

    # ------------------------------------------------------------------
    # Six-strategy cascade for call resolution
    # ------------------------------------------------------------------

    def resolve_call(
        self,
        callee_name: str,
        *,
        from_file: str,
        receiver: str | None = None,
        field_types: dict[str, str] | None = None,
        language: str | None = None,
    ) -> ResolvedRef | None:
        """Resolve a call site to a target symbol_ref with a confidence score.

        Tiers are tried in descending trust and the first hit wins. Unlike the
        legacy path, a receiver expression is never ignored: ``repo.find()``
        will not silently bind to a top-level ``find`` in the calling file.
        """
        if not callee_name:
            return None

        import_map = self.import_map_by_file.get(from_file, {})
        import_symbols = self.import_symbol_by_file.get(from_file, {})

        # A receiver means the call is qualified. Only type-directed lookup and
        # import-map lookup of the receiver itself are legitimate here.
        if receiver:
            return self._resolve_with_receiver(
                callee_name,
                receiver=receiver,
                from_file=from_file,
                field_types=field_types or {},
            )

        # Strategy 1: the name was explicitly imported into this file.
        target_file = import_map.get(callee_name)
        if target_file and target_file in self.definitions_by_file:
            original = import_symbols.get(callee_name, callee_name)
            hit = self._exact_in_file(target_file, original)
            if hit is not None:
                return ResolvedRef(hit, CONF_IMPORT_MAP, "import_map")

        # Strategy 3: defined in the calling file itself.
        local = self._local.get((from_file, callee_name))
        if local is not None and local.kind != SymbolKind.METHOD:
            return ResolvedRef(
                symbol_ref(from_file, local), CONF_SAME_MODULE, "same_module"
            )

        # Strategy 2: not imported by name, but present in an imported module.
        for module_file in dict.fromkeys(import_map.values()):
            if module_file == from_file:
                continue
            hit = self._exact_in_file(module_file, callee_name)
            if hit is not None:
                return ResolvedRef(hit, CONF_IMPORT_SUFFIX, "import_suffix")

        candidates = self._by_simple_name.get(callee_name, [])

        # Strategy 4: globally unique simple name, downgraded when the owning
        # file is not reachable through this file's imports.
        if len(candidates) == 1:
            fpath, d = candidates[0]
            if fpath == from_file:
                return ResolvedRef(
                    symbol_ref(fpath, d), CONF_SAME_MODULE, "same_module"
                )
            reachable = fpath in set(import_map.values())
            conf = CONF_UNIQUE_NAME if reachable else CONF_UNIQUE_NAME_UNREACHABLE
            return ResolvedRef(symbol_ref(fpath, d), conf, "unique_name")

        # Strategy 5: several same-named symbols; prefer the closest by import
        # reachability, then by shared path prefix.
        if len(candidates) > 1:
            best = self._closest_candidate(candidates, from_file, import_map)
            if best is not None:
                fpath, d = best
                return ResolvedRef(
                    symbol_ref(fpath, d), CONF_IMPORT_DISTANCE, "import_distance"
                )

        # Strategy 6: fuzzy fallback, deliberately conservative.
        fuzzy = self._fuzzy_match(callee_name)
        if fuzzy is not None:
            fpath, d, score = fuzzy
            return ResolvedRef(
                symbol_ref(fpath, d), round(CONF_FUZZY_FLOOR + 0.10 * score, 3), "fuzzy"
            )

        return None

    def _resolve_with_receiver(
        self,
        method_name: str,
        *,
        receiver: str,
        from_file: str,
        field_types: dict[str, str],
    ) -> ResolvedRef | None:
        type_name = field_types.get(receiver, receiver)
        resolved = self.resolve_method_on_type(
            type_name=type_name,
            method_name=method_name,
            from_file=from_file,
            prefer_impl=True,
        )
        if resolved is not None:
            return ResolvedRef(resolved, CONF_IMPORT_MAP, "type_resolved")

        # The receiver may be an imported module rather than an object, e.g.
        # `import user_repo` then `user_repo.find_by_username()`.
        import_map = self.import_map_by_file.get(from_file, {})
        module_file = import_map.get(receiver)
        if module_file:
            hit = self._exact_in_file(module_file, method_name)
            if hit is not None:
                return ResolvedRef(hit, CONF_IMPORT_MAP, "import_map")

        # Methods carry their owning class, so a unique method name across the
        # repo is still a usable signal at reduced confidence.
        methods = [
            (fpath, d)
            for fpath, defs in self.definitions_by_file.items()
            for d in defs
            if d.kind == SymbolKind.METHOD and d.name == method_name
        ]
        if len(methods) == 1:
            fpath, d = methods[0]
            return ResolvedRef(
                symbol_ref(fpath, d), CONF_UNIQUE_NAME_UNREACHABLE, "unique_name"
            )
        return None

    def _exact_in_file(self, file_path: str, name: str) -> str | None:
        for d in self.definitions_by_file.get(file_path, []):
            if d.name == name and d.kind != SymbolKind.METHOD:
                return symbol_ref(file_path, d)
        return None

    @staticmethod
    def _shared_prefix_len(a: str, b: str) -> int:
        pa, pb = a.split("/"), b.split("/")
        n = 0
        for x, y in zip(pa[:-1], pb[:-1]):
            if x != y:
                break
            n += 1
        return n

    def _closest_candidate(
        self,
        candidates: list[tuple[str, Definition]],
        from_file: str,
        import_map: dict[str, str],
    ) -> tuple[str, Definition] | None:
        imported = set(import_map.values())
        ranked = sorted(
            candidates,
            key=lambda c: (
                0 if c[0] in imported else 1,
                -self._shared_prefix_len(c[0], from_file),
                c[0],
            ),
        )
        best = ranked[0]
        # Refuse to guess when nothing distinguishes the top two candidates:
        # a wrong high-volume edge is worse than a missing one.
        if len(ranked) > 1:
            second = ranked[1]
            same_reach = (best[0] in imported) == (second[0] in imported)
            same_prefix = self._shared_prefix_len(
                best[0], from_file
            ) == self._shared_prefix_len(second[0], from_file)
            if same_reach and same_prefix:
                return None
        return best

    def _fuzzy_match(self, name: str) -> tuple[str, Definition, float] | None:
        best: tuple[str, Definition, float] | None = None
        for cand_name, entries in self._by_simple_name.items():
            if cand_name == name or len(entries) != 1:
                continue
            score = SequenceMatcher(None, name, cand_name).ratio()
            if score >= FUZZY_THRESHOLD and (best is None or score > best[2]):
                fpath, d = entries[0]
                best = (fpath, d, score)
        return best

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
