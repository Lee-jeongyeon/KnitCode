"""CALLS_OVERRIDE: subclass override candidates kept apart from resolved edges."""
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


class OverrideDispatchTests(unittest.TestCase):
    def analyze(self, source):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'repo'
            root.mkdir()
            (root / 'app.py').write_text(textwrap.dedent(source), encoding='utf-8')
            facts = scan_repository(root)
            self.assertEqual(facts['parse_errors'], [])
            data = build_symbol_result(facts)
        graph = annotate_call_statuses(reclassify_graph(build_graph(data), data), data)
        return data, graph

    def overrides(self, graph, source_text):
        return {e['target'].removeprefix('METHOD:app.'): e['evidence'] for e in graph['override_edges']
                if e['evidence']['source'] == source_text}

    def test_self_call_lists_subclass_overrides(self):
        _, graph = self.analyze('''
            class Base:
                def run(self):
                    return self.step()
                def step(self):
                    return 1
            class Child(Base):
                def step(self):
                    return 2
            class Grand(Child):
                def step(self):
                    return super().step() + 1
            class Plain(Base):
                pass
        ''')
        found = self.overrides(graph, 'self.step()')
        self.assertEqual(set(found), {'Child.step', 'Grand.step'})
        self.assertEqual(found['Child.step']['dispatch_receiver'], 'self_parameter')
        self.assertEqual(found['Child.step']['override_kind'], 'replaces')
        self.assertEqual(found['Grand.step']['override_kind'], 'extends')
        self.assertEqual(found['Grand.step']['inheritance_depth'], 2)
        self.assertEqual(found['Child.step']['base_method_kind'], 'concrete')
        self.assertEqual(found['Child.step']['subclass_location'], 'source')
        self.assertEqual(found['Child.step']['resolved_targets'], ['METHOD:app.Base.step'])
        self.assertEqual(found['Child.step']['base_definitions'], ['METHOD:app.Base.step'])

    def test_declared_type_uses_only_descendants_of_the_hint(self):
        _, graph = self.analyze('''
            class Base:
                def step(self): return 0
            class Left(Base):
                def step(self): return 1
            class LeftChild(Left):
                def step(self): return 2
            def use(x: Left):
                return x.step()
        ''')
        found = self.overrides(graph, 'x.step()')
        self.assertEqual(set(found), {'LeftChild.step'})
        self.assertEqual(found['LeftChild.step']['dispatch_receiver'], 'declared_parameter_type')
        self.assertEqual(found['LeftChild.step']['receiver_class'], 'app.Left')

    def test_constructed_receiver_has_exact_type(self):
        _, graph = self.analyze('''
            class Base:
                def step(self): return 0
            class Child(Base):
                def step(self): return 1
            def make():
                b = Base()
                return b.step()
        ''')
        self.assertEqual(self.overrides(graph, 'b.step()'), {})

    def test_abstract_base_methods(self):
        _, graph = self.analyze('''
            import abc
            class Shape(abc.ABC):
                def describe(self):
                    return self.area(), self.name()
                @abc.abstractmethod
                def area(self): ...
                def name(self):
                    """Subclasses must name themselves."""
                    raise NotImplementedError
            class Square(Shape):
                def area(self): return 4
                def name(self): return 'square'
        ''')
        self.assertEqual(self.overrides(graph, 'self.area()')['Square.area']['base_method_kind'], 'abstract')
        self.assertEqual(self.overrides(graph, 'self.name()')['Square.name']['base_method_kind'], 'abstract')

    def test_classmethod_receiver(self):
        _, graph = self.analyze('''
            class Base:
                @classmethod
                def create(cls):
                    return cls.default()
                @classmethod
                def default(cls): return 0
            class Child(Base):
                @classmethod
                def default(cls): return 1
        ''')
        found = self.overrides(graph, 'cls.default()')
        self.assertEqual(found['Child.default']['dispatch_receiver'], 'class_parameter')

    def test_not_an_open_receiver(self):
        _, graph = self.analyze('''
            class Base:
                @staticmethod
                def helper(self):
                    return self.step()
                def rebound(self, other):
                    self = other
                    return self.step()
                def step(self): return 0
            class Child(Base):
                def step(self): return 1
        ''')
        self.assertEqual(graph['override_edges'], [])

    def test_method_defined_only_in_subclass(self):
        data, graph = self.analyze('''
            class Template:
                def run(self):
                    return self.hook()
            class Impl(Template):
                def hook(self): return 1
        ''')
        found = self.overrides(graph, 'self.hook()')
        self.assertEqual(found['Impl.hook']['base_method_kind'], 'missing')
        self.assertEqual(found['Impl.hook']['resolved_targets'], [])
        # A candidate never turns an unresolved call into a resolved one.
        row = [r for r in graph['call_statuses'] if r['callee_text'] == 'self.hook'][0]
        self.assertEqual(row['status'], 'UNKNOWN')
        self.assertEqual(verify(graph, data), 0)

    def test_external_base_mro(self):
        _, graph = self.analyze('''
            import threading
            import requests
            class AppError(Exception):
                def show(self):
                    return self.message()
                def message(self): return 'x'
            class BadInput(AppError):
                def message(self): return 'bad'
            class Worker(threading.Thread):
                def go(self):
                    return self.work()
            class Job(Worker):
                def work(self): return 1
            class Client(requests.Session):
                def go(self):
                    return self.fetch()
            class Api(Client):
                def fetch(self): return 1
        ''')
        # Builtin and stdlib bases are known MRO nodes now.
        found = self.overrides(graph, 'self.message()')['BadInput.message']
        self.assertEqual(found['base_method_kind'], 'concrete')
        self.assertEqual(found['base_definitions'], ['METHOD:app.AppError.message'])
        self.assertEqual(found['receiver_mro'], 'complete')
        self.assertEqual(self.overrides(graph, 'self.work()')['Job.work']['base_method_kind'], 'missing')
        # A third-party base leaves the MRO open: the base may define it.
        found = self.overrides(graph, 'self.fetch()')['Api.fetch']
        self.assertEqual(found['base_method_kind'], 'unknown')
        self.assertNotEqual(found['receiver_mro'], 'complete')

    def test_candidates_stay_out_of_resolved_edges(self):
        data, graph = self.analyze('''
            class Base:
                def run(self): return self.step()
                def step(self): return 0
            class Child(Base):
                def step(self): return 1
            def use(b: Base):
                return b.step()
        ''')
        self.assertTrue(graph['override_edges'])
        self.assertNotIn('CALLS_OVERRIDE', {e['type'] for e in graph['edges']})
        self.assertEqual(graph['override_summary']['override_edges'], len(graph['override_edges']))
        self.assertEqual(verify(graph, data), 0)


if __name__ == '__main__':
    unittest.main()
