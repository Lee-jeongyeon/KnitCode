"""Subclass override candidates for open receivers (CALLS_OVERRIDE).

A call through ``self``/``cls`` or through a parameter whose type is only
*declared* can run a method that a project subclass overrides. These
candidates are kept in ``override_edges``, separate from ``edges``: they do
not change resolved/unresolved counts or call statuses. A receiver created by
a visible constructor (``c = Child(); c.run()``) has an exact class and gets no
override candidates.

Each candidate is classified:
  dispatch_receiver  self_parameter | class_parameter | declared_parameter_type
  override_kind      extends (calls super()/Base.m) | replaces
  subclass_location  source | tests (override defined in test code)
  base_method_kind   concrete | abstract (abstractmethod, pass, ..., raise
                     NotImplementedError) | missing (receiver class has no m)
                     | unknown (no project definition, but an external base
                     leaves the MRO open)
Only project classes are considered; subclasses outside the analyzed source
(other packages, runtime-created classes) are never claimed to be complete.
"""
from __future__ import annotations

import ast
import csv
from collections import Counter, defaultdict
from pathlib import Path


def _is_abstract(symbol: dict, fn: ast.AST | None) -> bool:
    if any(d.split('.')[-1] == 'abstractmethod' for d in symbol.get('decorators') or []):
        return True
    if fn is None:
        return False
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]                                   # docstring
    if not body:
        return True
    if len(body) != 1:
        return False
    stmt = body[0]
    if isinstance(stmt, ast.Pass):
        return True
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is Ellipsis:
        return True
    if isinstance(stmt, ast.Raise) and stmt.exc is not None:
        exc = stmt.exc.func if isinstance(stmt.exc, ast.Call) else stmt.exc
        return isinstance(exc, ast.Name) and exc.id == 'NotImplementedError'
    return False


def _in_tests(path: str) -> bool:
    parts = path.replace('\\', '/').split('/')
    name = parts[-1]
    return (any(p in ('test', 'tests', 'testing') for p in parts[:-1])
            or name.startswith('test_') or name.endswith('_test.py') or name == 'conftest.py')


def _extends_parent(name: str, fn: ast.AST | None) -> bool:
    """super().m(...) or Parent.m(self, ...) inside the override."""
    if fn is None:
        return False
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == name):
            continue
        owner = node.func.value
        if isinstance(owner, ast.Call) and isinstance(owner.func, ast.Name) and owner.func.id == 'super':
            return True
        if isinstance(owner, ast.Name) and node.args and isinstance(node.args[0], ast.Name) \
                and node.args[0].id in ('self', 'cls'):
            return True
    return False


