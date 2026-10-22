"""Builtin/stdlib bases as known MRO nodes, and class decorators/metaclasses.

Regression cases from the 2026-10-17 three-prototype comparison:
Sublist3r (multiprocessing.Process before a project base) and Rich
(@rich_repr / @dataclass classes calling their own methods).
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
from external_bases import ExternalClassIndex
from verify_v23 import verify


class ExternalBaseTests(unittest.TestCase):
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
        return graph

    def edges(self, graph, source_text):
        return {e['target'].split(':', 1)[1].removeprefix('app.'): e['evidence'] for e in graph['edges']
                if e['type'] == 'CALLS' and e['evidence'].get('source') == source_text}

    def test_sublist3r_shape_process_before_project_base(self):
        graph = self.analyze('''
            import multiprocessing
            class enumratorBase(object):
                def print_(self, text): return text
                def run(self): return 'base run'
            class enumratorBaseThreaded(multiprocessing.Process, enumratorBase):
                def __init__(self):
                    multiprocessing.Process.__init__(self)
            class GoogleEnum(enumratorBaseThreaded):
                def go(self):
                    self.print_('x')
                    return self.run()
        ''')
        found = self.edges(graph, "self.print_('x')")
        self.assertEqual(set(found), {'enumratorBase.print_'})
        checked = found['enumratorBase.print_']['external_bases_checked']
        self.assertEqual(checked['bases'], ['multiprocessing.Process'])
        # Process defines run(), and it comes first in the MRO.
        self.assertEqual(self.edges(graph, 'self.run()'), {})

    def test_builtin_exception_hierarchy(self):
        graph = self.analyze('''
            class AppError(Exception):
                def message(self): return 'x'
            class BadInput(AppError):
                def show(self):
                    return self.message()
        ''')
        self.assertEqual(set(self.edges(graph, 'self.message()')), {'AppError.message'})

    def test_external_instance_attribute_shadows_project_method(self):
        graph = self.analyze('''
            import threading
            class Mixin:
                def _target(self): return 1
                def helper(self): return 2
            class Worker(threading.Thread, Mixin):
                def go(self):
                    self.helper()
                    return self._target()
        ''')
        self.assertEqual(set(self.edges(graph, 'self.helper()')), {'Mixin.helper'})
        # Thread.__init__ assigns self._target, which wins over the method.
        self.assertEqual(self.edges(graph, 'self._target()'), {})

    def test_third_party_and_shadowed_bases_stay_unknown(self):
        graph = self.analyze('''
            import requests
            class Mixin:
                def helper(self): return 2
            class Client(requests.Session, Mixin):
                def go(self):
                    return self.helper()
            Exception = type('Exception', (), {})
            class Odd(Exception, Mixin):
                def go(self):
                    return self.helper()
        ''')
        self.assertEqual(self.edges(graph, 'self.helper()'), {})

    def test_known_class_decorators_keep_the_full_mro(self):
        graph = self.analyze('''
            from dataclasses import dataclass
            from functools import total_ordering
            import abc
            class Base:
                def shared(self): return 1
            @dataclass(frozen=True)
            @total_ordering
            class Point(Base):
                x: int = 0
                def go(self):
                    return self.shared()
                def __lt__(self, other): return self.x < other.x
            class Shape(Base, metaclass=abc.ABCMeta):
                def go(self):
                    return self.shared()
        ''')
        found = self.edges(graph, 'self.shared()')
        self.assertEqual(set(found), {'Base.shared'})
        self.assertNotIn('certainty', found['Base.shared'])

    def test_decorated_class_follows_jse_strict_policy(self):
        # JSE policy (class_decorators.py): a local method of a decorated class
        # is linked only for a plain class whose decorator is source-proven.
        graph = self.analyze('''
            def rich_repr(cls):
                return cls
            def opaque(cls):
                return make(cls)
            class Base:
                def shared(self): return 1
            @rich_repr
            class Style(Base):
                def render(self):
                    self.shared()
                    return self._make_ansi_codes()
                def _make_ansi_codes(self): return ''
            @rich_repr
            class Plain:
                def render(self):
                    return self.codes()
                def codes(self): return ''
            @opaque
            class Hidden:
                def render(self):
                    return self.codes()
                def codes(self): return ''
        ''')
        found = self.edges(graph, 'self.codes()')
        self.assertEqual(set(found), {'Plain.codes'})
        # A base class or an unproven decorator: nothing is assumed.
        self.assertEqual(self.edges(graph, 'self._make_ansi_codes()'), {})
        self.assertEqual(self.edges(graph, 'self.shared()'), {})

    def test_shadowed_dataclass_is_not_trusted(self):
        graph = self.analyze('''
            from dataclasses import dataclass
            def dataclass(cls):
                return object
            class Base:
                def shared(self): return 1
            @dataclass
            class Point(Base):
                def go(self):
                    return self.shared()
        ''')
        self.assertEqual(self.edges(graph, 'self.shared()'), {})


class ExternalClassIndexTests(unittest.TestCase):
    def test_static_attribute_sets(self):
        index = ExternalClassIndex()
        thread = index.attributes('threading.Thread')
        self.assertIn('run', thread)
        self.assertIn('_target', thread)               # set in Thread.__init__
        self.assertIn('run', index.attributes('multiprocessing.Process'))
        self.assertIn('args', index.attributes('builtins.Exception'))
        # Metaclass magic and conditional definitions are not guessed.
        self.assertIsNone(index.attributes('enum.Enum'))
        self.assertIsNone(index.attributes('builtins.no_such_type'))


if __name__ == '__main__':
    unittest.main()
