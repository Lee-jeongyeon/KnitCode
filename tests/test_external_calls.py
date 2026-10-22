"""Cross-boundary calls must carry provenance without requiring an installation."""
import unittest
from tests import test_flask_regressions as helpers
from ast_parser import scan_repository
from symbol_builder import build_symbol_result
from graph_builder import build_graph
from call_status import annotate_call_statuses
from unresolved_classifier import reclassify_graph
from external_calls import build_external_call_graph
from pathlib import Path
import tempfile
import textwrap


class ExternalCallTests(unittest.TestCase):
    def external(self, source, extra=None):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name, code in {'app.py': source, **(extra or {})}.items():
                (root/name).write_text(textwrap.dedent(code))
            symbols=build_symbol_result(scan_repository(root))
            graph=annotate_call_statuses(reclassify_graph(build_graph(symbols), symbols), symbols)
            result=build_external_call_graph(symbols, graph)
            nodes={n['id']: n['canonical_name'] for n in result['nodes']}
            return {nodes[e['target']] for e in result['edges']}

    def test_imported_function_and_builtin(self):
        self.assertEqual(self.external('''
            from absent_library import work as alias
            def go():
                alias()
                len([])
        '''), {'absent_library.work', 'builtins.len'})

    def test_constructor_receiver(self):
        names=self.external('''
            from absent_library import Client
            def go():
                client=Client()
                client.send()
        ''')
        self.assertIn('absent_library.Client.send', names)

    def test_function_alias_and_callback_argument(self):
        names=self.external('''
            from absent_library import work
            def invoke(fn): fn()
            def go():
                alias=work
                invoke(alias)
        ''')
        self.assertIn('absent_library.work', names)

    def test_constructor_field_crosses_internal_call(self):
        names=self.external('''
            from absent_library import Client
            class Box:
                def __init__(self, client): self.client=client
                def go(self): self.client.send()
            def main(): Box(Client()).go()
        ''')
        self.assertIn('absent_library.Client.send', names)

    def test_parameter_shadow_abstains(self):
        self.assertNotIn('absent_library.work', self.external('''
            from absent_library import work
            def go(work): work()
        '''))

    def test_reassignment_abstains(self):
        self.assertNotIn('absent_library.work', self.external('''
            from absent_library import work
            def go():
                work=None
                work()
        '''))

    def test_unknown_method_does_not_get_global_external_target(self):
        self.assertNotIn('absent_library.Client.send', self.external('''
            from absent_library import Client
            def go(obj): obj.send()
        '''))

    def test_internal_reexport_is_not_external(self):
        self.assertNotIn('helper.work', self.external('''
            from helper import work
            def go(): work()
        ''', {'helper.py': 'def work(): pass\n'}))

    def test_builtin_literal_method(self):
        self.assertIn('str.strip', self.external('def go(): return " x ".strip()'))

    def test_external_base_member(self):
        names=self.external("""
            from absent_library import Base
            class Child(Base):
                def go(self): self.send()
        """)
        self.assertIn('absent_library.Base.send', names)

    def test_explicit_super_external_base(self):
        names=self.external("""
            from absent_library import Base
            class Child(Base):
                def send(self): super().send()
        """)
        self.assertIn('absent_library.Base.send', names)

    def test_own_override_blocks_external_base(self):
        names=self.external("""
            from absent_library import Base
            class Child(Base):
                def send(self): pass
                def go(self): self.send()
        """)
        self.assertNotIn('absent_library.Base.send', names)

    def test_multiple_unknown_bases_do_not_invent_mro(self):
        names=self.external("""
            from absent_library import Base, Other
            class Child(Base, Other):
                def go(self): self.send()
        """)
        self.assertNotIn('absent_library.Base.send', names)
        self.assertNotIn('absent_library.Other.send', names)

    def test_imported_module_patch_abstains(self):
        names=self.external("""
            import absent_library as vendor
            vendor.work=None
            def go(): vendor.work()
        """)
        self.assertNotIn('absent_library.work', names)

    def test_imported_namespace_replacement_blocks_deeper_path(self):
        names=self.external("""
            import absent_library as vendor
            vendor.Client=None
            def go(): vendor.Client.send()
        """)
        self.assertNotIn('absent_library.Client.send', names)
