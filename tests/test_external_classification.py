"""External call classification ported from the LJY prototype.

Proven rules become KNOWN_NON_INTERNAL; name/flow guesses only add
`likely_origin` to UNKNOWN rows.
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
from verify_v231 import check_status


class ExternalClassificationTests(unittest.TestCase):
    def analyze(self, source):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'repo'
            root.mkdir()
            (root / 'app.py').write_text(textwrap.dedent(source), encoding='utf-8')
            facts = scan_repository(root)
            self.assertEqual(facts['parse_errors'], [])
            data = build_symbol_result(facts)
        graph = annotate_call_statuses(reclassify_graph(build_graph(data), data), data)
        self.assertEqual(check_status(graph, data), 0)
        return graph

    def row(self, graph, callee):
        rows = [r for r in graph['call_statuses'] if r['callee_text'] == callee]
        self.assertEqual(len(rows), 1, [r['callee_text'] for r in graph['call_statuses']])
        return rows[0]

    def assertKnown(self, row, kind, rule):
        self.assertEqual((row['status'], row['origin_kind'], row['evidence']['rule']),
                         ('KNOWN_NON_INTERNAL', kind, rule))

    def test_method_inherited_from_stdlib_base(self):
        graph = self.analyze('''
            import unittest
            import threading
            class MyTest(unittest.TestCase):
                def test_it(self):
                    self.assertRaises(ValueError, int, 'x')
            class Worker(threading.Thread):
                pass
            def go():
                w = Worker()
                w.start()
        ''')
        row = self.row(graph, 'self.assertRaises')
        self.assertKnown(row, 'STDLIB', 'INHERITED_FROM_EXTERNAL_BASE')
        self.assertEqual(row['evidence']['bases'], ['unittest.TestCase'])
        self.assertKnown(self.row(graph, 'w.start'), 'STDLIB', 'INHERITED_FROM_EXTERNAL_BASE')

    def test_instance_attribute_or_override_blocks_the_claim(self):
        graph = self.analyze('''
            import threading
            class Hooked(threading.Thread):
                def __init__(self, cb):
                    super().__init__()
                    self.start = cb
                def go(self):
                    return self.start()
            class Base(threading.Thread):
                def go(self):
                    return self.join()
            class Child(Base):
                def join(self, timeout=None):
                    return 1
        ''')
        self.assertEqual(self.row(graph, 'self.start')['status'], 'UNKNOWN')
        self.assertEqual(self.row(graph, 'self.join')['status'], 'UNKNOWN')

    def test_stdlib_class_instance_and_import_alias(self):
        graph = self.analyze('''
            import os
            import queue
            def go():
                q = queue.Queue()
                q.put(1)
                q.not_a_queue_method()
                p = os.path
                return p.join('a', 'b')
        ''')
        self.assertKnown(self.row(graph, 'q.put'), 'STDLIB', 'EXTERNAL_CLASS_INSTANCE')
        self.assertEqual(self.row(graph, 'q.not_a_queue_method')['status'], 'UNKNOWN')
        self.assertKnown(self.row(graph, 'p.join'), 'STDLIB', 'FLOW_ALIAS_OF_IMPORT')

    def test_rebinding_blocks_instance_rule(self):
        graph = self.analyze('''
            import queue
            def go(cb):
                q = queue.Queue()
                q.put = cb
                q.put(1)
        ''')
        self.assertEqual(self.row(graph, 'q.put')['status'], 'UNKNOWN')

    def test_likely_origin_hints_stay_unknown(self):
        graph = self.analyze('''
            import copy
            import re
            import requests
            class Client(requests.Session):
                def go(self):
                    return self.mount('x', None)
            def go(text, obj):
                pattern = re.compile('a')
                pattern.findall(text)
                text.strip()
                dup = copy.copy(obj)
                dup.run()
        ''')
        row = self.row(graph, 'pattern.findall')
        self.assertEqual(row['status'], 'UNKNOWN')
        self.assertEqual(row['certainty'], 'UNVERIFIED')
        self.assertEqual(row['likely_origin']['rule'], 'RESULT_OF_EXTERNAL_CALL')
        self.assertEqual(row['likely_origin']['kind'], 'STDLIB')
        row = self.row(graph, 'self.mount')
        self.assertEqual(row['likely_origin']['rule'], 'POSSIBLY_INHERITED_FROM_EXTERNAL_BASE')
        self.assertEqual(row['likely_origin']['unresolved_bases'], ['requests.Session'])
        row = self.row(graph, 'text.strip')
        self.assertEqual(row['likely_origin']['rule'], 'BUILTIN_METHOD_NAME')
        self.assertEqual(row['likely_origin']['confidence'], 'heuristic')
        # copy.copy hands back the caller's object: no external guess.
        self.assertNotIn('likely_origin', self.row(graph, 'dup.run'))

    def test_project_method_name_suppresses_builtin_guess(self):
        graph = self.analyze('''
            class Cleaner:
                def strip(self): return 1
            def go(x):
                return x.strip()
        ''')
        self.assertNotIn('likely_origin', self.row(graph, 'x.strip'))
        summary = graph['status_summary']
        self.assertIn('known_non_internal_rules', summary)
        self.assertIn('unknown_likely_origins', summary)


if __name__ == '__main__':
    unittest.main()
