"""Attribute names of external base classes, without importing them.

A project class may inherit from a builtin (Exception, dict) or a stdlib class
(threading.Thread). Python looks those up *before* later project bases in the
MRO, so a project-base method is only the target when the external class does
not define that name. This module answers "which names can the external class
provide?":

* builtin types: ``dir()`` of the interpreter's own builtin type (no target or
  library code runs).
* stdlib classes: the stdlib ``.py`` source is read with ``ast.parse`` only.
  Class-body names, ``self.x = ...`` assignments in its methods (instance
  attributes shadow methods) and every base, recursively, are collected.

Anything that cannot be read statically (dynamic exports, conditional or
decorated definitions, unknown metaclasses, ``__getattribute__``) returns
None, and the caller keeps its conservative behaviour. Results describe the
Python version running the analyzer, which is recorded as evidence.
"""
from __future__ import annotations

import ast
import builtins
import sys
import sysconfig
from pathlib import Path

PYTHON_VERSION = '%d.%d' % sys.version_info[:2]

# Exports that a stdlib package creates at import time and that AST cannot
# follow. multiprocessing/__init__.py copies the default context's attributes
# into its namespace; DefaultContext.Process is context.Process.
DYNAMIC_STDLIB_EXPORTS = {
    'multiprocessing.Process': 'multiprocessing.context.Process',
}

_SAFE_METACLASSES = {'abc.ABCMeta', 'ABCMeta'}


def builtin_class(name: str) -> type | None:
    value = getattr(builtins, name, None)
    return value if isinstance(value, type) else None


class ExternalClassIndex:
    def __init__(self):
        root = sysconfig.get_path('stdlib')
        self.stdlib = Path(root) if root else None
        self._modules: dict[str, ast.Module | None] = {}
        self._attrs: dict[str, frozenset | None] = {}
        self._packages: set[str] = set()

    def attributes(self, canonical: str) -> frozenset | None:
        """All names an instance of the external class can resolve, or None."""
        if canonical in self._attrs:
            return self._attrs[canonical]
        self._attrs[canonical] = None                  # cycle guard
        result = self._compute(canonical)
        self._attrs[canonical] = result
        return result

    def _compute(self, canonical: str) -> frozenset | None:
        module, _, name = canonical.rpartition('.')
        if module == 'builtins':
            cls = builtin_class(name)
            return frozenset(dir(cls)) if cls is not None else None
        canonical = DYNAMIC_STDLIB_EXPORTS.get(canonical, canonical)
        module, _, name = canonical.rpartition('.')
        tree = self._module(module)
        if tree is None:
            return None
        node = self._top_level(tree, name)
        if isinstance(node, ast.ClassDef):
            return self._class_attributes(node, module, tree)
        if isinstance(node, (ast.ImportFrom, ast.Import)):
            target = self._import_target(node, name, module)
            return self.attributes(target) if target else None
        return None

    def _module(self, module: str) -> ast.Module | None:
        if module in self._modules:
            return self._modules[module]
        tree = None
        if self.stdlib and module and module.split('.')[0] not in sys.builtin_module_names:
            base = self.stdlib.joinpath(*module.split('.'))
            for path in (base.with_suffix('.py'), base / '__init__.py'):
                if path.is_file():
                    if path.name == '__init__.py':
                        self._packages.add(module)
                    try:
                        tree = ast.parse(path.read_text(encoding='utf-8'))
                    except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
                        tree = None
                    break
        self._modules[module] = tree
        return tree

    @staticmethod
    def _module_scope_nodes(stmt: ast.AST):
        """Nodes of a compound statement that bind in the module namespace
        (class and function bodies have their own scope)."""
        pending = [stmt]
        while pending:
            node = pending.pop()
            yield node
            if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                pending.extend(ast.iter_child_nodes(node))

    @staticmethod
    def _top_level(tree: ast.Module, name: str):
        """The single unconditional module-level binding of name, if any."""
        found = []
        for stmt in tree.body:
            if isinstance(stmt, ast.ClassDef) and stmt.name == name:
                found.append(stmt)
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name == name:
                found.append(stmt)
            elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
                if any((a.asname or a.name.split('.')[0]) == name for a in stmt.names):
                    found.append(stmt)
            elif isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                if any(isinstance(n, ast.Name) and n.id == name for t in targets for n in ast.walk(t)):
                    found.append(stmt)
            elif isinstance(stmt, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                # A conditional rebinding makes the definition uncertain.
                if any(isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name or
                       isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Store) or
                       isinstance(n, ast.alias) and (n.asname or n.name.split('.')[0]) == name
                       for n in ExternalClassIndex._module_scope_nodes(stmt)):
                    return None
        return found[-1] if len(found) == 1 else None

    def _package(self, module: str, level: int) -> list[str] | None:
        """Package that a relative import of the given level refers to."""
        parts = module.split('.')
        if module not in self._packages:               # a plain module file
            parts = parts[:-1]
        if level > 1:
            parts = parts[:-(level - 1)]
        return parts or None

    def _import_target(self, node, name: str, module: str) -> str | None:
        for alias in node.names:
            if (alias.asname or alias.name.split('.')[0]) != name:
                continue
            if isinstance(node, ast.Import):
                return None                            # a module, not a class
            prefix = []
            if node.level:
                prefix = self._package(module, node.level)
                if prefix is None:
                    return None
            source = '.'.join([*prefix, *([node.module] if node.module else [])])
            return source + '.' + alias.name if source else None
        return None

    def _resolve_base(self, expr: ast.AST, module: str, tree: ast.Module) -> str | None:
        if isinstance(expr, ast.Name):
            node = self._top_level(tree, expr.id)
            if isinstance(node, ast.ClassDef):
                return module + '.' + expr.id
            if isinstance(node, ast.ImportFrom):
                return self._import_target(node, expr.id, module)
            if node is None and builtin_class(expr.id) is not None:
                return 'builtins.' + expr.id
            return None
        if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
            node = self._top_level(tree, expr.value.id)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if (alias.asname or alias.name) == expr.value.id:
                        return alias.name + '.' + expr.attr
            if isinstance(node, ast.ImportFrom) and node.level and node.module is None:
                # `from . import process` then `process.BaseProcess`
                package = self._package(module, node.level)
                return '.'.join([*package, expr.value.id, expr.attr]) if package else None
        return None

    def _class_attributes(self, node: ast.ClassDef, module: str, tree: ast.Module) -> frozenset | None:
        if node.decorator_list:
            return None
        for keyword in node.keywords:
            if keyword.arg != 'metaclass' or ast.unparse(keyword.value) not in _SAFE_METACLASSES:
                return None
        names = set()
        for stmt in node.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(stmt.name)
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store) and stmt in node.body \
                        and not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.add(sub.id)
                # Instance attributes set by any method shadow class methods.
                if isinstance(sub, ast.Attribute) and isinstance(sub.ctx, ast.Store) \
                        and isinstance(sub.value, ast.Name) and sub.value.id == 'self':
                    names.add(sub.attr)
        if '__getattribute__' in names:
            return None
        bases = node.bases or [ast.Name('object')]
        for base in bases:
            target = self._resolve_base(base, module, tree)
            inherited = self.attributes(target) if target else None
            if inherited is None:
                return None
            names |= inherited
        return frozenset(names)
