"""General regressions for false/missing calls exposed by Fabric."""
import unittest
from tests import test_external_calls as helpers


class FabricPrecisionTests(unittest.TestCase):
    external = helpers.ExternalCallTests.external

    def test_builtin_list_base_method(self):
        names=self.external('''
            class Group(list):
                def go(self, values): self.extend(values)
        ''')
        self.assertIn('list.extend', names)

    def test_builtin_dict_base_method(self):
        names=self.external('''
            class Result(dict):
                def go(self): return self.items()
        ''')
        self.assertIn('dict.items', names)

    def test_override_does_not_report_builtin_base(self):
        names=self.external('''
            class Group(list):
                def extend(self, values): pass
                def go(self, values): self.extend(values)
        ''')
        self.assertNotIn('list.extend', names)

    def test_shadowed_builtin_base_is_not_builtin(self):
        names=self.external('''
            class list:
                def extend(self, values): pass
            class Group(list):
                def go(self, values): self.extend(values)
        ''')
        self.assertNotIn('list.extend', names)

    def test_multiple_bases_do_not_guess_builtin_mro(self):
        names=self.external('''
            from vendor import Unknown
            class Group(Unknown, list):
                def go(self, values): self.extend(values)
        ''')
        self.assertNotIn('list.extend', names)

    def test_path_function_return_is_not_function_instance(self):
        names=self.external('''
            import os
            def go():
                path=os.path.abspath('x')
                path.seek()
        ''')
        self.assertIn('os.path.abspath', names)
        self.assertNotIn('os.path.abspath.seek', names)

    def test_no_call_parentheses_in_symbolic_api_path(self):
        names=self.external('''
            import vendor
            def go(): vendor.factory().make().run()
        ''')
        self.assertFalse(any('(' in name or ')' in name for name in names))

    def test_eager_consumption_of_map_records_builtin_callback(self):
        names=self.external('def go(values): return " ".join(map(str, values))')
        self.assertIn('builtins.str', names)

    def test_unused_lazy_map_does_not_invoke_callback(self):
        names=self.external('def go(values): return map(str, values)')
        self.assertNotIn('builtins.str', names)

    def test_empty_map_does_not_invoke_callback(self):
        names=self.external('def go(): return list(map(str, []))')
        self.assertNotIn('builtins.str', names)

    def test_shadowed_map_does_not_report_callback(self):
        names=self.external('def go(map, values): return list(map(str, values))')
        self.assertNotIn('builtins.str', names)

    def test_shadowed_callback_does_not_report_builtin(self):
        names=self.external('def go(str, values): return list(map(str, values))')
        self.assertNotIn('builtins.str', names)

    def test_documented_factory_result_is_not_factory_instance(self):
        names=self.external('''
            from py import path
            def go(): path.local.mkdtemp().join('file.txt')
        ''')
        self.assertIn('py.path.local.mkdtemp', names)
        self.assertNotIn('py.path.local.mkdtemp.join', names)

    def test_factory_result_alias_does_not_restore_snapshot_guess(self):
        names=self.external('''
            from py import path
            def go():
                result=path.local.mkdtemp()
                result.join('file.txt')
        ''')
        self.assertIn('py.path.local.mkdtemp', names)
        self.assertNotIn('py.path.local.mkdtemp.join', names)

    def test_factory_return_contract_does_not_apply_to_unrelated_api(self):
        names=self.external('''
            from other_vendor import path
            def go(): path.local.mkdtemp().join('file.txt')
        ''')
        self.assertIn('other_vendor.path.local.mkdtemp.join', names)
