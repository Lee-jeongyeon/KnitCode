"""Static cross-boundary call candidates, including unavailable dependencies.

Import paths are symbolic API names, not proof of a callable implementation.
Keep these edges separate from the internal graph and its status accounting.
"""
from __future__ import annotations

import ast
from collections import Counter, defaultdict
from call_status import StatusAnnotator
from import_resolution import canonical_import
from symbol_builder import save_json
from external_returns import NON_CONSTRUCTOR_RETURN_APIS


def build_external_call_graph(symbols, graph):
    status = StatusAnnotator(symbols)
    calls = {c['id']: c for c in symbols.get('calls', [])}
    nodes, edges = {}, []
    assigned = defaultdict(set)   # file -> assigned dotted names, indexed once
    for file, _, target in status.class_assignments:
        assigned[file].add(target)

    def assigned_name(file, name, prefix):
        """file assigns name, or (prefix='any'/'dotted') a (dotted) prefix of it."""
        return any(name == target or prefix and (prefix == 'any' or '.' in target)
                   and name.startswith(target + '.') for target in assigned.get(file, ()))

    def add(call, name, rule, certainty, origin):
        target = 'EXTERNAL:' + name
        nodes[target] = dict(id=target, canonical_name=name, kind='EXTERNAL_CALLABLE', origin_kind=origin)
        edges.append(dict(source=call.get('caller_id') or 'MODULE:' + call['module'], target=target,
                          type='CALLS_EXTERNAL', evidence=dict(call_id=call['id'], file=call['file'],
                          line=call['line'], column=call.get('column'), source=call.get('source'),
                          rule=rule, certainty=certainty)))

    def imported(call, name):
        head, dot, rest = name.partition('.')
        imp, conflicts, _ = status._bound_at(call, head)
        if not imp or conflicts:
            return None
        canonical = canonical_import(imp)
        if not canonical:
            return None
        origins = status.origins.trace(canonical)
        if len(origins) != 1 or origins[0]['kind'] not in ('external', 'stdlib'):
            return None
        full = origins[0]['name'] + ('.' + rest if dot else '')
        # Assignment to an imported namespace invalidates the original API.
        if assigned_name(call['file'], name, 'any'):
            return None
        return full, origins[0]['kind'].upper()

    for row in graph.get('call_statuses', []):
        call = calls[row['call_id']]
        if row['status'] == 'RESOLVED_INTERNAL':
            continue
        call_text = call.get('callee_text') or ''
        call_root = call_text.split('.')[0]
        root_import = status._bound_at(call, call_root)[0] if call_root.isidentifier() else None
        if assigned_name(call['file'], call_text, 'dotted' if root_import else None):
            continue
        evidence = row.get('evidence', {})
        name = evidence.get('origin_name', '')
        if (row['status'] == 'KNOWN_NON_INTERNAL' and name and ', ' not in name and ' or ' not in name
                and all(part.isidentifier() for part in name.split('.'))):
            add(call, name, evidence['rule'], 'SYMBOLIC_ORIGIN', row['origin_kind'])
            continue
        callee = call.get('callee_text') or ''
        # Import aliases and statically constructed receivers are tied to the
        # reaching definition at this site; never guess from a method name.
        flow = call.get('flow') or {}
        binding = flow.get('binding') or {}
        head, dot, method = callee.partition('.')
        if (dot and method.isidentifier() and flow.get('variable') == head
                and not binding.get('may_be_unbound')
                and not any(file == call['file'] and target == callee
                            for file, _, target in status.class_assignments)):
            options = binding.get('options') or []
            if options and all(o.get('kind') == 'constructed' for o in options):
                for option in options:
                    origin = imported(call, option.get('name', ''))
                    if origin and origin[0] not in NON_CONSTRUCTOR_RETURN_APIS:
                        add(call, origin[0] + '.' + method, 'IMPORTED_CONSTRUCTOR_RESULT',
                            'SYMBOLIC_RECEIVER_CANDIDATE', origin[1])
    # map is lazy: record its callback only for a recognized eager consumer,
    # rather than treating construction of an unused iterator as execution.
    locations = {(c['file'], c['line'], c.get('column')): c for c in calls.values()}
    for row in graph.get('call_statuses', []):
        if row.get('status') != 'KNOWN_NON_INTERNAL':
            continue
        origin = row.get('evidence', {}).get('origin_name')
        if origin not in {'builtins.list', 'builtins.tuple', 'builtins.set', 'builtins.dict',
                          'builtins.sorted', 'builtins.sum', 'builtins.any', 'builtins.all',
                          'str.join', 'bytes.join'}:
            continue
        outer = calls[row['call_id']]
        try:
            tree = ast.parse(outer.get('source') or '', mode='eval').body
        except (SyntaxError, ValueError):
            continue
        if not isinstance(tree, ast.Call) or len(tree.args) != 1:
            continue
        mapping = tree.args[0]
        if (not isinstance(mapping, ast.Call) or not isinstance(mapping.func, ast.Name)
                or mapping.func.id != 'map' or len(mapping.args) != 2 or mapping.keywords):
            continue
        iterable = mapping.args[1]
        if ((isinstance(iterable, (ast.List, ast.Tuple, ast.Set)) and not iterable.elts)
                or (isinstance(iterable, ast.Dict) and not iterable.keys)
                or (isinstance(iterable, ast.Constant) and iterable.value in ('', b''))):
            continue
        callback = mapping.args[0]
        if not isinstance(callback, (ast.Name, ast.Attribute)):
            continue
        # Coordinates in parsed call snippets are relative to the outer call.
        line = outer['line'] + mapping.lineno - 1
        column = mapping.col_offset + (outer.get('column', 0) if mapping.lineno == 1 else 0)
        trigger = locations.get((outer['file'], line, column))
        if not trigger or status.non_internal_origin(trigger)[2].get('origin_name') != 'builtins.map':
            continue
        category, _, provenance = status.non_internal_origin({**trigger, 'callee_text': ast.unparse(callback)})
        name = provenance.get('origin_name', '')
        if category and name and all(part.isidentifier() for part in name.split('.')):
            add(trigger, name, 'EAGER_MAP_CONSUMER_CALLBACK', 'CALLBACK_MAY_EXECUTE', category)
    for event in graph.get('external_flow_calls', []):
        call = calls[event['call_id']]
        if event['value_kind'] == 'external_reference':
            certainty = 'SYMBOLIC_ORIGIN'
        else:
            certainty = 'SYMBOLIC_RECEIVER_CANDIDATE'
        add(call, event['canonical_name'], 'INTERPROCEDURAL_IMPORT_FLOW', certainty, 'IMPORTED_API')
    unique = {(e['source'], e['target'], e['evidence']['call_id']): e for e in edges}
    edges = list(unique.values())
    return dict(policy='Static symbolic API candidates; dependency code is never imported or executed. '
                'Constructor-result paths do not certify return types, member existence or defining modules.',
                nodes=list(nodes.values()), edges=edges,
                summary=dict(edges=len(edges), targets=len(nodes), rules=dict(Counter(e['evidence']['rule'] for e in edges))))


def write_external_call_graph(symbols, graph, out):
    result = build_external_call_graph(symbols, graph)
    save_json(result, out / 'external_call_graph.json')
    return result
