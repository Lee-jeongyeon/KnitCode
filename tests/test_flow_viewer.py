"""flow_viewer.py: data for the function-centred KnitCode Flow page."""
import json
import re
import tempfile
import textwrap
import unittest
from pathlib import Path

import main as analyzer
from flow_viewer import build_view_data

SOURCE = '''
class Base:
    def step(self): return 0

class Child(Base):
    def step(self): return 1

class Box:
    def __init__(self, n):
        self.n = n
    @property
    def size(self):
        return self.n

def run(b: Base):
    b.step()
    return b.step()

def use():
    box = Box(1)
    marker = "</script><script>alert(1)</script>"
    return box.size
'''


class FlowViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        repo = Path(cls.tmp.name) / 'proj'
        (repo / 'tests').mkdir(parents=True)
        (repo / 'app.py').write_text(textwrap.dedent(SOURCE), encoding='utf-8')
        (repo / 'tests' / 'test_app.py').write_text('from app import use\n\ndef test_use():\n    use()\n',
                                                    encoding='utf-8')
        cls.out = Path(cls.tmp.name) / 'out'
        _, cls.symbols, cls.graph = analyzer.run(repo, cls.out)
        cls.data = build_view_data(cls.graph, cls.symbols)
        cls.ids = {n['id']: i for i, n in enumerate(cls.data['nodes'])}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def edge(self, source, target, kind):
        s, t = self.ids[source], self.ids[target]
        found = [e for e in self.data['edges'] if e[0] == s and e[1] == t and e[2] == kind]
        self.assertEqual(len(found), 1, (source, target, kind))
        return found[0]

    def test_edge_kinds_and_grouped_call_sites(self):
        call = self.edge('FUNCTION:app.run', 'METHOD:app.Base.step', 'call')
        self.assertEqual([s['line'] for s in call[3]], [16, 17])       # two sites, one edge
        self.edge('FUNCTION:app.use', 'METHOD:app.Box.__init__', 'ctor')
        implicit = self.edge('FUNCTION:app.use', 'METHOD:app.Box.size', 'implicit')
        self.assertEqual(implicit[3][0]['ik'], 'property_get')
        # The class node is not drawn when its __init__ is.
        self.assertFalse([e for e in self.data['edges']
                          if e[1] == self.ids['CLASS:app.Box'] and e[0] == self.ids['FUNCTION:app.use']])

    def test_nodes_sites_and_test_flags(self):
        nodes = self.data['nodes']
        test_fn = nodes[self.ids['FUNCTION:tests.test_app.test_use']]
        self.assertTrue(test_fn['t'])
        self.assertFalse(nodes[self.ids['FUNCTION:app.use']]['t'])
        self.assertEqual(test_fn['f'], 'tests/test_app.py')
        rows = self.data['sites'][str(self.ids['FUNCTION:app.run'])]
        self.assertEqual([r[2] for r in rows], ['RESOLVED_INTERNAL', 'RESOLVED_INTERNAL'])
        self.assertIn('app.py', self.data['sources'])
        self.assertEqual(self.data['summary']['implicit'], len(self.graph['implicit_edges']))

    def test_html_embeds_inert_data(self):
        html = (self.out / 'flow.html').read_text(encoding='utf-8')
        payload = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S).group(1)
        self.assertNotIn('</script>', payload)
        data = json.loads(payload)
        self.assertEqual(len(data['nodes']), len(self.data['nodes']))
        self.assertIn('</script><script>alert(1)</script>', data['sources']['app.py'])
        self.assertIn('<title>KnitCode Flow · proj</title>', html)


if __name__ == '__main__':
    unittest.main()
