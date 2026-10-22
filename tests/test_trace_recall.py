"""trace_recall.py: runtime validation tool."""
import importlib.util
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import main as analyzer
import trace_recall as T

APP = '''\
import functools


class Box:
    def __init__(self, n):
        self.n = n

    @property
    def size(self):
        return self.n

    def grow(self):
        return helper(self.n)


def helper(n):
    return n + 1


@functools.lru_cache(maxsize=None)
def cached(n):
    return [helper(i) for i in range(n)]


def use():
    box = Box(1)
    box.grow()
    return box.size


def untested():
    return helper(2)
'''

TESTS = '''\
from app import use, cached


def test_use():
    assert use() == 1


def test_cached():
    assert cached(2) == [1, 2]
'''


class TraceRecallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / 'proj'
        self.repo.mkdir()
        (self.repo / 'app.py').write_text(APP, encoding='utf-8')
        (self.repo / 'test_app.py').write_text(TESTS, encoding='utf-8')
        self.out = Path(self.tmp.name) / 'out'
        analyzer.run(self.repo, self.out)

    def tearDown(self):
        self.tmp.cleanup()

    def test_function_index_handles_decorators_and_comprehensions(self):
        index = T.FunctionIndex(self.repo)
        self.assertEqual(index.resolve('app.py', 8, 'size'), 'Box.size')     # line of @property
        self.assertEqual(index.resolve('app.py', 20, 'cached'), 'cached')    # line of @lru_cache
        self.assertEqual(index.resolve('app.py', 22, '<listcomp>'), 'cached')
        self.assertIsNone(index.resolve('app.py', 1, '<module>'))

    def synthetic_trace(self, reached_use=28):
        # Shape written by calltrace_plugin (file, first line, name, line, ...).
        return {'root': str(self.repo), 'tests_collected': 2, 'tests_failed': 0, 'calls': [
            ['app.py', 25, 'use', 26, 'app.py', 5, '__init__'],
            ['app.py', 25, 'use', 27, 'app.py', 12, 'grow'],
            ['app.py', 25, 'use', 28, 'app.py', 8, 'size'],
            ['app.py', 12, 'grow', 13, 'app.py', 16, 'helper'],
            ['app.py', 22, '<listcomp>', 22, 'app.py', 16, 'helper'],
            ['test_app.py', 4, 'test_use', 5, 'app.py', 25, 'use'],
        ], 'reached': [['app.py', 25, 'use', reached_use], ['app.py', 12, 'grow', 13]]}

    def write(self, trace):
        path = Path(self.tmp.name) / 'trace.json'
        path.write_text(json.dumps(trace), encoding='utf-8')
        return path

    def test_compare_scores(self):
        report = T.compare(self.out, self.write(self.synthetic_trace()))
        self.assertEqual(report['observed_function_calls'], 6)
        explicit = report['scores']['explicit']
        implicit = report['scores']['implicit_only']
        # Box(1) -> __init__, grow(), helper() x2 and use() are explicit edges;
        # box.size is a property read, i.e. an implicit edge.
        self.assertEqual(explicit['recall_hits'], 5)
        self.assertEqual(implicit['recall_hits'], 1)
        self.assertEqual(report['scores']['explicit+implicit']['recall'], 1.0)
        # untested() never ran, so its edge is not counted against confirmation.
        self.assertEqual(explicit['confirmed'], explicit['edges_on_executed_sites'])
        self.assertIn('trace_frames_not_mapped', report)

    def test_unreached_lines_are_not_counted(self):
        trace = self.synthetic_trace(reached_use=26)
        trace['calls'] = [c for c in trace['calls'] if c[2] != 'use' or c[3] == 26]
        report = T.compare(self.out, self.write(trace))
        explicit = report['scores']['explicit']
        full = T.compare(self.out, self.write(self.synthetic_trace()))['scores']['explicit']
        # use() stopped at line 26, so grow() on line 27 is not an executed
        # site: one edge fewer in the denominator, and still nothing unconfirmed.
        self.assertEqual(explicit['edges_on_executed_sites'], full['edges_on_executed_sites'] - 1)
        self.assertEqual(explicit['confirmed'], explicit['edges_on_executed_sites'])

    @unittest.skipUnless(importlib.util.find_spec('pytest'), 'pytest is not installed')
    def test_record_with_pytest(self):
        trace_path = Path(self.tmp.name) / 'recorded.json'
        T.record(self.repo, trace_path, tests='')
        trace = json.loads(trace_path.read_text(encoding='utf-8'))
        self.assertEqual(trace['tests_failed'], 0)
        report = T.compare(self.out, trace_path)
        self.assertEqual(report['scores']['explicit+implicit']['recall'], 1.0)

    def test_cli_help(self):
        result = subprocess.run([sys.executable, str(Path(T.__file__)), '--help'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        for command in ('prepare', 'record', 'compare', 'run'):
            self.assertIn(command, result.stdout)


if __name__ == '__main__':
    unittest.main()
