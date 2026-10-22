"""
Symbol index: where are the classes and functions?

Python is parsed with the standard `ast` module, which gives exact start and end lines.
JavaScript/TypeScript get a lightweight regex pass (start line only). Good enough to answer
"where is `login` defined?" without grepping for every call site.
"""

import ast
import re
from dataclasses import dataclass

from verifix.repos.files import MAX_FILE_BYTES, list_files
from verifix.repos.paths import RepoJail


@dataclass(frozen=True)
class Symbol:
    name: str
    qualname: str
    kind: str  # class | function | method
    path: str
    start: int
    end: int | None


_JS_PATTERNS = (
    (
        re.compile(
            r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)"
        ),
        "function",
    ),
    (
        re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)"),
        "class",
    ),
    (
        re.compile(
            r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*(?::[^=]+)?=>"
        ),
        "function",
    ),
)
_JS_EXT = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")


def python_symbols(source: str, path: str) -> list[Symbol]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    out: list[Symbol] = []

    def visit(node: ast.AST, prefix: str, in_class: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                q = f"{prefix}{child.name}"
                out.append(Symbol(child.name, q, "class", path, child.lineno, child.end_lineno))
                visit(child, q + ".", True)
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                q = f"{prefix}{child.name}"
                kind = "method" if in_class else "function"
                out.append(Symbol(child.name, q, kind, path, child.lineno, child.end_lineno))
                visit(child, q + ".", False)

    visit(tree, "", False)
    return out


def js_symbols(source: str, path: str) -> list[Symbol]:
    out: list[Symbol] = []
    for n, line in enumerate(source.splitlines(), start=1):
        if len(line) > 500:
            continue
        for rx, kind in _JS_PATTERNS:
            m = rx.match(line)
            if m:
                out.append(Symbol(m.group(1), m.group(1), kind, path, n, None))
                break
    return out


def index_symbols(jail: RepoJail, *, path_prefix: str | None = None) -> list[Symbol]:
    symbols: list[Symbol] = []
    for entry in list_files(jail).files:
        if path_prefix and not entry.path.startswith(path_prefix):
            continue
        if entry.size > MAX_FILE_BYTES:
            continue
        if not entry.path.endswith((".py", *_JS_EXT)):
            continue
        try:
            source = (jail.root / entry.path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if entry.path.endswith(".py"):
            symbols.extend(python_symbols(source, entry.path))
        else:
            symbols.extend(js_symbols(source, entry.path))
    return symbols


def find_symbol(jail: RepoJail, name: str, *, limit: int = 50) -> list[Symbol]:
    """Exact name matches first, then qualified-name and substring matches."""
    needle = name.strip()
    if not needle:
        return []
    low = needle.lower()
    all_symbols = index_symbols(jail)
    exact = [s for s in all_symbols if s.name == needle or s.qualname == needle]
    partial = [s for s in all_symbols if s not in exact and (low in s.qualname.lower())]
    return (exact + partial)[:limit]
