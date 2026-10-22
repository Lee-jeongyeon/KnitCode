"""Default factory propagation exposed by the pinned autojump benchmark."""
import unittest
from tests import test_flask_regressions as helpers


class DefaultFactoryTests(unittest.TestCase):
    graph = helpers.FlaskRegressions.graph
    expect = helpers.FlaskRegressions.expect
    def test_default_factory_flows_through_instance_field(self):
        self.expect('''
            class Worker:
                def run(self): pass
            class Factory:
                def __init__(self, worker_class=Worker): self.worker_class=worker_class
                def make(self): return self.worker_class()
            def go():
                worker=Factory().make()
                worker.run()
        ''', 'worker.run', ['METHOD:app.Worker.run'])

    def test_explicit_factory_replaces_default(self):
        self.expect('''
            class A:
                def run(self): pass
            class B:
                def run(self): pass
            def make(factory=A): return factory()
            def go():
                worker=make(B)
                worker.run()
        ''', 'worker.run', ['METHOD:app.B.run'])

    def test_keyword_only_default_uses_definition_scope(self):
        self.expect('''
            class Worker:
                def run(self): pass
            def make(*, factory=Worker):
                Worker=None
                return factory()
            def go():
                worker=make()
                worker.run()
        ''', 'worker.run', ['METHOD:app.Worker.run'])

    def test_nested_class_constructor_keeps_its_own_receiver(self):
        self.expect('''
            class Outer:
                class Inner:
                    def run(self): pass
                def make(self): return self.Inner()
            def go():
                worker=Outer().make()
                worker.run()
        ''', 'worker.run', ['METHOD:app.Outer.Inner.run'])

    def test_nested_class_alias(self):
        self.expect('''
            class Outer:
                class Inner:
                    def run(self): pass
                def make(self):
                    factory=self.Inner
                    return factory()
            def go():
                worker=Outer().make()
                worker.run()
        ''', 'worker.run', ['METHOD:app.Outer.Inner.run'])

    def test_instance_field_shadows_nested_class(self):
        self.expect('''
            class Outer:
                class Inner:
                    def run(self): pass
                def make(self):
                    self.Inner=None
                    return self.Inner()
            def go():
                worker=Outer().make()
                worker.run()
        ''', 'worker.run', [])
