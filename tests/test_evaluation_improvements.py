"""Semantic regressions from the 2026-10-21 engine evaluation."""
import tempfile
import unittest
from pathlib import Path
from ast_parser import scan_repository
from symbol_builder import build_symbol_result
from graph_builder import build_graph
from unresolved_classifier import reclassify_graph
from call_status import annotate_call_statuses


BASE = 'class A:\n    def run(self): pass\nclass B:\n    def run(self): pass\n'


class ImprovementsTests(unittest.TestCase):
    def targets(self, body, callee='obj.run', extra=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'project'
            root.mkdir()
            for name, source in {'app.py': BASE + body, **(extra or {})}.items():
                (root / name).write_text(source, encoding='utf-8')
            facts = scan_repository(root)
            self.assertEqual(facts['parse_errors'], [])
            symbols = build_symbol_result(facts)
            graph = annotate_call_statuses(reclassify_graph(build_graph(symbols), symbols), symbols)
        rows = [r for r in graph['call_statuses'] if r['callee_text'] == callee]
        self.assertEqual(len(rows), 1)
        return set(rows[0]['target_ids']), rows[0]

    def expect(self, body, targets, callee='obj.run', extra=None):
        got, row = self.targets(body, callee, extra)
        self.assertEqual(got, set(targets))
        if not targets:
            self.assertEqual(row['status'], 'UNKNOWN')

    def test_unknown_decorator_abstains(self):
        self.expect('def replace(fn): return B().run\n@replace\ndef original(): pass\n'
                    'def go(): original()\n', [], 'original')

    def test_constructor_does_not_add_unsafe_initializer_edge(self):
        self.expect('def deco(fn): return None\nclass Box:\n    @deco\n'
                    '    def __init__(self): pass\ndef go(): Box()\n',
                    ['CLASS:app.Box'], 'Box')

    def test_decorated_method_return_not_used(self):
        self.expect('def deco(fn): return B().run\nclass Box:\n    @deco\n'
                    '    def create(self): return A()\ndef go():\n'
                    '    obj=Box().create(); obj.run()\n', [])

    def test_class_method_reassignment_abstains(self):
        self.expect('def replacement(self): pass\ndef go():\n'
                    '    A.run=replacement\n    obj=A(); obj.run()\n', [])

    def test_class_alias_reassignment_abstains(self):
        self.expect('def go():\n    Alias=A; Alias.run=None\n    obj=A(); obj.run()\n', [])

    def test_deleted_class_method_abstains(self):
        self.expect('def go():\n    del A.run\n    obj=A(); obj.run()\n', [])

    def test_setattr_class_method_abstains(self):
        self.expect("def go():\n    setattr(A,'run',None)\n    obj=A(); obj.run()\n", [])

    def test_setattr_unknown_attribute_abstains(self):
        self.expect('def go(name):\n    setattr(A,name,None)\n    obj=A(); obj.run()\n', [])

    def test_imported_class_reassignment_abstains(self):
        self.expect('from models import Worker\ndef go():\n    Worker.run=None\n'
                    '    obj=Worker(); obj.run()\n', [], extra={
                        'models.py': 'class Worker:\n    def run(self): pass\n'})

    def test_module_class_alias_reassignment_abstains(self):
        self.expect('import models\ndef go():\n    Alias=models.Worker\n'
                    '    Alias.run=None\n    obj=Alias(); obj.run()\n', [], extra={
                        'models.py': 'class Worker:\n    def run(self): pass\n'})

    def test_patched_factory_return_not_propagated(self):
        self.expect('class Box:\n    def create(self): return A()\n'
                    'def go():\n    Box.create=None; obj=Box().create(); obj.run()\n', [])

    def test_awaited_factory(self):
        self.expect('async def make(): return A()\nasync def go():\n'
                    '    obj=await make(); obj.run()\n', ['METHOD:app.A.run'])

    def test_awaited_factory_with_argument(self):
        self.expect('async def identity(value): return value\nasync def go():\n'
                    '    obj=await identity(B()); obj.run()\n', ['METHOD:app.B.run'])

    def test_async_method_await(self):
        self.expect('class Box:\n    async def make(self): return A()\nasync def go():\n'
                    '    obj=await Box().make(); obj.run()\n', ['METHOD:app.A.run'])

    def test_unawaited_coroutine_is_not_return_value(self):
        self.expect('async def make(): return A()\ndef go():\n'
                    '    obj=make(); obj.run()\n', [])

    def test_async_generator_is_not_awaited_return_value(self):
        self.expect('async def make():\n    yield A()\nasync def go():\n'
                    '    obj=await make(); obj.run()\n', [])

    def test_unknown_awaitable_not_guessed(self):
        self.expect('async def go(external):\n    obj=await external(); obj.run()\n', [])

    def test_unawaited_coroutine_does_not_mutate_fields(self):
        self.expect('class Box: pass\nasync def change(box): box.worker=B()\ndef go():\n'
                    '    box=Box(); box.worker=A(); pending=change(box)\n'
                    '    obj=box.worker; obj.run()\n', ['METHOD:app.A.run'])

    def test_awaited_coroutine_mutates_fields(self):
        self.expect('class Box: pass\nasync def change(box): box.worker=B()\nasync def go():\n'
                    '    box=Box(); box.worker=A(); await change(box)\n'
                    '    obj=box.worker; obj.run()\n', ['METHOD:app.B.run'])

    def test_literal_sequence_indices(self):
        for expression, target in [('[A(),B()][0]', 'A'), ('(A(),B())[-1]', 'B')]:
            with self.subTest(expression=expression):
                self.expect(f'def go():\n    obj={expression}; obj.run()\n',
                            [f'METHOD:app.{target}.run'])

    def test_dict_duplicate_key_and_bool_key(self):
        for expression in ["{'x':A(),'x':B()}['x']", '{True:A(),1:B()}[True]']:
            with self.subTest(expression=expression):
                self.expect(f'def go():\n    obj={expression}; obj.run()\n', ['METHOD:app.B.run'])

    def test_unknown_indices_abstain(self):
        for expression in ['[A()][4]', '[A()][external]', '[A()][0:1]', "{'a':A()}['missing']"]:
            with self.subTest(expression=expression):
                self.expect(f'def go(external):\n    obj={expression}; obj.run()\n', [])

    def test_list_alias_mutation_invalidates_index(self):
        self.expect('def go():\n    values=[A()]; alias=values; alias[0]=B()\n'
                    '    obj=values[0]; obj.run()\n', [])

    def test_list_method_mutation_invalidates_index(self):
        self.expect('def go():\n    values=[A()]; values.clear()\n'
                    '    obj=values[0]; obj.run()\n', [])

    def test_unknown_container_escape_invalidates_index(self):
        self.expect('def go(external):\n    values=[A()]; external(values)\n'
                    '    obj=values[0]; obj.run()\n', [])

    def test_nested_container_escape_invalidates_index(self):
        self.expect('def go(external):\n    values=[A()]; external((values,))\n'
                    '    obj=values[0]; obj.run()\n', [])

    def test_objects_inside_escaped_container_are_invalidated(self):
        self.expect('class Box: pass\ndef go(external):\n'
                    '    box=Box(); box.worker=A(); values=[box]; external(values)\n'
                    '    obj=box.worker; obj.run()\n', [])

    def test_starred_escape_invalidates_container(self):
        self.expect('def go(external):\n    values=[A()]; external(*values)\n'
                    '    obj=values[0]; obj.run()\n', [])

    def test_iteration_after_mutation_does_not_reuse_stale_elements(self):
        self.expect('def go(external):\n    values=[A()]; external(values)\n'
                    '    for obj in values: obj.run()\n', [])

    def test_known_function_can_read_index(self):
        self.expect('def first(values): return values[0]\ndef go():\n'
                    '    obj=first([A()]); obj.run()\n', ['METHOD:app.A.run'])

    def test_indexed_function_reference_is_callable(self):
        self.expect('def target(): pass\ndef go():\n    fn=[target][0]; fn()\n',
                    ['FUNCTION:app.target'], 'fn')

    def test_dict_unknown_key_can_overwrite_known_key(self):
        self.expect("def go(key):\n    obj={'a':A(),key:B()}['a']; obj.run()\n", [])

    def test_constant_false_branch_pruned(self):
        self.expect('def go():\n    obj=A()\n    if False: obj=B()\n'
                    '    obj.run()\n', ['METHOD:app.A.run'])

    def test_finally_return_overrides_try(self):
        self.expect('def make():\n    try: return A()\n    finally: return B()\n'
                    'def go():\n    obj=make(); obj.run()\n', ['METHOD:app.B.run'])

    def test_finally_keeps_earlier_branch_return(self):
        self.expect('def make(flag):\n    if flag: return A()\n'
                    '    try: return A()\n    finally: return B()\n'
                    'def go(flag):\n    obj=make(flag); obj.run()\n',
                    ['METHOD:app.A.run', 'METHOD:app.B.run'])

    def test_finally_heap_of_overridden_return_discarded(self):
        self.expect('class Box: pass\ndef make():\n    box=Box(); box.worker=A()\n'
                    '    try: return box\n    finally:\n        box.worker=B()\n        return box\n'
                    'def go():\n    obj=make().worker; obj.run()\n', ['METHOD:app.B.run'])

    def test_match_union(self):
        self.expect('def go(flag):\n    match flag:\n        case True: obj=A()\n'
                    '        case _: obj=B()\n    obj.run()\n', ['METHOD:app.A.run', 'METHOD:app.B.run'])

    def test_match_capture_shadows_existing_binding(self):
        self.expect('def go(external):\n    obj=A()\n    match external:\n'
                    '        case obj: pass\n    obj.run()\n', [])

    def test_match_capture_cannot_be_classified_as_builtin(self):
        self.expect('def go(external):\n    match external:\n'
                    '        case print: print()\n', [], 'print')

    def test_match_no_match_path_preserved(self):
        self.expect('def go(flag):\n    obj=A()\n    match flag:\n'
                    '        case True: obj=B()\n    obj.run()\n', ['METHOD:app.A.run', 'METHOD:app.B.run'])

    def test_source_encoding_cookie_and_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'latin1.py').write_bytes(b'# coding: latin-1\n# caf\xe9\ndef ok(): pass\n')
            (root/'bom.py').write_text('# 한글\ndef ok(): pass\n', encoding='utf-8-sig')
            facts = scan_repository(root)
            self.assertEqual(facts['parse_errors'], [])
            self.assertEqual(facts['python_file_count'], 2)

    def test_invalid_encoding_reported_not_crashed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'bad.py').write_bytes(b'# coding: unknown-encoding\ndef ok(): pass\n')
            self.assertEqual(len(scan_repository(root)['parse_errors']), 1)