class OverrideDispatch:
    def __init__(self, resolver, functions: dict):
        self.r = resolver
        self.functions = functions                        # symbol id -> FunctionDef
        self.children = defaultdict(list)
        for cls in resolver.classes.values():
            for base in resolver._get_bases(cls):
                if base['canonical_name'] in resolver.classes:
                    self.children[base['canonical_name']].append(cls)

    def descendants(self, cls: dict) -> list[tuple[dict, int]]:
        """Project subclasses (any depth) with their inheritance distance."""
        seen, order, frontier, depth = {cls['canonical_name']}, [], [cls], 0
        while frontier:
            depth += 1
            nxt = []
            for parent in frontier:
                for child in self.children.get(parent['canonical_name'], []):
                    if child['canonical_name'] not in seen:
                        seen.add(child['canonical_name'])
                        order.append((child, depth))
                        nxt.append(child)
            frontier = nxt
        return order

    def defined(self, cls: dict, name: str) -> tuple[list[dict], str]:
        """First definition of a method on the receiver's MRO, decorated ones
        included, plus the MRO status.

        Resolver._find_method treats e.g. @abstractmethod as opaque, which is
        right for call edges but would misreport the base as 'missing'. When
        the MRO is not complete (an external base such as Exception), project
        ancestors are searched depth-first, left to right.
        """
        order, status = self.r._mro(cls)
        if status != 'complete':
            order, seen, pending = [], set(), [cls]
            while pending:
                current = pending.pop(0)
                if current['canonical_name'] in seen:
                    continue
                seen.add(current['canonical_name'])
                order.append(current['canonical_name'])
                pending[:0] = self.r._get_bases(current)
        for key in order:
            if key not in self.r.classes:
                continue
            found = [s for s in self.r._lookup(key + '.' + name) if s['kind'] == 'METHOD']
            if found:
                return found, status
        return [], status

    def _receiver(self, call: dict, targets: list[dict]):
        """(receiver class, method name, dispatch kind) for an open receiver."""
        text = call.get('callee_text') or ''
        if call.get('syntax_type') != 'ATTRIBUTE' or text.count('.') != 1:
            return None
        var, name = text.split('.')
        options = ((call.get('flow') or {}).get('binding') or {}).get('options') or []
        caller = self.r.by_id.get(call.get('caller_id') or '')
        if caller and caller['kind'] == 'METHOD' and (caller.get('parameters') or [None])[0] == var:
            decorators = {d.split('.')[-1] for d in caller.get('decorators') or []}
            cls = self.r.classes.get(caller['module'] + '.' + caller['scope'])
            # The first parameter must still hold the implicit receiver.
            # (`self = other` copies other's flow value, so check stores.)
            fn = self.functions.get(caller['id'])
            untouched = fn is not None and not any(
                isinstance(n, ast.Name) and n.id == var and isinstance(n.ctx, (ast.Store, ast.Del))
                for n in ast.walk(fn))
            if cls and untouched and 'staticmethod' not in decorators:
                kind = 'class_parameter' if 'classmethod' in decorators else 'self_parameter'
                return cls, name, kind
            return None
        hints = {o.get('name') for o in options if o.get('kind') == 'annotated_parameter'}
        if len(hints) != 1 or not targets:
            return None
        hint = hints.pop()
        refs = [s for s in self.r._resolve_reference(hint, call['module'], self.r.imports_at(call))
                if s['kind'] == 'CLASS']
        if len(refs) == 1:
            return refs[0], name, 'declared_parameter_type'
        return None

    def candidates(self, call: dict, caller_id: str, targets: list[dict]) -> list[dict]:
        found = self._receiver(call, targets)
        if not found:
            return []
        receiver, name, dispatch = found
        static, mro = self.defined(receiver, name)
        if not static and dispatch == 'declared_parameter_type':
            return []
        if not static:
            # An external base could still define it when the MRO is open.
            base_kind = 'missing' if mro == 'complete' else 'unknown'
        elif all(_is_abstract(s, self.functions.get(s['id'])) for s in static):
            base_kind = 'abstract'
        else:
            base_kind = 'concrete'
        static_ids = {s['id'] for s in static}
        existing = {t['id'] for t in targets}
        edges, emitted = [], set()
        for sub, depth in self.descendants(receiver):
            found_methods, path = self.r._find_method(sub, name)
            for method in found_methods:
                if method['kind'] != 'METHOD' or method['id'] in existing or method['id'] in emitted:
                    continue
                if method['id'] in static_ids:
                    continue                              # inherited unchanged
                emitted.add(method['id'])
                fn = self.functions.get(method['id'])
                edges.append({
                    'source': caller_id, 'target': method['id'], 'type': 'CALLS_OVERRIDE',
                    'evidence': {
                        'call_id': call['id'], 'file': call['file'], 'line': call['line'],
                        'column': call.get('column'), 'source': call.get('source'),
                        'rule': 'override_dispatch_candidate',
                        'certainty': 'subclass_override_may_call',
                        'dispatch_receiver': dispatch,
                        'receiver_class': receiver['canonical_name'],
                        'via_subclass': sub['canonical_name'],
                        'inheritance_depth': depth,
                        'subclass_location': 'tests' if _in_tests(method['file']) else 'source',
                        'resolved_targets': sorted(existing),
                        'base_definitions': sorted(static_ids),
                        'receiver_mro': mro,
                        'override_kind': 'extends' if _extends_parent(name, fn) else 'replaces',
                        'base_method_kind': base_kind,
                        'trace': path,
                        'note': ('Runs only if the receiver is this subclass at runtime; '
                                 'subclasses outside the analyzed source are not listed.')}})
        return edges


def summarize(override_edges: list[dict]) -> dict:
    ev = [e['evidence'] for e in override_edges]
    return {'override_edges': len(override_edges),
            'call_sites': len({e['call_id'] for e in ev}),
            'dispatch_receiver': dict(Counter(e['dispatch_receiver'] for e in ev)),
            'override_kind': dict(Counter(e['override_kind'] for e in ev)),
            'base_method_kind': dict(Counter(e['base_method_kind'] for e in ev)),
            'subclass_location': dict(Counter(e['subclass_location'] for e in ev))}


def write_implicit_csv(graph: dict, out: str | Path) -> None:
    fields = ('file', 'line', 'column', 'source', 'implicit_kind', 'member', 'caller', 'target', 'receiver_classes')
    with open(Path(out) / 'implicit_calls.csv', 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for edge in graph.get('implicit_edges', []):
            ev = edge['evidence']
            writer.writerow({**{k: ev.get(k) for k in fields}, 'caller': edge['source'], 'target': edge['target'],
                             'receiver_classes': ';'.join(ev['receiver_classes'])})


def write_override_csv(graph: dict, out: str | Path) -> None:
    fields = ('call_id', 'file', 'line', 'source', 'caller', 'target', 'dispatch_receiver',
              'receiver_class', 'via_subclass', 'inheritance_depth', 'subclass_location', 'override_kind',
              'base_method_kind', 'receiver_mro', 'resolved_targets', 'base_definitions')
    with open(Path(out) / 'override_calls.csv', 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for edge in graph.get('override_edges', []):
            ev = edge['evidence']
            writer.writerow({**{k: ev.get(k) for k in fields}, 'caller': edge['source'],
                             'target': edge['target'],
                             'resolved_targets': ';'.join(ev['resolved_targets']),
                             'base_definitions': ';'.join(ev['base_definitions'])})
