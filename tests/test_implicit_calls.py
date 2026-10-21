"""CALLS_IMPLICIT: methods Python runs without a call expression.

Ported from the LJY prototype's calls_implicit. Kept in implicit_edges,
apart from resolved call-site edges and statuses.
"""
import tempfile
import textwrap
import unittest
from pathlib import Path

from ast_parser import scan_repository
from symbol_builder import build_symbol_result
from graph_builder import build_graph
from unresolved_classifier import reclassify_graph
from call_status import annotate_call_statuses
from verify_v23 import verify
from verify_v231 import check_status

VECTOR = '''
class Vector:
    def __init__(self, x):
        self._x = x
    @property
    def x(self):
        return self._x
    @x.setter
    def x(self, value):
        self._x = value
    def __add__(self, other): return Vector(0)
    def __iadd__(self, other): return self
    def __eq__(self, other): return True
    def __neg__(self): return self
    def __contains__(self, item): return True
    def __getitem__(self, key): return 0
    def __setitem__(self, key, value): pass
    def __delitem__(self, key): pass
    def __len__(self): return 1
    def __str__(self): return 'v'
    def __iter__(self): return iter([])
    def __enter__(self): return self
    def __exit__(self, *exc): return False

class Number:
    def __radd__(self, other): return 0
'''


class ImplicitCallTests(unittest.TestCase):
    def analyze(self, source):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'repo'
            root.mkdir()
            (root / 'app.py').write_text(textwrap.dedent(source), encoding='utf-8')
            facts = scan_repository(root)
            self.assertEqual(facts['parse_errors'], [])
            data = build_symbol_result(facts)
        graph = annotate_call_statuses(reclassify_graph(build_graph(data), data), data)
        self.assertEqual(verify(graph, data), 0)
        self.assertEqual(check_status(graph, data), 0)
        return graph

    def implicit(self, graph, caller):
        return {(e['evidence']['implicit_kind'], e['target'].split(':', 1)[1].removeprefix('app.'))
                for e in graph['implicit_edges'] if e['source'] == 'FUNCTION:app.' + caller}

    def test_each_implicit_kind(self):
        graph = self.analyze(VECTOR + '''
def use():
    v = Vector(1)
    w = Vector(2)
    a = v.x
    v.x = 3
    b = v + w
    u = Vector(3)
    u += w  # u is unknown afterwards: __iadd__ may return anything
    c = v == w
    d = 1 in v
    e = -v
    f = v[0]
    v[0] = 1
    del v[0]
    n = len(v)
    s = str(v)
    for item in v:
        pass
    with v:
        pass
    g = 1 + Number()
''')
        self.assertEqual(self.implicit(graph, 'use'), {
            ('property_get', 'Vector.x'), ('property_set', 'Vector.x'),
            ('operator', 'Vector.__add__'), ('operator', 'Vector.__iadd__'),
            ('operator', 'Vector.__eq__'), ('operator', 'Vector.__contains__'),
            ('operator', 'Vector.__neg__'), ('operator', 'Number.__radd__'),
            ('subscript', 'Vector.__getitem__'), ('subscript', 'Vector.__setitem__'),
            ('subscript', 'Vector.__delitem__'),
            ('protocol_builtin', 'Vector.__len__'), ('protocol_builtin', 'Vector.__str__'),
            ('iteration', 'Vector.__iter__'),
            ('context_manager', 'Vector.__enter__'), ('context_manager', 'Vector.__exit__'),
        })

    def test_unknown_or_external_receivers_get_nothing(self):
        graph = self.analyze(VECTOR + '''
class Table(dict):
    pass

def len(x):
    return 0

def use(anything):
    t = Table()
    a = t['k']
    b = anything + anything
    with anything:
        pass
    v = Vector(1)
    n = len(v)
''')
        found = self.implicit(graph, 'use')
        # dict provides __getitem__ first; parameter types are unknown;
        # the project's own len() is an explicit call, not the protocol.
        self.assertEqual(found, set())

    def test_parameter_hint_and_self(self):
        graph = self.analyze(VECTOR + '''
def size(v: Vector):
    return len(v)
''')
        self.assertEqual(self.implicit(graph, 'size'), {('protocol_builtin', 'Vector.__len__')})

    def test_kept_apart_from_resolved_edges(self):
        graph = self.analyze(VECTOR + '''
def use():
    v = Vector(1)
    return v.x
''')
        self.assertTrue(graph['implicit_edges'])
        self.assertNotIn('CALLS_IMPLICIT', {e['type'] for e in graph['edges']})
        edge = graph['implicit_edges'][0]
        self.assertEqual(edge['evidence']['source'], 'v.x')
        self.assertEqual(edge['evidence']['receiver_classes'], ['app.Vector'])
        self.assertEqual(graph['implicit_summary']['kinds'], {'property_get': 1})


if __name__ == '__main__':
    unittest.main()
