"""Focused semantic probes, not a representative Python accuracy benchmark."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ast_parser import scan_repository
from symbol_builder import build_symbol_result
from graph_builder import build_graph
from unresolved_classifier import reclassify_graph
from call_status import annotate_call_statuses

BASE = 'class A:\n    def run(self): pass\nclass B:\n    def run(self): pass\n'
# Each focus appears once. Targets describe possible internal targets for the
# supplied entry function, over all its inputs. No fixture source is executed.
CASES = [
    ('direct', 'basic', 'def go():\n    obj=A(); obj.run()\n', 'obj.run', ['A.run']),
    ('branch', 'basic', 'def go(flag):\n    if flag: obj=A()\n    else: obj=B()\n    obj.run()\n', 'obj.run', ['A.run', 'B.run']),
    ('factory', 'basic', 'def make(): return A()\ndef go():\n    obj=make(); obj.run()\n', 'obj.run', ['A.run']),
    ('callback', 'basic', 'def invoke(fn): fn()\ndef go(): invoke(A().run)\n', 'fn', ['A.run']),
    ('alias_field', 'basic', 'class Box: pass\ndef go():\n    obj=Box(); alias=obj; obj.worker=A(); alias.worker=B(); obj.worker.run()\n', 'obj.worker.run', ['B.run']),
    ('reassignment_none', 'negative', 'def go():\n    obj=A(); obj=None; obj.run()\n', 'obj.run', []),
    ('unrelated_method', 'negative', 'def go(obj): obj.run()\n', 'obj.run', []),
    ('async_without_await', 'negative', 'async def make(): return A()\ndef go():\n    obj=make(); obj.run()\n', 'obj.run', []),
    ('tuple_unpack', 'boundary', 'def go():\n    obj, other = A(), B()\n    obj.run()\n', 'obj.run', ['A.run']),
    ('list_index', 'boundary', 'def go():\n    items=[A(),B()]; obj=items[0]; obj.run()\n', 'obj.run', ['A.run']),
    ('dict_index', 'boundary', "def go():\n    items={'a':A(),'b':B()}; obj=items['a']; obj.run()\n", 'obj.run', ['A.run']),
    ('getattr_literal', 'boundary', "def go():\n    obj=A(); fn=getattr(obj,'run'); fn()\n", 'fn', ['A.run']),
    ('property', 'boundary', 'class Box:\n    @property\n    def worker(self): return A()\ndef go():\n    obj=Box(); obj.worker.run()\n', 'obj.worker.run', ['A.run']),
    ('await_return', 'boundary', 'async def make(): return A()\nasync def go():\n    obj=await make(); obj.run()\n', 'obj.run', ['A.run']),
    ('context_manager', 'boundary', 'class Box:\n    def __enter__(self): return A()\n    def __exit__(self,*args): pass\ndef go():\n    with Box() as obj: obj.run()\n', 'obj.run', ['A.run']),
    ('match_assignment', 'boundary', 'def go(flag):\n    match flag:\n        case True: obj=A()\n        case _: obj=B()\n    obj.run()\n', 'obj.run', ['A.run', 'B.run']),
    ('two_loop_iterations', 'control', 'def go():\n    obj=A()\n    for i in range(2):\n        if isinstance(obj,A): obj=B()\n        else: obj=A()\n    obj.run()\n', 'obj.run', ['A.run']),
    ('finally_return_override', 'control', 'def make():\n    try: return A()\n    finally: return B()\ndef go():\n    obj=make(); obj.run()\n', 'obj.run', ['B.run']),
    ('constant_false', 'control', 'def go():\n    obj=A()\n    if False: obj=B()\n    obj.run()\n', 'obj.run', ['A.run']),
    ('decorator_replaces', 'dynamic', 'def replace(fn): return B().run\n@replace\ndef original(): pass\ndef go(): original()\n', 'original', ['B.run']),
    ('class_method_patch', 'dynamic', 'def replacement(self): pass\ndef go():\n    A.run=replacement\n    obj=A(); obj.run()\n', 'obj.run', ['FUNCTION:app.replacement']),
    ('closure', 'boundary', 'def make():\n    obj=A()\n    def inner(): return obj\n    return inner\ndef go():\n    fn=make(); obj=fn(); obj.run()\n', 'obj.run', ['A.run']),
]


def evaluate():
    records = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / 'probe'
        root.mkdir()
        for name, group, source, focus, expected in CASES:
            source = BASE + source
            (root / 'app.py').write_text(source)
            times = []
            for _ in range(3):
                start = time.perf_counter()
                facts = scan_repository(root)
                symbols = build_symbol_result(facts)
                graph = annotate_call_statuses(reclassify_graph(build_graph(symbols), symbols), symbols)
                times.append(time.perf_counter() - start)
            rows = [r for r in graph['call_statuses'] if r['callee_text'] == focus]
            if len(rows) != 1:
                raise AssertionError((name, rows))
            row = rows[0]
            actual = set(row['target_ids'])
            expected = {t if ':' in t else 'METHOD:app.' + t for t in expected}
            records.append(dict(name=name, group=group, source=source,
                                source_sha256=hashlib.sha256(source.encode()).hexdigest(),
                                focus=focus, line=row['line'], status=row['status'],
                                certainty=row['certainty'], expected=sorted(expected), actual=sorted(actual),
                                tp=len(expected & actual), fp=len(actual - expected), fn=len(expected - actual),
                                exact=actual == expected, median_seconds=statistics.median(times),
                                parse_errors=facts['parse_errors'], flow=graph.get('object_flow_stats', {})))
    tp, fp, fn = (sum(r[k] for r in records) for k in ('tp', 'fp', 'fn'))
    return dict(python=sys.version, scope='Hand-authored focus-call probes; no population accuracy claim. '
                'Negative unknown-parameter case expects abstention; it is not a complete runtime oracle.',
                cases=len(records), exact=sum(r['exact'] for r in records), tp=tp, fp=fp, fn=fn,
                precision=tp/(tp+fp) if tp+fp else None, recall=tp/(tp+fn) if tp+fn else None,
                groups={g: dict(cases=sum(r['group']==g for r in records),
                                exact=sum(r['exact'] and r['group']==g for r in records))
                        for g in sorted({r['group'] for r in records})}, records=records)


def parser_and_scale_probes():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / 'encoding'
        root.mkdir()
        (root / 'utf8.py').write_text('# 한글\ndef hello(): pass\n', encoding='utf-8')
        (root / 'latin1.py').write_bytes(b'# coding: latin-1\n# caf\xe9\ndef hello(): pass\n')
        (root / 'invalid.py').write_text('def invalid(:\n')
        data = scan_repository(root)
        parser = dict(valid_python_files=2, scanned=data['python_file_count'],
                      errors=data['parse_errors'],
                      note='latin1.py is valid Python with an encoding cookie; invalid.py is intentionally invalid.')
        scaling = []
        for count in (50, 200):
            root = Path(tmp) / f'scale{count}'
            root.mkdir()
            source = 'class Worker:\n    def run(self): pass\ndef go():\n    obj=Worker()\n    obj.run()\n'
            for i in range(count):
                (root / f'module{i}.py').write_text(source)
            times = []
            for _ in range(3):
                start = time.perf_counter()
                facts = scan_repository(root)
                symbols = build_symbol_result(facts)
                graph = annotate_call_statuses(reclassify_graph(build_graph(symbols), symbols), symbols)
                times.append(time.perf_counter() - start)
            scaling.append(dict(files=count, lines=count*5, calls=graph['total_call_sites'],
                                median_seconds=statistics.median(times),
                                cutoffs=graph['object_flow_stats']['recursion_or_budget_cutoffs']))
        return dict(parser=parser, scaling=scaling,
                    scope='Synthetic independent files, excludes Git history, reports and VS Code rendering. Not real-project latency.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'output/probes.json')
    args = parser.parse_args()
    result = evaluate()
    result['supplemental'] = parser_and_scale_probes()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k:v for k,v in result.items() if k != 'records'}, indent=2))
    for r in result['records']:
        print(r['name'], 'PASS' if r['exact'] else 'MISMATCH', r['actual'], 'expected', r['expected'])
