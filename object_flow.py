"""Bounded, context-sensitive abstract execution for internal object flow.

No target code is executed. Values are finite sets of references/allocation
sites plus explicit unknown/None alternatives. Recursive cutoffs yield top,
never a guessed class. The legacy graph resolver remains the fallback for
syntax outside this interpreter's supported subset.
"""
from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass, replace


# ast.TryStar was introduced in Python 3.11; KnitCode supports Python 3.10+.
TRY_STATEMENTS = (ast.Try,) + ((ast.TryStar,) if hasattr(ast, 'TryStar') else ())


@dataclass(frozen=True)
class Atom:
    kind: str
    name: str = ''
    site: str = ''
    receiver_class: str = ''  # For a bound method: the allocation site's class.


@dataclass(frozen=True)
class Value:
    atoms: frozenset[Atom] = frozenset()
    tags: frozenset[str] = frozenset()

    def join(self, other):
        # Values are immutable, so a join that adds nothing can return an
        # existing operand. Most merges in large projects are of this kind.
        if other is self or (other.atoms <= self.atoms and other.tags <= self.tags):
            if len(self.atoms) <= 32:
                return self
        elif self.atoms <= other.atoms and self.tags <= other.tags and len(other.atoms) <= 32:
            return other
        atoms = self.atoms | other.atoms
        return Value(atoms if len(atoms) <= 32 else frozenset({Atom('unknown')}), self.tags | other.tags)

    def tagged(self, tag):
        if tag in self.tags:
            return self
        return replace(self, tags=self.tags | {tag})


# Implicit calls: Python runs these methods without a call expression.
_BINARY = {ast.Add: 'add', ast.Sub: 'sub', ast.Mult: 'mul', ast.MatMult: 'matmul',
           ast.Div: 'truediv', ast.FloorDiv: 'floordiv', ast.Mod: 'mod', ast.Pow: 'pow',
           ast.LShift: 'lshift', ast.RShift: 'rshift', ast.BitAnd: 'and', ast.BitOr: 'or',
           ast.BitXor: 'xor'}
_UNARY = {ast.USub: '__neg__', ast.UAdd: '__pos__', ast.Invert: '__invert__'}
# (method on the left operand, reflected method on the right operand)
_COMPARE = {ast.Eq: ('__eq__', '__eq__'), ast.NotEq: ('__ne__', '__ne__'),
            ast.Lt: ('__lt__', '__gt__'), ast.LtE: ('__le__', '__ge__'),
            ast.Gt: ('__gt__', '__lt__'), ast.GtE: ('__ge__', '__le__')}
_PROTOCOL_BUILTINS = {'len': '__len__', 'iter': '__iter__', 'next': '__next__',
                      'reversed': '__reversed__', 'str': '__str__', 'repr': '__repr__',
                      'bool': '__bool__', 'hash': '__hash__', 'abs': '__abs__',
                      'format': '__format__', 'int': '__int__', 'float': '__float__',
                      'complex': '__complex__', 'bytes': '__bytes__', 'round': '__round__',
                      'dir': '__dir__'}


def is_property(symbol):
    for decorator in symbol.get('decorators') or []:
        name = decorator.split('(')[0]
        if name.split('.')[-1] in ('property', 'cached_property') or \
                name.endswith(('.setter', '.getter', '.deleter')):
            return True
    return False


UNKNOWN = Value(frozenset({Atom('unknown')}))
NONE = Value(frozenset({Atom('none')}))
BOTTOM = Value()


def one(kind, name='', site=''):
    return Value(frozenset({Atom(kind, name, site)}))


def merge_maps(a, b):
    # A key missing on one side is UNKNOWN there. Callers never depend on
    # the key order of the result.
    merged = dict(a)
    if a is b:
        return merged
    for k, right in b.items():
        left = merged.get(k)
        if left is None:
            merged[k] = UNKNOWN.join(right)
        elif left is not right:
            merged[k] = left.join(right)
    if len(merged) != len(b):
        for k in a.keys() - b.keys():
            merged[k] = a[k].join(UNKNOWN)
    return merged


