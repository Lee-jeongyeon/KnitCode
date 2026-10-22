"""Descriptor and classmethod behavior found in the pinned Fabric source."""
import unittest
from tests import test_flask_regressions as helpers


class FabricPropertyTests(unittest.TestCase):
    graph = helpers.FlaskRegressions.graph
    expect = helpers.FlaskRegressions.expect

    def pairs(self, source):
        graph = self.graph(source)
        names = {n['id']: n['canonical_name'] for n in graph['nodes']}
        # Getter runs are implicit calls (implicit_edges), apart from call sites.
        edges = [e for e in graph['edges'] if e['type'] == 'CALLS'] + graph['implicit_edges']
        return {(names[e['source']], names[e['target']]) for e in edges}

    def test_getter_access_has_implicit_edge(self):
        pairs = self.pairs('''
            class Box:
                @property
                def ready(self): return True
                def go(self):
                    if self.ready: return 1
        ''')
        self.assertIn(('app.Box.go', 'app.Box.ready'), pairs)

    def test_property_return_flows_to_method_call(self):
        self.expect('''
            class Worker:
                def run(self): pass
            class Box:
                @property
                def worker(self): return Worker()
                def go(self): self.worker.run()
        ''', 'self.worker.run', ['METHOD:app.Worker.run'])

    def test_inherited_getter_preserves_subclass_receiver(self):
        pairs = self.pairs('''
            class Base:
                @property
                def value(self): return self.make()
                def make(self): pass
            class Child(Base):
                def make(self): pass
            def go(): return Child().value
        ''')
        self.assertIn(('app.go', 'app.Base.value'), pairs)
        self.assertIn(('app.Base.value', 'app.Child.make'), pairs)

    def test_plain_subclass_attribute_shadows_base_property(self):
        pairs = self.pairs('''
            class Base:
                @property
                def value(self): return 1
            class Child(Base): value=2
            def go(): return Child().value
        ''')
        self.assertNotIn(('app.go', 'app.Base.value'), pairs)

    def test_property_data_descriptor_precedes_instance_field(self):
        self.expect('''
            class Worker:
                def run(self): pass
            class Box:
                @property
                def worker(self): return Worker()
                def go(self):
                    self.worker=None
                    self.worker.run()
        ''', 'self.worker.run', ['METHOD:app.Worker.run'])

    def test_user_defined_property_decorator_is_not_builtin(self):
        pairs = self.pairs('''
            def property(fn): return None
            class Box:
                @property
                def value(self): return 1
            def go(): return Box().value
        ''')
        self.assertNotIn(('app.go', 'app.Box.value'), pairs)

    def test_property_class_patch_blocks_original_getter(self):
        pairs = self.pairs('''
            class Box:
                @property
                def value(self): return 1
            def go():
                Box.value=None
                return Box().value
        ''')
        self.assertNotIn(('app.go', 'app.Box.value'), pairs)

    def test_standalone_classmethod_has_class_receiver(self):
        pairs = self.pairs('''
            class Group:
                def __init__(self): pass
                @classmethod
                def make(cls): return cls()
        ''')
        self.assertIn(('app.Group.make', 'app.Group.__init__'), pairs)
