"""General Python semantics behind failures found in the pinned Flask source."""
import contextlib
import io
from pathlib import Path
import tempfile
import textwrap
import unittest

from ast_parser import scan_repository
from symbol_builder import build_symbol_result
from graph_builder import build_graph
from unresolved_classifier import reclassify_graph
from call_status import annotate_call_statuses
from verify_v23 import verify_source


BASE = 'class A:\n    def run(self): pass\n    def other(self): pass\n'


class FlaskRegressions(unittest.TestCase):
    def graph(self, source, extra=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'project'
            root.mkdir()
            for name, code in {'app.py': textwrap.dedent(source), **(extra or {})}.items():
                (root/name).write_text(code)
            facts = scan_repository(root)
            self.assertEqual(facts['parse_errors'], [])
            symbols = build_symbol_result(facts)
            return annotate_call_statuses(reclassify_graph(build_graph(symbols), symbols), symbols)

    def expect(self, source, callee, targets, certainty=None, extra=None):
        graph = self.graph(source, extra)
        rows = [r for r in graph['call_statuses'] if r['callee_text']==callee]
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]['target_ids']), set(targets))
        if certainty:
            self.assertEqual(rows[0]['certainty'], certainty)
        return rows[0]

    def test_imported_function_does_not_resolve_to_same_named_method(self):
        self.expect('''
            from helpers import dumps
            class Serializer:
                def dumps(self, value): return dumps(value)
        ''', 'dumps', ['FUNCTION:helpers.dumps'], extra={'helpers.py': 'def dumps(value): pass\n'})

    def test_global_function_does_not_resolve_to_same_named_method(self):
        self.expect('''
            def loads(value): pass
            class Serializer:
                def loads(self, value): return loads(value)
        ''', 'loads', ['FUNCTION:app.loads'])

    def test_missing_global_is_not_recursive_method(self):
        self.expect('''
            class Serializer:
                def dumps(self, value): return dumps(value)
        ''', 'dumps', [])

    def test_builtin_does_not_resolve_to_class_method(self):
        row = self.expect('''
            class Box:
                def len(self): return len([])
        ''', 'len', [])
        self.assertEqual(row['status'], 'KNOWN_NON_INTERNAL')

    def test_enclosing_function_is_visible_through_class(self):
        self.expect('''
            def outer():
                def ping(): pass
                class Box:
                    def ping(self): pass
                    def go(self): ping()
        ''', 'ping', ['FUNCTION:app.outer.ping'])

    def test_nested_class_body_does_not_see_outer_class_namespace(self):
        self.expect('''
            class Outer:
                def ping(): pass
                class Inner:
                    value = ping()
        ''', 'ping', [])

    def test_method_default_sees_current_class_body(self):
        self.expect('''
            class Box:
                def ping(): pass
                def go(self, value=ping()): pass
        ''', 'ping', ['METHOD:app.Box.ping'])

    def test_closure_factory_beats_module_factory(self):
        self.expect('''
            class A:
                def run(self): pass
            class B:
                def run(self): pass
            def make(): return A()
            def go():
                def make(): return B()
                obj=make(); obj.run()
        ''', 'obj.run', ['METHOD:app.B.run'])

    def test_conditional_patch_retains_possible_original_as_may_call(self):
        self.expect(BASE + 'def go(flag):\n    if flag: A.run=None\n    obj=A(); obj.run()\n',
                    'obj.run', ['METHOD:app.A.run'], 'MAY_CALL')

    def test_definitely_patched_branch_does_not_keep_original(self):
        self.expect(BASE + 'def go(flag):\n    if flag:\n        A.run=None\n'
                    '        obj=A(); obj.run()\n', 'obj.run', [])

    def test_definitely_setattr_branch_does_not_keep_original(self):
        self.expect(BASE + "def go(flag):\n    if flag:\n        setattr(A,'run',None)\n"
                    '        obj=A(); obj.run()\n', 'obj.run', [])

    def test_saved_bound_method_survives_conditional_class_patch(self):
        self.expect(BASE + 'def go(flag):\n    obj=A(); fn=obj.run\n'
                    '    if flag: A.run=None\n    fn()\n', 'fn', ['METHOD:app.A.run'], 'MAY_CALL')

    def test_true_patch_is_not_optional_and_false_patch_is_unreachable(self):
        for flag, targets in [('True', []), ('False', ['METHOD:app.A.run'])]:
            with self.subTest(flag=flag):
                self.expect(BASE + f'def go():\n    if {flag}: A.run=None\n'
                            '    obj=A(); obj.run()\n', 'obj.run', targets)

    def test_constant_short_circuit_patch(self):
        for prefix, targets in [('True and', []), ('False and', ['METHOD:app.A.run']),
                                ('False or', []), ('True or', ['METHOD:app.A.run'])]:
            with self.subTest(prefix=prefix):
                self.expect(BASE + f'def go():\n    {prefix} setattr(A,"run",None)\n'
                            '    obj=A(); obj.run()\n', 'obj.run', targets)

    def test_empty_and_nonempty_literal_loop_patch(self):
        for values, targets in [('[]', ['METHOD:app.A.run']), ('[1]', [])]:
            with self.subTest(values=values):
                self.expect(BASE + f'def go():\n    for item in {values}: A.run=None\n'
                            '    obj=A(); obj.run()\n', 'obj.run', targets)

    def test_unknown_loop_patch_is_possible(self):
        self.expect(BASE + 'def go(items):\n    for item in items: A.run=None\n'
                    '    obj=A(); obj.run()\n', 'obj.run', ['METHOD:app.A.run'], 'MAY_CALL')

    def test_unconditional_patch_wins_over_conditional(self):
        self.expect(BASE + 'def go(flag):\n    if flag: A.run=None\n'
                    '    A.run=None\n    obj=A(); obj.run()\n', 'obj.run', [])

    def test_conditional_method_name_does_not_block_other_method(self):
        self.expect(BASE + 'def go(flag):\n    if flag: A.run=None\n'
                    '    obj=A(); obj.other()\n', 'obj.other', ['METHOD:app.A.other'], 'SOURCE_BACKED')

    def test_class_alias_lookup_after_patch_is_not_a_saved_descriptor(self):
        self.expect(BASE + 'def go(flag):\n    if flag:\n        A.run=None\n'
                    '        fn=A.run; fn(A())\n', 'fn', [])

    def test_source_verifier_accepts_lambda_and_encoding_cookie(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'project'; root.mkdir()
            (root/'app.py').write_bytes(b'# coding: latin-1\n# caf\xe9\ndef go():\n    return lambda: go()\n')
            symbols=build_symbol_result(scan_repository(root))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(verify_source(symbols, root), 0)

    def test_classmethod_cls_is_class_not_first_argument(self):
        self.expect('''
            class App:
                def __call__(self, *args, **kwargs): pass
            class Context:
                def __init__(self, app): self.app=app
                @classmethod
                def make(cls, app): return cls(app)
            def go(): Context.make(App())
        ''', 'cls', ['CLASS:app.Context', 'METHOD:app.Context.__init__'])

    def test_inherited_classmethod_preserves_subclass(self):
        self.expect('''
            class Base:
                @classmethod
                def make(cls): return cls()
            class Child(Base): pass
            def go(): Child.make()
        ''', 'cls', ['CLASS:app.Child'])

    def test_classmethod_inside_generic_library_method_binds_class(self):
        self.expect('''
            class App:
                def __call__(self, *args, **kwargs): pass
                def make_context(self): return Context.make(self)
            class Context:
                def __init__(self, app): self.app=app
                @classmethod
                def make(cls, app): return cls(app)
        ''', 'cls', ['CLASS:app.Context', 'METHOD:app.Context.__init__'])

    def test_classmethod_called_through_instance_binds_class(self):
        self.expect('''
            class Box:
                @classmethod
                def make(cls): return cls()
            def go(): Box().make()
        ''', 'cls', ['CLASS:app.Box'])

    def test_static_method_called_on_class_has_no_implicit_argument(self):
        self.expect('''
            class Item:
                def run(self): pass
            class Factory:
                @staticmethod
                def identity(obj): return obj
            def go():
                obj=Factory.identity(Item()); obj.run()
        ''', 'obj.run', ['METHOD:app.Item.run'])