def literal_key(node):
    """Recognize scalar keys without evaluating project expressions."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, str, bytes, type(None))):
        value = node.value
    elif (isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd))
          and isinstance(node.operand, ast.Constant) and type(node.operand.value) is int):
        value = -node.operand.value if isinstance(node.op, ast.USub) else node.operand.value
    else:
        return None
    # bool and int keys share equality in Python dictionaries.
    return ('index', int(value) if isinstance(value, bool) else value)


class ObjectFlow:
    def __init__(self, resolver, *, fields=False, dispatch=False):
        self.r = resolver
        self.fields = fields
        self.dispatch = dispatch
        self.functions = {}
        self.modules = {}
        self.events = {}
        self.summaries = {}
        self.stack = []
        self.return_heaps = []
        self.coroutines = {}
        self.lambda_symbols = {(s['file'],s['line'],s['column']):s for s in resolver.symbols if s['kind']=='LAMBDA'}
        self.callback_events = {}
        self.external_events = {}
        from call_status import StatusAnnotator
        self.call_origins = StatusAnnotator(resolver.data)
        self.steps = 0                  # Total work, for diagnostics only.
        self.root_steps = 0             # Budget belongs to one entry context.
        self.root_step_limit = 6000
        self.cutoffs = 0
        self.generic_context_fallbacks = 0
        self.specific = set()
        self._own_nodes_cache = {}
        self._member_cache = {}
        self._builtin_property_cache = {}
        self._assigned_targets = None   # file -> assigned dotted names (class_assignments)
        self._assigned_cache = {}
        # (file, line, column, kind, member) -> owner/module/end + per-mode targets
        self.implicit_events = {}
        self.implicit_results = []
        self._generator_cache = {}
        self._refs_cache = {}  # Static names only; never objects, heaps or returns.
        self.call_index = {(c['file'], c['line'], c['column'], c.get('end_line'), c.get('end_column')): c for c in resolver.calls}
        for file, source in resolver.data.get('analysis_sources', {}).items():
            tree = ast.parse(source)
            self.modules[file] = tree
            index = {n.lineno: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            for symbol in resolver.symbols:
                if symbol['file'] == file and symbol['kind'] in ('FUNCTION', 'METHOD') and symbol['line'] in index:
                    self.functions[symbol['id']] = index[symbol['line']]

    def refs(self, name, context, node):
        call = {**context, 'line': getattr(node, 'lineno', context.get('line', 0))}
        key = (name, call['file'], call['module'], call.get('scope'), call['line'])
        if key in self._refs_cache:
            return self._refs_cache[key]
        imports = self.r.imports_at(call)
        # Nested definitions in enclosing function scopes shadow imports; class
        # namespaces are not visible from their methods (lexical_scopes).
        refs = []
        if '.' not in name:
            for scope in self.r.lexical_scopes(context.get('scope'), context['module']):
                refs = self.r._lookup('.'.join([context['module'], scope, name]))
                if refs:
                    break
        refs = refs or self.r._resolve_reference(name, context['module'], imports)
        self._refs_cache[key] = refs
        return refs

    def assigned_name(self, file, name, prefix):
        """Whether file assigns name, or (prefix='any'/'dotted') a dotted prefix
        of it; 'dotted' only counts prefixes that are themselves dotted."""
        key = (file, name, prefix)
        if key not in self._assigned_cache:
            if self._assigned_targets is None:
                self._assigned_targets = defaultdict(set)
                for target_file, _, target in self.call_origins.class_assignments:
                    self._assigned_targets[target_file].add(target)
            self._assigned_cache[key] = any(
                name == target or prefix and (prefix == 'any' or '.' in target) and name.startswith(target + '.')
                for target in self._assigned_targets.get(file, ()))
        return self._assigned_cache[key]

    def external_reference(self, name, context, node):
        from import_resolution import canonical_import
        head, dot, rest = name.partition('.')
        call = {**context, 'line': node.lineno, 'column': node.col_offset}
        imp, conflicts, _ = self.call_origins._bound_at(call, head)
        if not imp or conflicts:
            return UNKNOWN
        origins = self.call_origins.origins.trace(canonical_import(imp))
        if len(origins) != 1 or origins[0]['kind'] not in ('stdlib', 'external'):
            return UNKNOWN
        if self.assigned_name(context['file'], name, 'any'):
            return UNKNOWN
        return one('external_reference', origins[0]['name'] + ('.' + rest if dot else ''))

    def external_base(self, cls, node, *, inherited_member=None, seen=frozenset()):
        # A single explicit base avoids inventing an MRO across unknown mixins.
        if not cls or cls['id'] in seen or len(cls.get('bases', [])) != 1:
            return UNKNOWN
        if inherited_member and self.r._local_method(cls, inherited_member)[1]:
            return UNKNOWN
        try:
            base = ast.parse(cls['bases'][0], mode='eval').body
        except SyntaxError:
            return UNKNOWN
        if not isinstance(base, (ast.Name, ast.Attribute)):
            return UNKNOWN
        context = {**cls, 'scope': cls.get('scope', '<module>')}
        name = ast.unparse(base)
        external = self.external_reference(name, context, node)
        if any(a.kind == 'external_reference' for a in external.atoms):
            return external
        refs = self.refs(name, context, node)
        if len(refs) == 1 and refs[0]['kind'] == 'CLASS':
            return self.external_base(refs[0], node, inherited_member=inherited_member, seen=seen | {cls['id']})
        return UNKNOWN

    def builtin_base(self, cls, member, node, seen=frozenset()):
        from builtin_receivers import METHODS
        if not cls or cls['id'] in seen or len(cls.get('bases', [])) != 1:
            return None
        if self.r._local_method(cls, member)[1]:
            return None
        try:
            base = ast.parse(cls['bases'][0], mode='eval').body
        except SyntaxError:
            return None
        if not isinstance(base, ast.Name):
            return None
        context = {**cls, 'scope': cls.get('scope', '<module>')}
        imported, conflicts, _ = self.call_origins._bound_at(context, base.id)
        if base.id in METHODS and member in METHODS[base.id] and not imported and not conflicts:
            return base.id
        refs = self.refs(base.id, context, node)
        if len(refs) == 1 and refs[0]['kind'] == 'CLASS':
            return self.builtin_base(refs[0], member, node, seen | {cls['id']})
        return None

    def hint(self, node, context):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                node = ast.parse(node.value, mode='eval').body
            except SyntaxError:
                return UNKNOWN
        if not isinstance(node, (ast.Name, ast.Attribute)):
            return UNKNOWN
        refs = self.refs(ast.unparse(node), context, node)
        if len(refs) == 1 and refs[0]['kind'] == 'CLASS':
            return one('object', refs[0]['canonical_name'], 'hint:' + refs[0]['id']).join(UNKNOWN).tagged('annotation')
        return UNKNOWN

    def namespace_shadow(self, cls, attribute, heap):
        """A write on this path suppresses the old descriptor, not saved aliases."""
        if not cls:
            return False
        hierarchy, _ = self.r._mro(cls)
        for name in hierarchy or [cls['canonical_name']]:
            for key in (attribute, '*'):
                value = heap.get(('namespace:' + name, key))
                if value and value.atoms and all(a.kind == 'namespace_write' for a in value.atoms):
                    return True
            if self.r._lookup(name + '.' + attribute):
                return False
        return False

    def namespace_write(self, receiver, attribute, heap):
        for atom in receiver.atoms:
            symbol = self.r.by_id.get(atom.name) if atom.kind == 'reference' else None
            if symbol and symbol['kind'] == 'CLASS':
                heap[('namespace:' + symbol['canonical_name'], attribute)] = one('namespace_write')

    def note(self, node, context, mode, targets, managed, tags=()):
        call = self.call_index.get((context['file'], node.lineno, node.col_offset, node.end_lineno, node.end_col_offset))
        if call is None:
            return
        key = (call['id'], mode)
        item = self.events.setdefault(key, {'targets': set(), 'managed': False, 'trace': set(), 'owner': call.get('caller_id')})
        item['targets'].update(s['id'] for s in targets)
        item['managed'] |= managed
        item['trace'].update(tags)
        item['trace'].add('context:' + ' > '.join(self.stack[-4:]))

    def property_getter(self, cls, name, heap):
        if not cls or self.namespace_shadow(cls, name, heap):
            return None
        order, status = self.r._mro(cls)
        if status not in ('complete', 'unknown_base'):
            return None
        for canonical in order if status == 'complete' else [cls['canonical_name']]:
            owner = self.r.classes.get(canonical)
            if not owner:
                continue
            local = self.r._lookup(canonical + '.' + name)
            assignments = [a for a in self.r.assignments.get((owner['file'], owner['qualified_name']), [])
                           if name in a.get('targets', [])]
            if assignments and (not local or max(a['line'] for a in assignments) >= max(f['line'] for f in local)):
                return None
            if not local:
                continue
            if len(local) != 1:
                return None
            fn = local[0]
            if (fn['kind'] != 'METHOD' or fn.get('decorators') != ['property']
                    or fn.get('control_depth') or fn['id'] in self.r.mutated_methods
                    or fn['id'] in self.r.conditionally_mutated_methods):
                return None
            context = {**fn, 'scope': fn['scope']}
            if ('property' in self.r.imports_at(context)
                    or self.refs('property', context, self.functions[fn['id']])
                    or self.r._find_binding(context, 'property')):
                return None
            return fn
        return None

    def invoke(self, symbol, args, kwargs, heap, mode, site, *, awaited=False, original_binding=False, property_access=False):
        if not self.stack:
            # Each independent method/function entry gets a fair bounded
            # opportunity, even if earlier files contain many expressions.
            self.root_steps = 0
        fn = self.functions.get(symbol['id'])
        if fn is None or (not property_access and not self.r.safe_callable(symbol)):
            return UNKNOWN.tagged('return')
        if symbol['id'] in self.r.conditionally_mutated_methods and not original_binding:
            original_heap, replaced_heap = dict(heap), dict(heap)
            value = self.invoke(symbol, args, kwargs, original_heap, mode, site,
                                awaited=awaited, original_binding=True)
            self.invalidate_escaped([*args, *kwargs.values()], replaced_heap)
            heap.clear(); heap.update(merge_maps(original_heap, replaced_heap))
            return value.join(UNKNOWN).tagged('return')
        if self.is_generator(fn):
            return UNKNOWN.tagged('return')
        if isinstance(fn, ast.AsyncFunctionDef) and not awaited:
            token = symbol['id'] + ':' + site
            record = (symbol['id'], tuple(args), tuple(kwargs.items()), mode, site)
            self.coroutines.setdefault(token, set()).add(record)
            return one('coroutine', token).tagged('return')
        if symbol['id'] in self.stack or len(self.stack) >= 16 or self.root_steps >= self.root_step_limit:
            self.cutoffs += 1
            return UNKNOWN.tagged('return')
        if mode == 'specific':
            self.specific.add(symbol['id'])
        context = {**symbol, 'scope': symbol['qualified_name'], 'caller_id': symbol['id']}
        params = [*fn.args.posonlyargs, *fn.args.args]
        if mode == 'specific':
            required = len(params) - len(fn.args.defaults)
            known = {p.arg for p in [*params, *fn.args.kwonlyargs]}
            invalid = (len(args) > len(params) and fn.args.vararg is None)
            invalid |= any(p.arg in kwargs for p in params[:len(args)])
            invalid |= any(p.arg in kwargs for p in fn.args.posonlyargs)
            invalid |= any(i >= len(args) and p.arg not in kwargs for i, p in enumerate(params[:required]))
            invalid |= any(p.arg not in kwargs and default is None for p, default in zip(fn.args.kwonlyargs, fn.args.kw_defaults))
            invalid |= any(k not in known for k in kwargs) and fn.args.kwarg is None
            if invalid:
                return UNKNOWN.tagged('return')
        env = {}
        defaults = dict(zip((p.arg for p in params[len(params)-len(fn.args.defaults):]), fn.args.defaults))
        defaults.update((p.arg, d) for p, d in zip(fn.args.kwonlyargs, fn.args.kw_defaults) if d is not None)
        # Defaults belong to the enclosing definition scope. Only immutable
        # literals and unshadowed names are reconstructed here; calls and
        # mutable expressions must not be re-evaluated at invocation time.
        default_context = {**context, 'scope': symbol.get('scope', '<module>')}
        def parameter_value(param, supplied=None):
            if supplied is not None:
                return supplied
            value = self.hint(param.annotation, context)
            default = defaults.get(param.arg)
            if isinstance(default, (ast.Name, ast.Constant)):
                default_value = self.expr(default, {}, heap, default_context, mode, site)
                value = default_value if mode == 'specific' else value.join(default_value)
            return value
        for i, param in enumerate(params):
            supplied = args[i] if i < len(args) else kwargs.get(param.arg)
            value = parameter_value(param, supplied)
            env[param.arg] = value.tagged('context') if mode == 'specific' else value
        for param in fn.args.kwonlyargs:
            value = parameter_value(param, kwargs.get(param.arg))
            env[param.arg] = value.tagged('context') if mode == 'specific' else value
        # Python determines locals lexically, even before their first store.
        for n in self.own_nodes(fn):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                env.setdefault(n.id, UNKNOWN)
        self.stack.append(symbol['id'])
        self.return_heaps.append([])
        _, result, live = self.block(fn.body, env, heap, context, mode, site)
        returned_heaps = self.return_heaps.pop()
        self.stack.pop()
        if live:
            result = result.join(NONE)
            returned_heaps.append(dict(heap))
        if returned_heaps:
            merged_heap = returned_heaps[0]
            for branch_heap in returned_heaps[1:]:
                merged_heap = merge_maps(merged_heap, branch_heap)
            heap.clear(); heap.update(merged_heap)
        stub = all(isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                   and (n.value.value is Ellipsis or isinstance(n.value.value, str)) for n in fn.body)
        # Concrete bodies outrank advisory hints; hints only supplement top or
        # ellipsis stubs, never contradict a known return value or implicit None.
        if stub or any(a.kind == 'unknown' for a in result.atoms):
            result = (UNKNOWN if stub else result).join(self.hint(fn.returns, context))
        result = result.tagged('return:' + symbol['canonical_name']).tagged('return')
        self.summaries[symbol['id']] = self.summaries.get(symbol['id'], BOTTOM).join(result)
        return result

    def own_nodes(self, fn):
        # invoke() revisits the same function many times; walk it once.
        nodes = self._own_nodes_cache.get(fn)
        if nodes is None:
            nodes, pending = [], list(fn.body)
            while pending:
                node = pending.pop()
                nodes.append(node)
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                    pending.extend(ast.iter_child_nodes(node))
            self._own_nodes_cache[fn] = nodes
        return nodes

    def is_generator(self, fn):
        if fn not in self._generator_cache:
            self._generator_cache[fn] = any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in self.own_nodes(fn))
        return self._generator_cache[fn]

    def member(self, class_name, name):
        """Project methods named `name` that the type `class_name` resolves,
        decorated ones included (properties). Empty when an external base or a
        class attribute provides the name first, or the class is unknown."""
        key = (class_name, name)
        if key in self._member_cache:
            return self._member_cache[key]
        found = []
        cls = self.r.classes.get(class_name)
        if cls:
            order, status = self.r._mro(cls)
            if status != 'complete':
                # Same policy as Resolver._find_method: a local definition on
                # an unknown base, or on a class whose decorator is
                # source-proven (class_decorators.py); nothing otherwise.
                from class_decorators import preserves_local_method
                local = status == 'unknown_base' or (
                    status == 'unknown_class_customization' and preserves_local_method(self.r, cls, name))
                order = [class_name] if local else []
            for k in order:
                if k == 'builtins.object':
                    break
                if k in self.r.external_bases:
                    if name in self.r.external_bases[k]:
                        break
                    continue
                if k in self.r._KNOWN_STDLIB_BASES or k not in self.r.classes:
                    continue
                found = [s for s in self.r._lookup(k + '.' + name) if s['kind'] == 'METHOD']
                owner = self.r.classes[k]
                if found or any(name in a.get('targets', []) for a in
                                self.r.assignments.get((owner['file'], owner['qualified_name']), [])):
                    break
        self._member_cache[key] = found
        return found

    def builtin_property(self, fn):
        """True when fn's descriptor decorator is the real property (or its
        accessor) or functools.cached_property, not a project name."""
        if fn['id'] in self._builtin_property_cache:
            return self._builtin_property_cache[fn['id']]
        context = {**fn, 'scope': fn['scope']}
        imports = self.r.imports_at(context)
        result = False
        for decorator in fn.get('decorators') or []:
            name = decorator.split('(')[0]
            if name.split('.')[-1] == 'cached_property':
                result = imports.get(name.split('.')[0]) in ('functools', 'functools.cached_property')
                break
            if name == 'property' or name.endswith(('.setter', '.getter', '.deleter')):
                result = not ('property' in imports or self.refs('property', context, self.functions.get(fn['id']))
                              or self.r._find_binding(context, 'property'))
                break
        self._builtin_property_cache[fn['id']] = result
        return result

    def implicit(self, node, context, mode, kind, name, value, *, want_property=False):
        """Record methods run implicitly on the project objects in value."""
        hits, receivers = set(), set()
        for atom in value.atoms:
            if atom.kind != 'object':
                continue
            # A patched method (Cls.m = ..., setattr) no longer certifies the
            # original definition; a descriptor must be the real property.
            methods = [m for m in self.member(atom.name, name)
                       if is_property(m) == want_property and m['id'] not in self.r.mutated_methods
                       and (not want_property or self.builtin_property(m))]
            if methods:
                hits.update(m['id'] for m in methods)
                receivers.add(atom.name)
        if not hits:
            return False
        key = (context['file'], node.lineno, node.col_offset, kind, name)
        event = self.implicit_events.setdefault(key, {
            'owner': context.get('caller_id'), 'module': context.get('module'),
            'end': (node.end_lineno, node.end_col_offset), 'modes': {}})
        slot = event['modes'].setdefault(mode, {'targets': set(), 'receivers': set()})
        slot['targets'].update(hits)
        slot['receivers'].update(receivers)
        return True

    def implicit_operator(self, node, context, mode, left_name, right_name, left, right):
        self.implicit(node, context, mode, 'operator', left_name, left)
        if right_name and self.lacks_forward(left, (left_name,)):
            self.implicit(node, context, mode, 'operator', right_name, right)

    def lacks_forward(self, value, names):
        """True only when every value of the left operand is a project object
        whose class provably defines none of `names`, or a numeric literal.
        Python then calls the reflected method of the right operand (object's
        default comparisons and int/float operators return NotImplemented).
        Unknown or opaque types, and str/bytes literals (`'%s' % x`), record
        nothing."""
        if not value.atoms:
            return False
        for atom in value.atoms:
            if atom.kind == 'literal' and not atom.name.startswith(("'", '"', "b'", 'b"')):
                continue
            cls = self.r.classes.get(atom.name) if atom.kind == 'object' else None
            if not cls:
                return False
            order, status = self.r._mro(cls)
            if status != 'complete':
                return False
            for k in order:
                if k == 'builtins.object':
                    break
                if k in self.r.external_bases:
                    if any(n in self.r.external_bases[k] for n in names):
                        return False
                    continue
                if k in self.r._KNOWN_STDLIB_BASES or k not in self.r.classes:
                    return False
                owner = self.r.classes[k]
                if any(s['kind'] == 'METHOD' for n in names for s in self.r._lookup(k + '.' + n)) or any(
                        n in a.get('targets', []) for n in names
                        for a in self.r.assignments.get((owner['file'], owner['qualified_name']), [])):
                    return False
        return True

    def peek(self, node, env, heap, context, mode, site):
        """Value of a call-free name/attribute/subscript expression that the
        legacy flow does not evaluate. The step budget is restored so the
        existing analysis is unaffected."""
        if any(isinstance(n, (ast.Call, ast.NamedExpr, ast.Await, ast.Yield, ast.YieldFrom, ast.Lambda))
               for n in ast.walk(node)):
            return UNKNOWN
        saved = self.steps, self.root_steps
        try:
            return self.expr(node, env, dict(heap), context, mode, site)
        finally:
            self.steps, self.root_steps = saved

    def expr(self, node, env, heap, context, mode, site):
        if node is None:
            return NONE
        self.steps += 1
        self.root_steps += 1
        if self.root_steps > self.root_step_limit:
            return UNKNOWN
        if isinstance(node, ast.Name):
            if node.id in env:
                return env[node.id]
            refs = self.refs(node.id, context, node)
            return (Value(frozenset(Atom('reference', s['id']) for s in refs)) if refs
                    else self.external_reference(node.id, context, node))
        if isinstance(node, ast.Constant):
            return NONE if node.value is None else one('literal', repr(node.value))
        if isinstance(node, ast.Starred):
            return self.expr(node.value, env, heap, context, mode, site)
        if isinstance(node, ast.Await):
            pending = self.expr(node.value, env, heap, context, mode, site)
            result, outcomes = BOTTOM, []
            for atom in pending.atoms:
                records = self.coroutines.get(atom.name, ()) if atom.kind == 'coroutine' else ()
                if not records:
                    result = result.join(UNKNOWN)
                    outcomes.append(dict(heap))
                for fn, args, kwargs, child_mode, child_site in sorted(records, key=repr):
                    branch = dict(heap)
                    result = result.join(self.invoke(self.r.by_id[fn], list(args), dict(kwargs),
                                                     branch, child_mode, child_site, awaited=True))
                    outcomes.append(branch)
            if outcomes:
                merged = outcomes[0]
                for branch in outcomes[1:]:
                    merged = merge_maps(merged, branch)
                heap.clear(); heap.update(merged)
            return (result if result.atoms else UNKNOWN).tagged('return')
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            # Keep collection elements distinct from directly callable values.
            # Only an explicit for-loop may unwrap the finite element set.
            elements = BOTTOM
            allocation = f"container:{context['file']}:{node.lineno}:{node.col_offset}:{site}"
            for i, item in enumerate(node.elts):
                values = self.expr(item, env, heap, context, mode, site)
                if not isinstance(node, ast.Set) and len(node.elts) <= 32:
                    heap[(allocation, ('index', i))] = values
                elements = elements.join(Value(frozenset(
                    Atom('collection_' + atom.kind, atom.name, atom.site, atom.receiver_class)
                    for atom in values.atoms), values.tags))
            elements = elements if node.elts else one('collection_unknown')
            if not isinstance(node, ast.Set):
                kind = 'tuple' if isinstance(node, ast.Tuple) else 'list'
                if len(node.elts) <= 32 and not any(isinstance(x, ast.Starred) for x in node.elts):
                    heap[(allocation, '*')] = BOTTOM
                else:
                    heap[(allocation, '*')] = UNKNOWN
                elements = elements.join(one('container', f'{kind}:{len(node.elts)}', allocation))
            return elements
        if isinstance(node, ast.Dict):
            allocation = f"container:{context['file']}:{node.lineno}:{node.col_offset}:{site}"
            heap[(allocation, '*')] = BOTTOM
            for key_node, value_node in zip(node.keys, node.values):
                self.expr(key_node, env, heap, context, mode, site)
                value = self.expr(value_node, env, heap, context, mode, site)
                key = literal_key(key_node)
                if key is None or len(node.keys) > 32:
                    self.invalidate_containers(one('container', 'dict', allocation), heap)
                    heap[(allocation, '*')] = UNKNOWN
                else:
                    heap[(allocation, key)] = value
            return one('container', 'dict', allocation).tagged('indexed')
        if isinstance(node, ast.Subscript):
            collection = self.expr(node.value, env, heap, context, mode, site)
            self.expr(node.slice, env, heap, context, mode, site)
            if isinstance(node.ctx, ast.Load):
                self.implicit(node, context, mode, 'subscript', '__getitem__', collection)
            key = literal_key(node.slice)
            result = BOTTOM
            for atom in collection.atoms:
                if atom.kind.startswith('collection_'):
                    continue
                if atom.kind != 'container' or key is None or heap.get((atom.site, '*'), UNKNOWN).atoms:
                    result = result.join(UNKNOWN)
                    continue
                actual_key = key
                if atom.name.startswith(('list:', 'tuple:')):
                    if type(key[1]) is not int:
                        result = result.join(UNKNOWN)
                        continue
                    if key[1] < 0:
                        actual_key = ('index', int(atom.name.split(':')[1]) + key[1])
                result = result.join(heap.get((atom.site, actual_key), UNKNOWN))
            return (result if result.atoms else UNKNOWN).tagged('return').tagged('indexed')
        if isinstance(node, ast.Lambda):
            # A lambda expression only evaluates its default arguments now;
            # its body is deferred until the lambda object is invoked.
            for default in [*node.args.defaults, *node.args.kw_defaults]:
                self.expr(default, env, heap, context, mode, site)
            symbol = self.lambda_symbols.get((context['file'],node.lineno,node.col_offset))
            return one('reference',symbol['id']).tagged('lambda') if symbol else UNKNOWN
        if isinstance(node, ast.BoolOp):
            # Preserve short-circuit side effects. For an unknown truth value,
            # retain both the stopped and the continuing heap, in linear work.
            branch = dict(heap)
            outcomes, result = [], BOTTOM
            for index, item in enumerate(node.values):
                value = self.expr(item, env, branch, context, mode, site)
                truth = bool(item.value) if isinstance(item, ast.Constant) else None
                stops = (truth is False if isinstance(node.op, ast.And) else truth is True)
                if index == len(node.values)-1 or stops:
                    result = result.join(value)
                    outcomes.append(dict(branch))
                    break
                if truth is None:
                    result = result.join(value)
                    outcomes.append(dict(branch))
            merged = outcomes[0]
            for outcome in outcomes[1:]:
                merged = merge_maps(merged, outcome)
            heap.clear(); heap.update(merged)
            return result
        if isinstance(node, ast.IfExp):
            self.expr(node.test, env, heap, context, mode, site)
            left, right = dict(heap), dict(heap)
            a = self.expr(node.body, dict(env), left, context, mode, site)
            b = self.expr(node.orelse, dict(env), right, context, mode, site)
            heap.clear(); heap.update(merge_maps(left, right))
            return a.join(b)
        if isinstance(node, ast.Attribute):
            receiver = self.expr(node.value, env, heap, context, mode, site)
            self.implicit(node, context, mode, 'property_get', node.attr, receiver, want_property=True)
            result = BOTTOM
            for atom in receiver.atoms:
                if atom.kind in ('external_reference', 'external_result'):
                    kind = 'external_reference' if atom.kind == 'external_reference' else 'external_bound'
                    result = result.join(one(kind, atom.name + '.' + node.attr))
                    continue
                if atom.kind != 'object':
                    result = result.join(UNKNOWN)
                    continue
                cls = self.r.classes.get(atom.name)
                getter = self.property_getter(cls, node.attr, heap)
                if getter:
                    # The getter edge itself is recorded by implicit(); here
                    # only its return value flows on (self.worker.run()).
                    value = self.invoke(getter, [one('object', atom.name, atom.site)], {}, heap,
                                        mode, site, property_access=True)
                    result = result.join(value.tagged('field'))
                    continue
                # Instance attributes take priority over non-data methods.
                field = heap.get((atom.site, node.attr)) if self.fields else None
                if field is not None:
                    result = result.join(field.tagged('field'))
                    continue
                cls = self.r.classes.get(atom.name)
                if self.namespace_shadow(cls, node.attr, heap):
                    result = result.join(UNKNOWN.tagged('binding'))
                    continue
                nested = [s for s in self.r._lookup(atom.name + '.' + node.attr) if s['kind'] == 'CLASS']
                if nested:
                    result = result.join(Value(frozenset(Atom('reference', s['id']) for s in nested)))
                    continue
                methods, _ = self.r._find_method(cls, node.attr) if cls else ([], [])
                if methods:
                    # Store the receiver as part of the value.  A later fn()
                    # call must use the object that produced fn = obj.method.
                    bound_methods = frozenset(
                        Atom('bound_method', fn['id'], atom.site, atom.name)
                        for fn in methods if all(
                            decorator in ('classmethod', 'staticmethod')
                            for decorator in fn.get('decorators', [])))
                    # A property or an arbitrary decorated descriptor may
                    # yield a non-callable value rather than the method.
                    result = result.join(Value(bound_methods) if len(bound_methods) == len(methods) else UNKNOWN)
                else:
                    result = result.join(UNKNOWN)
            # Module/class qualified references are resolved only if the root
            # is not an actual local receiver (no name-based fallback).
            root = node
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and root.id not in env:
                for atom in receiver.atoms:
                    cls = self.r.by_id.get(atom.name) if atom.kind == 'reference' else None
                    if cls and cls['kind'] == 'CLASS' and self.namespace_shadow(cls, node.attr, heap):
                        return UNKNOWN.tagged('binding')
                refs = self.refs(ast.unparse(node), context, node)
                if refs:
                    return Value(frozenset(Atom('reference', s['id']) for s in refs))
            tracked = any(a.kind == 'object' for a in receiver.atoms) or bool(receiver.tags & {'return', 'field'})
            return result.tagged('field') if self.fields and tracked else result
        if isinstance(node, ast.Call):
            return self.call(node, env, heap, context, mode, site)
        # Same evaluation order as the generic walk below, plus implicit calls.
        if isinstance(node, ast.BinOp):
            left = self.expr(node.left, env, heap, context, mode, site)
            right = self.expr(node.right, env, heap, context, mode, site)
            op = _BINARY.get(type(node.op))
            if op:
                self.implicit_operator(node, context, mode, f'__{op}__', f'__r{op}__', left, right)
            return UNKNOWN
        if isinstance(node, ast.UnaryOp):
            operand = self.expr(node.operand, env, heap, context, mode, site)
            if type(node.op) in _UNARY:
                self.implicit(node, context, mode, 'operator', _UNARY[type(node.op)], operand)
            return UNKNOWN
        if isinstance(node, ast.Compare):
            left = self.expr(node.left, env, heap, context, mode, site)
            for op, comparator in zip(node.ops, node.comparators):
                right = self.expr(comparator, env, heap, context, mode, site)
                if isinstance(op, (ast.In, ast.NotIn)):
                    self.implicit(node, context, mode, 'operator', '__contains__', right)
                elif type(op) in _COMPARE:
                    self.implicit_operator(node, context, mode, *_COMPARE[type(op)], left, right)
                left = right
            return UNKNOWN
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            for generator in node.generators:
                iterable = self.peek(generator.iter, env, heap, context, mode, site)
                self.implicit(generator.iter, context, mode, 'iteration',
                              '__aiter__' if generator.is_async else '__iter__', iterable)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                self.expr(child, env, heap, context, mode, site)
        return UNKNOWN

    def call(self, node, env, heap, context, mode, site):
        receiver = None
        targets = []
        bindings = []
        managed = False
        tags = set()
        external = []
        if isinstance(node.func, ast.Attribute):
            if (isinstance(node.func.value, ast.Call) and isinstance(node.func.value.func, ast.Name)
                    and node.func.value.func.id == 'super'):
                proxy = node.func.value
                owner = self.r._owner_class(context)
                symbol = self.r.by_id.get(context.get('caller_id'), {})
                params = symbol.get('parameters', [])
                call = self.call_index.get((context['file'], node.lineno, node.col_offset, node.end_lineno, node.end_col_offset))
                allowed = False
                if call:
                    _, why, _ = self.r._resolve_super(call, owner, self.r.imports_at(call))
                    allowed = why in ('super_c3_may_call', 'super_method_not_found')
                receiver = env.get(params[0], UNKNOWN) if params else UNKNOWN
                anchor = owner
                if len(proxy.args) == 2:
                    refs = self.refs(ast.unparse(proxy.args[0]), context, node)
                    anchor = refs[0] if len(refs) == 1 and refs[0]['kind'] == 'CLASS' else None
                if allowed and anchor:
                    for atom in receiver.atoms:
                        if atom.kind != 'object':
                            continue
                        order, status = self.r._mro(self.r.classes[atom.name])
                        if status == 'complete' and anchor['canonical_name'] in order:
                            found, path = self.r._lookup_mro(order[order.index(anchor['canonical_name']) + 1:], node.func.attr)
                            for fn in found:
                                targets.append(fn); bindings.append((fn, one('object', atom.name, atom.site)))
                            tags.update(path)
                managed = self.dispatch and mode == 'specific'
                tags.update(receiver.tags)
                tags.add('super_receiver_context')
                # Preserve a symbolic cross-boundary super call when no
                # project method is selected and the explicit anchor is valid.
                builtin_super = self.call_origins._bound_at(call, 'super') if call else (None, ['missing'], False)
                valid_anchor = len(proxy.args) in (0, 2) and anchor == owner
                if not targets and valid_anchor and not builtin_super[0] and not builtin_super[1]:
                    for external_atom in self.external_base(anchor, node).atoms:
                        if external_atom.kind == 'external_reference':
                            external.append((external_atom.name + '.' + node.func.attr, 'external_bound'))

            else:
                receiver = self.expr(node.func.value, env, heap, context, mode, site)
                tags.update(receiver.tags)
                managed = bool(receiver.tags & {'return', 'field', 'binding'}) or isinstance(node.func.value, ast.Call)
                # A tracked receiver's flow outranks the older syntax-only
                # snapshot (e.g. it has already pruned an `if False` branch).
                managed |= any(a.kind == 'object' for a in receiver.atoms)
                if self.dispatch and mode == 'specific' and 'context' in receiver.tags:
                    managed = True
                for atom in receiver.atoms:
                    if atom.kind in ('external_reference', 'external_result'):
                        kind = 'external_reference' if atom.kind == 'external_reference' else 'external_bound'
                        external.append((atom.name + '.' + node.func.attr, kind))
                    if atom.kind == 'object':
                        cls = self.r.classes.get(atom.name)
                        # Instance attributes shadow non-data method descriptors.
                        if self.fields and (atom.site, node.func.attr) in heap:
                            managed = True
                            tags.add('field_shadow')
                            field_value = heap[(atom.site, node.func.attr)]
                            tags.update(field_value.tags)
                            for field_atom in field_value.atoms:
                                if field_atom.kind == 'bound_method' and field_atom.name in self.r.by_id:
                                    fn = self.r.by_id[field_atom.name]
                                    targets.append(fn)
                                    bound = one('object', field_atom.receiver_class, field_atom.site)
                                    if 'classmethod' in fn.get('decorators', []):
                                        owner = self.r.classes.get(field_atom.receiver_class)
                                        if owner:
                                            bound = one('reference', owner['id'])
                                    bindings.append((fn, bound))
                                elif field_atom.kind == 'reference' and field_atom.name in self.r.by_id:
                                    fn = self.r.by_id[field_atom.name]
                                    targets.append(fn); bindings.append((fn, None))
                                elif field_atom.kind == 'object' and not field_atom.site.startswith('hint:'):
                                    field_cls = self.r.classes.get(field_atom.name)
                                    methods, _ = self.r._find_method(field_cls, '__call__') if field_cls else ([], [])
                                    for fn in methods:
                                        targets.append(fn)
                                        bindings.append((fn, one('object', field_atom.name, field_atom.site)))
                            continue
                        found, _ = self.r._find_method(cls, node.func.attr) if cls else ([], [])
                        if self.namespace_shadow(cls, node.func.attr, heap):
                            managed = True
                            tags.add('field_shadow')
                            continue
                        if not found and not self.namespace_shadow(cls, node.func.attr, heap):
                            builtin = self.builtin_base(cls, node.func.attr, node)
                            if builtin:
                                external.append((builtin + '.' + node.func.attr, 'external_bound'))
                            for base_atom in self.external_base(cls, node, inherited_member=node.func.attr).atoms:
                                if base_atom.kind == 'external_reference':
                                    external.append((base_atom.name + '.' + node.func.attr, 'external_bound'))
                        nested = [s for s in self.r._lookup(atom.name + '.' + node.func.attr) if s['kind'] == 'CLASS']
                        for nested_class in nested:
                            targets.append(nested_class); bindings.append((nested_class, None))
                        for fn in found:
                            targets.append(fn)
                            bound = (one('reference', cls['id']) if 'classmethod' in fn.get('decorators', [])
                                     else one('object', atom.name, atom.site))
                            bindings.append((fn, bound))
                    elif atom.kind == 'reference':
                        cls = self.r.by_id.get(atom.name)
                        if cls and cls['kind'] == 'CLASS':
                            if self.namespace_shadow(cls, node.func.attr, heap):
                                managed = True
                                tags.add('field_shadow')
                            else:
                                methods, _ = self.r._find_method(cls, node.func.attr)
                                for fn in methods:
                                    targets.append(fn)
                                    bound = one('reference', cls['id']) if 'classmethod' in fn.get('decorators', []) else None
                                    bindings.append((fn, bound))
                if not targets and not managed:
                    refs = self.refs(ast.unparse(node.func), context, node)
                    for ref in refs:
                        targets.append(ref); bindings.append((ref, None))
        else:
            function = self.expr(node.func, env, heap, context, mode, site)
            tags.update(function.tags)
            managed |= bool(function.tags & {'binding', 'indexed', 'context', 'lambda'})
            managed |= (isinstance(node.func, ast.Name) and node.func.id in env
                        and any(a.kind == 'reference' for a in function.atoms))
            for atom in function.atoms:
                if atom.kind in ('external_reference', 'external_bound'):
                    external.append((atom.name, atom.kind))
                if atom.kind == 'reference' and atom.name in self.r.by_id:
                    fn = self.r.by_id[atom.name]
                    targets.append(fn); bindings.append((fn, None))
                elif atom.kind == 'bound_method' and atom.name in self.r.by_id:
                    fn = self.r.by_id[atom.name]
                    bound = one('object', atom.receiver_class, atom.site)
                    if 'classmethod' in fn.get('decorators', []):
                        owner = self.r.classes.get(atom.receiver_class)
                        if owner:
                            bound = one('reference', owner['id'])
                    targets.append(fn); bindings.append((fn, bound))
                    managed = True  # A bound method is a tracked callable value.
                    tags.add('bound_method_alias')
                elif atom.kind == 'object' and not atom.site.startswith('hint:'):
                    # Python uses type(obj).__call__ for obj(); instance
                    # attributes named __call__ cannot override that lookup.
                    cls = self.r.classes.get(atom.name)
                    methods, _ = self.r._find_method(cls, '__call__') if cls else ([], [])
                    for fn in methods:
                        targets.append(fn)
                        bindings.append((fn, one('object', atom.name, atom.site)))
                    managed = True
                    tags.add('callable_instance')
        if any(not self.r.safe_callable(fn) for fn in targets):
            managed = True
            tags.add('decorated_or_mutated_binding_unknown')
            targets = [fn for fn in targets if self.r.safe_callable(fn)]
            bindings = [(fn, bound) for fn, bound in bindings if self.r.safe_callable(fn)]
        args = [self.expr(a, env, heap, context, mode, site) for a in node.args]
        kwargs = {k.arg: self.expr(k.value, env, heap, context, mode, site) for k in node.keywords}
        if (not targets and args and isinstance(node.func, ast.Name)
                and node.func.id in _PROTOCOL_BUILTINS and node.func.id not in env):
            self.implicit(node, context, mode, 'protocol_builtin', _PROTOCOL_BUILTINS[node.func.id], args[0])
        self.note_builtin_callback(node, args, kwargs, context)
        if isinstance(node.func, ast.Name) and node.func.id in ('setattr', 'delattr') and len(args) >= 2:
            key = node.args[1]
            attribute = key.value if isinstance(key, ast.Constant) and isinstance(key.value, str) else '*'
            self.namespace_write(args[0], attribute, heap)
        if receiver:
            for atom in receiver.atoms:
                if atom.kind == 'object':
                    tags.update(('receiver:' + atom.name, 'allocation:' + atom.site))
                else:
                    tags.add('receiver_alternative:' + atom.kind)
        self.note(node, context, mode, targets, managed, tags)
        external_text = ast.unparse(node.func)
        external_root = external_text.split('.')[0]
        root_import = None
        if external_root.isidentifier():
            root_import, _, _ = self.call_origins._bound_at(
                {**context, 'line': node.lineno, 'column': node.col_offset}, external_root)
        if self.assigned_name(context['file'], external_text, 'dotted' if root_import else None):
            external = []
        if external:
            call = self.call_index.get((context['file'], node.lineno, node.col_offset, node.end_lineno, node.end_col_offset))
            if call:
                self.external_events.setdefault((call['id'], mode), set()).update(external)
        callsite = f"{context['file']}:{node.lineno}:{node.col_offset}"
        child_site = callsite + ' > ' + site
        child_mode = 'generic' if mode == 'generic' else 'specific'
        if any(isinstance(arg, ast.Starred) for arg in node.args) or None in kwargs:
            self.invalidate_escaped([*args, *kwargs.values(), receiver or BOTTOM], heap)
            return UNKNOWN.tagged('return')
        result = BOTTOM
        start_heap = dict(heap)
        outcomes = []
        for fn, bound in bindings:
            branch_heap = dict(start_heap)
            if fn['kind'] == 'CLASS':
                custom_new, _ = self.r._find_method(fn, '__new__')
                if self.r._mro(fn)[1].startswith('invalid') or custom_new:
                    value = UNKNOWN.tagged('return')
                else:
                    allocation = child_site
                    value = one('object', fn['canonical_name'], allocation)
                    if self.fields:
                        initializers, _ = self.r._find_method(fn, '__init__')
                        for init in initializers:
                            self.invoke(init, [value, *args], kwargs, branch_heap, child_mode, child_site)
            else:
                actual = args
                if bound is not None and 'staticmethod' not in fn.get('decorators', []):
                    actual = [bound, *args]
                value = self.invoke(fn, actual, kwargs, branch_heap, child_mode, child_site)
            result = result.join(value)
            outcomes.append(branch_heap)
        if outcomes:
            merged = outcomes[0]
            for branch in outcomes[1:]:
                merged = merge_maps(merged, branch)
            heap.clear(); heap.update(merged)
        else:
            result = UNKNOWN.tagged('return')
            if self.fields:
                # Unknown callees may mutate escaping objects. Invalidate
                # reachable fields instead of keeping stale strong updates.
                self.invalidate_escaped([*args, *kwargs.values(), receiver or BOTTOM], heap)
        for name, kind in external:
            if kind == 'external_reference':
                from external_returns import NON_CONSTRUCTOR_RETURN_APIS
                if name in NON_CONSTRUCTOR_RETURN_APIS:
                    continue
                result = result.join(one('external_result', name).tagged('return'))
        return result

    def note_builtin_callback(self, node, args, kwargs, context):
        """sorted's key is a conditional callback, distinct from calling sorted."""
        if (not isinstance(node.func, ast.Name) or node.func.id!='sorted' or len(node.args)!=1
                or isinstance(node.args[0], ast.Starred) or set(kwargs)-{'key','reverse'} or 'key' not in kwargs):
            return
        iterable = node.args[0]
        if (isinstance(iterable,(ast.List,ast.Tuple,ast.Set)) and not iterable.elts
                or isinstance(iterable,ast.Dict) and not iterable.keys
                or isinstance(iterable,ast.Constant) and (not isinstance(iterable.value,(str,bytes)) or not iterable.value)):
            return
        call = self.call_index.get((context['file'],node.lineno,node.col_offset,node.end_lineno,node.end_col_offset))
        if not call or self.call_origins.non_internal_origin(call)[0]!='BUILTIN':
            return
        targets = self.callback_events.setdefault(call['id'],set())
        for atom in kwargs['key'].atoms:
            fn = self.r.by_id.get(atom.name) if atom.kind in ('reference','bound_method') else None
            if fn and fn['kind'] in ('FUNCTION','METHOD','LAMBDA') and self.r.safe_callable(fn):
                targets.add(fn['id'])

    @staticmethod
    def invalidate_escaped(values, heap):
        # Find the whole reachable graph BEFORE discarding container entries.
        escaped = {a.site for v in values for a in v.atoms if a.kind in ('object', 'container')}
        if not escaped:
            return  # Nothing reachable escapes: the heap is unchanged.
        while True:
            more = {a.site for (obj, _), v in heap.items() if obj in escaped
                    for a in v.atoms if a.kind in ('object', 'container')} - escaped
            if not more:
                break
            escaped.update(more)
        for key in heap:
            if key[0] in escaped:
                heap[key] = UNKNOWN.tagged('field')

    @staticmethod
    def invalidate_containers(value, heap):
        for atom in value.atoms:
            if atom.kind != 'container':
                continue
            for key in list(heap):
                if key[0] == atom.site:
                    heap[key] = UNKNOWN
            heap[(atom.site, '*')] = UNKNOWN

    def assign(self, target, value, env, heap, context, mode, site, record=True):
        if isinstance(target, ast.Name):
            env[target.id] = value
        elif isinstance(target, ast.Subscript):
            container = self.expr(target.value, env, heap, context, mode, site)
            self.expr(target.slice, env, heap, context, mode, site)
            if record:
                self.implicit(target, context, mode, 'subscript', '__setitem__', container)
            self.invalidate_containers(container, heap)
        elif isinstance(target, ast.Attribute) and self.fields:
            receiver = self.expr(target.value, env, heap, context, mode, site)
            if record:
                self.implicit(target, context, mode, 'property_set', target.attr, receiver, want_property=True)
            self.namespace_write(receiver, target.attr, heap)
            objects = [a for a in receiver.atoms if a.kind == 'object']
            strong = len(receiver.atoms) == 1 and len(objects) == 1 and not objects[0].site.startswith('hint:')
            for obj in objects:
                key = (obj.site, target.attr)
                heap[key] = value if strong else heap.get(key, UNKNOWN).join(value)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for part in target.elts:
                self.assign(part, UNKNOWN, env, heap, context, mode, site, record)

    def context_protocol(self, receiver, name, args, heap, mode, site):
        """Interpret a source-backed special method without inventing a call site.

        Python looks these methods up on the type, not instance attributes.
        Use the same allocation identity and ordinary return/heap interpreter.
        Unknown implementations cannot certify a yielded object or stale fields.
        """
        result, outcomes = BOTTOM, []
        for atom in receiver.atoms:
            branch = dict(heap)
            cls = self.r.classes.get(atom.name) if atom.kind == 'object' else None
            methods, _ = self.r._find_method(cls, name) if cls else ([], [])
            if len(methods) == 1:
                fn = methods[0]
                bound = one('object', atom.name, atom.site)
                if 'classmethod' in fn.get('decorators', []):
                    bound = one('reference', cls['id'])
                actual = args if 'staticmethod' in fn.get('decorators', []) else [bound, *args]
                value = self.invoke(fn, actual, {}, branch,
                                    'generic' if mode == 'generic' else 'specific', site)
            else:
                value = UNKNOWN
                # Match the ordinary unknown-call policy: only objects known
                # to escape to the manager may be mutated. An unrelated lock
                # is not evidence that every object in the heap was modified.
                escaped = {atom.site} if atom.kind == 'object' else set()
                while True:
                    more = {a.site for (obj, _), v in branch.items() if obj in escaped
                            for a in v.atoms if a.kind == 'object'} - escaped
                    if not more:
                        break
                    escaped.update(more)
                for key in branch:
                    if key[0] in escaped:
                        branch[key] = UNKNOWN.tagged('field')
            result = result.join(value)
            outcomes.append(branch)
        if outcomes:
            merged = outcomes[0]
            for branch in outcomes[1:]:
                merged = merge_maps(merged, branch)
            heap.clear(); heap.update(merged)
        return (result if result.atoms else UNKNOWN).tagged('return').tagged('context_manager')

    def block(self, body, env, heap, context, mode, site):
        returns = BOTTOM
        for stmt in body:
            if self.root_steps > self.root_step_limit:
                self.cutoffs += 1
                return env, returns.join(UNKNOWN), True
            if isinstance(stmt, ast.Return):
                value = self.expr(stmt.value, env, heap, context, mode, site)
                if self.return_heaps:
                    self.return_heaps[-1].append(dict(heap))
                return env, returns.join(value), False
            if isinstance(stmt, ast.Raise):
                # Calls in the exception expression execute before the raise.
                self.expr(stmt.exc, env, heap, context, mode, site)
                self.expr(stmt.cause, env, heap, context, mode, site)
                return env, returns, False
            if isinstance(stmt, (ast.Break, ast.Continue)):
                # End the current approximated loop iteration.  The enclosing
                # loop retains a path that leaves the loop afterwards.
                return env, returns, True
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                for alias in stmt.names:
                    name = alias.asname or (alias.name.split('.')[0] if isinstance(stmt, ast.Import) else alias.name)
                    if name in env:
                        env[name] = UNKNOWN
                continue
            if isinstance(stmt, ast.Pass):
                continue
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                value = self.expr(stmt.value, env, heap, context, mode, site)
                for target in stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]:
                    self.assign(target, value, env, heap, context, mode, site)
            elif isinstance(stmt, ast.Expr):
                self.expr(stmt.value, env, heap, context, mode, site)
            elif isinstance(stmt, ast.AugAssign):
                operand = self.expr(stmt.value, env, heap, context, mode, site)
                op = _BINARY.get(type(stmt.op))
                if op:
                    current = self.peek(stmt.target, env, heap, context, mode, site)
                    # x += y tries __iadd__, then __add__, then y.__radd__.
                    if not self.implicit(stmt, context, mode, 'operator', f'__i{op}__', current):
                        self.implicit(stmt, context, mode, 'operator', f'__{op}__', current)
                    if self.lacks_forward(current, (f'__i{op}__', f'__{op}__')):
                        self.implicit(stmt, context, mode, 'operator', f'__r{op}__', operand)
                self.assign(stmt.target, UNKNOWN, env, heap, context, mode, site)
            elif isinstance(stmt, ast.Delete):
                for target in stmt.targets:
                    if isinstance(target, ast.Subscript):
                        container = self.peek(target.value, env, heap, context, mode, site)
                        self.implicit(target, context, mode, 'subscript', '__delitem__', container)
                    elif isinstance(target, ast.Attribute):
                        owner = self.peek(target.value, env, heap, context, mode, site)
                        self.implicit(target, context, mode, 'property_delete', target.attr, owner,
                                      want_property=True)
                    self.assign(target, UNKNOWN, env, heap, context, mode, site, record=False)
            elif isinstance(stmt, ast.Assert):
                self.expr(stmt.test, env, heap, context, mode, site)
                self.expr(stmt.msg, env, heap, context, mode, site)
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                with_env, with_heap = dict(env), dict(heap)
                managers = []
                for item in stmt.items:
                    manager = self.expr(item.context_expr, with_env, with_heap, context, mode, site)
                    prefix = '__a' if isinstance(stmt, ast.AsyncWith) else '__'
                    self.implicit(item.context_expr, context, mode, 'context_manager', prefix + 'enter__', manager)
                    self.implicit(item.context_expr, context, mode, 'context_manager', prefix + 'exit__', manager)
                    managers.append(manager)
                    # Awaitable execution is not modeled: async with remains
                    # unknown rather than treating a coroutine as its result.
                    entered = (self.context_protocol(manager, '__enter__', [], with_heap, mode, site)
                               if isinstance(stmt, ast.With) else UNKNOWN.tagged('return'))
                    if item.optional_vars is not None:
                        self.assign(item.optional_vars, entered, with_env, with_heap, context, mode, site)
                return_start = len(self.return_heaps[-1]) if self.return_heaps else 0
                with_env, ret, live = self.block(stmt.body, with_env, with_heap, context, mode, site)
                if isinstance(stmt, ast.With):
                    # Apply exit effects on normal flow and captured returns.
                    exit_heaps = [with_heap]
                    if self.return_heaps:
                        exit_heaps += self.return_heaps[-1][return_start:]
                    for exit_heap in exit_heaps:
                        for manager in reversed(managers):
                            self.context_protocol(manager, '__exit__', [UNKNOWN] * 3,
                                                  exit_heap, mode, site)
                returns = returns.join(ret)
                # Failed entry cannot reach the next normal statement. Keep
                # the binding from the successful body, not a pre-with alias.
                env = with_env if live else env
                heap.clear(); heap.update(with_heap)
                if not live:
                    return env, returns, False
            elif isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
                if (isinstance(stmt, (ast.For, ast.AsyncFor))
                        and isinstance(stmt.iter, (ast.List, ast.Tuple, ast.Set))
                        and not stmt.iter.elts):
                    env, ret, live = self.block(stmt.orelse, env, heap, context, mode, site)
                    returns = returns.join(ret)
                    if not live:
                        return env, returns, False
                    continue
                if (isinstance(stmt, ast.While) and isinstance(stmt.test, ast.Constant)
                        and not stmt.test.value):
                    env, ret, live = self.block(stmt.orelse, env, heap, context, mode, site)
                    returns = returns.join(ret)
                    if not live:
                        return env, returns, False
                    continue
                # Zero-or-one iteration approximation, with explicit joining.
                # The zero path prevents a loop-body assignment from being
                # treated as guaranteed.  This is not a fixed-point solver.
                loop_env, loop_heap = dict(env), dict(heap)
                if isinstance(stmt, (ast.For, ast.AsyncFor)):
                    iterable = stmt.iter
                    element = UNKNOWN
                    collection = self.expr(iterable, env, heap, context, mode, site)
                    self.implicit(iterable, context, mode, 'iteration',
                                  '__aiter__' if isinstance(stmt, ast.AsyncFor) else '__iter__', collection)
                    unwrapped = frozenset(
                        Atom(atom.kind.removeprefix('collection_'), atom.name, atom.site, atom.receiver_class)
                        for atom in collection.atoms if atom.kind.startswith('collection_')
                        and atom.kind != 'collection_unknown')
                    # Arbitrary iterables may yield values we cannot model.
                    other = any(not atom.kind.startswith('collection_') or atom.kind == 'collection_unknown'
                                for atom in collection.atoms)
                    element = Value(unwrapped).join(UNKNOWN) if other else Value(unwrapped)
                    containers = [a for a in collection.atoms if a.kind == 'container']
                    if containers:
                        # Read the live heap, not the pre-mutation snapshot of
                        # literal elements retained for legacy collection flow.
                        element = BOTTOM
                        for atom in containers:
                            if atom.name == 'dict' or heap.get((atom.site, '*'), UNKNOWN).atoms:
                                element = element.join(UNKNOWN)
                            else:
                                for (allocation, key), value in heap.items():
                                    if allocation == atom.site and isinstance(key, tuple) and key[0] == 'index':
                                        element = element.join(value)
                        if any(a.kind != 'container' and not a.kind.startswith('collection_') for a in collection.atoms):
                            element = element.join(UNKNOWN)
                        element = element.tagged('binding')
                    if not element.atoms:
                        element = UNKNOWN.tagged('binding')
                    self.assign(stmt.target, element, loop_env, loop_heap, context, mode, site)
                else:
                    self.expr(stmt.test, env, heap, context, mode, site)
                loop_env, loop_ret, loop_live = self.block(stmt.body, loop_env, loop_heap, context, mode, site)
                returns = returns.join(loop_ret)
                # Loop else executes on normal exhaustion, not necessarily on
                # break.  An alternative zero-iteration path is always safe.
                else_env, else_heap = dict(env), dict(heap)
                else_env, else_ret, else_live = self.block(stmt.orelse, else_env, else_heap, context, mode, site)
                returns = returns.join(else_ret)
                alive = [(else_env, else_heap)] if else_live else []
                if loop_live:
                    alive.append((loop_env, loop_heap))
                if not alive:
                    return env, returns, False
                env, merged_heap = alive[0]
                for e, h in alive[1:]:
                    env = merge_maps(env, e)
                    merged_heap = merge_maps(merged_heap, h)
                heap.clear(); heap.update(merged_heap)
            elif isinstance(stmt, TRY_STATEMENTS):
                prior_returns = returns
                pending_heap_start = len(self.return_heaps[-1]) if self.return_heaps else 0
                entry_env, entry_heap = dict(env), dict(heap)
                normal_env, normal_heap = dict(env), dict(heap)
                normal_env, normal_ret, normal_live = self.block(stmt.body, normal_env, normal_heap, context, mode, site)
                returns = returns.join(normal_ret)
                alive = []
                if normal_live:
                    normal_env, else_ret, normal_live = self.block(stmt.orelse, normal_env, normal_heap, context, mode, site)
                    returns = returns.join(else_ret)
                    if normal_live:
                        alive.append((normal_env, normal_heap))
                # An exception may occur at any prefix of the try suite.
                # Join entry/finished states before starting each handler.
                for handler in stmt.handlers:
                    handler_env = merge_maps(entry_env, normal_env)
                    handler_heap = merge_maps(entry_heap, normal_heap)
                    self.expr(handler.type, handler_env, handler_heap, context, mode, site)
                    if handler.name:
                        handler_env[handler.name] = UNKNOWN
                    handler_env, ret, handler_live = self.block(handler.body, handler_env, handler_heap, context, mode, site)
                    returns = returns.join(ret)
                    if handler_live:
                        alive.append((handler_env, handler_heap))
                # Unhandled exceptions do not fall through to the next
                # statement in the current function.
                had_normal_exit = bool(alive)
                if stmt.finalbody:
                    # finally also executes during exception unwinding or a
                    # return.  Include that path without turning its effects
                    # into a guaranteed result on every normal path.
                    if not alive:
                        alive.append((merge_maps(entry_env, normal_env), merge_maps(entry_heap, normal_heap)))
                    finalized = []
                    final_returns = BOTTOM
                    final_return_heaps = []
                    for branch_env, branch_heap in alive:
                        start = len(self.return_heaps[-1]) if self.return_heaps else 0
                        branch_env, ret, live = self.block(stmt.finalbody, branch_env, branch_heap, context, mode, site)
                        returns = returns.join(ret)
                        final_returns = final_returns.join(ret)
                        if self.return_heaps:
                            final_return_heaps.extend(self.return_heaps[-1][start:])
                        if live:
                            finalized.append((branch_env, branch_heap))
                    # An explicit terminal return in finally replaces pending
                    # try/except returns, including their heap snapshots. Other
                    # completion patterns keep the existing conservative join.
                    if isinstance(stmt.finalbody[-1], ast.Return):
                        returns = prior_returns.join(final_returns)
                        if self.return_heaps:
                            del self.return_heaps[-1][pending_heap_start:]
                            self.return_heaps[-1].extend(final_return_heaps)
                    alive = finalized
                    if not had_normal_exit:
                        # finally runs even during return/unwinding, but it
                        # cannot make that pending return fall through.
                        return env, returns, False
                if not alive:
                    return env, returns, False
                env, merged_heap = alive[0]
                for e, h in alive[1:]:
                    env = merge_maps(env, e)
                    merged_heap = merge_maps(merged_heap, h)
                heap.clear(); heap.update(merged_heap)
            elif isinstance(stmt, ast.Match):
                subject = self.expr(stmt.subject, env, heap, context, mode, site)
                paths = []
                exhaustive = False
                all_heaps = []
                for case in stmt.cases:
                    branch_env, branch_heap = dict(env), dict(heap)
                    for pattern in ast.walk(case.pattern):
                        if isinstance(pattern, (ast.MatchAs, ast.MatchStar)) and pattern.name:
                            branch_env[pattern.name] = UNKNOWN.tagged('binding')
                        if isinstance(pattern, ast.MatchMapping) and pattern.rest:
                            branch_env[pattern.rest] = UNKNOWN.tagged('binding')
                    wildcard = isinstance(case.pattern, ast.MatchAs) and case.pattern.pattern is None
                    if wildcard and case.pattern.name:
                        branch_env[case.pattern.name] = subject.tagged('binding')
                    if case.guard is not None:
                        self.expr(case.guard, branch_env, branch_heap, context, mode, site)
                    branch_env, ret, live = self.block(case.body, branch_env, branch_heap, context, mode, site)
                    returns = returns.join(ret)
                    all_heaps.append(branch_heap)
                    if live:
                        paths.append((branch_env, branch_heap))
                    if wildcard and case.guard is None:
                        exhaustive = True
                        break
                if not exhaustive:
                    paths.append((dict(env), dict(heap)))
                if not paths:
                    for branch_heap in all_heaps:
                        heap.update(merge_maps(heap, branch_heap))
                    return env, returns, False
                env, merged_heap = paths[0]
                for branch_env, branch_heap in paths[1:]:
                    env = merge_maps(env, branch_env)
                    merged_heap = merge_maps(merged_heap, branch_heap)
                heap.clear(); heap.update(merged_heap)
            elif isinstance(stmt, ast.If):
                self.expr(stmt.test, env, heap, context, mode, site)
                branches = [stmt.body, stmt.orelse]
                if isinstance(stmt.test, ast.Constant):
                    branches = [stmt.body if stmt.test.value else stmt.orelse]
                live_envs, live_heaps, all_heaps = [], [], []
                for branch in branches:
                    branch_heap = dict(heap)
                    branch_env, ret, live = self.block(branch, dict(env), branch_heap, context, mode, site)
                    returns = returns.join(ret)
                    all_heaps.append(branch_heap)
                    if live:
                        live_envs.append(branch_env); live_heaps.append(branch_heap)
                if not live_envs:
                    merged_heap = all_heaps[0]
                    for branch_heap in all_heaps[1:]:
                        merged_heap = merge_maps(merged_heap, branch_heap)
                    heap.clear(); heap.update(merged_heap)
                    return env, returns, False
                env = live_envs[0]
                merged_heap = live_heaps[0]
                for branch_env, branch_heap in zip(live_envs[1:], live_heaps[1:]):
                    env = merge_maps(env, branch_env); merged_heap = merge_maps(merged_heap, branch_heap)
                heap.clear(); heap.update(merged_heap)
            else:
                # Unsupported compound statements: retain uncertainty, never
                # treat a syntactic return inside them as unconditional.
                for n in ast.walk(stmt):
                    if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                        env[n.id] = UNKNOWN
                    if isinstance(n, ast.Attribute) and isinstance(n.ctx, (ast.Store, ast.Del)):
                        for key in heap:
                            if key[1] == n.attr:
                                heap[key] = UNKNOWN.tagged('field')
                returns = returns.join(UNKNOWN)
        return env, returns, True

    def run(self):
        if self.fields:
            # Library methods are analyzed in a hypothetical owner-instance
            # context. Observed call contexts below take precedence over this
            # fallback; no subclass is invented merely from class membership.
            for symbol in self.r.symbols:
                if symbol['kind'] != 'METHOD' or symbol['name'] == '__init__':
                    continue
                cls = self.r.classes.get(symbol['module'] + '.' + symbol['scope'])
                if not cls or symbol.get('decorators') not in ([], ['classmethod']):
                    continue
                if symbol.get('decorators') == ['classmethod']:
                    self.invoke(symbol, [one('reference', cls['id'])], {}, {}, 'generic', 'root:' + symbol['id'], awaited=True)
                    continue
                obj = one('object', cls['canonical_name'], 'root:' + symbol['id'])
                heap = {}
                inits, _ = self.r._find_method(cls, '__init__')
                for init in inits:
                    self.invoke(init, [obj], {}, heap, 'generic', 'root:' + symbol['id'])
                self.invoke(symbol, [obj], {}, heap, 'generic', 'root:' + symbol['id'], awaited=True)
        for symbol in self.r.symbols:
            if symbol['kind'] == 'FUNCTION' and symbol['id'] in self.functions:
                self.invoke(symbol, [], {}, {}, 'root', 'root:' + symbol['id'], awaited=True)
        # Module-level statements are real entry points too.
        for file, tree in self.modules.items():
            self.root_steps = 0
            module = next((s['module'] for s in self.r.symbols if s['file'] == file), '')
            self.block(tree.body, {}, {}, {'file': file, 'module': module, 'scope': '<module>'}, 'root', 'module:' + file)
        result = {}
        for call in self.r.calls:
            owner = call.get('caller_id')
            modes = ['specific'] if owner in self.specific else ['root', 'generic']
            chosen = [self.events[(call['id'], mode)] for mode in modes if (call['id'], mode) in self.events]
            # A caller-specific observation that only contains an unknown
            # receiver is not a proof that generic owner contexts are
            # impossible. Preserve those generic *may*-call candidates.
            # However a definite instance-field shadow is authoritative and
            # must not be overridden by a generic method fallback.
            if (modes == ['specific'] and chosen and not any(e['targets'] for e in chosen)
                    and not any('field_shadow' in e['trace'] for e in chosen)):
                fallback = [self.events[(call['id'], m)] for m in ('root', 'generic')
                            if (call['id'], m) in self.events
                            and self.events[(call['id'], m)]['targets']]
                if fallback:
                    chosen = fallback
                    self.generic_context_fallbacks += 1
            if any(e['managed'] for e in chosen):
                ids = set().union(*(e['targets'] for e in chosen))
                trace = sorted(set().union(*(e['trace'] for e in chosen)))
                result[call['id']] = ([self.r.by_id[i] for i in sorted(ids)], 'object_flow_may_call', trace)
        # Implicit calls follow the same context policy as explicit ones: an
        # observed caller-specific context wins, else root/generic contexts.
        for (file, line, column, kind, name), event in sorted(self.implicit_events.items(), key=lambda kv: kv[0]):
            modes = ['specific'] if event['owner'] in self.specific else ['root', 'generic']
            chosen = [event['modes'][m] for m in modes if m in event['modes']]
            if not chosen:
                chosen = [event['modes'][m] for m in ('root', 'generic') if m in event['modes']]
            if not chosen:
                continue
            self.implicit_results.append({
                'file': file, 'line': line, 'column': column,
                'end_line': event['end'][0], 'end_column': event['end'][1],
                'kind': kind, 'member': name, 'owner': event['owner'], 'module': event['module'],
                'targets': sorted(set().union(*(c['targets'] for c in chosen))),
                'receivers': sorted(set().union(*(c['receivers'] for c in chosen)))})
        return result
